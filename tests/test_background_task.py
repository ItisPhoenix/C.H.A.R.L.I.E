import asyncio

import pytest

from charlie import background_task, recovery
from charlie.config import Config
from charlie.core import _TOOL_APPROVAL_TIMEOUT_SEC, Brain
from charlie.tasks import TaskManager


@pytest.fixture(autouse=True)
def _reset_state():
    background_task._current_task = None
    background_task._active_event_bus = None
    background_task._manager = TaskManager(max_parallel=1, on_status_change=background_task._on_manager_status_change)
    yield
    background_task._current_task = None


@pytest.fixture
def bg_config():
    return Config(
        llm_url="http://localhost:11434",
        llm_key="no-key",
        llm_model="dummy",
        iteration_budget_max=3,
        background_iteration_budget_max=5,
        background_max_actions=10,
    )


class FakeEventBus:
    def __init__(self):
        self.events = []

    async def emit(self, event_type, payload, meta=None):
        self.events.append((event_type, dict(payload)))


async def _fake_plan_chat_stream(
    self, user_input, platform="voice", skip_pre_search=False, session_id="default", skip_tools=False
):
    if "Break the following task" in user_input:
        yield "1. Step one\n2. Step two\n"
    else:
        yield ""


# --- start() / plan / immediate-run wiring (no approval gate) ---


@pytest.mark.asyncio
async def test_start_plans_and_runs_immediately(monkeypatch, bg_config):
    monkeypatch.setattr(Brain, "chat_stream", _fake_plan_chat_stream)
    monkeypatch.setattr(background_task, "_DESKTOP_AVAILABLE", False)
    bus = FakeEventBus()
    task = await background_task.start(bg_config, bus, "do the thing")
    assert task.status == "running"
    assert task.steps == ["Step one", "Step two"]
    background_task.cancel(task.id)
    await asyncio.sleep(0.05)  # let the spawned _run_loop observe cancel and close the brain


@pytest.mark.asyncio
async def test_second_start_queues_behind_first_active_task(monkeypatch, bg_config):
    monkeypatch.setattr(Brain, "chat_stream", _fake_plan_chat_stream)
    monkeypatch.setattr(background_task, "_DESKTOP_AVAILABLE", False)
    bus = FakeEventBus()
    first = await background_task.start(bg_config, bus, "first")
    assert first.status == "running"
    second = await background_task.start(bg_config, bus, "second")
    assert second.status == "queued"
    background_task.cancel(first.id)
    background_task.cancel(second.id)
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_start_returns_without_blocking_on_run_loop(monkeypatch, bg_config):
    """start() must return once the plan is generated and the run loop is
    scheduled, not await task completion -- the single-consumer WS command
    loop that delivers cancel commands would otherwise deadlock against
    itself while a long task runs."""
    monkeypatch.setattr(Brain, "chat_stream", _fake_plan_chat_stream)
    monkeypatch.setattr(background_task, "_DESKTOP_AVAILABLE", False)
    bus = FakeEventBus()
    task = await asyncio.wait_for(background_task.start(bg_config, bus, "do the thing"), timeout=2.0)
    assert task.status == "running"
    background_task.cancel(task.id)
    await asyncio.sleep(0.05)


class _FakeVoice:
    def __init__(self):
        self.spoken = []

    def speak(self, text, emotion="neutral"):
        self.spoken.append(text)


