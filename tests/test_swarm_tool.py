"""Tests for the delegate_to_agent tool and ToolRegistry set_blackboard."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from charlie.blackboard import Blackboard
from charlie.tools import registry as tool_registry


def test_set_blackboard_wires_global():
    """set_blackboard must inject the Blackboard into the module-level _blackboard."""
    blackboard = Blackboard()
    tool_registry.set_blackboard(blackboard)
    # We can't directly access module-level _blackboard, but delegate_to_agent
    # will return an error if _blackboard is None -- verify the tool can see it
    result = tool_registry.execute_tool(
        "delegate_to_agent",
        {"agent_name": "F.R.I.D.A.Y.", "task_description": "test"},
    )
    # Should not say "Swarm orchestrator is not running"
    assert "not running" not in result.lower()
    assert "Error" not in result or "Timeout" in result or "timed out" in result


def test_delegate_to_agent_unknown_agent():
    """Unknown agent_name returns a clear error."""
    from charlie.tools import delegate_to_agent

    result = delegate_to_agent("UNKNOWN", "test task")
    assert "Error" in result
    assert "Unknown agent" in result
    assert "F.R.I.D.A.Y." in result


def test_valid_agents_matches_agent_registry():
    """Regression test: _VALID_AGENTS must include every agent in
    AGENT_REGISTRY. Before this fix, it was a hand-maintained tuple listing
    only 5 of the 7 registered agents, silently excluding J.A.R.V.I.S. and
    Vision from delegate_to_agent."""
    from charlie.agents import AGENT_REGISTRY
    from charlie.tools import _VALID_AGENTS

    assert set(_VALID_AGENTS) == set(AGENT_REGISTRY.keys())
    assert "J.A.R.V.I.S." in _VALID_AGENTS
    assert "Vision" in _VALID_AGENTS


def test_delegate_to_agent_no_blackboard(monkeypatch):
    """With _blackboard as None, tool returns 'not running' error."""
    import charlie.tools as tools_mod

    monkeypatch.setattr(tools_mod, "_blackboard", None)
    result = tool_registry.execute_tool(
        "delegate_to_agent",
        {"agent_name": "F.R.I.D.A.Y.", "task_description": "test"},
    )
    assert "not running" in result.lower()


def test_delegate_to_agent_task_added_to_blackboard():
    """Verify the task is actually added to the blackboard."""
    blackboard = Blackboard()
    tool_registry.set_blackboard(blackboard)

    # Run delegate_to_agent in a thread so it doesn't block
    import threading

    results = []

    def run():
        r = tool_registry.execute_tool(
            "delegate_to_agent",
            {"agent_name": "A.I.D.A.", "task_description": "research quantum"},
        )
        results.append(r)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(0.3)  # Let the tool add the task and start polling

    # The task should now be on the blackboard
    tasks = blackboard.get_all_tasks()
    assert len(tasks) >= 1
    # Find our task
    quantum_tasks = [t for t in tasks if "quantum" in t.name]
    assert len(quantum_tasks) >= 1
    task = quantum_tasks[0]
    assert task.assigned_to == "A.I.D.A."
    assert task.status in ("pending", "running")

    # Complete the task so the polling loop exits
    blackboard.update_task(task.id, status="done", result="quantum research complete")
    t.join(timeout=5)
    assert len(results) == 1
    assert "quantum research complete" in results[0]
    assert "A.I.D.A." in results[0]


def test_delegate_to_agent_handles_failure():
    """When a task fails, the tool returns a failure message."""
    blackboard = Blackboard()
    tool_registry.set_blackboard(blackboard)

    import threading

    results = []

    def run():
        r = tool_registry.execute_tool(
            "delegate_to_agent",
            {"agent_name": "H.E.R.B.I.E.", "task_description": "failing task"},
        )
        results.append(r)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(0.3)

    tasks = blackboard.get_all_tasks()
    failing = [t for t in tasks if "failing" in t.name]
    assert len(failing) >= 1
    task = failing[0]

    # Mark as failed
    blackboard.update_task(task.id, status="failed", result="permission denied")
    t.join(timeout=5)
    assert len(results) == 1
    assert "failed" in results[0].lower()
