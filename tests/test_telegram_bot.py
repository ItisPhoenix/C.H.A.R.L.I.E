import pytest

import charlie.telegram_bot as telegram_bot
from charlie.telegram_bot import (
    is_authorized,
    parse_callback_data,
    parse_skill_review_callback_data,
    should_relay_approval,
)


@pytest.mark.parametrize(
    ("user_id", "allowed", "expected"),
    [(42, 42, True), (7, 42, False), (None, 42, False)],
)
def test_telegram_owner_gate(user_id, allowed, expected):
    assert is_authorized(user_id, allowed) is expected


def test_telegram_approval_relay_requires_live_owner_channel():
    assert should_relay_approval(True, 42) is True
    assert should_relay_approval(False, 42) is False
    assert should_relay_approval(True, 0) is False


def test_telegram_callback_data_is_strictly_parsed():
    assert parse_callback_data("approve:req-1") == ("req-1", True)
    assert parse_callback_data("decline:req-1") == ("req-1", False)
    assert parse_callback_data("approve:") is None
    assert parse_callback_data("other:req-1") is None


def test_skill_review_callback_is_separate_and_owner_only():
    import asyncio
    from types import SimpleNamespace

    from charlie.telegram_bot import TelegramBot

    async def exercise():
        tool_approvals = []
        reviews = []
        edits = []
        token = "0123456789abcdef"

        async def on_review(received_token, action):
            reviews.append((received_token, action))
            return True

        class FakeCallback:
            def __init__(self, user_id, data):
                self.from_user = SimpleNamespace(id=user_id)
                self.data = data

            async def answer(self):
                pass

            async def edit_message_reply_markup(self, **kwargs):
                edits.append(kwargs)

            async def edit_message_text(self, *_args, **_kwargs):
                raise AssertionError("successful review should only remove its keyboard")

        bot = TelegramBot.__new__(TelegramBot)
        bot._allowed_user_id = 42
        bot._on_approval = lambda request_id, approved: tool_approvals.append((request_id, approved)) or True
        bot._on_skill_candidate_review = on_review

        assert parse_skill_review_callback_data(f"skill_approve:{token}") == (token, "approve")
        assert parse_skill_review_callback_data(f"skill_reject:{token}") == (token, "reject")
        assert parse_skill_review_callback_data(f"skill_disable:{token}") == (token, "disable")
        assert parse_skill_review_callback_data("skill_approve:short") is None

        await bot._handle_callback(
            SimpleNamespace(callback_query=FakeCallback(13, f"skill_approve:{token}")), None
        )
        assert reviews == []

        await bot._handle_callback(
            SimpleNamespace(callback_query=FakeCallback(42, f"skill_approve:{token}")), None
        )
        assert reviews == [(token, "approve")]
        assert tool_approvals == []
        assert edits[0]["reply_markup"] is not None

    asyncio.run(exercise())


