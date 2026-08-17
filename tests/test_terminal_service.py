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


@pytest.mark.asyncio
async def test_terminal_resource_arbitration_and_transactions():
    manager = TerminalManager()
    session = await manager.get_or_create_primary()

    # 1. User typing takes over ownership and sets lease holder to 'user'
    session.write_bytes("echo hello\r\n", source="user")
    assert session.lease_holder == "user"

    # 2. Charlie command immediately rejected if user actively typing
    with pytest.raises(RuntimeError, match="actively interacting"):
        await manager.execute_charlie_command("primary", "Get-Date", task_id="task-agent-1")

    # 3. Simulate user idle time passing
    session._last_user_input_time = 0.0

    # 4. Charlie unapproved shell command requires approval
    with pytest.raises(PermissionError, match="Approval required"):
        await manager.execute_charlie_command("primary", "Get-Date", task_id="task-agent-1", approved=False)

    # 5. Approved execution succeeds, holds lease during execution, and audits outcome
    class DummyAudit:
        def __init__(self):
            self.entries = []
        def record(self, tool_name, args, outcome):
            self.entries.append({"tool": tool_name, "args": args, "outcome": outcome})

    audit = DummyAudit()
    approved_res = await manager.execute_charlie_command(
        "primary",
        "echo 'charlie_tx_test'",
        task_id="task-agent-1",
        audit_store=audit,
        approved=True,
    )
    assert approved_res["status"] == "ok"
    assert approved_res["exit_code"] == 0
    assert len(audit.entries) == 1
    assert audit.entries[0]["tool"] == "terminal_exec"
    assert audit.entries[0]["outcome"] == "COMPLETED"
    assert session.lease_holder == "idle"

    # 6. Second Charlie command cannot interleave during active command
    # Simulate an active transaction holding the lease
    from charlie.resource_locks import default_lease_manager
    lease = await default_lease_manager.acquire("terminal", owner_id="task-first", timeout=2.0)
    try:
        with pytest.raises(TimeoutError):
            await manager.execute_charlie_command(
                "primary",
                "echo 'interleaved'",
                task_id="task-second",
                approved=True,
                timeout=1.0,
            )
    finally:
        await lease.release()

    # 7. User Ctrl+C / manual takeover interrupts active Charlie transaction
    loop = asyncio.get_running_loop()
    fake_tx_id = "testtx123456"
    tx_fut = loop.create_future()
    session._pending_transactions[fake_tx_id] = tx_fut

    # Human hits Ctrl+C
    session.interrupt()
    assert session.lease_holder == "user"
    assert tx_fut.cancelled()
    assert fake_tx_id not in session._pending_transactions

    await manager.close("primary")


@pytest.mark.asyncio
async def test_terminal_real_windows_non_zero_exit_proof():
    """Verify authentic Windows non-zero exit codes on command execution and shell exit."""
    manager = TerminalManager()
    session = await manager.get_or_create_primary()
    session._last_user_input_time = 0.0

    class DummyAudit:
        def __init__(self):
            self.entries = []
        def record(self, tool_name, args, outcome):
            self.entries.append({"tool": tool_name, "args": args, "outcome": outcome})

    audit = DummyAudit()

    # 1. Execute a command that terminates with exit code 42
    if "powershell" in session.shell_name.lower():
        cmd = "powershell.exe -NoProfile -NonInteractive -Command \"exit 42\""
    else:
        cmd = "cmd.exe /c \"exit /b 42\""

    res = await manager.execute_charlie_command(
        "primary",
        cmd,
        task_id="task-nonzero-test",
        audit_store=audit,
        approved=True,
    )

    assert res["status"] == "failed"
    assert res["exit_code"] == 42
    assert len(audit.entries) == 1
    assert "FAILED (exit code 42)" in audit.entries[0]["outcome"]

    await manager.close("primary")


@pytest.mark.asyncio
async def test_conpty_process_termination_non_zero_exit():
    """Verify real Win32 ConPTY process termination exit code."""
    from charlie.terminal_service import WindowsConPTY, FallbackPTY, TerminalSession

    if _HAS_CONPTY:
        backend = WindowsConPTY(cols=80, rows=24)
        backend.start()
        session = TerminalSession(session_id="exit-test-direct", backend=backend)
        session.start_reader()
        # Direct exit from the shell process itself with code 42
        session.write("exit 42\r\n", source="user")
        for _ in range(50):
            await asyncio.sleep(0.1)
            snap = session.snapshot()
            if snap["status"] in ("exited", "failed"):
                break
        snap = session.snapshot()
        assert snap["status"] == "failed"
        assert snap["exit_code"] == 42
        session.close()
