"""Long-polling Telegram DM bridge -- a presentation channel of the Attention Engine, not a parallel assistant.

DM-only, single-owner gate. Owns no Brain/session/dispatch logic -- on_message/on_approval
callbacks are wired once from main.py, same shape as pet_entry.py's subprocess entry.
"""

import logging
from typing import Awaitable, Callable, Optional, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters

logger = logging.getLogger("charlie.telegram_bot")

OnMessage = Callable[[str, int], Awaitable[None]]
OnApproval = Callable[[str, bool], None]


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


class TelegramBot:
    def __init__(self, token: str, allowed_user_id: int, on_message: OnMessage, on_approval: OnApproval) -> None:
        self._allowed_user_id = allowed_user_id
        self._on_message = on_message
        self._on_approval = on_approval
        self._app = Application.builder().token(token).build()
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))
        self._app.add_handler(CallbackQueryHandler(self._handle_callback))

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

    async def send_approval_request(self, chat_id: int, request_id: str, tool_name: str, reason: str) -> None:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Approve", callback_data=f"approve:{request_id}"),
            InlineKeyboardButton("Decline", callback_data=f"decline:{request_id}"),
        ]])
        await self._app.bot.send_message(
            chat_id=chat_id, text=f"Approval needed: {tool_name}\n{reason}", reply_markup=keyboard
        )

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not is_authorized(user.id if user else None, self._allowed_user_id):
            logger.warning("Ignored Telegram message from unauthorized user %s", user.id if user else None)
            return
        text = update.message.text if update.message else None
        if text and update.effective_chat:
            await self._on_message(text, update.effective_chat.id)

    async def _handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None or not is_authorized(query.from_user.id if query.from_user else None, self._allowed_user_id):
            return
        await query.answer()
        parsed = parse_callback_data(query.data or "")
        if parsed is None:
            return
        request_id, approved = parsed
        self._on_approval(request_id, approved)
        await query.edit_message_reply_markup(reply_markup=None)