@pytest.mark.asyncio
async def test_lifecycle_alerts_speak_and_emit_start_and_complete(monkeypatch, bg_config):
    """Background-task start/complete must reuse the resource-alert pattern
    (event_bus "alert" emit + voice.speak), not stay silent."""
    monkeypatch.setattr(Brain, "chat_stream", _fake_plan_chat_stream)
    monkeypatch.setattr(background_task, "_DESKTOP_AVAILABLE", False)
    bus = FakeEventBus()
    fake_voice = _FakeVoice()
    task = await background_task.start(bg_config, bus, "do the thing", voice=fake_voice)
    await asyncio.sleep(0.05)  # let _run_loop finish both fake steps
    alert_messages = [payload["message"] for etype, payload in bus.events if etype == "alert"]
    assert any("Starting background task" in m for m in alert_messages)
    assert any("Background task complete" in m for m in alert_messages)
    assert any("Starting background task" in m for m in fake_voice.spoken)
    assert any("Background task complete" in m for m in fake_voice.spoken)
    assert task.status == "done"


@pytest.mark.asyncio
async def test_count_active_tasks_reflects_queue_depth(monkeypatch, bg_config):
    monkeypatch.setattr(Brain, "chat_stream", _fake_plan_chat_stream)
    monkeypatch.setattr(background_task, "_DESKTOP_AVAILABLE", False)
    bus = FakeEventBus()
    first = await background_task.start(bg_config, bus, "first")
    second = await background_task.start(bg_config, bus, "second")
    assert background_task.count_active_tasks() == 2
    background_task.cancel(first.id)
    background_task.cancel(second.id)
    await asyncio.sleep(0.05)


# --- restart persistence ---


@pytest.fixture(autouse=True)
def _isolate_state_file(monkeypatch, tmp_path):
    """Never touch the real .charlie_background_task_state.json on disk."""
    monkeypatch.setattr(background_task, "_STATE_FILE", str(tmp_path / "bg_state.json"))


def test_check_interrupted_task_returns_none_when_no_file():
    assert background_task.check_interrupted_task() is None


def test_check_interrupted_task_returns_none_when_last_run_was_terminal():
    task = background_task.BackgroundTask(id="t1", text="x", status="done")
    background_task._save_state(task)
    assert background_task.check_interrupted_task() is None


def test_check_interrupted_task_detects_and_marks_failed_non_terminal_state():
    """A BackgroundTask must not silently vanish if the process restarts
    mid-task -- the last known state should be recoverable and reported once."""
    task = background_task.BackgroundTask(
        id="t1", text="open notepad and calculator", status="running",
        steps=["Open notepad", "Open calculator"], current_step=0,
    )
    background_task._save_state(task)

    result = background_task.check_interrupted_task()
    assert result is not None
    assert result["text"] == "open notepad and calculator"
    assert result["current_step"] == 0
    assert result["status"] == "failed"
    assert "restarted" in result["error"]

    # Second call must not re-report -- the file was rewritten as terminal.
    assert background_task.check_interrupted_task() is None


@pytest.mark.asyncio
async def test_run_loop_persists_state_to_disk(monkeypatch, bg_config):
    monkeypatch.setattr(Brain, "chat_stream", _fake_plan_chat_stream)
    monkeypatch.setattr(background_task, "_DESKTOP_AVAILABLE", False)
    bus = FakeEventBus()
    task = await background_task.start(bg_config, bus, "do the thing")
    await asyncio.sleep(0.05)  # let _run_loop finish both fake steps
    assert task.status == "done"
    assert background_task.check_interrupted_task() is None  # done == terminal, nothing to report


def test_parse_steps_ignores_non_numbered_lines():
    text = "Sure, here is the plan:\n1. First step\n2) Second step\nSome trailing note"
    assert background_task._parse_steps(text) == ["First step", "Second step"]


def test_scan_gated_steps_flags_desktop_and_shell_keywords():
    steps = ["Open notepad and type hello", "Read the weather", "rm -rf /tmp/foo"]
    flagged = background_task._scan_gated_steps(steps)
    assert 0 in flagged  # desktop keywords ("Open"/"type")
    assert 2 in flagged  # gated shell keyword
    assert 1 not in flagged


# --- pause-on-user-activity (_wait_until_clear) ---


