import asyncio

import pytest

from charlie.terminal_service import (
    _HAS_CONPTY,
    TerminalManager,
    TerminalSession,
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

    # Cleanup
    await manager.close("primary")
    assert session.status == "closed"


@pytest.mark.asyncio
async def test_conpty_process_termination_non_zero_exit():
    """Verify real Win32 ConPTY process termination exit code."""
    from charlie.terminal_service import WindowsConPTY

    if _HAS_CONPTY:
        backend = WindowsConPTY(cols=80, rows=24)
        backend.start()
        session = TerminalSession(session_id="exit-test-direct", backend=backend)
        session.start_reader()
        # Direct exit from the shell process itself with code 42
        session.write_bytes("exit 42\r\n", source="user")
        for _ in range(50):
            await asyncio.sleep(0.1)
            snap = session.snapshot()
            if snap["status"] in ("exited", "failed"):
                break
        snap = session.snapshot()
        assert snap["status"] == "failed"
        assert snap["exit_code"] == 42
        session.close()
