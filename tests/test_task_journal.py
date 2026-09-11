import json

import pytest

from charlie.task_journal import (
    TaskJournal,
    TaskOrigin,
    TaskPriority,
    TaskStatus,
    TaskTransitionError,
    normalize_task_status,
)


def test_default_task_journal_is_shared_singleton() -> None:
    from charlie.task_journal import get_task_journal

    assert get_task_journal() is get_task_journal()


def test_task_journal_creates_stable_canonical_record() -> None:
    journal = TaskJournal()

    task = journal.create_task(
        title="Research deployment status",
        origin=TaskOrigin.RESEARCH,
        priority=TaskPriority.HIGH,
        session_id="session-1",
        capability_requirements=("browser",),
    )

    assert task.id
    assert task.status is TaskStatus.QUEUED
    assert task.origin is TaskOrigin.RESEARCH
    assert task.priority is TaskPriority.HIGH
    assert task.session_id == "session-1"
    assert task.capability_requirements == ("browser",)
    assert task.to_dict()["status"] == "queued"
    assert "done" not in {status.value for status in TaskStatus}
    assert "awaiting_approval" not in {status.value for status in TaskStatus}


def test_task_journal_enforces_lifecycle_and_rejects_terminal_regression() -> None:
    journal = TaskJournal()
    task = journal.create_task(title="Inspect logs")

    journal.transition(task.id, TaskStatus.PLANNING)
    journal.transition(task.id, TaskStatus.RUNNING)
    journal.transition(task.id, TaskStatus.VERIFYING)
    journal.complete(task.id, result_reference="result-1")

    assert journal.get(task.id).status is TaskStatus.COMPLETED
    assert journal.get(task.id).result_reference == "result-1"
    with pytest.raises(TaskTransitionError):
        journal.transition(task.id, TaskStatus.RUNNING)


def test_task_journal_supports_waiting_approval_progress_and_idempotent_cancel() -> None:
    journal = TaskJournal()
    task = journal.create_task(title="Open account portal")

    journal.transition(task.id, TaskStatus.PLANNING)
    journal.require_approval(task.id, approval_reference="approval-1")
    journal.update_progress(task.id, progress=0.4, current_action="Waiting for approval")
    journal.cancel(task.id)
    journal.cancel(task.id)

    current = journal.get(task.id)
    assert current.status is TaskStatus.CANCELLED
    assert current.approval_reference == "approval-1"
    assert current.progress == 0.4
    assert current.current_action == "Waiting for approval"


def test_task_journal_normalizes_legacy_statuses_and_persists(tmp_path) -> None:
    path = tmp_path / "task-journal.json"
    path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "legacy-1",
                        "title": "Old task",
                        "status": "done",
                        "origin": "background",
                        "priority": "normal",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    journal = TaskJournal(state_path=path)
    assert journal.get("legacy-1").status is TaskStatus.COMPLETED
    assert normalize_task_status("awaiting_approval") is TaskStatus.APPROVAL_REQUIRED

    journal.create_task(title="New task", origin=TaskOrigin.SYSTEM)
    restored = TaskJournal(state_path=path)
    assert {task.title for task in restored.list()} == {"Old task", "New task"}
    assert all(task.status in set(TaskStatus) for task in restored.list())


def test_task_journal_prunes_oldest_terminal_records_after_transition(monkeypatch) -> None:
    counter = iter(range(100))
    monkeypatch.setattr(
        "charlie.task_journal.utc_now_iso",
        lambda: f"2026-01-01T00:00:00.{next(counter):06d}Z",
    )
    journal = TaskJournal(max_terminal_records=2)

    for index in range(3):
        task = journal.create_task(f"Task {index}", task_id=f"task-{index}")
        journal.transition(task.id, TaskStatus.PLANNING)
        journal.transition(task.id, TaskStatus.RUNNING)
        journal.transition(task.id, TaskStatus.VERIFYING)
        journal.complete(task.id)

    assert {task.id for task in journal.list()} == {"task-1", "task-2"}
    assert len([task for task in journal.list() if task.status in {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    }]) == 2


def test_task_journal_prunes_on_load_and_persists_converged_state(tmp_path) -> None:
    path = tmp_path / "task-journal.json"
    path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "old",
                        "title": "Old",
                        "status": "completed",
                        "created_at": "2026-01-01T00:00:00.000000Z",
                        "updated_at": "2026-01-01T00:00:01.000000Z",
                        "completed_at": "2026-01-01T00:00:01.000000Z",
                    },
                    {
                        "id": "newer",
                        "title": "Newer",
                        "status": "failed",
                        "created_at": "2026-01-01T00:00:02.000000Z",
                        "updated_at": "2026-01-01T00:00:03.000000Z",
                        "completed_at": "2026-01-01T00:00:03.000000Z",
                    },
                    {
                        "id": "newest",
                        "title": "Newest",
                        "status": "cancelled",
                        "created_at": "2026-01-01T00:00:04.000000Z",
                        "updated_at": "2026-01-01T00:00:05.000000Z",
                        "completed_at": "2026-01-01T00:00:05.000000Z",
                    },
                    {
                        "id": "active",
                        "title": "Active",
                        "status": "running",
                        "created_at": "2026-01-01T00:00:06.000000Z",
                        "updated_at": "2026-01-01T00:00:06.000000Z",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    journal = TaskJournal(state_path=path, max_terminal_records=2)
    assert {task.id for task in journal.list()} == {"newer", "newest", "active"}

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert {task["id"] for task in persisted["tasks"]} == {"newer", "newest", "active"}
    restored = TaskJournal(state_path=path, max_terminal_records=2)
    assert {task.id for task in restored.list()} == {"newer", "newest", "active"}


def test_task_journal_rejects_negative_history_bound() -> None:
    with pytest.raises(ValueError):
        TaskJournal(max_terminal_records=-1)

    assert TaskJournal(max_terminal_records=0).max_terminal_records == 0


def test_load_prune_persistence_failure_keeps_pruned_records(tmp_path, monkeypatch) -> None:
    path = tmp_path / "task-journal.json"
    path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "old",
                        "title": "Old",
                        "status": "completed",
                        "completed_at": "2026-01-01T00:00:01.000000Z",
                    },
                    {
                        "id": "new",
                        "title": "New",
                        "status": "completed",
                        "completed_at": "2026-01-01T00:00:02.000000Z",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    def fail_persist(_journal):
        raise OSError("normalization write failed")

    monkeypatch.setattr(TaskJournal, "_persist", fail_persist)
    journal = TaskJournal(state_path=path, max_terminal_records=1)

    assert [task.id for task in journal.list()] == ["new"]
