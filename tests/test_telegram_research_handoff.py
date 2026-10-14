import pytest

from main import (
    _is_research_telegram_handoff,
    _latest_completed_research_result,
    _send_recent_research_to_telegram,
)


def _result(session_id="voice-session", **overrides):
    return {
        "session_id": session_id,
        "text": "Verified findings\n\nSources: https://example.org",
        "answer": "Verified findings",
        "partial": False,
        "completeness": "complete",
        "termination_reason": "completed",
        **overrides,
    }


def test_explicit_research_telegram_handoff_wording_is_recognized():
    assert _is_research_telegram_handoff("Can you send me that research to my Telegram?")
    assert _is_research_telegram_handoff("Please forward this report to Telegram")
    assert not _is_research_telegram_handoff("What is currently trending on Twitter?")
    assert not _is_research_telegram_handoff("Research Telegram bots")


def test_latest_completed_research_is_session_scoped_and_excludes_partial_or_cancelled():
    completed = _result()
    history = {
        "voice-session": [
            completed,
            _result(partial=True, completeness="partial"),
            _result(termination_reason="cancelled"),
        ],
        "other-session": [_result(session_id="other-session", text="Do not leak")],
    }

    assert _latest_completed_research_result(history, "voice-session") is completed
    assert _latest_completed_research_result(
        {"voice-session": [_result(partial=True, completeness="partial")]}, "voice-session"
    ) is None
    assert _latest_completed_research_result(
        {"voice-session": [_result(termination_reason="cancelled")]}, "voice-session"
    ) is None
    assert _latest_completed_research_result(history, "missing-session") is None


@pytest.mark.asyncio
async def test_handoff_sends_only_completed_session_result_to_configured_owner():
    class FakeTelegram:
        def __init__(self):
            self.messages = []

        async def send_message(self, chat_id, text):
            self.messages.append((chat_id, text))
            return 123

    result = _result()
    bot = FakeTelegram()
    outcome = await _send_recent_research_to_telegram(
        {"voice-session": [result]},
        "voice-session",
        telegram_enabled=True,
        telegram_token_configured=True,
        telegram_user_id=42,
        telegram_bot=bot,
    )

    assert outcome == "sent"
    assert bot.messages == [(42, result["text"])]


@pytest.mark.asyncio
async def test_handoff_refuses_unconfigured_or_missing_results_without_sending():
    class FakeTelegram:
        def __init__(self):
            self.messages = []

        async def send_message(self, chat_id, text):
            self.messages.append((chat_id, text))
            return 123

    bot = FakeTelegram()
    base = {
        "telegram_enabled": True,
        "telegram_token_configured": True,
        "telegram_user_id": 42,
        "telegram_bot": bot,
    }
    assert await _send_recent_research_to_telegram(
        {"voice-session": [_result()]},
        "voice-session",
        **{**base, "telegram_enabled": False},
    ) == "unavailable"
    assert await _send_recent_research_to_telegram(
        {"voice-session": [_result(partial=True, completeness="partial")]},
        "voice-session",
        **base,
    ) == "missing"
    assert bot.messages == []
