import pytest

from charlie.background_task import RESTART_ERROR
from charlie.calendar_runtime import CalendarRuntime
from charlie.calendar_scheduler import deliver_due_automation_tasks
from charlie.task_journal import TaskJournal, TaskOrigin, TaskStatus

NOW = "2026-09-24T09:00:00Z"


async def _schedule(runtime):
    return await runtime.execute(
        "create_automation", "task", "Run report", NOW, "once", "UTC"
    )


@pytest.mark.asyncio
async def test_due_task_dispatch_waits_for_task_journal_terminal_state(tmp_path):
    runtime = CalendarRuntime(str(tmp_path / "calendar.sqlite3"))
    journal = TaskJournal(tmp_path / "tasks.json")
    schedule = await _schedule(runtime)
    dispatched = []

    async def dispatch(claim):
        dispatched.append(claim["active_task_id"])
        journal.create_task(
            claim["text"], task_id=claim["active_task_id"], origin=TaskOrigin.BACKGROUND,
            status=TaskStatus.RUNNING,
        )

    try:
        assert await deliver_due_automation_tasks(
            runtime, NOW, dispatch_callback=dispatch, task_journal=journal
        ) == 1
        task_id = dispatched[0]
        assert task_id == (await runtime.execute("get_automation", schedule["id"]))["active_task_id"]
        assert await deliver_due_automation_tasks(
            runtime, NOW, dispatch_callback=dispatch, task_journal=journal
        ) == 0
        assert dispatched == [task_id]

        journal.transition(task_id, TaskStatus.VERIFYING)
        journal.transition(task_id, TaskStatus.COMPLETED)
        assert await deliver_due_automation_tasks(
            runtime, NOW, dispatch_callback=dispatch, task_journal=journal
        ) == 1
        finished = await runtime.execute("get_automation", schedule["id"])
        assert finished["active_run_status"] == "succeeded"
        assert finished["status"] == "completed"
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_dispatch_failure_without_task_record_is_finalized(tmp_path):
    runtime = CalendarRuntime(str(tmp_path / "calendar.sqlite3"))
    journal = TaskJournal(tmp_path / "tasks.json")
    schedule = await _schedule(runtime)

    async def fail(_claim):
        raise RuntimeError("startup unavailable")

    try:
        assert await deliver_due_automation_tasks(
            runtime, NOW, dispatch_callback=fail, task_journal=journal
        ) == 1
        failed = await runtime.execute("get_automation", schedule["id"])
        assert failed["active_run_status"] == "failed"
        assert failed["active_run_error"] == "RuntimeError: startup unavailable"
        assert failed["claim_token"] is None
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_brain_dispatch_passes_internal_task_id_to_background_start(monkeypatch):
    from types import SimpleNamespace

    from charlie import background_task, recovery
    from charlie.core import Brain

    captured = {}

    async def start(_config, _event_bus, _text, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id=kwargs["task_id"], status="queued")

    monkeypatch.setattr(recovery, "_event_bus", object())
    monkeypatch.setattr(background_task, "start", start)
    brain = SimpleNamespace(
        config=object(), session_store=None, memory_store=None, memory_graph=None, memory_service=None,
        on_tool_call=None, on_tool_result=None, on_operation_result=None,
        on_thinking_update=None, on_result_stored=None, on_research_result=None,
    )
    await Brain._handle_start_background_task(brain, {"text": "Run report"}, task_id="stable-task-id")
    assert captured["task_id"] == "stable-task-id"


@pytest.mark.asyncio
async def test_restart_retries_only_when_task_record_is_absent(tmp_path):
    runtime = CalendarRuntime(str(tmp_path / "calendar.sqlite3"))
    journal = TaskJournal(tmp_path / "tasks.json")
    schedule = await _schedule(runtime)
    first_claim = await runtime.execute("claim_due_automation", NOW, kind="task")
    assert first_claim is not None
    assert await runtime.execute(
        "start_automation_run", schedule["id"], first_claim["claim_token"], first_claim["revision"]
    )
    task_id = first_claim["active_task_id"]
    assert await runtime.execute("reconcile_automation_claims") == 1
    replayed = []

    async def dispatch(claim):
        replayed.append(claim["active_task_id"])
        journal.create_task(
            claim["text"], task_id=claim["active_task_id"], origin=TaskOrigin.BACKGROUND,
            status=TaskStatus.RUNNING,
        )

    try:
        assert await deliver_due_automation_tasks(
            runtime, NOW, dispatch_callback=dispatch, task_journal=journal
        ) == 1
        assert replayed == [task_id]
        assert (await runtime.execute("get_automation", schedule["id"]))["active_run_status"] == "running"
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_restart_failed_matching_record_stays_interrupted(tmp_path):
    runtime = CalendarRuntime(str(tmp_path / "calendar.sqlite3"))
    journal = TaskJournal(tmp_path / "tasks.json")
    schedule = await _schedule(runtime)
    claim = await runtime.execute("claim_due_automation", NOW, kind="task")
    assert claim is not None
    assert await runtime.execute("start_automation_run", schedule["id"], claim["claim_token"], claim["revision"])
    journal.create_task(
        claim["text"], task_id=claim["active_task_id"], origin=TaskOrigin.BACKGROUND,
        status=TaskStatus.RUNNING,
    )
    journal.transition(claim["active_task_id"], TaskStatus.FAILED, error_summary=RESTART_ERROR)
    assert await runtime.execute("reconcile_automation_claims") == 1
    dispatched = []

    async def should_not_replay(claim):
        dispatched.append(claim["active_task_id"])

    try:
        assert await deliver_due_automation_tasks(
            runtime, NOW, dispatch_callback=should_not_replay, task_journal=journal
        ) == 0
        interrupted = await runtime.execute("get_automation", schedule["id"])
        assert interrupted["active_run_status"] == "interrupted"
        assert interrupted["active_task_id"] == claim["active_task_id"]
        assert dispatched == []
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_restart_approval_pending_matching_record_stays_interrupted(tmp_path):
    runtime = CalendarRuntime(str(tmp_path / "calendar.sqlite3"))
    journal = TaskJournal(tmp_path / "tasks.json")
    schedule = await _schedule(runtime)
    claim = await runtime.execute("claim_due_automation", NOW, kind="task")
    assert claim is not None
    assert await runtime.execute("start_automation_run", schedule["id"], claim["claim_token"], claim["revision"])
    journal.create_task(
        claim["text"], task_id=claim["active_task_id"], origin=TaskOrigin.BACKGROUND,
        status=TaskStatus.APPROVAL_REQUIRED,
    )
    assert await runtime.execute("reconcile_automation_claims") == 1
    dispatched = []

    async def should_not_replay(claim):
        dispatched.append(claim["active_task_id"])

    try:
        assert await deliver_due_automation_tasks(
            runtime, NOW, dispatch_callback=should_not_replay, task_journal=journal
        ) == 0
        interrupted = await runtime.execute("get_automation", schedule["id"])
        assert interrupted["active_run_status"] == "interrupted"
        assert interrupted["active_task_id"] == claim["active_task_id"]
        assert dispatched == []
    finally:
        runtime.close()
