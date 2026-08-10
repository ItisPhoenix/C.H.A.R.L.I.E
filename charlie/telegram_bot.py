"""Telegram remote control: long-polling bot, DM-only, single owner user."""

import asyncio
import contextlib
import logging
import re
import time
from typing import Awaitable, Callable, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters

logger = logging.getLogger("charlie.telegram_bot")

OnMessage = Callable[[str, str], Awaitable[None]]
OnApproval = Callable[[str, bool], Awaitable[None]]
OnMedia = Callable[[bytes, str, str], Awaitable[None]]

_MSG_LIMIT = 4096
_TYPING_INTERVAL_S = 2.0
_STREAM_EDIT_INTERVAL_S = 2.0
_QUICK_ACTIONS = (
    ("Screenshot", "take a screenshot"),
    ("Status", "what's your status"),
    ("Stop", "stop"),
)
_THINKING_FRAMES = ("Thinking", "Thinking.", "Thinking..", "Thinking...")


_HEADER_RE = re.compile(r"^#{1,6}[ \t]+(.*)$", re.MULTILINE)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _to_telegram_markdown(text: str) -> str:
    """Normalize the model's CommonMark-ish output to Telegram's legacy Markdown (V1) dialect.

    Telegram has no headers, so '# Title' becomes bold '*Title*'; and its bold syntax is a
    single asterisk, not GFM's double, so '**bold**' becomes '*bold*'. Belt-and-suspenders on
    top of the system prompt already asking for this -- models don't always comply exactly.
    """
    text = _HEADER_RE.sub(r"*\1*", text)
    text = _BOLD_RE.sub(r"*\1*", text)
    return text


def _split_message(text: str, limit: int = _MSG_LIMIT) -> list:
    """Split text into <=limit chunks, preferring newline boundaries."""
    if len(text) <= limit:
        return [text]
    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    return chunks


def _quick_action_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=f"qa:{text}") for label, text in _QUICK_ACTIONS]]
    )


