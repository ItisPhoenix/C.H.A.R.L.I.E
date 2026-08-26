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


def test_completed_zero_step_task_is_not_admitted_to_full_workspace(tmp_path):
    journal = TaskJournal(state_path=tmp_path / "tasks.json")
    task = journal.create_task("CPU query", task_id="cpu-query")
    journal.transition("cpu-query", "running")
    journal.transition("cpu-query", "verifying")
    task = journal.complete("cpu-query", result_reference="session:voice_secret")

    assert main._task_workspace_admitted(task) is False


def test_active_zero_step_task_without_execution_detail_is_not_admitted(tmp_path):
    journal = TaskJournal(state_path=tmp_path / "tasks.json")
    journal.create_task("Fast-path placeholder", task_id="placeholder")
    journal.transition("placeholder", "running")

    assert main._task_workspace_admitted(journal.get("placeholder")) is False


def test_waiting_task_with_real_reason_is_admitted_without_steps(tmp_path):
    journal = TaskJournal(state_path=tmp_path / "tasks.json")
    journal.create_task("Approval wait", task_id="approval")
    journal.transition("approval", "waiting", waiting_reason="Awaiting user confirmation")

    assert main._task_workspace_admitted(journal.get("approval")) is True


def test_genuine_multistep_task_remains_admitted_after_completion(tmp_path):
    journal = TaskJournal(state_path=tmp_path / "tasks.json")
    task = journal.create_task("Research", task_id="research", total_steps=3)
    journal.transition("research", "running")
    journal.transition("research", "verifying")
    task = journal.complete("research", result_reference="session:voice_research")

    assert main._task_workspace_admitted(task) is True
