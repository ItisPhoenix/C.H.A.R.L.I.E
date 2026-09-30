"""Long-polling Telegram DM bridge for the canonical Attention Engine.

DM-only, single-owner gate. Owns no Brain/session/dispatch logic -- on_message/on_approval
callbacks are wired once from main.py.
"""

# Postponed annotation evaluation: ContextTypes.DEFAULT_TYPE appears in handler signatures and
# would otherwise be resolved while the class body executes, which happens even when the
# optional telegram extra is missing. This keeps the module importable without it.
from __future__ import annotations

import asyncio
import inspect
import logging
import re
from datetime import timedelta
from io import BytesIO
from typing import Any, Awaitable, Callable, Optional, Tuple

# python-telegram-bot is an optional extra (pyproject "telegram") and CI deliberately does not
# install it. Importing it unguarded used to make this module fail at collection time, which
# aborted the whole pytest run instead of skipping Telegram tests. Mirrors the guarded pattern
# in charlie/browser/__init__.py: callers check TELEGRAM_AVAILABLE before constructing the bridge.
try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
    from telegram.constants import ChatAction
    from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut
    from telegram.ext import (
        AIORateLimiter,
        Application,
        CallbackQueryHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )

    _HAS_TELEGRAM = True
    TELEGRAM_IMPORT_ERROR: Optional[BaseException] = None
except ImportError as exc:  # pragma: no cover - the guarded path is asserted by reload tests
    _HAS_TELEGRAM = False
    TELEGRAM_IMPORT_ERROR = exc
    # Stand in as None rather than leaving the names undefined so that any use that slips past
    # the constructor guard fails with a clear AttributeError instead of a bare NameError.
    InlineKeyboardButton = InlineKeyboardMarkup = InputFile = Update = None  # type: ignore[assignment]
    ChatAction = None  # type: ignore[assignment]
    NetworkError = RetryAfter = TimedOut = None  # type: ignore[assignment]
    BadRequest = None  # type: ignore[assignment]
    AIORateLimiter = Application = CallbackQueryHandler = None  # type: ignore[assignment]
    ContextTypes = MessageHandler = filters = None  # type: ignore[assignment]

TELEGRAM_AVAILABLE = _HAS_TELEGRAM

TELEGRAM_IMPORT_HINT = (
    "Telegram support is unavailable: python-telegram-bot is an optional dependency of "
    "C.H.A.R.L.I.E. and is not installed. Install it with "
    "`pip install \"charlie[telegram]\"` (or `pip install \"python-telegram-bot[rate-limiter]\"` "
    "to also enable the client-side rate limiter)."
)

# The Bot API hard-rejects any text message over 4096 characters. Nine main.py call sites send
# unbounded assistant text here (background results, watcher digests, long replies), and the
# BadRequest they trigger is swallowed by the caller's bare `except Exception` -- so an overlong
# reply means the user silently receives nothing. Every send path must respect this.
#
# The API counts UTF-16 CODE UNITS, not Python code points. An astral character (emoji,
# regional-indicator flag, many CJK extensions) is one code point but two units, so budgeting
# with len(text) produces chunks the API rejects. Every split/clip decision here therefore goes
# through _utf16_len, never len().
TELEGRAM_MAX_MESSAGE_CHARS = 4096

# Telegram answers a burst with 429 RetryAfter and a flaky network with NetworkError/TimedOut.
# Both are transient by definition, so they are retried. The caps exist so a single stuck send
# cannot hold an assistant turn open: a rate-limit sleep is honoured as asked but never beyond
# MAX_RETRY_AFTER_SECONDS, and transport errors back off exponentially up to the ceiling.
MAX_SEND_ATTEMPTS = 3
MAX_RETRY_AFTER_SECONDS = 30.0
RETRY_BACKOFF_BASE_SECONDS = 0.5
RETRY_BACKOFF_MAX_SECONDS = 8.0

# The Bot API refuses uploads over 50 MB. Guarding before the call keeps an oversized send from
# surfacing as an opaque BadRequest that callers swallow.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

# Test seam: backoff policy is asserted without ever waiting on a real delay.
_SLEEP = asyncio.sleep

