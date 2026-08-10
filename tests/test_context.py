from unittest.mock import MagicMock

from charlie import context


def _reset_cache():
    context._cache = None


def test_build_context_reads_idle_and_foreground(monkeypatch):
    _reset_cache()
    monkeypatch.setattr(context, "user_idle_seconds", lambda: 12.5)
    monkeypatch.setattr(
        context, "get_foreground_window",
        lambda: {"hwnd": 1, "title": "Untitled - Notepad", "pid": 99, "process_name": "notepad.exe"},
    )
    monkeypatch.setattr(context, "get_current_task", lambda: None)
    ctx = context.build_context(now=100.0)
    assert ctx.idle_seconds == 12.5
    assert ctx.foreground_app == "notepad.exe"
    assert ctx.foreground_title == "Untitled - Notepad"


def test_build_context_handles_no_foreground_window(monkeypatch):
    _reset_cache()
    monkeypatch.setattr(context, "user_idle_seconds", lambda: 0.0)
    monkeypatch.setattr(context, "get_foreground_window", lambda: None)
    monkeypatch.setattr(context, "get_current_task", lambda: None)
    ctx = context.build_context(now=100.0)
    assert ctx.foreground_app is None
    assert ctx.foreground_title is None
    assert ctx.focus_mode is False


def test_focus_mode_true_for_productive_app_with_recent_input(monkeypatch):
    _reset_cache()
    monkeypatch.setattr(context, "user_idle_seconds", lambda: 2.0)
    monkeypatch.setattr(
        context, "get_foreground_window",
        lambda: {"hwnd": 1, "title": "main.py - Charlie", "pid": 5, "process_name": "code.exe"},
    )
    monkeypatch.setattr(context, "get_current_task", lambda: None)
    ctx = context.build_context(now=100.0)
    assert ctx.focus_mode is True


def test_focus_mode_false_for_non_productive_app(monkeypatch):
    _reset_cache()
    monkeypatch.setattr(context, "user_idle_seconds", lambda: 2.0)
    monkeypatch.setattr(
        context, "get_foreground_window",
        lambda: {"hwnd": 1, "title": "Spotify", "pid": 5, "process_name": "spotify.exe"},
    )
    monkeypatch.setattr(context, "get_current_task", lambda: None)
    ctx = context.build_context(now=100.0)
    assert ctx.focus_mode is False


def test_focus_mode_false_when_idle_too_long_even_on_productive_app(monkeypatch):
    _reset_cache()
    monkeypatch.setattr(context, "user_idle_seconds", lambda: 999.0)
    monkeypatch.setattr(
        context, "get_foreground_window",
        lambda: {"hwnd": 1, "title": "code", "pid": 5, "process_name": "code.exe"},
    )
    monkeypatch.setattr(context, "get_current_task", lambda: None)
    ctx = context.build_context(now=100.0)
    assert ctx.focus_mode is False


def test_voice_override_wins_over_heuristic(monkeypatch):
    _reset_cache()
    monkeypatch.setattr(context, "user_idle_seconds", lambda: 999.0)
    monkeypatch.setattr(
        context, "get_foreground_window",
        lambda: {"hwnd": 1, "title": "Spotify", "pid": 5, "process_name": "spotify.exe"},
    )
    monkeypatch.setattr(context, "get_current_task", lambda: None)
    ctx = context.build_context(voice_focus_override=True, now=100.0)
    assert ctx.focus_mode is True


def test_running_task_count_reflects_active_background_task(monkeypatch):
    _reset_cache()
    monkeypatch.setattr(context, "user_idle_seconds", lambda: 0.0)
    monkeypatch.setattr(context, "get_foreground_window", lambda: None)
    fake_task = MagicMock(status="running")
    monkeypatch.setattr(context, "get_current_task", lambda: fake_task)
    ctx = context.build_context(now=100.0)
    assert ctx.running_task_count == 1