def test_large_skill_review_uploads_full_document_before_hash_pinned_buttons(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from charlie.telegram_bot import TelegramBot

    async def exercise():
        sent = []

        class FakeBot:
            async def send_document(self, **kwargs):
                sent.append(("document", kwargs))

            async def send_message(self, **kwargs):
                sent.append(("message", kwargs))

        monkeypatch.setattr(
            telegram_bot,
            "InputFile",
            lambda handle, filename: (handle.read(), filename),
        )
        bot = TelegramBot.__new__(TelegramBot)
        bot._app = SimpleNamespace(bot=FakeBot())
        content = "# Large skill\n" + ("instruction line\n" * 500)
        digest = "b" * 64
        token = "0123456789abcdef"

        await bot.send_skill_candidate_review(42, "large_skill", digest, token, content)

        assert [item[0] for item in sent] == ["document", "message"]
        assert sent[0][1]["document"] == (content.encode("utf-8"), "large_skill.SKILL.md")
        assert digest in sent[0][1]["caption"]
        assert digest in sent[1][1]["text"]
        buttons = sent[1][1]["reply_markup"].inline_keyboard[0]
        assert buttons[0].callback_data == f"skill_approve:{token}"
        assert buttons[1].callback_data == f"skill_reject:{token}"

    asyncio.run(exercise())


def test_telegram_free_text_can_decline_but_never_approve():
    parse_text = getattr(telegram_bot, "parse_text_approval_response", None)
    assert callable(parse_text), "Telegram approval text parser should exist"
    assert parse_text("no") is False
    assert parse_text(" Cancel ") is False
    assert parse_text("yes") is None
    assert parse_text("yes please") is None


def test_stale_telegram_approval_button_reports_expiration():
    import asyncio
    from types import SimpleNamespace

    from charlie.telegram_bot import TelegramBot

    async def exercise():
        deleted = []

        class FakeBot:
            async def delete_message(self, **kwargs):
                deleted.append(kwargs)

        class FakeCallback:
            from_user = SimpleNamespace(id=42)
            data = "approve:expired-request"
            message = SimpleNamespace(chat=SimpleNamespace(id=42), message_id=7)

            async def answer(self):
                pass

        bot = TelegramBot.__new__(TelegramBot)
        bot._allowed_user_id = 42
        bot._on_approval = lambda _request_id, _approved: False
        bot._app = SimpleNamespace(bot=FakeBot())
        await bot._handle_callback(SimpleNamespace(callback_query=FakeCallback()), None)

        assert deleted == [{"chat_id": 42, "message_id": 7}]

    asyncio.run(exercise())


def test_telegram_approval_prompt_is_deleted_after_resolution():
    import asyncio
    from types import SimpleNamespace

    from charlie.telegram_bot import TelegramBot

    async def exercise():
        deleted = []

        class FakeBot:
            async def send_message(self, **kwargs):
                return SimpleNamespace(message_id=11)

            async def delete_message(self, **kwargs):
                deleted.append(kwargs)

        class FakeCallback:
            from_user = SimpleNamespace(id=42)
            data = "approve:req-1"
            message = SimpleNamespace(chat=SimpleNamespace(id=42), message_id=11)

            async def answer(self):
                pass

        bot = TelegramBot.__new__(TelegramBot)
        bot._allowed_user_id = 42
        bot._app = SimpleNamespace(bot=FakeBot())
        bot._on_approval = lambda request_id, approved: (request_id, approved) == ("req-1", True)

        await bot.send_approval_request(42, "req-1", "desktop_close_app", "Needs approval")
        await bot._handle_callback(SimpleNamespace(callback_query=FakeCallback()), None)

        assert deleted == [{"chat_id": 42, "message_id": 11}]
        assert bot._approval_message_ids == {}

    asyncio.run(exercise())


def test_slow_message_does_not_block_owner_approval_callback():
    import asyncio
    from types import SimpleNamespace

    from charlie.telegram_bot import TelegramBot

    async def exercise():
        started = asyncio.Event()
        release = asyncio.Event()
        approvals = []

        async def on_message(_text, _chat_id):
            started.set()
            await release.wait()

        class FakeApplication:
            def __init__(self):
                self.tasks = []

            def create_task(self, coroutine, **_kwargs):
                task = asyncio.create_task(coroutine)
                self.tasks.append(task)
                return task

        bot = TelegramBot.__new__(TelegramBot)
        bot._allowed_user_id = 42
        bot._on_message = on_message
        bot._on_approval = lambda request_id, approved: approvals.append((request_id, approved)) or True
        bot._app = FakeApplication()

        message = SimpleNamespace(
            effective_user=SimpleNamespace(id=42),
            effective_chat=SimpleNamespace(id=42, type="private"),
            message=SimpleNamespace(text="a slow request"),
        )
        await asyncio.wait_for(bot._handle_message(message, None), timeout=0.1)
        await asyncio.wait_for(started.wait(), timeout=0.1)

        class FakeCallback:
            from_user = SimpleNamespace(id=42)
            data = "approve:req-1"

            async def answer(self):
                pass

            async def edit_message_reply_markup(self, **_kwargs):
                pass

        callback = SimpleNamespace(callback_query=FakeCallback())
        await asyncio.wait_for(bot._handle_callback(callback, None), timeout=0.1)
        assert approvals == [("req-1", True)]
        assert not bot._app.tasks[0].done()

        release.set()
        await asyncio.gather(*bot._app.tasks)

    asyncio.run(exercise())


def test_telegram_message_dispatch_requires_owner_private_chat():
    import asyncio
    from types import SimpleNamespace

    from charlie.telegram_bot import TelegramBot

    async def exercise():
        dispatched = []
        tasks = []

        async def on_message(text, chat_id):
            dispatched.append((text, chat_id))

        class FakeApplication:
            def create_task(self, coroutine, **_kwargs):
                tasks.append(asyncio.create_task(coroutine))

        bot = TelegramBot.__new__(TelegramBot)
        bot._allowed_user_id = 42
        bot._on_message = on_message
        bot._app = FakeApplication()

        for user_id, chat_id, chat_type in (
            (42, -10042, "supergroup"),
            (13, 13, "private"),
        ):
            update = SimpleNamespace(
                effective_user=SimpleNamespace(id=user_id),
                effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
                message=SimpleNamespace(text="run this"),
            )
            await bot._handle_message(update, None)

        if tasks:
            await asyncio.gather(*tasks)
        assert dispatched == []

    asyncio.run(exercise())


def test_private_owner_can_send_typing_and_manage_one_status_message():
    import asyncio
    from types import SimpleNamespace

    from telegram.constants import ChatAction

    from charlie.telegram_bot import TelegramBot

    async def exercise():
        calls = []

        class FakeBot:
            async def send_chat_action(self, **kwargs):
                calls.append(("typing", kwargs))

            async def send_message(self, **kwargs):
                calls.append(("send", kwargs))
                return SimpleNamespace(message_id=17)

            async def edit_message_text(self, **kwargs):
                calls.append(("edit", kwargs))

            async def delete_message(self, **kwargs):
                calls.append(("delete", kwargs))

        bot = TelegramBot.__new__(TelegramBot)
        bot._allowed_user_id = 42
        bot._app = SimpleNamespace(bot=FakeBot())

        await bot.send_typing(42)
        message_id = await bot.send_status_message(42, "Searching")
        await bot.edit_status_message(42, message_id, "Reading")
        await bot.delete_status_message(42, message_id)

        assert message_id == 17
        assert calls == [
            ("typing", {"chat_id": 42, "action": ChatAction.TYPING}),
            ("send", {"chat_id": 42, "text": "Searching", "disable_notification": True}),
            ("edit", {"chat_id": 42, "message_id": 17, "text": "Reading"}),
            ("delete", {"chat_id": 42, "message_id": 17}),
        ]

        await bot.send_typing(-10042)
        assert await bot.send_status_message(-10042, "Group status") is None
        await bot.edit_status_message(-10042, 17, "Group edit")
        await bot.delete_status_message(-10042, 17)
        assert len(calls) == 4

    asyncio.run(exercise())


def test_telegram_activity_and_status_api_failures_are_non_fatal():
    import asyncio
    from types import SimpleNamespace

    from charlie.telegram_bot import TelegramBot

    async def exercise():
        class FailingBot:
            async def send_chat_action(self, **_kwargs):
                raise RuntimeError("typing unavailable")

            async def send_message(self, **_kwargs):
                raise RuntimeError("message unavailable")

            async def edit_message_text(self, **_kwargs):
                raise RuntimeError("edit unavailable")

            async def delete_message(self, **_kwargs):
                raise RuntimeError("delete unavailable")

        bot = TelegramBot.__new__(TelegramBot)
        bot._allowed_user_id = 42
        bot._app = SimpleNamespace(bot=FailingBot())

        await bot.send_typing(42)
        assert await bot.send_status_message(42, "Working") is None
        await bot.edit_status_message(42, 17, "Still working")
        await bot.delete_status_message(42, 17)

    asyncio.run(exercise())


def test_text_approval_attempt_matches_only_exact_affirmative_tokens():
    is_attempt = getattr(telegram_bot, "is_text_approval_attempt", None)
    assert callable(is_attempt), "Telegram approval-attempt helper should exist"
    for text in ("yes", "Y", " approve ", "APPROVED"):
        assert is_attempt(text)
    for text in ("yes please", "yep", "approve this", "no", ""):
        assert not is_attempt(text)


def test_approval_request_shows_optional_operation_preview_and_keeps_buttons():
    import asyncio
    from types import SimpleNamespace

    from charlie.telegram_bot import TelegramBot

    async def exercise():
        sent = []

        class FakeBot:
            async def send_message(self, **kwargs):
                sent.append(kwargs)

        bot = TelegramBot.__new__(TelegramBot)
        bot._app = SimpleNamespace(bot=FakeBot())

        await bot.send_approval_request(42, "req-1", "shell_execute", "Needs approval")
        await bot.send_approval_request(
            42, "req-2", "shell_execute", "Needs approval", operation_preview="Get-ChildItem C:\\"
        )
        await bot.send_approval_request(
            42,
            "req-3",
            "file_write",
            "Overwrite of existing file 'summary.md' requires approval.",
            operation_preview="Write to C:\\Users\\abhi2\\Downloads\\summary.md (42 characters)",
        )

        assert sent[0]["text"].splitlines()[0] == "Please review this action"
        assert "Operation preview" not in sent[0]["text"]
        assert sent[1]["text"].splitlines()[0] == "Please review this action"
        assert "Needs approval" in sent[1]["text"].splitlines()
        assert "Get-ChildItem C:\\" in sent[1]["text"].splitlines()
        assert "Command:" in sent[1]["text"].splitlines()
        assert "Overwrite of existing file 'summary.md' requires approval." in sent[2]["text"]
        assert "Action:" in sent[2]["text"].splitlines()
        assert r"C:\Users\abhi2\Downloads\summary.md" in sent[2]["text"]
        buttons = sent[1]["reply_markup"].inline_keyboard[0]
        assert [button.callback_data for button in buttons] == ["approve:req-2", "decline:req-2"]

    asyncio.run(exercise())