# Boundary preference for chunking, most semantically meaningful first. The bare-space
# fallback inside _find_safe_cut is what stops a chunk from ending mid-word.
_SPLIT_BOUNDARIES = ("\n\n", "\n")
_SENTENCE_BOUNDARIES = (". ", "! ", "? ")

# Content after a hard split is NOT lost -- it continues in the next message -- so the marker
# says the block was cut and where the rest went.
HARD_SPLIT_MARKER = (
    "\n\n[... cut here: this block alone is longer than the "
    f"{TELEGRAM_MAX_MESSAGE_CHARS}-character Telegram limit, so it is split mid-block. "
    "The rest continues in the next message ...]"
)

# Content after a truncation IS lost, so the marker says so rather than implying a continuation.
TRUNCATION_MARKER = (
    "\n\n[... truncated: this message is over the "
    f"{TELEGRAM_MAX_MESSAGE_CHARS}-character Telegram limit and cannot be sent in full ...]"
)

logger = logging.getLogger("charlie.telegram_bot")

OnMessage = Callable[[str, int], Awaitable[None]]
OnApproval = Callable[[str, bool], bool]
OnSkillCandidateReview = Callable[[str, str], Awaitable[bool] | bool]


def is_authorized(user_id: Optional[int], allowed_user_id: int) -> bool:
    return user_id is not None and user_id == allowed_user_id


def should_relay_approval(bot_available: bool, allowed_user_id: int) -> bool:
    """Telegram approval is additive for every action origin when the owner channel is live."""
    return bot_available and allowed_user_id > 0


def parse_callback_data(data: str) -> Optional[Tuple[str, bool]]:
    action, _, request_id = data.partition(":")
    if not request_id or action not in ("approve", "decline"):
        return None
    return request_id, action == "approve"


def parse_skill_review_callback_data(data: str) -> Optional[Tuple[str, str]]:
    action, _, token = data.partition(":")
    if not token or not re.fullmatch(r"skill_(?:approve|reject|disable):[a-f0-9]{16}", data):
        return None
    return token, action.removeprefix("skill_")


def parse_text_approval_response(text: str) -> Optional[bool]:
    """Telegram text may decline a request; approval requires its matching button."""
    return False if text.strip().casefold() in {"no", "cancel"} else None


def is_text_approval_attempt(text: str) -> bool:
    return text.strip().casefold() in {"yes", "y", "approve", "approved"}


def _seconds_until(retry_after: Any) -> float:
    """RetryAfter.retry_after is a plain int in some PTB releases and a timedelta in others."""
    if isinstance(retry_after, timedelta):
        return retry_after.total_seconds()
    return float(retry_after)


def _utf16_len(text: str) -> int:
    """Length of `text` the way the Bot API measures it: UTF-16 code units.

    `len(text)` counts code points, which undercounts every astral character by one unit. The
    Bot API rejects anything past 4096 UNITS, so budgeting on code points silently overflows.
    """
    return len(text.encode("utf-16-le")) // 2


def _utf16_prefix(text: str, budget: int) -> str:
    """Longest prefix of `text` that fits in `budget` UTF-16 code units.

    Bytes are sliced instead of counting code points one at a time, so the cost is a single
    encode. An even byte count can still cut an astral character in half, leaving an orphaned
    high surrogate at the end; that half-character is dropped rather than kept, so a prefix
    always ends on a whole character and the result decodes cleanly.
    """
    if budget <= 0:
        return ""
    raw = text.encode("utf-16-le")
    if len(raw) <= budget * 2:
        return text
    head = raw[: budget * 2]
    try:
        return head.decode("utf-16-le")
    except UnicodeDecodeError:
        return head[:-2].decode("utf-16-le")


