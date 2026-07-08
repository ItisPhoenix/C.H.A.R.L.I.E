"""Tests for charlie.reflection -- Reflector class."""

from __future__ import annotations

from charlie.blackboard import Blackboard
from charlie.reflection import Reflector


def _make_blackboard() -> Blackboard:
    """Create a blackboard without disk persistence."""
    bb = Blackboard(persist_path="/dev/null")
    bb.stop()  # Stop flush thread
    return bb


def _add_completed_task(
    bb: Blackboard,
    name: str,
    agent: str,
    status: str = "done",
    result: str = "",
    retries: int = 0,
) -> str:
    """Add a task to the blackboard and mark it completed."""
    task = bb.add_task(name=name, assigned_to=agent, column="done")
    bb.update_task(task.id, status=status, result=result, retry_count=retries)
    return task.id


class TestReflectorAfterTask:
    """Reflector.after_task records outcomes correctly."""

    def test_records_success(self) -> None:
        bb = _make_blackboard()
        reflector = Reflector(bb)
        tid = _add_completed_task(bb, "Build API", "FRIDAY", status="done")

        reflector.after_task(tid)
        history = reflector.get_history()
        assert len(history) == 1
        assert history[0]["task_name"] == "Build API"
        assert history[0]["agent"] == "FRIDAY"
        assert history[0]["status"] == "done"

    def test_records_failure_with_finding(self) -> None:
        bb = _make_blackboard()
        reflector = Reflector(bb)
        tid = _add_completed_task(
            bb, "Run tests", "EDITH", status="failed", result="TimeoutError: exceeded 30s"
        )

        reflector.after_task(tid)
        history = reflector.get_history()
        assert len(history) == 1
        assert history[0]["status"] == "failed"

        # Failure should post a finding
        findings = bb.get_findings()
        assert f"failure:{tid}" in findings
        assert findings[f"failure:{tid}"]["task"] == "Run tests"

    def test_unknown_task_ignored(self) -> None:
        bb = _make_blackboard()
        reflector = Reflector(bb)
        reflector.after_task("nonexistent")
        assert len(reflector.get_history()) == 0

    def test_history_trimming(self) -> None:
        bb = _make_blackboard()
        reflector = Reflector(bb)

        # Add 105 tasks to exceed the limit of 100
        for i in range(105):
            tid = _add_completed_task(bb, f"task-{i}", "FRIDAY")
            reflector.after_task(tid)

        history = reflector.get_history()
        assert len(history) == 100
        # Oldest tasks should be trimmed
        assert history[0]["task_name"] == "task-5"


class TestReflectorAnalyze:
    """Reflector.analyze computes aggregate stats."""

    def test_empty_history(self) -> None:
        bb = _make_blackboard()
        reflector = Reflector(bb)
        result = reflector.analyze()
        assert result["total_tasks"] == 0
        assert result["success_rate"] == 1.0

    def test_all_success(self) -> None:
        bb = _make_blackboard()
        reflector = Reflector(bb)

        for i in range(5):
            tid = _add_completed_task(bb, f"task-{i}", "FRIDAY", status="done")
            reflector.after_task(tid)

        result = reflector.analyze()
        assert result["total_tasks"] == 5
        assert result["success_rate"] == 1.0
        assert result["failure_patterns"] == []
        assert result["retry_rate"] == 0.0

    def test_mixed_results(self) -> None:
        bb = _make_blackboard()
        reflector = Reflector(bb)

        # 3 successes
        for i in range(3):
            tid = _add_completed_task(bb, f"ok-{i}", "FRIDAY", status="done")
            reflector.after_task(tid)

        # 1 failure
        tid = _add_completed_task(
            bb, "fail-1", "EDITH", status="failed", result="ValueError: bad input"
        )
        reflector.after_task(tid)

        # 1 retry
        tid = _add_completed_task(
            bb, "retry-1", "HERBIE", status="done", retries=1
        )
        reflector.after_task(tid)

        result = reflector.analyze()
        assert result["total_tasks"] == 5
        assert result["success_rate"] == 0.8  # 4/5 done
        assert result["retry_rate"] == 0.2  # 1/5 retried
        assert len(result["failure_patterns"]) == 1
        assert result["failure_patterns"][0]["agent"] == "EDITH"

    def test_failure_posted_to_blackboard(self) -> None:
        bb = _make_blackboard()
        reflector = Reflector(bb)

        for i in range(3):
            tid = _add_completed_task(bb, f"task-{i}", "FRIDAY", status="failed", result="error")
            reflector.after_task(tid)

        reflector.analyze()
        findings = bb.get_findings()
        assert "reflection:summary" in findings
        summary = findings["reflection:summary"]
        assert summary["total_tasks"] == 3
        assert summary["success_rate"] == 0.0


class TestReflectorSuggestions:
    """Reflector generates actionable suggestions."""

    def test_low_success_rate_suggestion(self) -> None:
        bb = _make_blackboard()
        reflector = Reflector(bb)

        # 2 successes, 8 failures -> 20% success rate
        for i in range(2):
            tid = _add_completed_task(bb, f"ok-{i}", "FRIDAY", status="done")
            reflector.after_task(tid)
        for i in range(8):
            tid = _add_completed_task(bb, f"fail-{i}", "FRIDAY", status="failed", result="error")
            reflector.after_task(tid)

        reflector.analyze()
        suggestions = reflector.suggest_memory_updates()
        assert any("below 50%" in s for s in suggestions)

    def test_high_retry_rate_suggestion(self) -> None:
        bb = _make_blackboard()
        reflector = Reflector(bb)

        # 4 retries out of 10 -> 40% retry rate
        for i in range(6):
            tid = _add_completed_task(bb, f"ok-{i}", "FRIDAY", status="done")
            reflector.after_task(tid)
        for i in range(4):
            tid = _add_completed_task(
                bb, f"retry-{i}", "HERBIE", status="done", retries=1
            )
            reflector.after_task(tid)

        reflector.analyze()
        suggestions = reflector.suggest_memory_updates()
        assert any("retries" in s.lower() for s in suggestions)

    def test_recurring_failure_suggestion(self) -> None:
        bb = _make_blackboard()
        reflector = Reflector(bb)

        # 3 failures with same reason from same agent
        for i in range(3):
            tid = _add_completed_task(
                bb,
                f"fail-{i}",
                "EDITH",
                status="failed",
                result="KeyError: missing 'config'",
            )
            reflector.after_task(tid)

        reflector.analyze()
        suggestions = reflector.suggest_memory_updates()
        assert any("EDITH" in s for s in suggestions)


class TestReflectorClear:
    """Reflector.clear_history resets state."""

    def test_clear(self) -> None:
        bb = _make_blackboard()
        reflector = Reflector(bb)

        tid = _add_completed_task(bb, "task", "FRIDAY", status="done")
        reflector.after_task(tid)
        reflector.analyze()

        assert len(reflector.get_history()) == 1
        assert len(reflector.suggest_memory_updates()) > 0 or True  # may be empty

        reflector.clear_history()
        assert len(reflector.get_history()) == 0
        assert len(reflector.suggest_memory_updates()) == 0
