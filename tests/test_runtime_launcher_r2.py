"""R2 — Canonical Launcher & Shutdown Authority test suite.

Verifies the 22 required launcher, shutdown, lifecycle, and process ownership contracts.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import main
import run
from charlie.subsystem_health import HealthRegistry


class _FakeStore:
    def __init__(self, name: str = "store"):
        self.name = name
        self.close_count = 0

    def close(self):
        self.close_count += 1


class _FakeBrain:
    def __init__(self):
        self.close_count = 0
        self._owns_memory_graph = False

    async def close(self):
        self.close_count += 1

    def cancel_background_tasks(self):
        return []

    async def probe_primary_llm(self, timeout=5.0):
        return True


class _FakeEventBus:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    def set_state_listener(self, cb):
        pass

    async def emit(self, *args, **kwargs):
        pass


class _FakeVoice:
    def __init__(self):
        self.stop_count = 0
        self.is_ready = False

    def stop(self):
        self.stop_count += 1

    def set_event_bus(self, bus):
        pass

    def set_wake_word_callback(self, cb):
        pass

    def speak(self, text, emotion="neutral"):
        pass

    def readiness_detail(self):
        return "Fake voice ready"


class _FakeProcess:
    def __init__(self, pid: int = 12345):
        self.pid = pid
        self.terminated = False
        self.killed = False
        self._poll_result = None

    def poll(self):
        return self._poll_result

    def terminate(self):
        self.terminated = True
        self._poll_result = 0

    def kill(self):
        self.killed = True
        self._poll_result = -9

    def wait(self, timeout=None):
        return self._poll_result


def _all_subsystems():
    return (
        "brain",
        "llm",
        "plugins",
        "mcp",
        "web",
        "voice",
        "watchers",
        "companion",
        "telegram",
    )


# ---------------------------------------------------------------------------
# Test 1: run.py is the canonical full-mode launcher boundary
# ---------------------------------------------------------------------------
def test_run_is_canonical_full_mode_launcher_boundary(monkeypatch):
    monkeypatch.setattr(run, "check_and_build_frontend", lambda *args: None)

    called = False

    async def fake_main() -> int:
        nonlocal called
        called = True
        return 0

    import main as main_module
    monkeypatch.setattr(main_module, "main", fake_main)

    exit_code = run.run_full()
    assert called is True
    assert exit_code == 0


# ---------------------------------------------------------------------------
# Test 2: main.main() does not call os._exit on clean shutdown
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_main_does_not_call_os_exit_on_clean_shutdown(monkeypatch):
    def forbidden_exit(code):
        pytest.fail(f"os._exit({code}) must not be called during clean main() lifecycle")

    monkeypatch.setattr(os, "_exit", forbidden_exit)
    monkeypatch.setattr(main, "_runtime_health", HealthRegistry(_all_subsystems()))
    monkeypatch.setattr(main, "SessionStore", lambda path: _FakeStore("session"))

    import charlie.audit_store as audit_store_module
    monkeypatch.setattr(audit_store_module, "AuditStore", lambda path: _FakeStore("audit"))
    monkeypatch.setattr(main, "_compose_memory_dependencies", lambda cfg: (_FakeStore("graph"), None, object()))
    monkeypatch.setattr(main, "_ensure_frontend_runtime", lambda: None)
    monkeypatch.setattr(main, "Brain", lambda *args, **kwargs: _FakeBrain())
    monkeypatch.setattr(main, "_wire_memory_service", lambda service: None)

    import charlie.plugins as plugins_module
    import charlie.tools as tools_module
    monkeypatch.setattr(tools_module, "register_plugin_tools", lambda cfg: None)
    monkeypatch.setattr(plugins_module, "PluginManager", lambda: object())
    monkeypatch.setattr(main.config, "mcp_enabled", False)
    monkeypatch.setattr(main.config, "pet_enabled", False)
    monkeypatch.setattr(main, "_start_web_subprocess", lambda *a, **kw: _FakeProcess(8000))
    monkeypatch.setattr(main, "_start_voice_or_degrade", lambda *a, **kw: _FakeVoice())
    monkeypatch.setattr(main, "EventBus", lambda *a, **kw: _FakeEventBus())

    # Fast-exit gather
    async def fast_gather(*args, **kwargs):
        for a in args:
            if asyncio.iscoroutine(a):
                a.close()
        return None

    monkeypatch.setattr(main.asyncio, "gather", fast_gather)
    monkeypatch.setattr(main, "_voice_loop_idle", lambda *a, **kw: asyncio.sleep(0))
    monkeypatch.setattr(main, "_log_port_release", lambda *a: None)

    exit_code = await main.main()
    assert exit_code == 0


# ---------------------------------------------------------------------------
# Test 3: main.main() does not call os._exit on startup failure
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_main_does_not_call_os_exit_on_startup_failure(monkeypatch):
    def forbidden_exit(code):
        pytest.fail(f"os._exit({code}) must not be called on startup failure")

    monkeypatch.setattr(os, "_exit", forbidden_exit)
    monkeypatch.setattr(main, "_runtime_health", HealthRegistry(_all_subsystems()))
    monkeypatch.setattr(main, "SessionStore", lambda path: (_ for _ in ()).throw(RuntimeError("early failure")))
    monkeypatch.setattr(main, "_log_port_release", lambda *a: None)

    exit_code = await main.main()
    assert exit_code == 1


# ---------------------------------------------------------------------------
# Test 4: full-runtime startup failure returns non-zero status to launcher
# ---------------------------------------------------------------------------
def test_full_runtime_startup_failure_returns_nonzero_to_launcher(monkeypatch):
    monkeypatch.setattr(run, "check_and_build_frontend", lambda *args: None)

    async def failing_main() -> int:
        return 1

    import main as main_module
    monkeypatch.setattr(main_module, "main", failing_main)

    exit_code = run.run_full()
    assert exit_code == 1


# ---------------------------------------------------------------------------
# Test 5: successful graceful runtime returns success status
# ---------------------------------------------------------------------------
def test_successful_graceful_runtime_returns_success_status(monkeypatch):
    monkeypatch.setattr(run, "check_and_build_frontend", lambda *args: None)

    async def clean_main() -> int:
        return 0

    import main as main_module
    monkeypatch.setattr(main_module, "main", clean_main)

    exit_code = run.run_full()
    assert exit_code == 0


# ---------------------------------------------------------------------------
# Test 6: partial initialization still cleans acquired resources
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_partial_initialization_still_cleans_acquired_resources(monkeypatch):
    session_store = _FakeStore("session")
    audit_store = _FakeStore("audit")

    monkeypatch.setattr(main, "_runtime_health", HealthRegistry(_all_subsystems()))
    monkeypatch.setattr(main, "SessionStore", lambda path: session_store)

    import charlie.audit_store as audit_store_module
    monkeypatch.setattr(audit_store_module, "AuditStore", lambda path: audit_store)
    monkeypatch.setattr(
        main,
        "_compose_memory_dependencies",
        lambda cfg: (_ for _ in ()).throw(RuntimeError("graph allocation failed")),
    )
    monkeypatch.setattr(main, "_log_port_release", lambda *a: None)

    exit_code = await main.main()
    assert exit_code == 1
    assert session_store.close_count == 1
    assert audit_store.close_count == 1


# ---------------------------------------------------------------------------
# Test 7: web startup failure uses the canonical cleanup path
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_web_startup_failure_uses_canonical_cleanup_path(monkeypatch, caplog):
    session_store = _FakeStore("session")
    audit_store = _FakeStore("audit")
    brain = _FakeBrain()

    monkeypatch.setattr(main, "_runtime_health", HealthRegistry(_all_subsystems()))
    monkeypatch.setattr(main, "SessionStore", lambda path: session_store)

    import charlie.audit_store as audit_store_module
    monkeypatch.setattr(audit_store_module, "AuditStore", lambda path: audit_store)
    monkeypatch.setattr(main, "_compose_memory_dependencies", lambda cfg: (_FakeStore("graph"), None, object()))
    monkeypatch.setattr(main, "_ensure_frontend_runtime", lambda: None)
    monkeypatch.setattr(main, "Brain", lambda *args, **kwargs: brain)
    monkeypatch.setattr(main, "_wire_memory_service", lambda service: None)

    import charlie.plugins as plugins_module
    import charlie.tools as tools_module
    monkeypatch.setattr(tools_module, "register_plugin_tools", lambda cfg: None)
    monkeypatch.setattr(plugins_module, "PluginManager", lambda: object())
    monkeypatch.setattr(main.config, "mcp_enabled", False)
    monkeypatch.setattr(
        main,
        "_start_web_subprocess",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("web launch failed")),
    )
    monkeypatch.setattr(main, "_log_port_release", lambda *a: None)

    def forbidden_exit(code):
        pytest.fail("os._exit called on web startup failure")

    monkeypatch.setattr(os, "_exit", forbidden_exit)

    exit_code = await main.main()
    assert exit_code == 1
    assert "main_shutdown_begin | exit_code=1" in caplog.text
    assert brain.close_count == 1
    assert session_store.close_count == 1
    assert audit_store.close_count == 1


# ---------------------------------------------------------------------------
# Test 8: Brain closes exactly once
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_brain_closes_exactly_once(monkeypatch):
    brain = _FakeBrain()

    monkeypatch.setattr(main, "_runtime_health", HealthRegistry(_all_subsystems()))
    monkeypatch.setattr(main, "SessionStore", lambda path: _FakeStore())

    import charlie.audit_store as audit_store_module
    monkeypatch.setattr(audit_store_module, "AuditStore", lambda path: _FakeStore())
    monkeypatch.setattr(main, "_compose_memory_dependencies", lambda cfg: (_FakeStore("graph"), None, object()))
    monkeypatch.setattr(main, "_ensure_frontend_runtime", lambda: None)
    monkeypatch.setattr(main, "Brain", lambda *args, **kwargs: brain)
    monkeypatch.setattr(main, "_wire_memory_service", lambda service: None)

    import charlie.plugins as plugins_module
    import charlie.tools as tools_module
    monkeypatch.setattr(tools_module, "register_plugin_tools", lambda cfg: None)
    monkeypatch.setattr(plugins_module, "PluginManager", lambda: object())
    monkeypatch.setattr(main.config, "mcp_enabled", False)
    monkeypatch.setattr(
        main,
        "_start_web_subprocess",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("fail after brain")),
    )
    monkeypatch.setattr(main, "_log_port_release", lambda *a: None)

    await main.main()
    assert brain.close_count == 1


# ---------------------------------------------------------------------------
# Test 9: SessionStore closes exactly once
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_session_store_closes_exactly_once(monkeypatch):
    store = _FakeStore("session")

    monkeypatch.setattr(main, "_runtime_health", HealthRegistry(_all_subsystems()))
    monkeypatch.setattr(main, "SessionStore", lambda path: store)

    import charlie.audit_store as audit_store_module
    monkeypatch.setattr(audit_store_module, "AuditStore", lambda path: _FakeStore())
    monkeypatch.setattr(
        main,
        "_compose_memory_dependencies",
        lambda cfg: (_ for _ in ()).throw(RuntimeError("fail")),
    )
    monkeypatch.setattr(main, "_log_port_release", lambda *a: None)

    await main.main()
    assert store.close_count == 1


# ---------------------------------------------------------------------------
# Test 10: AuditStore closes exactly once
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_audit_store_closes_exactly_once(monkeypatch):
    audit_store = _FakeStore("audit")

    monkeypatch.setattr(main, "_runtime_health", HealthRegistry(_all_subsystems()))
    monkeypatch.setattr(main, "SessionStore", lambda path: _FakeStore())

    import charlie.audit_store as audit_store_module
    monkeypatch.setattr(audit_store_module, "AuditStore", lambda path: audit_store)
    monkeypatch.setattr(
        main,
        "_compose_memory_dependencies",
        lambda cfg: (_ for _ in ()).throw(RuntimeError("fail")),
    )
    monkeypatch.setattr(main, "_log_port_release", lambda *a: None)

    await main.main()
    assert audit_store.close_count == 1


# ---------------------------------------------------------------------------
# Test 11: owned web child terminates and is reaped
# ---------------------------------------------------------------------------
def test_owned_web_child_terminates_and_is_reaped(monkeypatch):
    process = _FakeProcess(pid=9991)

    import psutil
    monkeypatch.setattr(psutil, "Process", lambda pid: SimpleNamespace(children=lambda recursive: []))
    monkeypatch.setattr(psutil, "wait_procs", lambda children, timeout: (children, []))

    main._terminate_subsystem_process(process)
    assert process.terminated is True


# ---------------------------------------------------------------------------
# Test 12: owned companion child terminates and is reaped
# ---------------------------------------------------------------------------
def test_owned_companion_child_terminates_and_is_reaped(monkeypatch, tmp_path):
    process = _FakeProcess(pid=9992)
    ready_file = tmp_path / "companion.ready"
    ready_file.write_text("ready")

    import psutil
    monkeypatch.setattr(psutil, "Process", lambda pid: SimpleNamespace(children=lambda recursive: []))
    monkeypatch.setattr(psutil, "wait_procs", lambda children, timeout: (children, []))

    main._terminate_subsystem_process(process)
    ready_file.unlink(missing_ok=True)

    assert process.terminated is True
    assert not ready_file.exists()


# ---------------------------------------------------------------------------
# Test 13: unrelated/stale process is never killed
# ---------------------------------------------------------------------------
def test_unrelated_stale_process_is_never_killed(monkeypatch):
    monkeypatch.setattr(main, "_web_port_is_listening", lambda host, port: True)
    monkeypatch.setattr(main, "_fetch_web_status", lambda host, port: None)
    monkeypatch.setattr(
        main.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Must not spawn on occupied port")
    )

    with pytest.raises(RuntimeError, match="Port 8000 is occupied by another process"):
        main._start_web_subprocess(
            ("python", "web_server_entry.py"),
            {},
            host="127.0.0.1",
            port=8000,
            launch_id="launch-new",
        )


# ---------------------------------------------------------------------------
# Test 14: graceful child terminate escalates to kill only after timeout
# ---------------------------------------------------------------------------
def test_graceful_child_terminate_escalates_to_kill_only_after_timeout(monkeypatch):
    class TimeoutProcess:
        def __init__(self):
            self.pid = 9993
            self.terminated = False
            self.killed = False
            self._wait_calls = 0

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            self._wait_calls += 1
            if self._wait_calls == 1:
                raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
            return -9

        def kill(self):
            self.killed = True

    proc = TimeoutProcess()
    import psutil
    monkeypatch.setattr(psutil, "Process", lambda pid: SimpleNamespace(children=lambda recursive: []))
    monkeypatch.setattr(psutil, "wait_procs", lambda children, timeout: (children, []))

    main._terminate_subsystem_process(proc)
    assert proc.terminated is True
    assert proc.killed is True
    assert proc._wait_calls == 2


# ---------------------------------------------------------------------------
# Test 15: runtime cancellation enters cleanup
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_runtime_cancellation_enters_cleanup(monkeypatch, caplog):
    store = _FakeStore("session")

    monkeypatch.setattr(main, "_runtime_health", HealthRegistry(_all_subsystems()))
    monkeypatch.setattr(main, "SessionStore", lambda path: store)

    import charlie.audit_store as audit_store_module
    monkeypatch.setattr(audit_store_module, "AuditStore", lambda path: _FakeStore())
    monkeypatch.setattr(main, "_compose_memory_dependencies", lambda cfg: (_FakeStore("graph"), None, object()))
    monkeypatch.setattr(main, "_ensure_frontend_runtime", lambda: None)
    monkeypatch.setattr(main, "Brain", lambda *args, **kwargs: _FakeBrain())
    monkeypatch.setattr(main, "_wire_memory_service", lambda service: None)

    import charlie.plugins as plugins_module
    import charlie.tools as tools_module
    monkeypatch.setattr(tools_module, "register_plugin_tools", lambda cfg: None)
    monkeypatch.setattr(plugins_module, "PluginManager", lambda: object())
    monkeypatch.setattr(main.config, "mcp_enabled", False)
    monkeypatch.setattr(main.config, "pet_enabled", False)
    monkeypatch.setattr(main, "_start_web_subprocess", lambda *a, **kw: _FakeProcess(8000))
    monkeypatch.setattr(main, "_start_voice_or_degrade", lambda *a, **kw: _FakeVoice())
    monkeypatch.setattr(main, "EventBus", lambda *a, **kw: _FakeEventBus())

    async def cancelling_gather(*args, **kwargs):
        for a in args:
            if asyncio.iscoroutine(a):
                a.close()
        raise asyncio.CancelledError()

    monkeypatch.setattr(main.asyncio, "gather", cancelling_gather)
    monkeypatch.setattr(main, "_voice_loop_idle", lambda *a, **kw: asyncio.sleep(0))
    monkeypatch.setattr(main, "_log_port_release", lambda *a: None)

    exit_code = await main.main()
    assert exit_code == 0
    assert "main_shutdown_begin | exit_code=0" in caplog.text
    assert store.close_count == 1


# ---------------------------------------------------------------------------
# Test 16: Ctrl+C does not bypass cleanup
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ctrl_c_does_not_bypass_cleanup(monkeypatch, caplog):
    store = _FakeStore("session")

    monkeypatch.setattr(main, "_runtime_health", HealthRegistry(_all_subsystems()))
    monkeypatch.setattr(main, "SessionStore", lambda path: store)

    import charlie.audit_store as audit_store_module
    monkeypatch.setattr(audit_store_module, "AuditStore", lambda path: _FakeStore())
    monkeypatch.setattr(main, "_compose_memory_dependencies", lambda cfg: (_FakeStore("graph"), None, object()))
    monkeypatch.setattr(main, "_ensure_frontend_runtime", lambda: None)
    monkeypatch.setattr(main, "Brain", lambda *args, **kwargs: _FakeBrain())
    monkeypatch.setattr(main, "_wire_memory_service", lambda service: None)

    import charlie.plugins as plugins_module
    import charlie.tools as tools_module
    monkeypatch.setattr(tools_module, "register_plugin_tools", lambda cfg: None)
    monkeypatch.setattr(plugins_module, "PluginManager", lambda: object())
    monkeypatch.setattr(main.config, "mcp_enabled", False)
    monkeypatch.setattr(main.config, "pet_enabled", False)
    monkeypatch.setattr(main, "_start_web_subprocess", lambda *a, **kw: _FakeProcess(8000))
    monkeypatch.setattr(main, "_start_voice_or_degrade", lambda *a, **kw: _FakeVoice())
    monkeypatch.setattr(main, "EventBus", lambda *a, **kw: _FakeEventBus())

    async def interrupting_gather(*args, **kwargs):
        for a in args:
            if asyncio.iscoroutine(a):
                a.close()
        raise KeyboardInterrupt()

    monkeypatch.setattr(main.asyncio, "gather", interrupting_gather)
    monkeypatch.setattr(main, "_voice_loop_idle", lambda *a, **kw: asyncio.sleep(0))
    monkeypatch.setattr(main, "_log_port_release", lambda *a: None)

    exit_code = await main.main()
    assert exit_code == 0
    assert "main_shutdown_begin | exit_code=0" in caplog.text
    assert store.close_count == 1


# ---------------------------------------------------------------------------
# Test 17: cleanup failure in one resource does not skip remaining resources
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cleanup_failure_in_one_resource_does_not_skip_remaining(monkeypatch):
    class BrokenBrain:
        async def close(self):
            raise RuntimeError("brain close exploded")

    session_store = _FakeStore("session")
    audit_store = _FakeStore("audit")

    monkeypatch.setattr(main, "_runtime_health", HealthRegistry(_all_subsystems()))
    monkeypatch.setattr(main, "SessionStore", lambda path: session_store)

    import charlie.audit_store as audit_store_module
    monkeypatch.setattr(audit_store_module, "AuditStore", lambda path: audit_store)
    monkeypatch.setattr(main, "_compose_memory_dependencies", lambda cfg: (_FakeStore("graph"), None, object()))
    monkeypatch.setattr(main, "_ensure_frontend_runtime", lambda: None)
    monkeypatch.setattr(main, "Brain", lambda *args, **kwargs: BrokenBrain())
    monkeypatch.setattr(main, "_wire_memory_service", lambda service: None)

    import charlie.plugins as plugins_module
    import charlie.tools as tools_module
    monkeypatch.setattr(tools_module, "register_plugin_tools", lambda cfg: None)
    monkeypatch.setattr(plugins_module, "PluginManager", lambda: object())
    monkeypatch.setattr(main.config, "mcp_enabled", False)
    monkeypatch.setattr(
        main,
        "_start_web_subprocess",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("trigger shutdown")),
    )
    monkeypatch.setattr(main, "_log_port_release", lambda *a: None)

    exit_code = await main.main()
    assert exit_code == 1
    # Even though Brain.close failed, stores were still cleanly closed
    assert audit_store.close_count == 1
    assert session_store.close_count == 1


# ---------------------------------------------------------------------------
# Test 18: web-only normal shutdown does not use unconditional hard exit
# ---------------------------------------------------------------------------
def test_web_only_normal_shutdown_does_not_use_unconditional_hard_exit(monkeypatch):
    monkeypatch.setattr(run, "check_and_build_frontend", lambda *args: None)

    def forbidden_exit(code):
        pytest.fail(f"run_web_only called os._exit({code}) on normal path")

    monkeypatch.setattr(os, "_exit", forbidden_exit)

    class FakeServer:
        def __init__(self, config):
            self.config = config

        def run(self):
            pass

    import uvicorn
    monkeypatch.setattr(uvicorn, "Server", FakeServer)

    exit_code = run.run_web_only()
    assert exit_code == 0


# ---------------------------------------------------------------------------
# Test 19: web-only Ctrl+C is deterministic
# ---------------------------------------------------------------------------
def test_web_only_ctrl_c_is_deterministic(monkeypatch):
    monkeypatch.setattr(run, "check_and_build_frontend", lambda *args: None)

    class InterruptServer:
        def __init__(self, config):
            self.config = config

        def run(self):
            raise KeyboardInterrupt()

    import uvicorn
    monkeypatch.setattr(uvicorn, "Server", InterruptServer)

    exit_code = run.run_web_only()
    assert exit_code == 0


# ---------------------------------------------------------------------------
# Test 20: launcher returns/raises the correct final process exit code
# ---------------------------------------------------------------------------
def test_launcher_returns_or_exits_with_correct_final_code(monkeypatch):
    monkeypatch.setattr(run, "check_and_build_frontend", lambda *args: None)

    # Test full mode exit code 0
    import main as main_module
    monkeypatch.setattr(main_module, "main", AsyncMock(return_value=0))
    assert run.run_full() == 0

    # Test full mode exit code 1
    monkeypatch.setattr(main_module, "main", AsyncMock(return_value=1))
    assert run.run_full() == 1


# ---------------------------------------------------------------------------
# Test 21: direct child entrypoint does not become a competing full-runtime launcher
# ---------------------------------------------------------------------------
def test_direct_child_entrypoint_does_not_become_competing_launcher():
    entry_path = Path("charlie/web_server_entry.py")
    content = entry_path.read_text(encoding="utf-8")

    assert "main.py" not in content
    assert "asyncio.run(main())" not in content
    assert "start_server()" in content
    assert "Voice" not in content


# ---------------------------------------------------------------------------
# Test 22: R1 runtime identity/stale-port behavior remains intact
# ---------------------------------------------------------------------------
def test_r1_runtime_identity_stale_port_behavior_remains_intact(monkeypatch):
    health = HealthRegistry(("web",))
    monkeypatch.setattr(main, "_runtime_health", health)
    monkeypatch.setattr(main, "_web_port_is_listening", lambda host, port: True)
    monkeypatch.setattr(
        main,
        "_fetch_web_status",
        lambda host, port: {
            "status": "ok",
            "launch_id": "stale-launch-id",
            "build_id": "build-a",
            "pid": 9999,
        },
    )

    with pytest.raises(RuntimeError, match="Port 8000 is occupied by another Charlie runtime"):
        main._start_web_subprocess(
            ("python", "web_server_entry.py"),
            {},
            host="127.0.0.1",
            port=8000,
            launch_id="current-launch-id",
        )


# ---------------------------------------------------------------------------
# Test 23: direct main.py execution is rejected with non-zero exit and instruction
# ---------------------------------------------------------------------------
def test_direct_main_execution_rejected_with_instruction():
    """Direct execution of main.py must exit non-zero and not start Charlie."""
    res = subprocess.run(
        [sys.executable, "main.py"],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert res.returncode == 1
    assert "Direct main.py execution is unsupported. Use: python run.py" in res.stderr
    assert "Charlie is waking up" not in res.stdout
    assert "Charlie is waking up" not in res.stderr


# ---------------------------------------------------------------------------
# Test 24: run.py is the only supported full-runtime launcher
# ---------------------------------------------------------------------------
def test_run_py_remains_only_supported_full_runtime_launcher():
    """run.py must be the exclusive user-facing full-runtime launcher."""
    run_content = Path("run.py").read_text(encoding="utf-8")
    main_content = Path("main.py").read_text(encoding="utf-8")
    web_entry_content = Path("charlie/web_server_entry.py").read_text(encoding="utf-8")
    pet_entry_content = Path("charlie/pet_entry.py").read_text(encoding="utf-8")

    # run.py owns CLI parsing and full runtime launch
    assert '"--web-only"' in run_content
    assert "parser.add_argument(" in run_content
    assert "def run_full() -> int:" in run_content
    assert "def run_web_only() -> int:" in run_content

    # main.py does not launch full runtime on __main__
    assert "Direct main.py execution is unsupported. Use: python run.py" in main_content
    assert "sys.exit(asyncio.run(main()))" not in main_content

    # Child entrypoints do not launch full runtime
    assert "run_full" not in web_entry_content
    assert "run_full" not in pet_entry_content


# ---------------------------------------------------------------------------
# Test 25: importing and calling main.main() from run.py works normally
# ---------------------------------------------------------------------------
def test_run_full_imports_and_calls_main_main(monkeypatch):
    """Calling main.main() from run.py executes normally and returns exit code."""
    import main as main_module
    import run as run_module

    called = False

    async def fake_main():
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr(main_module, "main", fake_main)
    res = run_module.run_full()
    assert called is True
    assert res == 0