def _find_safe_cut(text: str, limit: int) -> Optional[int]:
    """Index in `text` to cut at so the chunk fits in `limit` UTF-16 code units.

    Preference order is readability order: blank line, then line, then sentence end, then a
    bare space. The space fallback is what keeps the cut from landing mid-word.

    The search window is the longest prefix within the unit budget, not `text[:limit]` -- a
    code-point window of `limit` would overflow the budget for any astral-heavy text and hand
    back a cut that the API rejects.

    Returns None when no boundary exists anywhere inside the window -- a single run longer
    than a whole message (a base64 blob, an unbroken URL, one enormous word). There is no
    safe cut to make, so the caller hard-splits with an explicit marker rather than hanging.

    Every non-None return is >= 1, which is what guarantees the split loop makes progress.
    """
    window = _utf16_prefix(text, limit)
    for boundary in _SPLIT_BOUNDARIES:
        index = window.rfind(boundary)
        if index > 0:
            return index + len(boundary)
    index = max(window.rfind(boundary) for boundary in _SENTENCE_BOUNDARIES)
    if index > 0:
        return index + len(_SENTENCE_BOUNDARIES[0])
    space = window.rfind(" ")
    if space > 0:
        return space + 1
    return None


def split_message_text(text: str, limit: int = TELEGRAM_MAX_MESSAGE_CHARS) -> list[str]:
    """Split `text` into sequential chunks, each at most `limit` UTF-16 code units.

    UTF-16 units are the unit the Bot API counts, so that is the unit the budget is enforced
    in. For pure BMP text one unit is one character and the chunking is identical to a
    character budget; astral characters (emoji, flags, CJK extensions) cost two units each, and
    they are accounted for rather than assumed to be free.

    Reads as one continuous message to the user: chunks break on paragraph, line or sentence
    boundaries so a reply is never severed in the middle of a thought. Slicing never splits a
    code point, so a multi-code-point emoji is not torn in half either (Python 3 strings are
    UCS-4, so one emoji is one element, not a surrogate pair).
    """
    if limit <= 0:
        raise ValueError("split_message_text requires a positive limit")
    if _utf16_len(text) <= limit:
        return [text]
    parts: list[str] = []
    remaining = text
    while _utf16_len(remaining) > limit:
        cut = _find_safe_cut(remaining, limit)
        if cut is None:
            room = limit - _utf16_len(HARD_SPLIT_MARKER)
            if room < 2:
                # 2 is the widest a single character gets (an astral one is a surrogate pair),
                # so a smaller remainder cannot hold the marker plus one whole character.
                raise ValueError("limit is too small to hold the hard-split marker")
            # No boundary exists, so the block is cut mid-run. The remainder is not dropped --
            # it becomes the next chunk -- but the marker is appended so the reader can see the
            # seam rather than assume the text simply ended there.
            head = _utf16_prefix(remaining, room)
            parts.append(head + HARD_SPLIT_MARKER)
            remaining = remaining[len(head) :]
            continue
        head = remaining[:cut]
        # Whitespace at a break point is layout, not content; dropping it keeps chunks clean.
        parts.append(head.rstrip() or head)
        remaining = remaining[cut:]
    parts.append(remaining)
    return parts


def clip_message_text(
    text: str, limit: int = TELEGRAM_MAX_MESSAGE_CHARS, marker: str = TRUNCATION_MARKER
) -> str:
    """Single-message variant of split_message_text, for surfaces that must stay one message.

    Status messages and approval prompts are addressed by message_id and later edited in place,
    so chunking them would orphan the handle the caller is holding. Truncating with a visible
    marker is the honest alternative: the user sees that something was cut. The clip point is
    chosen in UTF-16 code units, so an emoji-heavy text is cut before the API limit rather
    than after it.
    """
    if _utf16_len(text) <= limit:
        return text
    room = limit - _utf16_len(marker)
    if room <= 0:
        raise ValueError("limit is too small to hold the truncation marker")
    return _utf16_prefix(text, room) + marker


