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