class _FakeSession:
    def __init__(self, idle_seconds=999.0, external=False):
        self.idle_seconds = idle_seconds
        self._external = external
        self.calls = 0

    def user_idle_seconds(self):
        return self.idle_seconds

    def external_input_since(self, tick):
        self.calls += 1
        return self._external


class _FakeActions:
    def __init__(self, tick=0, halted=False):
        self.tick = tick
        self.halted = halted

    def is_halted(self):
        return self.halted

    def last_action_tick_ms(self):
        return self.tick


@pytest.mark.asyncio
async def test_wait_until_clear_uses_idle_seconds_before_any_action(monkeypatch, bg_config):
    fake_session = _FakeSession(idle_seconds=999.0)
    fake_actions = _FakeActions(tick=0)
    monkeypatch.setattr(background_task, "_DESKTOP_AVAILABLE", True)
    monkeypatch.setattr(background_task, "desktop_session", fake_session)
    monkeypatch.setattr(background_task, "desktop_actions", fake_actions)
    task = background_task.BackgroundTask(id="t1", text="x")
    bus = FakeEventBus()
    result = await background_task._wait_until_clear(task, bg_config, bus)
    assert result is True
    assert fake_session.calls == 0  # tick==0 sentinel -- must not call external_input_since


@pytest.mark.asyncio
async def test_wait_until_clear_pauses_then_clears_on_real_input(monkeypatch, bg_config):
    fake_session = _FakeSession(idle_seconds=999.0, external=True)
    fake_actions = _FakeActions(tick=1000)
    monkeypatch.setattr(background_task, "_DESKTOP_AVAILABLE", True)
    monkeypatch.setattr(background_task, "desktop_session", fake_session)
    monkeypatch.setattr(background_task, "desktop_actions", fake_actions)
    monkeypatch.setattr(background_task, "_POLL_INTERVAL_SEC", 0.01)
    task = background_task.BackgroundTask(id="t1", text="x")
    bus = FakeEventBus()

    async def _clear_after_one_poll():
        await asyncio.sleep(0.02)
        fake_session._external = False

    poller = asyncio.create_task(_clear_after_one_poll())
    result = await background_task._wait_until_clear(task, bg_config, bus)
    await poller
    assert result is True
    assert task.status == "running"
    statuses = [e[1]["status"] for e in bus.events]
    assert "paused" in statuses


@pytest.mark.asyncio
async def test_wait_until_clear_cancel_returns_false(monkeypatch, bg_config):
    fake_session = _FakeSession(idle_seconds=0.0, external=True)
    fake_actions = _FakeActions(tick=1000)
    monkeypatch.setattr(background_task, "_DESKTOP_AVAILABLE", True)
    monkeypatch.setattr(background_task, "desktop_session", fake_session)
    monkeypatch.setattr(background_task, "desktop_actions", fake_actions)
    task = background_task.BackgroundTask(id="t1", text="x", cancel_requested=True)
    bus = FakeEventBus()
    result = await background_task._wait_until_clear(task, bg_config, bus)
    assert result is False


# --- Brain re-entrancy params (core.py 2.1) ---


def test_register_panic_hotkey_false_skips_listener(bg_config):
    cfg = Config(
        llm_url=bg_config.llm_url,
        llm_key=bg_config.llm_key,
        llm_model=bg_config.llm_model,
        desktop_control_enabled=True,
    )
    brain = Brain(cfg, register_panic_hotkey=False)
    assert brain._panic_hotkey_listener is None


@pytest.mark.asyncio
async def test_approval_timeout_none_parks_instead_of_declining(monkeypatch, bg_config):
    monkeypatch.setattr(recovery, "get_active_ws_count", lambda: 0)
    brain = Brain(bg_config, register_panic_hotkey=False, approval_timeout=None)
    brain.on_thought_callback = lambda prompt: None

    captured = {}

    async def fake_wait_for(aw, timeout):
        captured["timeout"] = timeout
        if hasattr(aw, "cancel"):
            aw.cancel()
        return True

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
    result = await brain.request_tool_approval("shell_execute", {"command": "rm -rf /"}, "test")
    assert captured["timeout"] is None
    assert result is True


