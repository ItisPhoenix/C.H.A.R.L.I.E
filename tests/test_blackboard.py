"""Tests for Blackboard state engine."""

import os
import tempfile

import pytest

from charlie.blackboard import Blackboard


@pytest.fixture
def board():
    """Blackboard with temp persist file, cleaned up after test."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    bb = Blackboard(persist_path=path)
    yield bb
    bb.stop()
    try:
        os.unlink(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Task operations
# ---------------------------------------------------------------------------

class TestTaskOperations:
    def test_add_task(self, board: Blackboard):
        task = board.add_task("Build API", assigned_to="F.R.I.D.A.Y.")
        assert task.name == "Build API"
        assert task.assigned_to == "F.R.I.D.A.Y."
        assert task.status == "pending"
        assert task.id in [t.id for t in board.get_all_tasks()]

    def test_update_task(self, board: Blackboard):
        task = board.add_task("Test task")
        updated = board.update_task(task.id, status="running")
        assert updated is not None
        assert updated.status == "running"

    def test_update_nonexistent_task(self, board: Blackboard):
        result = board.update_task("nonexistent", status="done")
        assert result is None

    def test_get_pending_tasks(self, board: Blackboard):
        t1 = board.add_task("Step 1", approval_status="approved")
        t2 = board.add_task("Step 2", dependencies=[t1.id], approval_status="approved")
        board.update_task(t1.id, status="done")

        pending = board.get_pending_tasks()
        assert len(pending) == 1
        assert pending[0].id == t2.id

    def test_pending_tasks_respect_dependencies(self, board: Blackboard):
        t1 = board.add_task("Prerequisite", approval_status="approved")
        board.add_task("Dependent", dependencies=[t1.id], approval_status="approved")

        pending = board.get_pending_tasks()
        assert len(pending) == 1
        assert pending[0].id == t1.id  # Only t1 is unblocked

    def test_task_retry(self, board: Blackboard):
        task = board.add_task("Flaky task")
        board.update_task(task.id, status="failed")
        reset = board.reset_for_retry(task.id)
        assert reset is not None
        assert reset.status == "pending"
        assert reset.retry_count == 1

    def test_escalation_check(self, board: Blackboard):
        task = board.add_task("Failing task")
        board.update_task(task.id, status="failed")
        escalated = board.check_escalation()
        assert len(escalated) == 1
        assert escalated[0].id == task.id

    def test_escalation_check_includes_retry_exhausted_tasks(self, board: Blackboard):
        """Regression test: a task that has exhausted its retries must still
        be returned by check_escalation so the caller (swarm's
        _handle_escalation) can mark it permanently failed. Before this fix,
        the retry_count < MAX_RETRIES filter excluded these tasks entirely,
        so they sat in "failed" status forever with no terminal message."""
        from charlie.blackboard import MAX_RETRIES

        task = board.add_task("Chronically failing task")
        board.update_task(task.id, status="failed", retry_count=MAX_RETRIES)
        escalated = board.check_escalation()
        assert len(escalated) == 1
        assert escalated[0].id == task.id
        assert escalated[0].retry_count == MAX_RETRIES


# ---------------------------------------------------------------------------
# Agent operations
# ---------------------------------------------------------------------------

class TestAgentOperations:
    def test_register_agent(self, board: Blackboard):
        card = board.register_agent("J.A.R.V.I.S.")
        assert card.name == "J.A.R.V.I.S."
        assert card.status == "idle"

    def test_update_agent(self, board: Blackboard):
        board.register_agent("F.R.I.D.A.Y.")
        updated = board.update_agent(
            "F.R.I.D.A.Y.", status="working", current_task="task-1"
        )
        assert updated is not None
        assert updated.status == "working"
        assert updated.current_task == "task-1"

    def test_get_agents(self, board: Blackboard):
        board.register_agent("Agent A")
        board.register_agent("Agent B")
        agents = board.get_agents()
        assert len(agents) == 2
        assert "Agent A" in agents
        assert "Agent B" in agents


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------

class TestFindings:
    def test_add_finding(self, board: Blackboard):
        board.add_finding("fact_1", "Charlie runs on Windows")
        findings = board.get_findings()
        assert findings["fact_1"] == "Charlie runs on Windows"

    def test_overwrite_finding(self, board: Blackboard):
        board.add_finding("key", "value1")
        board.add_finding("key", "value2")
        assert board.get_findings()["key"] == "value2"


# ---------------------------------------------------------------------------
# Snapshot & persistence
# ---------------------------------------------------------------------------

class TestSnapshot:
    def test_snapshot_structure(self, board: Blackboard):
        board.add_task("Task 1")
        board.register_agent("Agent 1")
        board.add_finding("x", 1)

        snap = board.snapshot()
        assert "tasks" in snap
        assert "agents" in snap
        assert "findings" in snap
        assert len(snap["tasks"]) == 1
        assert "Agent 1" in snap["agents"]
        assert snap["findings"]["x"] == 1

    def test_persistence_flush(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            bb = Blackboard(persist_path=path)
            bb.add_task("Persist me")
            bb.update_task(
                list(bb._tasks.keys())[0], status="done"
            )
            bb._flush()  # Force flush

            import json
            data = json.loads(open(path, encoding="utf-8").read())
            assert len(data["tasks"]) == 1
            assert data["tasks"][0]["status"] == "done"
            bb.stop()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
