from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from charlie.telegram_bot import TelegramBot, _split_message, _to_telegram_markdown, get_active_bot, set_active_bot


def _make_update(user_id=None, text=None, chat_id=None, callback_data=None):
    user = SimpleNamespace(id=user_id) if user_id is not None else None
    message = SimpleNamespace(text=text) if text is not None else None
    chat = SimpleNamespace(id=chat_id) if chat_id is not None else None
    callback_query = None
    if callback_data is not None:
        callback_query = SimpleNamespace(
            data=callback_data, answer=AsyncMock(), edit_message_reply_markup=AsyncMock()
        )
    return SimpleNamespace(
        effective_user=user, message=message, effective_chat=chat, callback_query=callback_query
    )


@pytest.mark.asyncio
async def test_owner_message_dispatches():
    on_message = AsyncMock()
    bot = TelegramBot("token", "123", on_message)
    update = _make_update(user_id=123, text="hello", chat_id=999)
    await bot._handle_message(update, None)
    on_message.assert_awaited_once_with("hello", "999")


@pytest.mark.asyncio
async def test_non_owner_message_ignored():
    on_message = AsyncMock()
    bot = TelegramBot("token", "123", on_message)
    update = _make_update(user_id=456, text="hello", chat_id=999)
    await bot._handle_message(update, None)
    on_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_callback_approval_dispatches():
    on_approval = AsyncMock()
    bot = TelegramBot("token", "123", AsyncMock(), on_approval=on_approval)
    update = _make_update(user_id=123, callback_data="approve:req1:yes")
    await bot._handle_callback(update, None)
    on_approval.assert_awaited_once_with("req1", True)
    update.callback_query.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_callback_decline_dispatches_false():
    on_approval = AsyncMock()
    bot = TelegramBot("token", "123", AsyncMock(), on_approval=on_approval)
    update = _make_update(user_id=123, callback_data="approve:req1:no")
    await bot._handle_callback(update, None)
    on_approval.assert_awaited_once_with("req1", False)


@pytest.mark.asyncio
async def test_callback_non_owner_ignored():
    on_approval = AsyncMock()
    bot = TelegramBot("token", "123", AsyncMock(), on_approval=on_approval)
    update = _make_update(user_id=456, callback_data="approve:req1:yes")
    await bot._handle_callback(update, None)
    on_approval.assert_not_awaited()


def test_active_bot_registry():
    bot = TelegramBot("token", "123", AsyncMock())
    set_active_bot(bot)
    assert get_active_bot() is bot
    set_active_bot(None)
    assert get_active_bot() is None


def test_to_telegram_markdown_converts_headers_to_bold():
    assert _to_telegram_markdown("# Title\ntext") == "*Title*\ntext"


def test_to_telegram_markdown_converts_double_asterisk_bold():
    assert _to_telegram_markdown("**bold**") == "*bold*"


def test_to_telegram_markdown_leaves_plain_text_unchanged():
    assert _to_telegram_markdown("plain text, no markup") == "plain text, no markup"


def test_split_message_under_limit_unchanged():
    assert _split_message("short text") == ["short text"]


def test_split_message_splits_on_newline_boundary():
    chunk = "a" * 4090
    text = chunk + "\n" + "b" * 100
    parts = _split_message(text, limit=4096)
    assert len(parts) == 2
    assert parts[0] == chunk
    assert parts[1] == "b" * 100


def test_split_message_force_splits_no_newline():
    text = "a" * 9000
    parts = _split_message(text, limit=4096)
    assert len(parts) == 3
    assert "".join(parts) == text
    assert all(len(p) <= 4096 for p in parts)


@pytest.mark.asyncio
async def test_quick_action_callback_dispatches_as_message():
    on_message = AsyncMock()
    bot = TelegramBot("token", "123", on_message)
    update = _make_update(user_id=123, chat_id=999, callback_data="qa:stop")
    update.effective_chat = SimpleNamespace(id=999)
    await bot._handle_callback(update, None)
    on_message.assert_awaited_once_with("stop", "999")


@pytest.mark.asyncio
async def test_stream_lifecycle():
    bot = TelegramBot("token", "123", AsyncMock())
    sent_message = SimpleNamespace(message_id=42)
    fake_bot = AsyncMock()
    fake_bot.send_message.return_value = sent_message
    bot._app = SimpleNamespace(bot=fake_bot)

    await bot.stream_start("999")
    assert bot._streams["999"]["message_id"] == 42

    bot._streams["999"]["last_edit"] = 0.0
    await bot.stream_append("999", "hello")
    fake_bot.edit_message_text.assert_awaited_with(chat_id="999", message_id=42, text="hello")

    await bot.stream_finish("999")
    assert "999" not in bot._streams