class TelegramBot:
    """Wraps a python-telegram-bot Application; caller drives the async lifecycle explicitly."""

    def __init__(
        self,
        token: str,
        user_id: str,
        on_message: OnMessage,
        on_approval: Optional[OnApproval] = None,
        on_voice: Optional[OnMedia] = None,
        on_photo: Optional[OnMedia] = None,
    ):
        self._token = token
        self._user_id = user_id
        self._on_message = on_message
        self._on_approval = on_approval
        self._on_voice = on_voice
        self._on_photo = on_photo
        self._app: Optional[Application] = None
        self._streams: dict = {}

    def _is_owner(self, update: Update) -> bool:
        return bool(update.effective_user) and str(update.effective_user.id) == self._user_id

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update):
            sender = update.effective_user.id if update.effective_user else "?"
            logger.info("Ignored Telegram message from non-owner user %s", sender)
            return
        if not update.message or not update.message.text or not update.effective_chat:
            return
        chat_id = str(update.effective_chat.id)
        try:
            await self._on_message(update.message.text, chat_id)
        except Exception as e:
            logger.error("Telegram on_message handler failed: %s", e, exc_info=True)

    async def _handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update) or self._on_voice is None:
            return
        if not update.message or not update.message.voice or not update.effective_chat:
            return
        chat_id = str(update.effective_chat.id)
        try:
            tg_file = await update.message.voice.get_file()
            audio_bytes = bytes(await tg_file.download_as_bytearray())
            await self._on_voice(audio_bytes, "", chat_id)
        except Exception as e:
            logger.error("Telegram on_voice handler failed: %s", e, exc_info=True)

    async def _handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update) or self._on_photo is None:
            return
        if not update.message or not update.message.photo or not update.effective_chat:
            return
        chat_id = str(update.effective_chat.id)
        try:
            tg_file = await update.message.photo[-1].get_file()
            photo_bytes = bytes(await tg_file.download_as_bytearray())
            caption = update.message.caption or ""
            await self._on_photo(photo_bytes, caption, chat_id)
        except Exception as e:
            logger.error("Telegram on_photo handler failed: %s", e, exc_info=True)

    async def _handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        logger.info(
            "Telegram callback received: data=%r owner=%s", query.data if query else None, self._is_owner(update)
        )
        if query is None or not self._is_owner(update):
            return
        await query.answer()
        data = query.data or ""
        if data.startswith("approve:") and self._on_approval is not None:
            _, approval_id, decision = data.split(":", 2)
            try:
                await self._on_approval(approval_id, decision == "yes")
            except Exception as e:
                logger.error("Telegram on_approval handler failed: %s", e, exc_info=True)
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception as e:
                logger.debug("Failed to clear Telegram approval buttons: %s", e)
        elif data.startswith("qa:") and update.effective_chat:
            action_text = data[len("qa:"):]
            try:
                await self._on_message(action_text, str(update.effective_chat.id))
            except Exception as e:
                logger.error("Telegram quick-action handler failed: %s", e, exc_info=True)

    async def _handle_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("Telegram error: %s", context.error, exc_info=context.error)

    async def start(self) -> None:
        """Never run_polling() -- explicit initialize -> start -> start_polling, per PTB v22 guidance."""
        self._app = Application.builder().token(self._token).build()
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))
        self._app.add_handler(MessageHandler(filters.VOICE, self._handle_voice))
        self._app.add_handler(MessageHandler(filters.PHOTO, self._handle_photo))
        self._app.add_handler(CallbackQueryHandler(self._handle_callback))
        self._app.add_error_handler(self._handle_error)
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()
        logger.info("Telegram bot started (long-polling)")

    async def stop(self) -> None:
        if self._app is None:
            return
        try:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        except Exception as e:
            logger.warning("Telegram bot shutdown issue (non-fatal): %s", e, exc_info=True)
        self._app = None

    async def _send_chunk(self, chat_id: str, text: str, reply_markup=None) -> None:
        try:
            await self._app.bot.send_message(
                chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup
            )
        except BadRequest as e:
            logger.debug("Markdown parse failed, retrying as plain text: %s", e)
            try:
                await self._app.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
            except Exception as e2:
                logger.warning("Failed to send Telegram message (plain fallback): %s", e2, exc_info=True)
        except Exception as e:
            logger.warning("Failed to send Telegram message: %s", e, exc_info=True)

    async def send_message(self, chat_id: str, text: str, quick_actions: bool = False) -> None:
        if self._app is None or not text:
            return
        chunks = _split_message(_to_telegram_markdown(text))
        for i, chunk in enumerate(chunks):
            markup = _quick_action_keyboard() if quick_actions and i == len(chunks) - 1 else None
            await self._send_chunk(chat_id, chunk, reply_markup=markup)

    async def send_photo(self, chat_id: str, photo: bytes, caption: str = "") -> None:
        if self._app is None:
            return
        try:
            await self._app.bot.send_photo(chat_id=chat_id, photo=photo, caption=caption[:1024])
        except Exception as e:
            logger.warning("Failed to send Telegram photo: %s", e, exc_info=True)

    async def send_document(self, chat_id: str, filename: str, content: bytes, caption: str = "") -> None:
        if self._app is None:
            return
        try:
            await self._app.bot.send_document(
                chat_id=chat_id, document=content, filename=filename, caption=caption[:1024]
            )
        except Exception as e:
            logger.warning("Failed to send Telegram document: %s", e, exc_info=True)

    async def send_approval_prompt(self, chat_id: str, prompt: str, approval_id: str) -> None:
        if self._app is None:
            return
        keyboard = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("Yes", callback_data=f"approve:{approval_id}:yes"),
                InlineKeyboardButton("No", callback_data=f"approve:{approval_id}:no"),
            ]]
        )
        try:
            await self._app.bot.send_message(chat_id=chat_id, text=prompt, reply_markup=keyboard)
        except Exception as e:
            logger.warning("Failed to send Telegram approval prompt: %s", e, exc_info=True)

    @contextlib.asynccontextmanager
    async def typing(self, chat_id: str):
        """Keep Telegram's typing indicator alive for the duration of the with-block."""
        if self._app is None:
            yield
            return

        async def _loop():
            frame = 0
            while True:
                try:
                    await self._app.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                except Exception as e:
                    logger.debug("Failed to send Telegram typing action: %s", e)
                # Animate the placeholder while no real content has streamed in yet (tool calls/search still running).
                state = self._streams.get(chat_id)
                if state is not None and not state["text"]:
                    with contextlib.suppress(Exception):
                        await self._app.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=state["message_id"],
                            text=_THINKING_FRAMES[frame % len(_THINKING_FRAMES)],
                        )
                    frame += 1
                await asyncio.sleep(_TYPING_INTERVAL_S)

        task = asyncio.create_task(_loop())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def stream_start(self, chat_id: str) -> None:
        """Post a placeholder message that stream_append will progressively edit."""
        if self._app is None:
            return
        try:
            msg = await self._app.bot.send_message(chat_id=chat_id, text=_THINKING_FRAMES[0])
        except Exception as e:
            logger.debug("Failed to start Telegram stream: %s", e)
            return
        self._streams[chat_id] = {"message_id": msg.message_id, "text": "", "last_edit": 0.0}

    async def stream_append(self, chat_id: str, chunk: str) -> None:
        """Append text to the active stream, editing the placeholder at most every _STREAM_EDIT_INTERVAL_S."""
        state = self._streams.get(chat_id)
        if state is None or self._app is None:
            return
        state["text"] += chunk
        now = time.monotonic()
        if now - state["last_edit"] < _STREAM_EDIT_INTERVAL_S:
            return
        if len(state["text"]) > _MSG_LIMIT:
            # Overflowed the single-message budget -- finalize this bubble, start a fresh one for the rest.
            await self._flush_stream_edit(chat_id, state["text"][:_MSG_LIMIT])
            overflow = state["text"][_MSG_LIMIT:]
            await self.stream_start(chat_id)
            self._streams[chat_id]["text"] = overflow
            return
        state["last_edit"] = now
        await self._flush_stream_edit(chat_id, state["text"])

    async def _flush_stream_edit(self, chat_id: str, text: str) -> None:
        state = self._streams.get(chat_id)
        if state is None:
            return
        try:
            await self._app.bot.edit_message_text(
                chat_id=chat_id, message_id=state["message_id"], text=text or "..."
            )
        except BadRequest as e:
            if "not modified" not in str(e).lower():
                logger.debug("Telegram stream edit failed: %s", e)
        except Exception as e:
            logger.debug("Telegram stream edit failed: %s", e)

    async def stream_finish(self, chat_id: str, quick_actions: bool = False) -> None:
        """Final edit with Markdown parse mode (falling back to plain on parse failure), then clear stream state."""
        state = self._streams.pop(chat_id, None)
        if state is None or self._app is None:
            return
        text = state["text"].strip()
        if not text:
            with contextlib.suppress(Exception):
                await self._app.bot.delete_message(chat_id=chat_id, message_id=state["message_id"])
            return
        text = _to_telegram_markdown(text)
        markup = _quick_action_keyboard() if quick_actions else None
        try:
            await self._app.bot.edit_message_text(
                chat_id=chat_id,
                message_id=state["message_id"],
                text=text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=markup,
            )
        except BadRequest:
            with contextlib.suppress(Exception):
                await self._app.bot.edit_message_text(
                    chat_id=chat_id, message_id=state["message_id"], text=text, reply_markup=markup
                )
        except Exception as e:
            logger.debug("Telegram stream final edit failed: %s", e)


# Module-level registry (same out-of-band pattern as charlie.tools.set_event_bus).
_active_bot: Optional[TelegramBot] = None


def set_active_bot(bot: Optional[TelegramBot]) -> None:
    global _active_bot
    _active_bot = bot


def get_active_bot() -> Optional[TelegramBot]:
    return _active_bot
