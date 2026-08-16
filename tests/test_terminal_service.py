import pytest
import asyncio
from charlie.terminal_service import (
    TerminalSession,
    TerminalManager,
    _HAS_CONPTY,
)
from charlie.web_server import validate_ws_origin


def test_validate_ws_origin():
    assert validate_ws_origin(None) is True
    assert validate_ws_origin("") is True
    assert validate_ws_origin("http://localhost:5173") is True
    assert validate_ws_origin("http://127.0.0.1:8000") is True
    assert validate_ws_origin("http://localhost") is True
    assert validate_ws_origin("http://127.0.0.1") is True
    assert validate_ws_origin("tauri://localhost") is True
    assert validate_ws_origin("http://tauri.localhost") is True
    assert validate_ws_origin("https://tauri.localhost") is True

    # Untrusted external origins must be rejected
    assert validate_ws_origin("http://evil.com") is False
    assert validate_ws_origin("http://attacker.local") is False
    assert validate_ws_origin("https://phishing.site") is False
    assert validate_ws_origin("http://localhost.evil.com") is False


@pytest.mark.asyncio
async def test_terminal_session_lifecycle():
    manager = TerminalManager()
    session = await manager.get_or_create_primary()

    assert session is not None
    assert session.session_id == "primary"
    assert session.status == "running"
    assert "powershell" in session.shell_name.lower() or "cmd" in session.shell_name.lower()
    if _HAS_CONPTY:
        assert session.pid is not None and session.pid > 0

    # Snapshot check
    snap = manager.snapshot("primary")
    assert snap["session_id"] == "primary"
    assert snap["status"] == "running"
    assert snap["cols"] == 80
    assert snap["rows"] == 24

    # Persistence check: get_or_create_primary returns the exact same session
    session_again = await manager.get_or_create_primary()
    assert session_again.session_id == session.session_id
    assert session_again.pid == session.pid

    # Resize check
    await manager.resize("primary", 120, 40)
    assert session.cols == 120
    assert session.rows == 40

    # Subscription and human write check
    queue = session.subscribe()
    assert queue is not None

    # User write (does not require approval)
    await manager.write_bytes("primary", "echo test_conpty_ok\r\n", source="user")

    # Wait for queue output
    received_output = False
    for _ in range(20):
        try:
            msg = await asyncio.wait_for(queue.get(), timeout=0.2)
            if msg.get("type") == "output":
                received_output = True
                break
        except asyncio.TimeoutError:
            pass

    session.unsubscribe(queue)
    assert received_output is True

    # Charlie write check
    await manager.write("primary", "Get-Process -Id $PID", source="charlie")

    # Cleanup
    await manager.close("primary")
    assert session.status == "closed"
