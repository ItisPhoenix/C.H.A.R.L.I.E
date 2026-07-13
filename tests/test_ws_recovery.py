import asyncio
import json
from typing import List
from unittest.mock import AsyncMock, MagicMock

import pytest

from charlie import web_server
from charlie.blackboard import Blackboard
from charlie.recovery import (
    pending_proposals,
    recover_tool,
    set_active_session_id,
    set_active_ws_count,
)


class DummyWebSocket:
    def __init__(self, session_id: str | None = None):
        self.session_id = session_id
        self.sent: List[dict] = []
        self.closed = False

    async def send_text(self, message: str) -> None:
        if self.closed:
            raise RuntimeError("WebSocket closed")
        self.sent.append(json.loads(message))

@pytest.mark.asyncio
async def test_session_isolation_and_routing():
    """Assert no token, transcript, or tool activity crosses session boundaries."""
    ws_a = DummyWebSocket("session_A")
    ws_b = DummyWebSocket("session_B")

    web_server.active_connections.clear()
    web_server.ws_sessions.clear()

    web_server.active_connections.add(ws_a)
    web_server.active_connections.add(ws_b)
    web_server.ws_sessions[ws_a] = "session_A"
    web_server.ws_sessions[ws_b] = "session_B"

    # 1. token event (scoped)
    await web_server.broadcast({
        "type": "token",
        "session_id": "session_A",
        "payload": {"text": "A-only-token"}
    })
    # 2. transcript event (scoped)
    await web_server.broadcast({
        "type": "transcript",
        "session_id": "session_B",
        "payload": {"text": "B-only-transcript"}
    })
    # 3. non-scoped event (broadcast to all)
    await web_server.broadcast({
        "type": "thinking",
        "payload": {"status": "thinking"}
    })

    # Assert A only got A's scoped and non-scoped
    a_sent = [m["type"] for m in ws_a.sent]
    assert "token" in a_sent
    assert "transcript" not in a_sent
    assert "thinking" in a_sent

    # Assert B only got B's scoped and non-scoped
    b_sent = [m["type"] for m in ws_b.sent]
    assert "token" not in b_sent
    assert "transcript" in b_sent
    assert "thinking" in b_sent

@pytest.mark.asyncio
async def test_recovery_approval_gate():
    """Assert rejected/disconnected recovery never calls shell execution."""
    # 1. Disconnected test
    set_active_ws_count(0)
    res = await recover_tool(
        brain=MagicMock(),
        tool_name="shell_execute",
        arguments={"command": "dir"},
        e=FileNotFoundError("[winerror 2] The system cannot find the file specified")
    )
    # Should fail safely with None when disconnected (no dynamic recovery run)
    assert res is None

    # 2. Rejected test
    set_active_ws_count(1)
    set_active_session_id("session_A")

    # Mock event bus
    mock_bus = AsyncMock()
    import charlie.recovery
    charlie.recovery._event_bus = mock_bus

    # Async task that simulates user rejection via WS command
    async def simulate_reject():
        await asyncio.sleep(0.1)
        # Find the proposal
        pids = list(pending_proposals.keys())
        if pids:
            pending_proposals[pids[0]].set_result(False)

    asyncio.create_task(simulate_reject())

    # Mock big LLM query to return a replacement command
    mock_brain = MagicMock()
    mock_brain.config.big_llm_key = "test-key"
    mock_brain.config.big_llm_model = "test-model"
    # mock http post client for LLM query
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": '{"fixed_command": "dir c:\\\\", "explanation": "fix"}'
                }
            }
        ]
    }
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_brain._big_client = mock_client

    res = await recover_tool(
        brain=mock_brain,
        tool_name="shell_execute",
        arguments={"command": "dir_nonexistent"},
        e=FileNotFoundError("[winerror 2] The system cannot find the file specified")
    )

    # Should return rejection message and NOT execute the command
    assert res is not None
    assert "rejected by user" in res.lower()
    assert "dir c:\\" in res

@pytest.mark.asyncio
async def test_task_approval_scheduling_gate():
    """Assert the blackboard scheduler only selects approved pending tasks."""
    board = Blackboard()

    # 1. Swarm created task (pending_approval)
    t1 = board.add_task("Jarvis Task", assigned_to="J.A.R.V.I.S.", approval_status="pending_approval")

    # get_pending_tasks should be empty since t1 is not approved
    assert len(board.get_pending_tasks()) == 0

    # 2. User approves task
    board.update_task(t1.id, approval_status="approved")

    # Now it is ready for execution
    pending = board.get_pending_tasks()
    assert len(pending) == 1
    assert pending[0].id == t1.id

    # 3. User rejects a task
    t2 = board.add_task("Friday Task", assigned_to="F.R.I.D.A.Y.", approval_status="pending_approval")
    board.update_task(t2.id, approval_status="rejected", status="failed", result="Rejection: Not needed")

    assert len(board.get_pending_tasks()) == 1  # Only t1 is pending/approved
    assert board.get_task(t2.id).status == "failed"
