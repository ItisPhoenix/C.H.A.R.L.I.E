"""Tests for agent gated-action pause-and-notify (Task C2).

Background swarm agents have no human turn to prompt mid-conversation, so a
gated tool call used to be a hard block (see the original comment on
charlie/agents/base.py:_call_tool). It now pauses the owning task and asks
the user via the same tool_approval_request/tool_approve/tool_reject channel
charlie.core.Brain.request_tool_approval uses for the foreground chat turn.
"""

import asyncio

import pytest

from charlie import recovery
from charlie.agents.base import BaseAgent
from charlie.blackboard import Blackboard
from charlie.core import pending_tool_approvals, resolve_tool_approval


class _GatedAgent(BaseAgent):
    name = "TestGatedAgent"
    allowed_tools = ("shell_execute",)

    async def _do_action(self, task_name, task):
        return await self._call_tool("shell_execute", {"command": "rm -rf foo"})


class _FakeEventBus:
    def __init__(self):
        self.emitted = []

    async def emit(self, event_type, payload):
        self.emitted.append((event_type, payload))


@pytest.fixture(autouse=True)
def _reset_recovery_state(monkeypatch):
    monkeypatch.setattr(recovery, "_active_ws_count", 0)
    monkeypatch.setattr(recovery, "_event_bus", None)
    yield
    pending_tool_approvals.clear()


@pytest.mark.asyncio
async def test_gated_tool_call_pauses_task_and_emits_approval_request(monkeypatch):
    bus = _FakeEventBus()
    monkeypatch.setattr(recovery, "_active_ws_count", 1)
    monkeypatch.setattr(recovery, "_event_bus", bus)
    monkeypatch.setattr(
        "charlie.tools.registry.execute_tool",
        lambda name, arguments: "shell ran fine",
    )

    blackboard = Blackboard()
    task = blackboard.add_task(
        "dangerous task", assigned_to="TestGatedAgent", approval_status="approved"
    )
    agent = _GatedAgent(blackboard, None)
    agent._task_id = task.id

    async def approve_shortly():
        await asyncio.sleep(0.05)
        paused_task = blackboard.get_task(task.id)
        assert paused_task.status == "paused"
        assert paused_task.approval_status == "pending_approval"
        assert bus.emitted, "expected an approval-request event on the bus"
        event_type, payload = bus.emitted[0]
        assert event_type == "tool_approval_request"
        assert resolve_tool_approval(payload["request_id"], True) is True

    approve_task = asyncio.create_task(approve_shortly())
    result = await agent._do_action(task.name, task)
    await approve_task

    assert result == "shell ran fine"
    resumed = blackboard.get_task(task.id)
    assert resumed.status == "running"
    assert resumed.approval_status == "approved"


@pytest.mark.asyncio
async def test_gated_tool_call_rejected_blocks_with_message(monkeypatch):
    bus = _FakeEventBus()
    monkeypatch.setattr(recovery, "_active_ws_count", 1)
    monkeypatch.setattr(recovery, "_event_bus", bus)
    called = []
    monkeypatch.setattr(
        "charlie.tools.registry.execute_tool",
        lambda name, arguments: called.append(name),
    )

    blackboard = Blackboard()
    task = blackboard.add_task(
        "dangerous task", assigned_to="TestGatedAgent", approval_status="approved"
    )
    agent = _GatedAgent(blackboard, None)
    agent._task_id = task.id

    async def reject_shortly():
        await asyncio.sleep(0.05)
        event_type, payload = bus.emitted[0]
        assert resolve_tool_approval(payload["request_id"], False) is True

    reject_task = asyncio.create_task(reject_shortly())
    result = await agent._do_action(task.name, task)
    await reject_task

    assert "Blocked by user decision" in result
    assert called == []
    resumed = blackboard.get_task(task.id)
    assert resumed.status == "running"
    assert resumed.approval_status == "rejected"


@pytest.mark.asyncio
async def test_gated_tool_call_declines_safely_with_no_channel():
    """No dashboard connected and no notify callback wired -- fail safe
    (declined) rather than hang or silently proceed, matching
    Brain.request_tool_approval's existing no-channel behavior."""
    blackboard = Blackboard()
    task = blackboard.add_task(
        "dangerous task", assigned_to="TestGatedAgent", approval_status="approved"
    )
    agent = _GatedAgent(blackboard, None)
    agent._task_id = task.id

    result = await agent._do_action(task.name, task)

    assert "Error" in result
    assert "Blocked by user decision" in result
    resumed = blackboard.get_task(task.id)
    assert resumed.status == "running"
    assert resumed.approval_status == "rejected"
