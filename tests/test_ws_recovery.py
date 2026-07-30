import asyncio
import json
from typing import List
from unittest.mock import AsyncMock, MagicMock

import pytest

from charlie import web_server
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
async def test_recovery_approval_gate(monkeypatch):
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

    # Mock a recovery-cache hit to return a replacement command (deterministic
    # path to the approval gate, now that the big-LLM fallback is removed).
    import charlie.recovery_cache
    monkeypatch.setattr(
        charlie.recovery_cache, "get_cached_resolution", lambda *a, **k: "dir c:\\"
    )

    res = await recover_tool(
        brain=MagicMock(),
        tool_name="shell_execute",
        arguments={"command": "dir_nonexistent"},
        e=FileNotFoundError("[winerror 2] The system cannot find the file specified")
    )

    # Should return rejection message and NOT execute the command
    assert res is not None
    assert "rejected by user" in res.lower()
    assert "dir c:\\" in res