@pytest.mark.asyncio
async def test_background_approval_omits_session_id(monkeypatch, bg_config):
    """Regression: a background task's gated-tool approval must not be tagged
    with the foreground's active session_id. The dashboard filters
    tool_approval_request by "is this the session I'm currently viewing" --
    a background task has no open chat tab, so tagging it with the
    foreground session silently drops the event and the approval hangs
    forever (approval_timeout=None has no fallback). Found live in Phase 3
    testing: a real task parked indefinitely with no visible approval UI."""
    monkeypatch.setattr(recovery, "get_active_ws_count", lambda: 1)
    monkeypatch.setattr(recovery, "get_active_session_id", lambda: "some-foreground-session")

    class FakeEventBusForApproval:
        def __init__(self):
            self.emitted = []

        async def emit(self, event_type, payload, meta=None):
            self.emitted.append((event_type, payload))

    fake_bus = FakeEventBusForApproval()
    monkeypatch.setattr(recovery, "_event_bus", fake_bus)

    brain = Brain(bg_config, register_panic_hotkey=False, approval_timeout=None, is_background=True)

    async def fake_wait_for(aw, timeout):
        if hasattr(aw, "cancel"):
            aw.cancel()
        return True

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
    await brain.request_tool_approval("desktop_type", {"text": "hi"}, "type text")

    assert len(fake_bus.emitted) == 1
    event_type, payload = fake_bus.emitted[0]
    assert event_type == "tool_approval_request"
    assert payload["session_id"] is None


@pytest.mark.asyncio
async def test_foreground_approval_uses_active_session_id(monkeypatch, bg_config):
    monkeypatch.setattr(recovery, "get_active_ws_count", lambda: 1)
    monkeypatch.setattr(recovery, "get_active_session_id", lambda: "some-foreground-session")

    class FakeEventBusForApproval:
        def __init__(self):
            self.emitted = []

        async def emit(self, event_type, payload, meta=None):
            self.emitted.append((event_type, payload))

    fake_bus = FakeEventBusForApproval()
    monkeypatch.setattr(recovery, "_event_bus", fake_bus)

    brain = Brain(bg_config)  # default is_background=False

    async def fake_wait_for(aw, timeout):
        if hasattr(aw, "cancel"):
            aw.cancel()
        return True

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
    await brain.request_tool_approval("shell_execute", {"command": "rm -rf x"}, "test")

    assert fake_bus.emitted[0][1]["session_id"] == "some-foreground-session"


@pytest.mark.asyncio
async def test_start_wires_session_store_and_memory_store_into_background_brain(monkeypatch, bg_config):
    monkeypatch.setattr(Brain, "chat_stream", _fake_plan_chat_stream)
    bus = FakeEventBus()
    sentinel_store = object()
    sentinel_memory = object()
    task = await background_task.start(
        bg_config, bus, "do the thing", session_store=sentinel_store, memory_store=sentinel_memory
    )
    assert task.brain.session_store is sentinel_store
    assert task.brain.memory_store is sentinel_memory
    await task.brain.close()


@pytest.mark.asyncio
async def test_approval_timeout_defaults_to_module_constant(monkeypatch, bg_config):
    monkeypatch.setattr(recovery, "get_active_ws_count", lambda: 0)
    brain = Brain(bg_config)
    brain.on_thought_callback = lambda prompt: None

    captured = {}

    async def fake_wait_for(aw, timeout):
        captured["timeout"] = timeout
        if hasattr(aw, "cancel"):
            aw.cancel()
        return True

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
    await brain.request_tool_approval("shell_execute", {"command": "rm -rf /"}, "test")
    assert captured["timeout"] == _TOOL_APPROVAL_TIMEOUT_SEC
