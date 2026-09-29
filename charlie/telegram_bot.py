"""Long-polling Telegram DM bridge for the canonical Attention Engine.

DM-only, single-owner gate. Owns no Brain/session/dispatch logic -- on_message/on_approval
callbacks are wired once from main.py.
"""

import inspect
import logging
import re
from io import BytesIO
from typing import Awaitable, Callable, Optional, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.constants import ChatAction
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters

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


class TelegramBot:
    def __init__(
        self,
        token: str,
        allowed_user_id: int,
        on_message: OnMessage,
        on_approval: OnApproval,
        on_skill_candidate_review: Optional[OnSkillCandidateReview] = None,
    ) -> None:
        self._allowed_user_id = allowed_user_id
        self._on_message = on_message
        self._on_approval = on_approval
        self._on_skill_candidate_review = on_skill_candidate_review
        self._app = Application.builder().token(token).build()
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))
        self._app.add_handler(CallbackQueryHandler(self._handle_callback))
        self._approval_message_ids: dict[str, tuple[int, int]] = {}

    async def start(self) -> None:
        await self._app.initialize()
        await self._app.start()
        # allowed_updates is sticky across webhook/polling switches, so claim every type explicitly.
        await self._app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        logger.info("Telegram bot polling started")

    async def stop(self) -> None:
        await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()

    async def send_message(self, chat_id: int, text: str) -> None:
        await self._app.bot.send_message(chat_id=chat_id, text=text)

    async def send_typing(self, chat_id: int) -> None:
        if chat_id != self._allowed_user_id:
            return
        try:
            await self._app.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:
            logger.warning("Telegram typing action failed", exc_info=True)

    async def send_status_message(self, chat_id: int, text: str) -> Optional[int]:
        if chat_id != self._allowed_user_id:
            return None
        try:
            message = await self._app.bot.send_message(
                chat_id=chat_id, text=text, disable_notification=True
            )
        except Exception:
            logger.warning("Telegram status message send failed", exc_info=True)
            return None
        return getattr(message, "message_id", None)

    async def edit_status_message(self, chat_id: int, message_id: int, text: str) -> None:
        if chat_id != self._allowed_user_id:
            return
        try:
            await self._app.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text)
        except Exception:
            logger.warning("Telegram status message edit failed", exc_info=True)

    async def delete_status_message(self, chat_id: int, message_id: int) -> None:
        if chat_id != self._allowed_user_id:
            return
        try:
            await self._app.bot.delete_message(chat_id=chat_id, message_id=message_id)
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
        message = await self._app.bot.send_message(
            chat_id=chat_id, text=text, reply_markup=keyboard
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
            await self._app.bot.delete_message(chat_id=chat_id, message_id=message_id)
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
            await self._app.bot.send_document(
                chat_id=chat_id,
                document=InputFile(BytesIO(content.encode("utf-8")), filename=f"{name}.SKILL.md"),
                caption=f"Full skill candidate {name}\nSHA-256: {content_hash}",
            )
        await self._app.bot.send_message(
            chat_id=chat_id,
            text=f"Skill candidate review: {name}\nFull SHA-256: {content_hash}\n\n{body}",
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