def ensure_upload_within_limit(
    payload: Any, *, limit: int = MAX_UPLOAD_BYTES, label: str = "document"
) -> int:
    """Reject an oversized upload before the Bot API call; return the payload's byte size.

    Telegram answers an over-50 MB upload with a 400 that is indistinguishable from any other
    BadRequest, and callers here wrap sends in bare `except Exception`, so the failure mode is
    a command that claims to have attached a file and attached nothing. This is a pure size
    check, ready for any future document path as well as the skill-candidate upload.
    """
    size = payload if isinstance(payload, int) else len(payload)
    if size < 0:
        raise ValueError(f"Telegram {label} size must not be negative")
    if size > limit:
        raise ValueError(
            f"Telegram {label} is {size} bytes, over the {limit}-byte upload limit "
            f"({size / (1024 * 1024):.1f} MiB vs {limit / (1024 * 1024):.0f} MiB). "
            "Split or compress it, or send a summary instead."
        )
    return size


def build_rate_limiter() -> Optional[Any]:
    """Return an AIORateLimiter, or None when PTB's optional rate-limiter extra is absent.

    AIORateLimiter imports from telegram.ext but raises RuntimeError at construction unless
    `aiolimiter` is installed, i.e. unless python-telegram-bot[rate-limiter] was used. Failing
    the whole bridge over an optional latency tweak would be a worse outcome than running
    without it, so the limiter degrades to None and _send_with_retry stays the correctness
    backstop for the same 429s.
    """
    if AIORateLimiter is None:
        return None
    try:
        return AIORateLimiter()
    except RuntimeError as exc:
        logger.warning("Client-side Telegram rate limiter unavailable: %s", exc)
        return None


