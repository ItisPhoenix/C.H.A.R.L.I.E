import pytest

import charlie.telegram_bot as telegram_bot
from charlie.telegram_bot import (
    HARD_SPLIT_MARKER,
    TELEGRAM_MAX_MESSAGE_CHARS,
    TRUNCATION_MARKER,
    clip_message_text,
    is_authorized,
    parse_callback_data,
    parse_skill_review_callback_data,
    should_relay_approval,
    split_message_text,
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


def test_telegram_approval_prompt_is_deleted_when_resolver_raises():
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
            data = "approve:req-1"
            message = SimpleNamespace(chat=SimpleNamespace(id=42), message_id=11)

            async def answer(self):
                pass

        def failing_resolver(_request_id, _approved):
            raise RuntimeError("resolver failed")

        bot = TelegramBot.__new__(TelegramBot)
        bot._allowed_user_id = 42
        bot._app = SimpleNamespace(bot=FakeBot())
        bot._on_approval = failing_resolver

        with pytest.raises(RuntimeError, match="resolver failed"):
            await bot._handle_callback(SimpleNamespace(callback_query=FakeCallback()), None)

        assert deleted == [{"chat_id": 42, "message_id": 11}]

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


def test_send_message_returns_message_id_for_transient_cleanup():
    import asyncio
    from types import SimpleNamespace

    from charlie.telegram_bot import TelegramBot

    async def exercise():
        class FakeBot:
            async def send_message(self, **_kwargs):
                return SimpleNamespace(message_id=23)

        bot = TelegramBot.__new__(TelegramBot)
        bot._allowed_user_id = 42
        bot._app = SimpleNamespace(bot=FakeBot())

        assert await bot.send_message(42, "Background task started") == 23

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


# ---------------------------------------------------------------------------
# ITEM 0.3 -- python-telegram-bot is an optional extra; importing it unguarded
# used to abort pytest collection for the whole suite.
# ---------------------------------------------------------------------------


def test_telegram_bridge_constructs_a_real_application_when_the_extra_is_installed():
    """Construct the object for real, not via __new__ + injected fake.

    Every other test in this file bypasses the constructor, so nothing would catch a broken
    Application build. Building an Application is pure construction -- no network, no poll.
    """
    from telegram.ext import Application

    from charlie.telegram_bot import TELEGRAM_AVAILABLE, TelegramBot

    assert TELEGRAM_AVAILABLE is True, "python-telegram-bot should be installed in this env"

    async def on_message(_text, _chat_id):
        return None

    bot = TelegramBot(
        token="123456:AAH-fixture-token-not-a-real-bot",
        allowed_user_id=42,
        on_message=on_message,
        on_approval=lambda _request_id, _approved: True,
    )

    assert isinstance(bot._app, Application)
    # Both handlers must be registered, or the bridge would answer nothing at all.
    assert len(bot._app.handlers[0]) == 2
    assert bot._approval_message_ids == {}


def test_telegram_module_imports_and_reports_absence_without_the_optional_extra():
    """Import the module with `telegram` made unimportable and assert it degrades cleanly."""
    import contextlib
    import importlib.util
    import sys

    import charlie.telegram_bot as installed_module

    module_path = installed_module.__file__

    class _BlockTelegram:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "telegram" or fullname.startswith("telegram."):
                raise ImportError(f"No module named {fullname!r} (optional extra absent)")
            return None

    @contextlib.contextmanager
    def telegram_unimportable():
        # sys.modules is consulted before meta_path, so the real package must be evicted first.
        saved = {
            name: module
            for name, module in sys.modules.items()
            if name == "telegram" or name.startswith("telegram.")
        }
        blocker = _BlockTelegram()
        for name in saved:
            del sys.modules[name]
        sys.meta_path.insert(0, blocker)
        try:
            yield
        finally:
            sys.meta_path.remove(blocker)
            sys.modules.update(saved)

    with telegram_unimportable():
        spec = importlib.util.spec_from_file_location("charlie_telegram_bot_without_extra", module_path)
        module = importlib.util.module_from_spec(spec)
        # The whole point: this must not raise ImportError out of module scope.
        spec.loader.exec_module(module)

    assert module.TELEGRAM_AVAILABLE is False
    assert isinstance(module.TELEGRAM_IMPORT_ERROR, ImportError)

    async def on_message(_text, _chat_id):
        return None

    with pytest.raises(RuntimeError) as failure:
        module.TelegramBot(
            token="123456:AAH-fixture",
            allowed_user_id=42,
            on_message=on_message,
            on_approval=lambda _request_id, _approved: True,
        )
    message = str(failure.value)
    assert "python-telegram-bot" in message
    assert "pip install" in message, "the failure must tell the operator how to fix it"


# ---------------------------------------------------------------------------
# ITEM 0.6 -- messages over Telegram's 4096-character limit were silently dropped.
# ---------------------------------------------------------------------------


def test_oversized_reply_is_sent_as_sequential_messages_within_the_api_limit():
    import asyncio
    from types import SimpleNamespace

    from charlie.telegram_bot import TELEGRAM_MAX_MESSAGE_CHARS, TelegramBot

    async def exercise():
        sent = []

        class FakeBot:
            async def send_message(self, **kwargs):
                sent.append(kwargs["text"])
                return SimpleNamespace(message_id=100 + len(sent))

        bot = TelegramBot.__new__(TelegramBot)
        bot._allowed_user_id = 42
        bot._app = SimpleNamespace(bot=FakeBot())

        # ~10k characters: the shape of a real long assistant reply or watcher digest.
        reply = "\n\n".join(
            f"Section {index}. " + ("Reasoning about the requested work. " * 12)
            for index in range(24)
        )
        assert len(reply) > TELEGRAM_MAX_MESSAGE_CHARS * 2

        first_message_id = await bot.send_message(42, reply)

        assert len(sent) > 1, "an overlong reply must be chunked, not dropped"
        assert all(len(part) <= TELEGRAM_MAX_MESSAGE_CHARS for part in sent), [
            len(part) for part in sent
        ]
        # No content is lost. Whitespace at a chunk boundary is deliberately trimmed, so the
        # invariant is word-level: the reassembled chunks hold exactly the original words.
        assert sorted(" ".join(sent).split()) == sorted(reply.split())
        # Cleanup in main.py keys off the first chunk, which is the one anchored in the transcript.
        assert first_message_id == 101

    asyncio.run(exercise())


def test_reply_is_split_on_paragraph_boundaries_not_mid_word_or_mid_emoji():
    import asyncio
    from types import SimpleNamespace

    from charlie.telegram_bot import TELEGRAM_MAX_MESSAGE_CHARS, TelegramBot, split_message_text

    paragraphs = [
        "Paragraph about the work done. " + ("detail " * 200) + "\U0001F600" for _ in range(3)
    ]
    reply = "\n\n".join(paragraphs)

    parts = split_message_text(reply)
    assert all(len(part) <= TELEGRAM_MAX_MESSAGE_CHARS for part in parts)
    # Every chunk but the last must end on a real boundary: a blank-line break, a sentence end,
    # or a space. Never a fragment of a word.
    for part in parts[:-1]:
        assert part.rstrip()[-1] in ".!?\U0001F600" or part.endswith("\n")
    # No chunk may begin or end inside a multi-code-point emoji.
    for part in parts:
        assert not part.endswith("\ud83d")
        assert not part.startswith("\ude00")
    assert sum(part.count("\U0001F600") for part in parts) == 3

    async def exercise():
        sent = []

        class FakeBot:
            async def send_message(self, **kwargs):
                sent.append(kwargs["text"])
                return SimpleNamespace(message_id=1)

        bot = TelegramBot.__new__(TelegramBot)
        bot._app = SimpleNamespace(bot=FakeBot())
        await bot.send_message(42, reply)
        assert sent == parts

    asyncio.run(exercise())


def test_unbreakable_block_is_hard_split_with_an_explicit_truncation_marker():
    import asyncio
    from types import SimpleNamespace

    from charlie.telegram_bot import (
        HARD_SPLIT_MARKER,
        TELEGRAM_MAX_MESSAGE_CHARS,
        TelegramBot,
        split_message_text,
    )

    # A single run with no whitespace anywhere: no safe boundary exists anywhere in it.
    blob = "A" * (TELEGRAM_MAX_MESSAGE_CHARS * 2 + 500)
    parts = split_message_text(blob)
    assert len(parts) == 3
    assert all(len(part) <= TELEGRAM_MAX_MESSAGE_CHARS for part in parts)
    assert HARD_SPLIT_MARKER in parts[0]
    assert HARD_SPLIT_MARKER in parts[1]
    assert HARD_SPLIT_MARKER not in parts[-1], "the final chunk is not a cut, so it carries no marker"
    # The user can tell a cut happened, and can still reassemble the whole payload.
    assert "truncated" in HARD_SPLIT_MARKER or "cut" in HARD_SPLIT_MARKER
    assert "".join(part.replace(HARD_SPLIT_MARKER, "") for part in parts) == blob

    async def exercise():
        sent = []

        class FakeBot:
            async def send_message(self, **kwargs):
                sent.append(kwargs["text"])
                return SimpleNamespace(message_id=1)

        bot = TelegramBot.__new__(TelegramBot)
        bot._app = SimpleNamespace(bot=FakeBot())
        await bot.send_message(42, blob)
        assert sent == parts

    asyncio.run(exercise())


def test_approval_and_status_surfaces_truncate_instead_of_splitting():
    import asyncio
    from types import SimpleNamespace

    from charlie.telegram_bot import (
        TELEGRAM_MAX_MESSAGE_CHARS,
        TRUNCATION_MARKER,
        TelegramBot,
    )

    async def exercise():
        sent = []

        class FakeBot:
            async def send_message(self, **kwargs):
                sent.append(kwargs)
                return SimpleNamespace(message_id=5)

        bot = TelegramBot.__new__(TelegramBot)
        bot._allowed_user_id = 42
        bot._app = SimpleNamespace(bot=FakeBot())

        # These carry a message_id handle that main.py later edits or deletes, so they must
        # stay exactly one message; the honest alternative is a visible truncation.
        await bot.send_status_message(42, "z" * (TELEGRAM_MAX_MESSAGE_CHARS * 2))
        await bot.send_approval_request(
            42, "req-long", "shell_execute", "Needs approval",
            operation_preview="q" * (TELEGRAM_MAX_MESSAGE_CHARS * 2),
        )

        assert len(sent) == 2
        for payload in sent:
            assert len(payload["text"]) <= TELEGRAM_MAX_MESSAGE_CHARS
            assert TRUNCATION_MARKER in payload["text"]
        buttons = sent[1]["reply_markup"].inline_keyboard[0]
        assert [button.callback_data for button in buttons] == ["approve:req-long", "decline:req-long"]

    asyncio.run(exercise())


# ---------------------------------------------------------------------------
# ITEM 0.6 (regression) -- the 4096 budget is UTF-16 code units, not code points.
# python-telegram-bot 22.8 performs no outgoing length check, so an over-budget chunk is not
# caught locally: _send_with_retry re-raises the BadRequest and main.py:2224 turns it into
# delivery["telegram"] = "failed" plus a WARNING. An emoji-heavy reply was therefore dropped
# in silence. The tests below assert the unit the API actually counts.
# ---------------------------------------------------------------------------


def _utf16_units(text):
    """Length the Bot API counts, measured independently of the implementation under test."""
    return len(text.encode("utf-16-le")) // 2


def _assert_within_api_limit(parts):
    """Every chunk must fit the real limit, and must not hold half of an astral character."""
    limit = TELEGRAM_MAX_MESSAGE_CHARS
    oversized = [
        (index, _utf16_units(part), len(part))
        for index, part in enumerate(parts)
        if _utf16_units(part) > limit
    ]
    assert not oversized, f"chunks over {limit} UTF-16 units (index, units, code points): {oversized}"
    for part in parts:
        # A lone surrogate means a chunk boundary landed inside a surrogate pair. A Python str
        # holds no surrogate on its own, so finding one proves an astral character was torn.
        for char in part:
            assert not 0xD800 <= ord(char) <= 0xDFFF, "chunk holds a lone surrogate"
    return parts


def _assert_content_preserved(parts, original):
    """Splitting may drop the whitespace at a break point; it must drop nothing else.

    The hard-split marker is the one chunk content the splitter is allowed to add, so it is
    removed before the comparison.
    """
    kept = sum(_utf16_units(part.replace(HARD_SPLIT_MARKER, "")) for part in parts)
    dropped = _utf16_units(original) - kept
    assert 0 <= dropped <= len(parts) - 1, (
        f"content accounting is off: {dropped} units dropped across {len(parts)} chunks "
        f"(a break point may drop at most its own whitespace)"
    )
    return parts


def test_emoji_only_reply_never_produces_a_chunk_over_the_utf16_limit():
    # 5000 astral emoji = 5000 code points but 10_000 UTF-16 units. A code-point budget emits a
    # 4096-code-point first chunk that measures 8039 units: the API answers 400 and the reply is
    # silently dropped.
    reply = "\U0001F600" * 5000
    parts = _assert_within_api_limit(split_message_text(reply))
    assert len(parts) > 1, "an over-limit reply must actually be split"
    assert sum(part.count("\U0001F600") for part in parts) == 5000, "no emoji may be lost"
    _assert_content_preserved(parts, reply)


def test_mixed_astral_and_ascii_reply_never_produces_a_chunk_over_the_utf16_limit():
    reply = "\U0001F600" * 2500 + "a" * 2500

    parts = _assert_within_api_limit(split_message_text(reply))

    assert len(parts) > 1
    assert sum(part.count("\U0001F600") for part in parts) == 2500
    _assert_content_preserved(parts, reply)


def test_bmp_multibyte_and_emoji_reply_never_produces_a_chunk_over_the_utf16_limit():
    # BMP multibyte (Latin-1 accents, CJK, punctuation) is one unit per character, so it must
    # not be charged twice; only the astral characters cost extra.
    reply = ("é世界 \U0001F300\U0001F1FA\U0001F1F8 " * 900).strip()

    parts = _assert_within_api_limit(split_message_text(reply))

    assert len(parts) > 1
    _assert_content_preserved(parts, reply)
    # Whitespace at a break point is deliberately trimmed, so the invariant is word-level.
    assert sorted(" ".join(parts).split()) == sorted(reply.split())


def test_plain_ascii_at_the_limit_stays_one_chunk():
    # Guard against the opposite failure: budgeting in units must not over-split normal text.
    # 4096 ASCII characters is exactly 4096 units, so it is one message, not two.
    reply = "a" * TELEGRAM_MAX_MESSAGE_CHARS

    assert _utf16_units(reply) == TELEGRAM_MAX_MESSAGE_CHARS
    assert split_message_text(reply) == [reply]


def test_hard_split_of_an_emoji_only_token_respects_the_utf16_limit():
    # No boundary exists anywhere in a run of emoji, so this takes the hard-split path. The
    # marker is measured in units too, and a chunk must never end mid-emoji to fit it.
    blob = "\U0001F600" * 5000
    parts = _assert_within_api_limit(split_message_text(blob))

    assert HARD_SPLIT_MARKER in parts[0]
    assert "".join(part.replace(HARD_SPLIT_MARKER, "") for part in parts) == blob


def test_clip_message_text_counts_units_and_never_torn_an_emoji():
    text = "\U0001F600" * 5000

    clipped = clip_message_text(text)

    assert _utf16_units(clipped) <= TELEGRAM_MAX_MESSAGE_CHARS
    assert TRUNCATION_MARKER in clipped
    for char in clipped:
        assert not 0xD800 <= ord(char) <= 0xDFFF, "clip point tore an astral character"
    # A text already inside the limit is returned untouched, units or not.
    assert clip_message_text("ok") == "ok"
    assert clip_message_text("e" * TELEGRAM_MAX_MESSAGE_CHARS) == "e" * TELEGRAM_MAX_MESSAGE_CHARS


# ---------------------------------------------------------------------------
# ITEM 0.7 -- no RetryAfter / NetworkError handling anywhere in the bridge.
# ---------------------------------------------------------------------------





def _recording_bridge(monkeypatch):
    """A bridge whose backoff sleeps are recorded instead of actually waited on."""
    import asyncio
    from types import SimpleNamespace

    from charlie.telegram_bot import TelegramBot

    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(telegram_bot, "_SLEEP", fake_sleep)

    class FakeBot:
        def __init__(self):
            self.attempts = 0

        async def send_message(self, **kwargs):
            self.attempts += 1
            return SimpleNamespace(message_id=self.attempts)

    bot = TelegramBot.__new__(TelegramBot)
    bot._allowed_user_id = 42
    bot._app = SimpleNamespace(bot=FakeBot())
    return bot, sleeps, asyncio


def test_rate_limited_send_honours_retry_after_then_succeeds(monkeypatch):
    from types import SimpleNamespace

    from telegram.error import RetryAfter

    bot, sleeps, asyncio = _recording_bridge(monkeypatch)
    calls = []

    async def flaky_send(**kwargs):
        calls.append(kwargs)
        if len(calls) <= 2:
            raise RetryAfter(2)
        return SimpleNamespace(message_id=len(calls))

    bot._app.bot.send_message = flaky_send

    async def exercise():
        return await bot.send_message(42, "hello")

    assert asyncio.run(exercise()) == 3
    assert len(calls) == 3
    # The wait is exactly what Telegram asked for, twice, and the send then lands.
    assert sleeps == [2.0, 2.0]


def test_rate_limit_sleep_is_capped_so_one_stuck_send_cannot_stall_a_turn(monkeypatch):
    from telegram.error import RetryAfter

    bot, sleeps, asyncio = _recording_bridge(monkeypatch)
    calls = []

    async def always_limited(**kwargs):
        calls.append(kwargs)
        raise RetryAfter(9999)

    bot._app.bot.send_message = always_limited

    async def exercise():
        return await bot.send_message(42, "hello")

    with pytest.raises(RetryAfter):
        asyncio.run(exercise())
    assert len(calls) == telegram_bot.MAX_SEND_ATTEMPTS
    assert sleeps == [telegram_bot.MAX_RETRY_AFTER_SECONDS] * (len(calls) - 1)
    assert all(delay <= telegram_bot.MAX_RETRY_AFTER_SECONDS for delay in sleeps)


def test_network_error_backs_off_exponentially_and_capped(monkeypatch):
    from types import SimpleNamespace

    from telegram.error import NetworkError

    bot, sleeps, asyncio = _recording_bridge(monkeypatch)
    calls = []

    async def flaky_send(**kwargs):
        calls.append(kwargs)
        if len(calls) <= 2:
            raise NetworkError("connection reset")
        return SimpleNamespace(message_id=len(calls))

    bot._app.bot.send_message = flaky_send

    async def exercise():
        return await bot.send_message(42, "hello")

    assert asyncio.run(exercise()) == 3
    assert sleeps == [telegram_bot.RETRY_BACKOFF_BASE_SECONDS, telegram_bot.RETRY_BACKOFF_BASE_SECONDS * 2]
    assert all(delay <= telegram_bot.RETRY_BACKOFF_MAX_SECONDS for delay in sleeps)


def test_send_gives_up_after_the_configured_attempts_and_propagates(monkeypatch):
    from telegram.error import TimedOut

    bot, sleeps, asyncio = _recording_bridge(monkeypatch)
    calls = []

    async def always_down(**kwargs):
        calls.append(kwargs)
        raise TimedOut()

    bot._app.bot.send_message = always_down

    async def exercise():
        return await bot.send_message(42, "hello")

    with pytest.raises(TimedOut):
        asyncio.run(exercise())
    assert len(calls) == telegram_bot.MAX_SEND_ATTEMPTS
    assert len(sleeps) == telegram_bot.MAX_SEND_ATTEMPTS - 1


def test_permanent_telegram_errors_are_not_retried(monkeypatch):
    """A BadRequest (e.g. the 4096 overflow) will not fix itself; retrying only delays the error."""
    from telegram.error import BadRequest

    bot, sleeps, asyncio = _recording_bridge(monkeypatch)
    calls = []

    async def rejected(**kwargs):
        calls.append(kwargs)
        raise BadRequest("message is too long")

    bot._app.bot.send_message = rejected

    async def exercise():
        return await bot.send_message(42, "hello")

    with pytest.raises(BadRequest, match="too long"):
        asyncio.run(exercise())
    assert len(calls) == 1
    assert sleeps == []


def test_oversized_uploads_are_rejected_before_the_bot_api_call():
    import asyncio
    from types import SimpleNamespace

    from charlie.telegram_bot import MAX_UPLOAD_BYTES, TelegramBot, ensure_upload_within_limit

    assert MAX_UPLOAD_BYTES == 50 * 1024 * 1024
    assert ensure_upload_within_limit(b"x" * 1024) == 1024
    assert ensure_upload_within_limit(MAX_UPLOAD_BYTES) == MAX_UPLOAD_BYTES

    with pytest.raises(ValueError, match="over the"):
        ensure_upload_within_limit(b"x" * (MAX_UPLOAD_BYTES + 1))
    with pytest.raises(ValueError, match="MiB"):
        ensure_upload_within_limit(MAX_UPLOAD_BYTES + 1)

    async def exercise():
        sent = []

        class FakeBot:
            async def send_document(self, **kwargs):
                sent.append(kwargs)

            async def send_message(self, **kwargs):
                sent.append(kwargs)

        bot = TelegramBot.__new__(TelegramBot)
        bot._app = SimpleNamespace(bot=FakeBot())

        # A skill candidate past 50 MB: the guard must fail before the upload is attempted.
        with pytest.raises(ValueError, match="SKILL.md"):
            await bot.send_skill_candidate_review(
                42, "huge", "c" * 64, "0123456789abcdef", "A" * (MAX_UPLOAD_BYTES + 10)
            )
        assert sent == [], "the oversized document must never reach the Bot API"

    asyncio.run(exercise())


def test_rate_limiter_is_wired_into_the_application_and_degrades_when_absent(monkeypatch):
    """AIORateLimiter needs python-telegram-bot[rate-limiter], which is a SEPARATE extra.

    This environment does not have aiolimiter, so AIORateLimiter() raises RuntimeError here.
    Both halves are asserted deterministically: the limiter reaches the Application when it can
    be built, and its absence degrades to None rather than taking the whole bridge down.
    """
    from charlie.telegram_bot import TelegramBot, build_rate_limiter

    async def on_message(_text, _chat_id):
        return None

    def build_bridge():
        return TelegramBot(
            token="123456:AAH-fixture-token-not-a-real-bot",
            allowed_user_id=42,
            on_message=on_message,
            on_approval=lambda _request_id, _approved: True,
        )

    class _WorkingRateLimiter:
        def __init__(self, *args, **kwargs):
            self.built = True

    # The extra IS installed: the limiter must be built and handed to the Application.
    monkeypatch.setattr(telegram_bot, "AIORateLimiter", _WorkingRateLimiter)
    assert isinstance(build_rate_limiter(), _WorkingRateLimiter)
    bot = build_bridge()
    assert isinstance(bot._app.bot.rate_limiter, _WorkingRateLimiter)

    # The extra is NOT installed: AIORateLimiter() raises. That must not escape.
    class _UnbuildableRateLimiter:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError(
                'To use `AIORateLimiter`, PTB must be installed via '
                '`pip install "python-telegram-bot[rate-limiter]"`.'
            )

    monkeypatch.setattr(telegram_bot, "AIORateLimiter", _UnbuildableRateLimiter)
    assert build_rate_limiter() is None
    bot = build_bridge()
    assert bot._app.bot.rate_limiter is None
    assert len(bot._app.handlers[0]) == 2, "the bridge must still be fully wired"