def test_running_task_count_zero_for_terminal_status(monkeypatch):
    _reset_cache()
    monkeypatch.setattr(context, "user_idle_seconds", lambda: 0.0)
    monkeypatch.setattr(context, "get_foreground_window", lambda: None)
    fake_task = MagicMock(status="done")
    monkeypatch.setattr(context, "get_current_task", lambda: fake_task)
    ctx = context.build_context(now=100.0)
    assert ctx.running_task_count == 0


def test_running_task_count_zero_when_no_task(monkeypatch):
    _reset_cache()
    monkeypatch.setattr(context, "user_idle_seconds", lambda: 0.0)
    monkeypatch.setattr(context, "get_foreground_window", lambda: None)
    monkeypatch.setattr(context, "get_current_task", lambda: None)
    ctx = context.build_context(now=100.0)
    assert ctx.running_task_count == 0


def test_conversation_age_computed_from_last_turn(monkeypatch):
    _reset_cache()
    monkeypatch.setattr(context, "user_idle_seconds", lambda: 0.0)
    monkeypatch.setattr(context, "get_foreground_window", lambda: None)
    monkeypatch.setattr(context, "get_current_task", lambda: None)
    ctx = context.build_context(last_turn_ended_at=70.0, now=100.0)
    assert ctx.conversation_age_seconds == 30.0


def test_conversation_age_none_when_not_provided(monkeypatch):
    _reset_cache()
    monkeypatch.setattr(context, "user_idle_seconds", lambda: 0.0)
    monkeypatch.setattr(context, "get_foreground_window", lambda: None)
    monkeypatch.setattr(context, "get_current_task", lambda: None)
    ctx = context.build_context(now=100.0)
    assert ctx.conversation_age_seconds is None


def test_context_summary_uses_world_model_when_provided(monkeypatch):
    _reset_cache()
    monkeypatch.setattr(context, "user_idle_seconds", lambda: 0.0)
    monkeypatch.setattr(context, "get_foreground_window", lambda: None)
    monkeypatch.setattr(context, "get_current_task", lambda: None)
    fake_world_model = MagicMock()
    fake_world_model.context_slice.return_value = "Open threads:\n- fix bug"
    ctx = context.build_context(world_model=fake_world_model, now=100.0)
    assert ctx.context_summary == "Open threads:\n- fix bug"


def test_context_summary_empty_when_no_world_model(monkeypatch):
    _reset_cache()
    monkeypatch.setattr(context, "user_idle_seconds", lambda: 0.0)
    monkeypatch.setattr(context, "get_foreground_window", lambda: None)
    monkeypatch.setattr(context, "get_current_task", lambda: None)
    ctx = context.build_context(now=100.0)
    assert ctx.context_summary == ""


def test_cache_returns_same_snapshot_within_ttl(monkeypatch):
    _reset_cache()
    calls = {"n": 0}

    def fake_idle():
        calls["n"] += 1
        return float(calls["n"])

    monkeypatch.setattr(context, "user_idle_seconds", fake_idle)
    monkeypatch.setattr(context, "get_foreground_window", lambda: None)
    monkeypatch.setattr(context, "get_current_task", lambda: None)
    ctx1 = context.build_context(now=100.0)
    ctx2 = context.build_context(now=100.5)
    assert ctx1 is ctx2
    assert calls["n"] == 1


def test_cache_refreshes_after_ttl_expires(monkeypatch):
    _reset_cache()
    calls = {"n": 0}

    def fake_idle():
        calls["n"] += 1
        return float(calls["n"])

    monkeypatch.setattr(context, "user_idle_seconds", fake_idle)
    monkeypatch.setattr(context, "get_foreground_window", lambda: None)
    monkeypatch.setattr(context, "get_current_task", lambda: None)
    ctx1 = context.build_context(now=100.0)
    ctx2 = context.build_context(now=100.0 + context._CACHE_TTL_SECONDS + 0.1)
    assert ctx1 is not ctx2
    assert calls["n"] == 2
