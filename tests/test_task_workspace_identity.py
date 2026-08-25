"""Runtime-authoritative task to workspace identity regressions."""

import main
from charlie.task_journal import TaskJournal


def test_task_focus_uses_stable_runtime_identity_and_replayable_intent(tmp_path):
    journal = TaskJournal(state_path=tmp_path / "tasks.json")
    task = journal.create_task("Task A", task_id="task-a")

    first = main._task_workspace_intent(task).to_dict()
    second = main._task_workspace_intent(journal.get("task-a")).to_dict()

    assert first["id"] == second["id"] == "task-workspace:task-a"
    assert first["task_id"] == second["task_id"] == "task-a"
    assert first["workspace_type"] == second["workspace_type"] == "tasks"
    assert first["replayable"] is True


def test_distinct_tasks_have_distinct_workspace_identity_without_frontend_lookup(tmp_path):
    journal = TaskJournal(state_path=tmp_path / "tasks.json")
    task_a = journal.create_task("Task A", task_id="task-a")
    task_b = journal.create_task("Task B", task_id="task-b")

    intent_a = main._task_workspace_intent(task_a).to_dict()
    intent_b = main._task_workspace_intent(task_b).to_dict()

    assert intent_a["id"] != intent_b["id"]
    assert intent_a["content"]["task_id"] == "task-a"
    assert intent_b["content"]["task_id"] == "task-b"
