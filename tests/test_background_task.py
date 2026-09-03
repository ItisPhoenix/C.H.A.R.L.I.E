import asyncio
import json
from unittest.mock import Mock

import pytest

from charlie import background_task, recovery
from charlie.config import Config
from charlie.core import _TOOL_APPROVAL_TIMEOUT_SEC, Brain
from charlie.task_journal import TaskJournal, TaskOrigin, TaskStatus, TaskTransitionError
from charlie.tasks import TaskManager


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch, tmp_path):
    background_task._current_task = None
    background_task._active_event_bus = None
    monkeypatch.setattr(
        background_task,
        "_journal",
        TaskJournal(state_path=tmp_path / "task-journal.json"),
    )
    background_task._manager = TaskManager(max_parallel=1, on_status_change=background_task._on_manager_status_change)
    yield
    background_task._current_task = None


@pytest.fixture
def bg_config(tmp_path):
    return Config(
        llm_url="http://localhost:11434",
        llm_key="no-key",
        llm_model="dummy",
        iteration_budget_max=3,
        background_iteration_budget_max=5,
        background_max_actions=10,
        session_db_path=str(tmp_path / "bg_sessions_test.db"),
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


def _journal_with_path(monkeypatch, tmp_path):
    journal = TaskJournal(state_path=tmp_path / "task-journal.json")
    monkeypatch.setattr(background_task, "_journal", journal)
    return journal


def _write_legacy_state(state):
    with open(background_task._STATE_FILE, "w", encoding="utf-8") as state_file:
        json.dump(state, state_file)


def test_background_task_creation_records_canonical_journal_record():
    task = background_task.BackgroundTask(
        id="create-1",
        text="Inspect deployment",
        steps=["Inspect logs", "Report result"],
        session_id="bg:session-1",
        priority=2,
    )

    record = background_task._record_task_lifecycle(task, status=TaskStatus.PLANNING)

    assert record.id == task.id
    assert record.title == task.text
    assert record.status is TaskStatus.PLANNING
    assert record.origin.value == "background"
    assert record.priority.value == "high"
    assert record.session_id == task.session_id
    assert record.total_steps == 2
    assert background_task._journal.get(task.id).status is TaskStatus.PLANNING


def test_manager_status_callback_commits_queued_and_running_synchronously():
    task = background_task.BackgroundTask(id="manager-1", text="Run task")
    background_task._record_task_lifecycle(task, status=TaskStatus.PLANNING)

    # TaskManager mutates its compatibility object before invoking the callback.
    task.status = "queued"
    background_task._on_manager_status_change(task)
    assert background_task._journal.get(task.id).status is TaskStatus.QUEUED

    task.status = "running"
    background_task._on_manager_status_change(task)
    assert background_task._journal.get(task.id).status is TaskStatus.RUNNING
    assert task.status == "running"


def test_identical_lifecycle_update_is_idempotent():
    task = background_task.BackgroundTask(id="idempotent-1", text="Run task")

    first = background_task._record_task_lifecycle(task, status=TaskStatus.RUNNING)
    second = background_task._record_task_lifecycle(task, status=TaskStatus.RUNNING)

    assert second.status is first.status is TaskStatus.RUNNING
    assert len(background_task._journal.list()) == 1


@pytest.mark.asyncio
async def test_manager_callback_emits_captured_canonical_snapshots():
    bus = FakeEventBus()
    background_task._active_event_bus = bus
    task = background_task.BackgroundTask(
        id="snapshot-1", text="Run two steps", steps=["one", "two"], current_step=0
    )
    background_task._record_task_lifecycle(task, status=TaskStatus.PLANNING)

    task.status = "queued"
    background_task._on_manager_status_change(task)
    task.status = "running"
    task.current_step = 1
    background_task._on_manager_status_change(task)
    await asyncio.sleep(0.01)

    task_events = [payload for event_type, payload in bus.events if event_type == "background_task"]
    assert [payload["status"] for payload in task_events] == ["queued", "running"]
    assert task_events[0]["current_step"] == 0
    assert task_events[0]["progress"] == 0.0
    assert task_events[1]["current_step"] == 1
    assert task_events[1]["progress"] == 0.5


def test_progress_and_current_step_update_canonical_journal():
    task = background_task.BackgroundTask(
        id="progress-1", text="Run steps", steps=["one", "two", "three"], status="running", current_step=1
    )

    first = background_task._record_task_lifecycle(task, status=TaskStatus.RUNNING)
    assert first.current_step == 1
    assert first.total_steps == 3
    assert first.progress == pytest.approx(1 / 3)
    assert first.current_action == "two"

    task.current_step = 2
    second = background_task._record_task_lifecycle(task)
    assert second.current_step == 2
    assert second.progress == pytest.approx(2 / 3)
    assert second.current_action == "three"


def test_completion_uses_verifying_bridge_and_mirrors_legacy_done(monkeypatch):
    changes = []
    monkeypatch.setattr(background_task, "_journal", TaskJournal(on_change=changes.append))
    task = background_task.BackgroundTask(id="complete-1", text="Complete task", steps=["one"], current_step=1)

    background_task._record_task_lifecycle(task, status=TaskStatus.PLANNING)
    background_task._record_task_lifecycle(task, status=TaskStatus.RUNNING)
    record = background_task._record_task_lifecycle(task, status=TaskStatus.COMPLETED)

    assert record.status is TaskStatus.COMPLETED
    assert background_task._journal.get(task.id).status is TaskStatus.COMPLETED
    assert task.status == "done"
    assert any(change.status is TaskStatus.VERIFYING for change in changes)
    assert changes[-1].status is TaskStatus.COMPLETED
    assert background_task._public_event_from_record(record)["current_action"] is None


def test_approval_status_keeps_legacy_awaiting_approval_mirror():
    task = background_task.BackgroundTask(id="approval-1", text="Approve task")

    background_task._record_task_lifecycle(task, status=TaskStatus.PLANNING)
    record = background_task._record_task_lifecycle(task, status="awaiting_approval")

    assert record.status is TaskStatus.APPROVAL_REQUIRED
    assert task.status == "awaiting_approval"


@pytest.mark.asyncio
async def test_canonical_event_projection_omits_raw_failure_text():
    bus = FakeEventBus()
    task = background_task.BackgroundTask(
        id="failure-event-1", text="Inspect deployment", status="running", error="api-key=secret"
    )
    background_task._record_task_lifecycle(task, status=TaskStatus.RUNNING)
    record = background_task._record_task_lifecycle(task, status=TaskStatus.FAILED)

    await background_task._emit_task_event(bus, record, task=task)

    payload = bus.events[0][1]
    assert payload["status"] == "failed"
    assert "error" not in payload
    assert "api-key=secret" not in str(payload)


def test_cancellation_reaches_canonical_journal_and_legacy_mirror():
    task = background_task.BackgroundTask(id="cancel-1", text="Cancel task")
    background_task._record_task_lifecycle(task, status=TaskStatus.PLANNING)
    background_task._record_task_lifecycle(task, status=TaskStatus.RUNNING)
    record = background_task._record_task_lifecycle(task, status=TaskStatus.CANCELLED)

    assert record.status is TaskStatus.CANCELLED
    assert background_task._journal.get(task.id).status is TaskStatus.CANCELLED
    assert task.status == "cancelled"


@pytest.mark.asyncio
async def test_terminal_regression_is_rejected_and_not_emitted():
    bus = FakeEventBus()
    background_task._active_event_bus = bus
    task = background_task.BackgroundTask(id="terminal-1", text="Terminal task")
    background_task._record_task_lifecycle(task, status=TaskStatus.PLANNING)
    background_task._record_task_lifecycle(task, status=TaskStatus.RUNNING)
    background_task._record_task_lifecycle(task, status=TaskStatus.COMPLETED)

    task.status = "running"
    with pytest.raises(TaskTransitionError):
        background_task._record_task_lifecycle(task)
    assert task.status == "done"
    assert background_task._journal.get(task.id).status is TaskStatus.COMPLETED

    task.status = "running"
    background_task._on_manager_status_change(task)
    await asyncio.sleep(0.01)
    assert bus.events == []
    assert task.status == "done"


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
    bg_config.background_max_parallel_tasks = 1
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


@pytest.mark.asyncio
async def test_sustained_research_uses_task_lane_and_preserves_request_identity(monkeypatch, bg_config):
    from charlie.research.models import ResearchMode, ResearchProgress, ResearchReport

    class FakeBrain:
        def __init__(self, config, **kwargs):
            self.config = config
            self.on_result_stored = kwargs.get("on_result_stored")
            self.on_research_result = kwargs.get("on_research_result")

        async def close(self):
            return None

        def cancel_chat(self):
            return None

        async def _research_browser_fetch(self, result):
            return None

    progress = []

    class FakeEngine:
        def __init__(self, config, *, progress=None, browser_fetch=None):
            self.progress_callback = progress

        async def run(self, query, mode, *, cancel_event=None):
            await self.progress_callback(ResearchProgress("searching", "Searching", mode=ResearchMode.DEEP))
            progress.append(query)
            await self.progress_callback(ResearchProgress("done", "Done", mode=ResearchMode.DEEP))
            return ResearchReport(query=query, mode=ResearchMode.DEEP, stop_reason="evidence-sufficient")

    monkeypatch.setattr(background_task, "Brain", FakeBrain)
    monkeypatch.setattr("charlie.research.engine.ResearchEngine", FakeEngine)
    bus = FakeEventBus()
    published = []

    def on_research_result(report, **identity):
        published.append((report.query, identity))

    task = await background_task.start(
        bg_config,
        bus,
        "Deep research Windows security",
        session_id="session-research",
        turn_id="turn-research",
        origin=TaskOrigin.RESEARCH,
        capability_requirements=("research",),
        research_query="Deep research Windows security",
        on_research_result=on_research_result,
        announce=False,
    )
    await asyncio.sleep(0.05)

    record = background_task._journal.get(task.id)
    assert task.status == "done"
    assert record.status is TaskStatus.COMPLETED
    assert record.origin is TaskOrigin.RESEARCH
    assert record.session_id == "session-research"
    assert record.turn_id == "turn-research"
    assert record.capability_requirements == ("research",)
    assert progress == ["Deep research Windows security"]
    assert published[0][1] == {
        "session_id": "session-research",
        "task_id": task.id,
        "turn_id": "turn-research",
    }
    assert any(event_type == "research_progress" for event_type, _payload in bus.events)


@pytest.mark.asyncio
async def test_two_safe_research_tasks_overlap_within_configured_bound(monkeypatch, bg_config):
    from charlie.research.models import ResearchMode, ResearchReport

    started = []
    release = asyncio.Event()

    class FakeBrain:
        def __init__(self, config, **kwargs):
            self.config = config
            self.on_result_stored = None
            self.on_research_result = None

        async def close(self):
            return None

        def cancel_chat(self):
            return None

        async def _research_browser_fetch(self, result):
            return None

    class FakeEngine:
        def __init__(self, config, **kwargs):
            pass

        async def run(self, query, mode, *, cancel_event=None):
            started.append(query)
            await release.wait()
            return ResearchReport(query=query, mode=ResearchMode.STANDARD, stop_reason="evidence-sufficient")

    monkeypatch.setattr(background_task, "Brain", FakeBrain)
    monkeypatch.setattr("charlie.research.engine.ResearchEngine", FakeEngine)
    bg_config.background_max_parallel_tasks = 2
    bus = FakeEventBus()
    first = await background_task.start(bg_config, bus, "research one", research_query="research one", announce=False)
    second = await background_task.start(bg_config, bus, "research two", research_query="research two", announce=False)
    await asyncio.sleep(0.05)

    assert first.status == "running"
    assert second.status == "running"
    assert started == ["research one", "research two"]
    release.set()
    await asyncio.sleep(0.05)
    assert first.status == "done"
    assert second.status == "done"


@pytest.mark.asyncio
async def test_cancelling_sustained_research_reaches_canonical_cancelled(monkeypatch, bg_config):
    from charlie.research.models import ResearchMode, ResearchReport

    class FakeBrain:
        def __init__(self, config, **kwargs):
            self.config = config
            self.on_result_stored = None
            self.on_research_result = kwargs.get("on_research_result")

        async def close(self):
            return None

        def cancel_chat(self):
            return None

        async def _research_browser_fetch(self, result):
            return None

    class FakeEngine:
        def __init__(self, config, **kwargs):
            pass

        async def run(self, query, mode, *, cancel_event=None):
            while cancel_event is None or not cancel_event.is_set():
                await asyncio.sleep(0.005)
            return ResearchReport(query=query, mode=ResearchMode.DEEP, stop_reason="cancelled")

    monkeypatch.setattr(background_task, "Brain", FakeBrain)
    monkeypatch.setattr("charlie.research.engine.ResearchEngine", FakeEngine)
    bus = FakeEventBus()
    task = await background_task.start(
        bg_config,
        bus,
        "Deep research cancellation",
        research_query="Deep research cancellation",
        origin=TaskOrigin.RESEARCH,
        capability_requirements=("research",),
        announce=False,
    )
    await asyncio.sleep(0.02)

    assert background_task.cancel(task.id) is True
    await asyncio.sleep(0.05)

    assert task.status == "cancelled"
    assert background_task._journal.get(task.id).status is TaskStatus.CANCELLED
    assert not [event for event_type, event in bus.events if event_type == "research_result"]


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
    assert background_task._journal.get(task.id).status is TaskStatus.COMPLETED
    task_events = [payload for event_type, payload in bus.events if event_type == "background_task"]
    assert task_events[-1]["status"] == "completed"


@pytest.mark.asyncio
async def test_failed_task_alert_omits_raw_exception_text(monkeypatch, bg_config):
    async def failing_chat_stream(self, user_input, **kwargs):
        if "Break the following task" in user_input:
            yield "1. Inspect deployment\n"
            return
        raise RuntimeError("ConnectionError: api-key=secret")
        yield ""

    monkeypatch.setattr(Brain, "chat_stream", failing_chat_stream)
    monkeypatch.setattr(background_task, "_DESKTOP_AVAILABLE", False)
    bus = FakeEventBus()
    task = await background_task.start(bg_config, bus, "Check deployment")
    await asyncio.sleep(0.05)

    assert task.status == "failed"
    alerts = [payload["message"] for event_type, payload in bus.events if event_type == "alert"]
    assert all("api-key=secret" not in message for message in alerts)
    assert background_task._journal.get(task.id).status is TaskStatus.FAILED
    task_events = [payload for event_type, payload in bus.events if event_type == "background_task"]
    assert task_events[-1]["status"] == "failed"
    assert all("api-key=secret" not in str(payload) for payload in task_events)


@pytest.mark.asyncio
async def test_cancelling_running_task_cancels_its_active_brain_generation():
    release = asyncio.Event()
    task = background_task.BackgroundTask(id="running-task", text="do the thing")
    task.brain = Mock()

    async def run():
        await release.wait()

    background_task._manager.submit(task, run)
    await asyncio.sleep(0.01)

    assert background_task.cancel(task.id) is True
    task.brain.cancel_chat.assert_called_once_with()

    release.set()
    await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_running_cancellation_records_canonical_request_without_event(bg_config):
    release = asyncio.Event()
    bus = FakeEventBus()
    background_task._active_event_bus = bus
    task = background_task.BackgroundTask(id="running-cancel-request", text="do the thing")
    task.brain = Mock()

    async def run():
        await release.wait()

    background_task._manager.submit(task, run)
    await asyncio.sleep(0.01)
    bus.events.clear()

    assert background_task.cancel(task.id) is True

    record = background_task._journal.get(task.id)
    assert record.status is TaskStatus.RUNNING
    assert record.cancel_requested is True
    assert task.status == "running"
    assert task.cancel_requested is True
    task.brain.cancel_chat.assert_called_once_with()
    assert bus.events == []

    release.set()
    await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_running_cancellation_eventually_emits_canonical_cancelled(bg_config, monkeypatch):
    monkeypatch.setattr(background_task, "_DESKTOP_AVAILABLE", False)
    bus = FakeEventBus()
    background_task._active_event_bus = bus
    task = background_task.BackgroundTask(
        id="running-cancelled", text="do the thing", steps=["step"]
    )
    task.brain = Mock()
    task.brain.config = bg_config

    async def close_brain():
        return None

    task.brain.close = close_brain
    background_task._manager.submit(task, lambda: background_task._run_loop(task, bus))

    assert task.status == "running"
    assert background_task.cancel(task.id) is True
    await asyncio.sleep(0.05)

    record = background_task._journal.get(task.id)
    assert record.status is TaskStatus.CANCELLED
    assert record.cancel_requested is True
    assert task.status == "cancelled"
    assert task.cancel_requested is True
    task_events = [payload for event_type, payload in bus.events if event_type == "background_task"]
    assert task_events[-1]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_queued_cancellation_records_canonical_terminal_request():
    bus = FakeEventBus()
    background_task._active_event_bus = bus
    background_task._manager = TaskManager(
        max_parallel=0, on_status_change=background_task._on_manager_status_change
    )
    task = background_task.BackgroundTask(id="queued-cancel", text="queued task")

    async def run():
        return None

    background_task._manager.submit(task, run)
    await asyncio.sleep(0.01)
    bus.events.clear()

    assert task.status == "queued"
    assert background_task.cancel(task.id) is True
    await asyncio.sleep(0.01)

    record = background_task._journal.get(task.id)
    assert background_task._manager.get(task.id).status == "cancelled"
    assert record.status is TaskStatus.CANCELLED
    assert record.cancel_requested is True
    assert task.status == "cancelled"
    assert task.cancel_requested is True
    task_events = [payload for event_type, payload in bus.events if event_type == "background_task"]
    assert [payload["status"] for payload in task_events] == ["cancelled"]


@pytest.mark.asyncio
async def test_manual_takeover_records_canonical_request_and_legacy_signal():
    release = asyncio.Event()
    bus = FakeEventBus()
    background_task._active_event_bus = bus
    task = background_task.BackgroundTask(id="takeover-cancel", text="desktop task")

    async def run():
        await release.wait()

    background_task._manager.submit(task, run)
    await asyncio.sleep(0.01)
    bus.events.clear()

    background_task._on_manual_takeover(task.id, ("desktop",))

    record = background_task._journal.get(task.id)
    assert record.status is TaskStatus.RUNNING
    assert record.cancel_requested is True
    assert task.status == "running"
    assert task.cancel_requested is True
    assert bus.events == []

    release.set()
    await asyncio.sleep(0.01)


def test_cancellation_missing_journal_record_reuses_lifecycle_adapter(monkeypatch):
    background_task._manager = TaskManager(
        max_parallel=0, on_status_change=background_task._on_manager_status_change
    )
    task = background_task.BackgroundTask(id="missing-cancel-journal", text="queued task")

    async def run():
        return None

    background_task._manager.submit(task, run)
    monkeypatch.setattr(background_task, "_journal", TaskJournal())

    assert background_task.cancel(task.id) is True

    record = background_task._journal.get(task.id)
    assert record.status is TaskStatus.CANCELLED
    assert record.cancel_requested is True
    assert task.status == "cancelled"
    assert task.cancel_requested is True


@pytest.mark.asyncio
async def test_completed_task_persists_a_result(monkeypatch, bg_config):
    from charlie.results import ResultsStore

    monkeypatch.setattr(Brain, "chat_stream", _fake_plan_chat_stream)
    monkeypatch.setattr(background_task, "_DESKTOP_AVAILABLE", False)
    bus = FakeEventBus()
    task = await background_task.start(bg_config, bus, "do the thing")
    await asyncio.sleep(0.05)  # let _run_loop finish both fake steps
    assert task.status == "done"

    store = ResultsStore(db_path=bg_config.session_db_path)
    recent = store.get_recent(limit=5)
    store.close()

    assert len(recent) == 1
    assert recent[0].task_id == task.id
    assert "done" in recent[0].summary

    result_stored_events = [payload for etype, payload in bus.events if etype == "result_stored"]
    assert len(result_stored_events) == 1
    assert result_stored_events[0]["task_id"] == task.id


@pytest.mark.asyncio
async def test_completed_task_fires_on_result_stored_callback(monkeypatch, bg_config):
    monkeypatch.setattr(Brain, "chat_stream", _fake_plan_chat_stream)
    monkeypatch.setattr(background_task, "_DESKTOP_AVAILABLE", False)
    bus = FakeEventBus()
    captured = {}

    def on_result_stored(task_id, summary, attention_level):
        captured["task_id"] = task_id
        captured["summary"] = summary
        captured["attention_level"] = attention_level

    task = await background_task.start(bg_config, bus, "do the thing", on_result_stored=on_result_stored)
    await asyncio.sleep(0.05)  # let _run_loop finish both fake steps

    assert captured["task_id"] == task.id
    assert isinstance(captured["attention_level"], int)


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


@pytest.mark.parametrize(
    "status",
    [
        TaskStatus.PLANNING,
        TaskStatus.QUEUED,
        TaskStatus.PAUSED,
        TaskStatus.WAITING,
        TaskStatus.APPROVAL_REQUIRED,
        TaskStatus.VERIFYING,
        TaskStatus.RUNNING,
    ],
)
def test_check_interrupted_task_reconciles_persisted_non_terminal_background_records(
    monkeypatch, tmp_path, status
):
    journal = _journal_with_path(monkeypatch, tmp_path)
    journal.create_task(
        "Restarted task",
        task_id=f"restart-{status.value}",
        origin=TaskOrigin.BACKGROUND,
        status=status,
        current_step=1,
        total_steps=3,
    )

    assert background_task.check_interrupted_task() is None

    record = journal.get(f"restart-{status.value}")
    assert record.status is TaskStatus.FAILED
    assert record.error_summary == "Charlie restarted while this task was still running."
    restored = TaskJournal(state_path=tmp_path / "task-journal.json")
    assert restored.get(record.id).status is TaskStatus.FAILED


@pytest.mark.parametrize(
    ("canonical_status", "legacy_status"),
    [
        (TaskStatus.COMPLETED, "running"),
        (TaskStatus.FAILED, "running"),
        (TaskStatus.CANCELLED, "running"),
    ],
)
def test_check_interrupted_task_preserves_canonical_terminal_truth(
    monkeypatch, tmp_path, canonical_status, legacy_status
):
    journal = _journal_with_path(monkeypatch, tmp_path)
    task_id = f"terminal-{canonical_status.value}"
    journal.create_task(
        "Already terminal",
        task_id=task_id,
        origin=TaskOrigin.BACKGROUND,
        status=canonical_status,
    )
    _write_legacy_state({"id": task_id, "text": "Already terminal", "status": legacy_status})

    assert background_task.check_interrupted_task() is None
    assert journal.get(task_id).status is canonical_status
    with open(background_task._STATE_FILE, "r", encoding="utf-8") as state_file:
        mirrored = json.load(state_file)
    assert mirrored["status"] == ("done" if canonical_status is TaskStatus.COMPLETED else canonical_status.value)


def test_check_interrupted_task_reconciles_all_background_records_without_touching_other_origins(
    monkeypatch, tmp_path
):
    journal = _journal_with_path(monkeypatch, tmp_path)
    background_ids = []
    for index, status in enumerate((TaskStatus.PLANNING, TaskStatus.QUEUED, TaskStatus.PAUSED)):
        task_id = f"background-stale-{index}"
        background_ids.append(task_id)
        journal.create_task("Stale background", task_id=task_id, origin=TaskOrigin.BACKGROUND, status=status)
    journal.create_task(
        "Terminal background",
        task_id="background-terminal",
        origin=TaskOrigin.BACKGROUND,
        status=TaskStatus.COMPLETED,
    )
    other_origins = {
        origin: journal.create_task(
            f"{origin.value} task",
            task_id=f"{origin.value}-active",
            origin=origin,
            status=TaskStatus.RUNNING,
        )
        for origin in (
            TaskOrigin.FOREGROUND,
            TaskOrigin.BROWSER,
            TaskOrigin.SYSTEM,
            TaskOrigin.MAINTENANCE,
            TaskOrigin.CHILD,
        )
    }
    research_task = journal.create_task(
        "research task",
        task_id="research-active",
        origin=TaskOrigin.RESEARCH,
        status=TaskStatus.RUNNING,
    )

    background_task.check_interrupted_task()

    assert all(journal.get(task_id).status is TaskStatus.FAILED for task_id in background_ids)
    assert journal.get("background-terminal").status is TaskStatus.COMPLETED
    assert journal.get(research_task.id).status is TaskStatus.FAILED
    assert all(journal.get(task.id).status is TaskStatus.RUNNING for task in other_origins.values())


def test_terminal_legacy_state_does_not_block_other_background_reconciliation(monkeypatch, tmp_path):
    journal = _journal_with_path(monkeypatch, tmp_path)
    journal.create_task(
        "Another stale task",
        task_id="another-stale",
        origin=TaskOrigin.BACKGROUND,
        status=TaskStatus.RUNNING,
    )
    _write_legacy_state({"id": "old-task", "text": "Old task", "status": "done"})

    assert background_task.check_interrupted_task() is None
    assert journal.get("another-stale").status is TaskStatus.FAILED


def test_check_interrupted_task_returns_existing_legacy_interruption_after_canonical_failure(
    monkeypatch, tmp_path
):
    journal = _journal_with_path(monkeypatch, tmp_path)
    journal.create_task(
        "Open deployment console",
        task_id="matching-legacy",
        origin=TaskOrigin.BACKGROUND,
        status=TaskStatus.RUNNING,
        session_id="bg:session-1",
        current_step=1,
        total_steps=3,
    )
    _write_legacy_state(
        {
            "id": "matching-legacy",
            "text": "Open deployment console",
            "steps": ["Open console", "Inspect logs", "Report"],
            "current_step": 1,
            "status": "running",
            "session_id": "bg:session-1",
        }
    )

    result = background_task.check_interrupted_task()

    assert result is not None
    assert result["text"] == "Open deployment console"
    assert result["current_step"] == 1
    assert result["steps"] == ["Open console", "Inspect logs", "Report"]
    assert result["status"] == "failed"
    assert result["error"] == "Charlie restarted while this task was still running."
    assert journal.get("matching-legacy").status is TaskStatus.FAILED
    assert background_task.check_interrupted_task() is None


def test_absent_legacy_state_still_reconciles_persisted_background_records(monkeypatch, tmp_path):
    journal = _journal_with_path(monkeypatch, tmp_path)
    journal.create_task(
        "No compatibility file",
        task_id="no-legacy-file",
        origin=TaskOrigin.BACKGROUND,
        status=TaskStatus.RUNNING,
    )

    assert background_task.check_interrupted_task() is None
    assert journal.get("no-legacy-file").status is TaskStatus.FAILED


def test_active_legacy_state_without_canonical_record_is_reconstructed_and_failed(
    monkeypatch, tmp_path
):
    journal = _journal_with_path(monkeypatch, tmp_path)
    _write_legacy_state(
        {
            "id": "legacy-only",
            "text": "Rebuild legacy task",
            "steps": ["First", "Second", "Third"],
            "current_step": 1,
            "status": "running",
            "session_id": "bg:legacy-session",
        }
    )

    result = background_task.check_interrupted_task()

    assert result is not None
    record = journal.get("legacy-only")
    assert record.id == "legacy-only"
    assert record.title == "Rebuild legacy task"
    assert record.origin is TaskOrigin.BACKGROUND
    assert record.session_id == "bg:legacy-session"
    assert record.current_step == 1
    assert record.total_steps == 3
    assert record.progress == pytest.approx(1 / 3)
    assert record.status is TaskStatus.FAILED
    assert record.error_summary == "Charlie restarted while this task was still running."


def test_active_legacy_state_without_id_gets_generated_canonical_task(monkeypatch, tmp_path):
    journal = _journal_with_path(monkeypatch, tmp_path)
    _write_legacy_state(
        {
            "text": "Legacy task",
            "steps": ["one", "two"],
            "current_step": 1,
            "status": "running",
        }
    )

    result = background_task.check_interrupted_task()

    assert result is not None
    assert result["id"]
    with open(background_task._STATE_FILE, "r", encoding="utf-8") as state_file:
        persisted = json.load(state_file)
    assert persisted["id"] == result["id"]
    assert persisted["status"] == "failed"
    assert result["status"] == "failed"
    record = journal.get(result["id"])
    assert record.id == result["id"]
    assert record.origin is TaskOrigin.BACKGROUND
    assert record.title == "Legacy task"
    assert record.current_step == 1
    assert record.total_steps == 2
    assert record.status is TaskStatus.FAILED
    assert record.error_summary == background_task._RESTART_ERROR

    assert background_task.check_interrupted_task() is None
    assert journal.get(result["id"]).status is TaskStatus.FAILED


def test_active_legacy_state_with_empty_id_gets_generated_canonical_task(monkeypatch, tmp_path):
    journal = _journal_with_path(monkeypatch, tmp_path)
    _write_legacy_state(
        {
            "id": "",
            "text": "Legacy empty-id task",
            "steps": ["one", "two"],
            "current_step": 1,
            "status": "running",
        }
    )

    result = background_task.check_interrupted_task()

    assert result is not None
    assert result["id"]
    with open(background_task._STATE_FILE, "r", encoding="utf-8") as state_file:
        persisted = json.load(state_file)
    assert persisted["id"] == result["id"]
    assert persisted["status"] == "failed"
    assert result["status"] == "failed"
    record = journal.get(result["id"])
    assert record.origin is TaskOrigin.BACKGROUND
    assert record.title == "Legacy empty-id task"
    assert record.current_step == 1
    assert record.total_steps == 2
    assert record.status is TaskStatus.FAILED
    assert record.error_summary == background_task._RESTART_ERROR


def test_legacy_only_create_failure_does_not_report_success(monkeypatch, tmp_path, caplog):
    journal = _journal_with_path(monkeypatch, tmp_path)
    _write_legacy_state({"text": "Create failure", "status": "running"})

    def reject_create(*args, **kwargs):
        raise ValueError("forced legacy reconstruction create failure")

    monkeypatch.setattr(journal, "create_task", reject_create)
    with caplog.at_level("ERROR", logger="charlie.background_task"):
        result = background_task.check_interrupted_task()

    assert result is None
    with open(background_task._STATE_FILE, "r", encoding="utf-8") as state_file:
        persisted = json.load(state_file)
    assert persisted["status"] == "running"
    assert "id" not in persisted
    assert "Failed to reconstruct legacy background task" in caplog.text


def test_legacy_only_transition_failure_does_not_report_success(monkeypatch, tmp_path, caplog):
    journal = _journal_with_path(monkeypatch, tmp_path)
    _write_legacy_state({"text": "Transition failure", "status": "running"})

    def reject_transition(*args, **kwargs):
        raise TaskTransitionError("forced legacy reconstruction transition failure")

    monkeypatch.setattr(journal, "transition", reject_transition)
    with caplog.at_level("ERROR", logger="charlie.background_task"):
        result = background_task.check_interrupted_task()

    assert result is None
    with open(background_task._STATE_FILE, "r", encoding="utf-8") as state_file:
        persisted = json.load(state_file)
    assert persisted["status"] == "running"
    assert "id" not in persisted
    assert "Failed to reconcile reconstructed legacy background task" in caplog.text


def test_transition_failure_is_logged_without_fabricating_reconciliation(
    monkeypatch, tmp_path, caplog
):
    journal = _journal_with_path(monkeypatch, tmp_path)
    journal.create_task(
        "Transition failure",
        task_id="transition-failure",
        origin=TaskOrigin.BACKGROUND,
        status=TaskStatus.RUNNING,
    )
    _write_legacy_state({"id": "transition-failure", "text": "Transition failure", "status": "running"})

    def reject_transition(*args, **kwargs):
        raise TaskTransitionError("forced restart transition failure")

    monkeypatch.setattr(journal, "transition", reject_transition)
    with caplog.at_level("ERROR", logger="charlie.background_task"):
        result = background_task.check_interrupted_task()

    assert result is None
    assert journal.get("transition-failure").status is TaskStatus.RUNNING
    with open(background_task._STATE_FILE, "r", encoding="utf-8") as state_file:
        assert json.load(state_file)["status"] == "running"
    assert "Failed to reconcile persisted background task transition-failure" in caplog.text


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
    assert "running" in statuses
    assert background_task._journal.get(task.id).status is TaskStatus.RUNNING


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


# --- desktop capability lock (charlie.resource_locks) ---


@pytest.mark.asyncio
async def test_wait_for_desktop_acquires_immediately_when_free(monkeypatch, bg_config):
    from charlie import resource_locks
    monkeypatch.setattr(background_task, "_DESKTOP_AVAILABLE", True)
    resource_locks._owners.pop("desktop", None)
    task = background_task.BackgroundTask(id="t1", text="x")

    assert await background_task._wait_for_desktop(task) is True
    assert resource_locks.current_owner("desktop") == "t1"
    resource_locks.release("desktop", "t1")


@pytest.mark.asyncio
async def test_wait_for_desktop_serializes_two_concurrent_tasks(monkeypatch, bg_config):
    from charlie import resource_locks
    monkeypatch.setattr(background_task, "_DESKTOP_AVAILABLE", True)
    monkeypatch.setattr(background_task, "_POLL_INTERVAL_SEC", 0.01)
    resource_locks._owners.pop("desktop", None)
    resource_locks.acquire("desktop", "t1")  # simulate a first task already holding it
    task2 = background_task.BackgroundTask(id="t2", text="x")

    async def _release_after_one_poll():
        await asyncio.sleep(0.02)
        resource_locks.release("desktop", "t1")

    releaser = asyncio.create_task(_release_after_one_poll())
    result = await background_task._wait_for_desktop(task2)
    await releaser

    assert result is True
    assert resource_locks.current_owner("desktop") == "t2"
    resource_locks.release("desktop", "t2")


@pytest.mark.asyncio
async def test_wait_for_desktop_gives_up_when_cancelled_while_waiting(monkeypatch, bg_config):
    from charlie import resource_locks
    monkeypatch.setattr(background_task, "_DESKTOP_AVAILABLE", True)
    monkeypatch.setattr(background_task, "_POLL_INTERVAL_SEC", 0.01)
    resource_locks._owners.pop("desktop", None)
    resource_locks.acquire("desktop", "t1")
    task2 = background_task.BackgroundTask(id="t2", text="x")

    async def _cancel_after_one_poll():
        await asyncio.sleep(0.02)
        task2.cancel_requested = True

    canceller = asyncio.create_task(_cancel_after_one_poll())
    result = await background_task._wait_for_desktop(task2)
    await canceller

    assert result is False
    resource_locks.release("desktop", "t1")


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
async def test_start_wires_process_memory_dependencies_into_background_brain(monkeypatch, bg_config):
    monkeypatch.setattr(Brain, "chat_stream", _fake_plan_chat_stream)
    bus = FakeEventBus()
    sentinel_store = object()
    sentinel_memory = object()
    sentinel_graph = object()
    sentinel_service = object()
    task = await background_task.start(
        bg_config,
        bus,
        "do the thing",
        session_store=sentinel_store,
        memory_store=sentinel_memory,
        memory_graph=sentinel_graph,
        memory_service=sentinel_service,
    )
    assert task.brain.session_store is sentinel_store
    assert task.brain.memory_store is sentinel_memory
    assert task.brain.memory_graph is sentinel_graph
    assert task.brain.memory_service is sentinel_service
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