class TelegramBot:
    def __init__(
        self,
        token: str,
        allowed_user_id: int,
        on_message: OnMessage,
        on_approval: OnApproval,
        on_skill_candidate_review: Optional[OnSkillCandidateReview] = None,
    ) -> None:
        # Fail here, loudly and with an install command, instead of letting the caller build a
        # half-usable bridge or discover the problem as a silent no-op channel.
        if not TELEGRAM_AVAILABLE:
            raise RuntimeError(
                f"{TELEGRAM_IMPORT_HINT} (import error: {TELEGRAM_IMPORT_ERROR!r})"
            )
        self._allowed_user_id = allowed_user_id
        self._on_message = on_message
        self._on_approval = on_approval
        self._on_skill_candidate_review = on_skill_candidate_review
        self._app = (
            Application.builder()
            .token(token)
            .rate_limiter(build_rate_limiter())
            .build()
        )
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))
        self._app.add_handler(CallbackQueryHandler(self._handle_callback))
        self._approval_message_ids: dict[str, tuple[int, int]] = {}

    async def _send_with_retry(
        self, send: Callable[..., Awaitable[Any]], **kwargs: Any
    ) -> Any:
        """The single outbound-send path for every Bot API call this bridge makes.

        Two transient failure modes reach this bridge: 429 RetryAfter on a burst, and
        NetworkError/TimedOut on a flaky socket. Both are retryable by definition, and the
        main.py call sites wrap this bridge in bare `except Exception` -- so without a retry
        here, one rate-limited send silently becomes a message the user never receives.

        RetryAfter is honoured exactly as Telegram asks, capped at MAX_RETRY_AFTER_SECONDS so a
        pathological retry_after value cannot pin an assistant turn open for minutes. Transport
        errors use capped exponential backoff. Everything else (BadRequest, Forbidden,
        ChatMigrated) is permanent: it is re-raised on the first attempt so the caller's own
        handling still applies and no delay is added to an error that will not fix itself.
        """
        attempt = 0
        while True:
            attempt += 1
            try:
                return await send(**kwargs)
            except RetryAfter as exc:
                if attempt < MAX_SEND_ATTEMPTS:
                    delay = min(_seconds_until(exc.retry_after), MAX_RETRY_AFTER_SECONDS)
                    logger.warning(
                        "Telegram rate limited a send (attempt %s/%s); waiting %.1fs as instructed",
                        attempt, MAX_SEND_ATTEMPTS, delay,
                    )
                    await _SLEEP(delay)
                    continue
                logger.error("Telegram still rate limited after %s attempts", MAX_SEND_ATTEMPTS)
                raise
            except BadRequest:
                # Must be caught before NetworkError: PTB models BadRequest as a SUBCLASS of
                # NetworkError, so a bare `except NetworkError` would retry permanent 400s
                # (message too long, chat not found, malformed markup) three times with backoff
                # and only then surface the error the caller already had a handler for.
                raise
            except (NetworkError, TimedOut) as exc:
                if attempt < MAX_SEND_ATTEMPTS:
                    delay = min(
                        RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)),
                        RETRY_BACKOFF_MAX_SECONDS,
                    )
                    logger.warning(
                        "Telegram transport error on send (attempt %s/%s); retrying in %.1fs: %s",
                        attempt, MAX_SEND_ATTEMPTS, delay, exc,
                    )
                    await _SLEEP(delay)
                    continue
                logger.error("Telegram send failed after %s attempts: %s", MAX_SEND_ATTEMPTS, exc)
                raise

    async def start(self) -> None:
        # Repeated from __init__ so a bridge rebuilt without the constructor (tests, recovery
        # paths) still refuses to poll a channel that does not exist.
        if not TELEGRAM_AVAILABLE:
            raise RuntimeError(TELEGRAM_IMPORT_HINT)
        await self._app.initialize()
        await self._app.start()
        # allowed_updates is sticky across webhook/polling switches, so claim every type explicitly.
        await self._app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        logger.info("Telegram bot polling started")

    async def stop(self) -> None:
        await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()

    async def send_message(self, chat_id: int, text: str) -> Optional[int]:
        """Send `text`, splitting it into sequential messages when it exceeds the Bot API limit.

        Returns the FIRST chunk's message id. main.py keys background-task ack cleanup off this
        value, and the first chunk is the one the user sees anchored in the transcript; later
        chunks are continuation text with nothing useful to edit or delete.
        """
        first_message_id: Optional[int] = None
        for part in split_message_text(text):
            message = await self._send_with_retry(
                self._app.bot.send_message, chat_id=chat_id, text=part
            )
            if first_message_id is None:
                first_message_id = getattr(message, "message_id", None)
        return first_message_id

    async def send_typing(self, chat_id: int) -> None:
        if chat_id != self._allowed_user_id:
            return
        try:
            await self._send_with_retry(
                self._app.bot.send_chat_action, chat_id=chat_id, action=ChatAction.TYPING
            )
        except Exception:
            logger.warning("Telegram typing action failed", exc_info=True)

    async def send_status_message(self, chat_id: int, text: str) -> Optional[int]:
        if chat_id != self._allowed_user_id:
            return None
        try:
            message = await self._send_with_retry(
                self._app.bot.send_message,
                chat_id=chat_id,
                text=clip_message_text(text),
                disable_notification=True,
            )
        except Exception:
            logger.warning("Telegram status message send failed", exc_info=True)
            return None
        return getattr(message, "message_id", None)

    async def edit_status_message(self, chat_id: int, message_id: int, text: str) -> None:
        if chat_id != self._allowed_user_id:
            return
        try:
            await self._send_with_retry(
                self._app.bot.edit_message_text,
                chat_id=chat_id,
                message_id=message_id,
                text=clip_message_text(text),
            )
        except Exception:
            logger.warning("Telegram status message edit failed", exc_info=True)

    async def delete_status_message(self, chat_id: int, message_id: int) -> None:
        if chat_id != self._allowed_user_id:
            return
        try:
            await self._send_with_retry(
                self._app.bot.delete_message, chat_id=chat_id, message_id=message_id
            )
        except Exception:
            logger.warning("Telegram status message delete failed", exc_info=True)


    async def send_approval_request(
        self,
        chat_id: int,
        request_id: str,
        tool_name: str,
        reason: str,
        operation_preview: Optional[str] = None,
    ) -> Optional[int]:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Approve", callback_data=f"approve:{request_id}"),
            InlineKeyboardButton("Decline", callback_data=f"decline:{request_id}"),
        ]])
        text = f"Please review this action\n{reason}"
        if operation_preview:
            label = "Command" if tool_name == "shell_execute" else "Action"
            text += f"\n\n{label}:\n{operation_preview}"
        # Clipped rather than split: the inline keyboard lives on this one message, so chunking
        # would strand the buttons on a chunk the user had no reason to keep.
        message = await self._send_with_retry(
            self._app.bot.send_message,
            chat_id=chat_id,
            text=clip_message_text(text),
            reply_markup=keyboard,
        )
        message_id = getattr(message, "message_id", None)
        if message_id is not None:
            if not hasattr(self, "_approval_message_ids"):
                self._approval_message_ids = {}
            self._approval_message_ids[request_id] = (chat_id, int(message_id))
        return message_id

    async def delete_approval_message(
        self,
        request_id: str,
        *,
        chat_id: Optional[int] = None,
        message_id: Optional[int] = None,
    ) -> None:
        """Remove an approval prompt after it resolves or expires."""
        stored = getattr(self, "_approval_message_ids", {}).pop(request_id, None)
        if stored is not None:
            chat_id, message_id = stored
        if chat_id is None or message_id is None:
            return
        try:
            await self._send_with_retry(
                self._app.bot.delete_message, chat_id=chat_id, message_id=message_id
            )
        except Exception:
            logger.warning("Telegram approval message delete failed", exc_info=True)

    async def send_skill_candidate_review(
        self, chat_id: int, name: str, content_hash: str, review_token: str, content: str
    ) -> None:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Approve skill", callback_data=f"skill_approve:{review_token}"),
            InlineKeyboardButton("Reject skill", callback_data=f"skill_reject:{review_token}"),
        ]])
        full_document = len(content) > 3000
        body = content if not full_document else content[:3000] + "\n[Full SKILL.md attached above.]"
        if full_document:
            payload = content.encode("utf-8")
            # Checked before the call so an oversized skill fails with a legible reason rather
            # than an opaque BadRequest the caller would swallow.
            ensure_upload_within_limit(payload, label=f"SKILL.md for {name}")
            await self._send_with_retry(
                self._app.bot.send_document,
                chat_id=chat_id,
                document=InputFile(BytesIO(payload), filename=f"{name}.SKILL.md"),
                caption=f"Full skill candidate {name}\nSHA-256: {content_hash}",
            )
        await self._send_with_retry(
            self._app.bot.send_message,
            chat_id=chat_id,
            text=clip_message_text(
                f"Skill candidate review: {name}\nFull SHA-256: {content_hash}\n\n{body}"
            ),
            reply_markup=keyboard,
        )


    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        chat = update.effective_chat
        if (
            not is_authorized(user.id if user else None, self._allowed_user_id)
            or chat is None
            or chat.type != "private"
            or chat.id != self._allowed_user_id
        ):
            logger.warning("Ignored Telegram message outside authorized private chat")
            return
        text = update.message.text if update.message else None
        if text:
            self._app.create_task(self._on_message(text, chat.id), update=update)

    async def _handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None or not is_authorized(query.from_user.id if query.from_user else None, self._allowed_user_id):
            return
        await query.answer()
        skill_review = parse_skill_review_callback_data(query.data or "")
        if skill_review is not None:
            if self._on_skill_candidate_review is None:
                accepted = False
            else:
                token, action = skill_review
                result = self._on_skill_candidate_review(token, action)
                accepted = await result if inspect.isawaitable(result) else result
            if accepted:
                if skill_review[1] == "approve":
                    keyboard = InlineKeyboardMarkup([[
                        InlineKeyboardButton("Disable update", callback_data=f"skill_disable:{skill_review[0]}"),
                    ]])
                    await query.edit_message_reply_markup(reply_markup=keyboard)
                else:
                    await query.edit_message_reply_markup(reply_markup=None)
            else:
                await query.edit_message_text(
                    "This skill review is no longer pending. Check the current candidate status.",
                    reply_markup=None,
                )
            return
        parsed = parse_callback_data(query.data or "")
        if parsed is None:
            return
        request_id, approved = parsed
        message = getattr(query, "message", None)
        fallback_chat_id = getattr(getattr(message, "chat", None), "id", None)
        fallback_message_id = getattr(message, "message_id", None)
        try:
            self._on_approval(request_id, approved)
        finally:
            await self.delete_approval_message(
                request_id,
                chat_id=fallback_chat_id,
                message_id=fallback_message_id,
            )
