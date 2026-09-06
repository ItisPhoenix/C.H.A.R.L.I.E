"""R2 — Canonical Launcher & Shutdown Authority test suite.

Verifies the 22 required launcher, shutdown, lifecycle, and process ownership contracts.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
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

    async def next_command(self):
        await asyncio.sleep(3600)
        return {}


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

    orig_gather = main.asyncio.gather

    # Fast-exit gather
    async def fast_gather(*args, **kwargs):
        if kwargs.get("return_exceptions"):
            return await orig_gather(*args, **kwargs)
        for a in args:
            if asyncio.iscoroutine(a):
                a.close()
            elif isinstance(a, asyncio.Task) and not a.done():
                a.cancel()
        return []

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


# ---------------------------------------------------------------------------
# Test 26: Housekeeping cancellation is awaited before EventBus close
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_housekeeping_cancellation_awaited_before_event_bus_close(monkeypatch):
    order: list[str] = []

    class TrackedEventBus(_FakeEventBus):
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            order.append("event_bus_close")

    monkeypatch.setattr(main, "_runtime_health", HealthRegistry(_all_subsystems()))
    monkeypatch.setattr(main, "SessionStore", lambda path: _FakeStore("session"))
    import charlie.audit_store as audit_store_module
    monkeypatch.setattr(audit_store_module, "AuditStore", lambda path: _FakeStore("audit"))
    monkeypatch.setattr(main, "_compose_memory_dependencies", lambda cfg: (_FakeStore("graph"), None, object()))
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
    monkeypatch.setattr(main, "EventBus", lambda *a, **kw: TrackedEventBus())
    monkeypatch.setattr(main, "_log_port_release", lambda *a: None)

    async def fake_housekeeping():
        try:
            await asyncio.sleep(100)
        finally:
            order.append("housekeeping_finalizer")

    hk_task = None
    orig_gather = main.asyncio.gather

    async def fast_gather(*args, **kwargs):
        nonlocal hk_task
        if kwargs.get("return_exceptions"):
            return await orig_gather(*args, **kwargs)
        hk_task = asyncio.create_task(fake_housekeeping(), name="test_hk_task")
        main.background_housekeeping_tasks.add(hk_task)
        await asyncio.sleep(0.01)
        for a in args:
            if asyncio.iscoroutine(a):
                a.close()
            elif isinstance(a, asyncio.Task) and not a.done():
                a.cancel()
        return []

    monkeypatch.setattr(main.asyncio, "gather", fast_gather)
    exit_code = await main.main()
    assert exit_code == 0
    assert "housekeeping_finalizer" in order
    assert "event_bus_close" in order
    assert order.index("housekeeping_finalizer") < order.index("event_bus_close")


# ---------------------------------------------------------------------------
# Test 27: Active foreground task is awaited before EventBus close and before Brain/store close
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_active_foreground_task_awaited_before_eventbus_and_stores_close(monkeypatch):
    order: list[str] = []

    class TrackedEventBus(_FakeEventBus):
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            order.append("event_bus_close")

    class TrackedStore(_FakeStore):
        def close(self):
            order.append(f"{self.name}_close")
            super().close()

    class TrackedBrain(_FakeBrain):
        async def close(self):
            order.append("brain_close")
            await super().close()

    monkeypatch.setattr(main, "_runtime_health", HealthRegistry(_all_subsystems()))
    monkeypatch.setattr(main, "SessionStore", lambda path: TrackedStore("session"))
    import charlie.audit_store as audit_store_module
    monkeypatch.setattr(audit_store_module, "AuditStore", lambda path: TrackedStore("audit"))
    monkeypatch.setattr(main, "_compose_memory_dependencies", lambda cfg: (TrackedStore("graph"), None, object()))
    monkeypatch.setattr(main, "Brain", lambda *args, **kwargs: TrackedBrain())
    monkeypatch.setattr(main, "_wire_memory_service", lambda service: None)
    import charlie.plugins as plugins_module
    import charlie.tools as tools_module
    monkeypatch.setattr(tools_module, "register_plugin_tools", lambda cfg: None)
    monkeypatch.setattr(plugins_module, "PluginManager", lambda: object())
    monkeypatch.setattr(main.config, "mcp_enabled", False)
    monkeypatch.setattr(main.config, "pet_enabled", False)
    monkeypatch.setattr(main, "_start_web_subprocess", lambda *a, **kw: _FakeProcess(8000))
    monkeypatch.setattr(main, "_start_voice_or_degrade", lambda *a, **kw: _FakeVoice())
    monkeypatch.setattr(main, "EventBus", lambda *a, **kw: TrackedEventBus())
    monkeypatch.setattr(main, "_log_port_release", lambda *a: None)

    async def foreground_turn():
        try:
            await asyncio.sleep(100)
        finally:
            order.append("foreground_finalizer")

    fg_task = None
    orig_gather = main.asyncio.gather

    async def fast_gather(*args, **kwargs):
        nonlocal fg_task
        if kwargs.get("return_exceptions"):
            return await orig_gather(*args, **kwargs)
        fg_task = asyncio.create_task(foreground_turn(), name="foreground_turn_task")
        main.active_process_task = fg_task
        await asyncio.sleep(0.01)
        for a in args:
            if asyncio.iscoroutine(a):
                a.close()
            elif isinstance(a, asyncio.Task) and not a.done():
                a.cancel()
        return []

    monkeypatch.setattr(main.asyncio, "gather", fast_gather)
    exit_code = await main.main()
    assert exit_code == 0
    assert "foreground_finalizer" in order
    assert "event_bus_close" in order
    assert "brain_close" in order
    assert "session_close" in order
    assert "audit_close" in order

    fg_idx = order.index("foreground_finalizer")
    assert fg_idx < order.index("event_bus_close")
    assert fg_idx < order.index("brain_close")
    assert fg_idx < order.index("session_close")
    assert fg_idx < order.index("audit_close")


# ---------------------------------------------------------------------------
# Test 28: Steady-state task failure cancels and drains pending siblings before EventBus close
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_steady_state_task_failure_cancels_siblings_before_event_bus_exit(monkeypatch):
    order: list[str] = []

    class TrackedEventBus(_FakeEventBus):
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            order.append("event_bus_close")

    monkeypatch.setattr(main, "_runtime_health", HealthRegistry(_all_subsystems()))
    monkeypatch.setattr(main, "SessionStore", lambda path: _FakeStore("session"))
    import charlie.audit_store as audit_store_module
    monkeypatch.setattr(audit_store_module, "AuditStore", lambda path: _FakeStore("audit"))
    monkeypatch.setattr(main, "_compose_memory_dependencies", lambda cfg: (_FakeStore("graph"), None, object()))
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
    monkeypatch.setattr(main, "EventBus", lambda *a, **kw: TrackedEventBus())
    monkeypatch.setattr(main, "_log_port_release", lambda *a: None)

    async def failing_deliver(store, now, callback):
        await asyncio.sleep(0.01)
        raise RuntimeError("calendar loop crashed")

    async def sibling_idle(voice):
        try:
            await asyncio.sleep(100)
        finally:
            order.append("sibling_finalizer")

    import charlie.calendar_scheduler as calendar_scheduler
    monkeypatch.setattr(calendar_scheduler, "deliver_due_reminders", failing_deliver)
    monkeypatch.setattr(main, "_voice_loop_idle", sibling_idle)

    exit_code = await main.main()
    assert exit_code == 1
    assert "sibling_finalizer" in order
    assert "event_bus_close" in order
    assert order.index("sibling_finalizer") < order.index("event_bus_close")


# ---------------------------------------------------------------------------
# Test 29: Companion monitor task is drained before EventBus close
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_companion_monitor_drained_before_event_bus_close(monkeypatch, tmp_path):
    order: list[str] = []

    class TrackedEventBus(_FakeEventBus):
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            order.append("event_bus_close")

    ready_file = tmp_path / "companion.ready"
    ready_file.write_text("ready")

    monkeypatch.setattr(main, "_runtime_health", HealthRegistry(_all_subsystems()))
    monkeypatch.setattr(main, "SessionStore", lambda path: _FakeStore("session"))
    import charlie.audit_store as audit_store_module
    monkeypatch.setattr(audit_store_module, "AuditStore", lambda path: _FakeStore("audit"))
    monkeypatch.setattr(main, "_compose_memory_dependencies", lambda cfg: (_FakeStore("graph"), None, object()))
    monkeypatch.setattr(main, "Brain", lambda *args, **kwargs: _FakeBrain())
    monkeypatch.setattr(main, "_wire_memory_service", lambda service: None)
    import charlie.plugins as plugins_module
    import charlie.tools as tools_module
    monkeypatch.setattr(tools_module, "register_plugin_tools", lambda cfg: None)
    monkeypatch.setattr(plugins_module, "PluginManager", lambda: object())
    monkeypatch.setattr(main.config, "mcp_enabled", False)
    monkeypatch.setattr(main.config, "pet_enabled", True)
    pet_proc = _FakeProcess(8001)
    monkeypatch.setattr(main, "_companion_dependency_status", lambda: (True, None))
    monkeypatch.setattr(main, "_start_web_subprocess", lambda *a, **kw: _FakeProcess(8000))
    monkeypatch.setattr(main, "_start_subsystem_process", lambda *a, **kw: pet_proc)
    monkeypatch.setattr(main, "_start_voice_or_degrade", lambda *a, **kw: _FakeVoice())
    monkeypatch.setattr(main, "EventBus", lambda *a, **kw: TrackedEventBus())
    monkeypatch.setattr(main, "_log_port_release", lambda *a: None)

    async def mock_monitor(proc, file, bus):
        try:
            await asyncio.sleep(100)
        finally:
            order.append("companion_monitor_finalizer")

    monkeypatch.setattr(main, "_monitor_companion_readiness", mock_monitor)

    orig_gather = main.asyncio.gather
    async def fast_gather(*args, **kwargs):
        if kwargs.get("return_exceptions"):
            return await orig_gather(*args, **kwargs)
        await asyncio.sleep(0.01)
        for a in args:
            if asyncio.iscoroutine(a):
                a.close()
            elif isinstance(a, asyncio.Task) and not a.done():
                a.cancel()
        return []

    monkeypatch.setattr(main.asyncio, "gather", fast_gather)
    exit_code = await main.main()
    assert exit_code == 0
    assert "companion_monitor_finalizer" in order
    assert "event_bus_close" in order
    assert order.index("companion_monitor_finalizer") < order.index("event_bus_close")


# ---------------------------------------------------------------------------
# Test 30: No main-owned EventBus-dependent steady-state task remains pending
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_no_eventbus_dependent_steady_state_task_pending_after_main_returns(monkeypatch):
    created_tasks: list[asyncio.Task] = []
    orig_create_task = asyncio.create_task

    def tracked_create_task(*args, **kwargs):
        task = orig_create_task(*args, **kwargs)
        created_tasks.append(task)
        return task

    monkeypatch.setattr(asyncio, "create_task", tracked_create_task)
    monkeypatch.setattr(main.asyncio, "create_task", tracked_create_task)

    monkeypatch.setattr(main, "_runtime_health", HealthRegistry(_all_subsystems()))
    monkeypatch.setattr(main, "SessionStore", lambda path: _FakeStore("session"))
    import charlie.audit_store as audit_store_module
    monkeypatch.setattr(audit_store_module, "AuditStore", lambda path: _FakeStore("audit"))
    monkeypatch.setattr(main, "_compose_memory_dependencies", lambda cfg: (_FakeStore("graph"), None, object()))
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
    monkeypatch.setattr(main, "_log_port_release", lambda *a: None)

    orig_gather = main.asyncio.gather
    async def fast_gather(*args, **kwargs):
        if kwargs.get("return_exceptions"):
            return await orig_gather(*args, **kwargs)
        for a in args:
            if asyncio.iscoroutine(a):
                a.close()
            elif isinstance(a, asyncio.Task) and not a.done():
                a.cancel()
        return []

    monkeypatch.setattr(main.asyncio, "gather", fast_gather)
    exit_code = await main.main()
    assert exit_code == 0
    pending_tasks = [t for t in created_tasks if not t.done()]
    assert pending_tasks == [], f"Tasks remained pending: {[t.get_name() for t in pending_tasks]}"
    registry = main._main_event_bus_registry
    assert registry is not None
    tracked_tasks, tracked_futures = registry.snapshot()
    assert tracked_tasks == ()
    assert tracked_futures == ()


# ---------------------------------------------------------------------------
# Test 31: Pending local EventBus submission drains before EventBus exit
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pending_local_eventbus_submission_drains_before_eventbus_exit():
    order: list[str] = []
    started = asyncio.Event()
    registry = main._EventBusSubmissionRegistry()

    class Bus:
        async def emit(self, *_args, **_kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                order.append("local_finalizer")

        async def __aexit__(self, *_args):
            order.append("event_bus_close")

    loop = asyncio.get_running_loop()
    task = registry.submit_task(Bus().emit("pending"), loop)
    await started.wait()
    registry.close()
    await main._drain_event_bus_submissions(registry, loop=loop)
    await Bus().__aexit__(None, None, None)

    assert task is not None and task.done()
    assert order == ["local_finalizer", "event_bus_close"]


# ---------------------------------------------------------------------------
# Test 32: Pending thread-safe EventBus future resolves before EventBus exit
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pending_threadsafe_eventbus_future_resolves_before_eventbus_exit():
    order: list[str] = []
    started = asyncio.Event()
    registry = main._EventBusSubmissionRegistry()

    class Bus:
        async def emit(self, *_args, **_kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                order.append("future_finalizer")

        async def __aexit__(self, *_args):
            order.append("event_bus_close")

    loop = asyncio.get_running_loop()
    future = registry.submit_threadsafe(Bus().emit("pending"), loop)
    await started.wait()
    registry.close()
    await main._drain_event_bus_submissions(registry, loop=loop)
    await Bus().__aexit__(None, None, None)

    assert future is not None and future.done()
    assert order == ["future_finalizer", "event_bus_close"]


# ---------------------------------------------------------------------------
# Test 33: Late Voice-style callback is rejected by the closed submission gate
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_late_voice_callback_creates_no_eventbus_submission(monkeypatch):
    registry = main._EventBusSubmissionRegistry()
    monkeypatch.setattr(main, "_main_event_bus_registry", registry)
    loop = asyncio.get_running_loop()
    calls: list[str] = []

    class Bus:
        async def emit(self, event, *_args, **_kwargs):
            calls.append(event)
            raise AssertionError("closed EventBus must not receive late Voice callback")

    registry.close()
    future = main._submit_event_threadsafe(Bus().emit("speaking_start"), loop)

    assert future is None
    assert calls == []
    await main._drain_event_bus_submissions(registry, loop=loop)


# ---------------------------------------------------------------------------
# Test 34: Producer race drains admitted work and rejects late work
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_eventbus_producer_race_closes_gate_before_eventbus_exit():
    order: list[str] = []
    started = asyncio.Event()
    registry = main._EventBusSubmissionRegistry()

    class Bus:
        async def emit(self, event, *_args, **_kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                order.append(f"{event}_finalizer")

        async def __aexit__(self, *_args):
            order.append("event_bus_close")

    loop = asyncio.get_running_loop()
    admitted = registry.submit_task(Bus().emit("admitted"), loop)
    await started.wait()
    registry.close()
    late = registry.submit_threadsafe(Bus().emit("late"), loop)
    await main._drain_event_bus_submissions(registry, loop=loop)
    after_drain = registry.submit_task(Bus().emit("after_drain"), loop)
    await Bus().__aexit__(None, None, None)

    assert admitted is not None and admitted.done()
    assert late is None
    assert after_drain is None
    assert order == ["admitted_finalizer", "event_bus_close"]
    assert registry.is_empty()


# ---------------------------------------------------------------------------
# Test 35: Timeout path cancels cooperative local and thread-safe submissions
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_eventbus_timeout_cancels_submissions_to_zero_live_entries():
    local_started = asyncio.Event()
    future_started = asyncio.Event()
    registry = main._EventBusSubmissionRegistry()

    class Bus:
        async def emit(self, kind, *_args, **_kwargs):
            (local_started if kind == "local" else future_started).set()
            await asyncio.Event().wait()

    loop = asyncio.get_running_loop()
    local_task = registry.submit_task(Bus().emit("local"), loop)
    future = registry.submit_threadsafe(Bus().emit("future"), loop)
    await asyncio.gather(local_started.wait(), future_started.wait())
    registry.close()
    await main._drain_event_bus_submissions(registry, loop=loop, timeout=0)

    assert local_task is not None and local_task.done()
    assert future is not None and future.done()
    tracked_tasks, tracked_futures = registry.snapshot()
    assert tracked_tasks == ()
    assert tracked_futures == ()


# ---------------------------------------------------------------------------
# T1.1 R2 investigation: executor work outlives EventBus registry drain
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_terminal_executor_and_subprocess_outlive_eventbus_drain(monkeypatch):
    from collections import OrderedDict

    import charlie.core as core
    from charlie.config import Config
    from charlie.execution_context import get_current_execution_context, terminate_process_tree
    from charlie.resource_locks import default_lease_manager

    default_lease_manager.manual_takeover(["terminal"])
    brain = core.Brain(
        Config(llm_url="http://localhost:11434", llm_key="no-key", llm_model="dummy"),
        register_panic_hotkey=False,
    )
    loop = asyncio.get_running_loop()
    callable_started = asyncio.Event()
    callable_finished = asyncio.Event()
    process_box = {}

    async def approve(*_args, **_kwargs):
        return True

    def controlled_shell(*_args):
        process = subprocess.Popen([shutil.which("python") or sys.executable, "-c", "import time; time.sleep(5)"])
        process_box["process"] = process
        loop.call_soon_threadsafe(callable_started.set)
        context = get_current_execution_context()
        owned_process = context.register_process(process)
        while process.poll() is None:
            if context is not None and context.cancellation_requested:
                terminate_process_tree(owned_process)
                break
            time.sleep(0.01)
        process.wait()
        loop.call_soon_threadsafe(callable_finished.set)
        return "controlled shell complete"

    class Bus:
        async def emit(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(brain, "request_tool_approval", approve)
    monkeypatch.setattr(core.tool_registry, "execute_tool", controlled_shell)
    registry = main._EventBusSubmissionRegistry()
    task = registry.submit_task(
        main._handle_terminal_command_request(
            brain,
            Bus(),
            request_id="terminal-r2-shutdown-probe",
            terminal_session_id="primary",
            command="controlled shell",
            result_cache=OrderedDict(),
            in_flight={},
        ),
        loop,
    )

    process = None
    try:
        await asyncio.wait_for(callable_started.wait(), timeout=2.0)
        process = process_box["process"]
        registry.close()
        await main._drain_event_bus_submissions(registry, loop=loop, timeout=1.0)

        callable_finished_at_drain = callable_finished.is_set()
        process_alive_at_drain = process.poll() is None
        assert task.done()
        assert registry.is_empty()
        assert callable_finished_at_drain
        assert not process_alive_at_drain
        assert process.wait(timeout=1) is not None
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            await asyncio.to_thread(process.wait)
        await brain.close()


@pytest.mark.asyncio
async def test_generic_executor_refusal_fails_closed_before_worker_quiescence(monkeypatch):
    from collections import OrderedDict
    from threading import Event

    import charlie.core as core
    from charlie.config import Config
    from charlie.resource_locks import default_lease_manager

    default_lease_manager.manual_takeover(["terminal"])
    brain = core.Brain(
        Config(llm_url="http://localhost:11434", llm_key="no-key", llm_model="dummy"),
        register_panic_hotkey=False,
    )
    started = Event()
    finished = Event()
    release = Event()

    async def approve(*_args, **_kwargs):
        return True

    def non_cooperative_worker(*_args):
        started.set()
        release.wait(2.0)
        finished.set()
        return "released"

    class Bus:
        async def emit(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(brain, "request_tool_approval", approve)
    monkeypatch.setattr(core.tool_registry, "execute_tool", non_cooperative_worker)
    registry = main._EventBusSubmissionRegistry()
    loop = asyncio.get_running_loop()
    task = registry.submit_task(
        main._handle_terminal_command_request(
            brain,
            Bus(),
            request_id="terminal-r2-generic-refusal",
            terminal_session_id="primary",
            command="non-cooperative",
            result_cache=OrderedDict(),
            in_flight={},
        ),
        loop,
    )

    try:
        assert await asyncio.to_thread(started.wait, 2.0)
        registry.close()
        with pytest.raises(RuntimeError, match="quiescence"):
            await main._drain_event_bus_submissions(registry, loop=loop, timeout=0)
        assert not finished.is_set()
        assert not task.done()

        release.set()
        assert await asyncio.to_thread(finished.wait, 2.0)
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except asyncio.CancelledError:
            pass
        await main._drain_event_bus_submissions(registry, loop=loop, timeout=0)
    finally:
        release.set()
        if not task.done():
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except asyncio.CancelledError:
                pass
        await brain.close()


@pytest.mark.asyncio
async def test_cancel_and_drain_is_bounded_for_non_cooperative_task():
    release = asyncio.Event()

    async def stubborn():
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    task = asyncio.create_task(stubborn())
    await asyncio.sleep(0)

    with pytest.raises(RuntimeError, match="quiescence"):
        await main._cancel_and_drain([task], label="bounded_test", timeout=0.05)
    assert not task.done()

    release.set()
    task.cancel()
    await task


def test_windows_identity_mismatch_fails_closed_without_destructive_calls(monkeypatch):
    import charlie.execution_context as execution_context

    class FakePsutilProcess:
        pid = 101

        def is_running(self):
            return True

        def create_time(self):
            return 2.0

        def children(self, recursive=False):
            return []

        def terminate(self):
            raise AssertionError("identity mismatch must not terminate")

        def kill(self):
            raise AssertionError("identity mismatch must not kill")

    fake_psutil = SimpleNamespace(
        Process=lambda _pid: FakePsutilProcess(),
        NoSuchProcess=type("NoSuchProcess", (Exception,), {}),
        ZombieProcess=type("ZombieProcess", (Exception,), {}),
        AccessDenied=type("AccessDenied", (Exception,), {}),
        Error=Exception,
        wait_procs=lambda *_args, **_kwargs: ([], []),
    )
    monkeypatch.setattr(execution_context, "psutil", fake_psutil)
    owned = execution_context.OwnedProcess(
        popen=SimpleNamespace(pid=101),
        identity=FakePsutilProcess(),
        pid=101,
        creation_time=1.0,
        process_group_id=None,
    )

    assert execution_context.terminate_process_tree(owned) is False


def test_already_exited_owned_process_is_quiescent_without_fallback(monkeypatch):
    import charlie.execution_context as execution_context

    class GoneIdentity:
        pid = 102

        def is_running(self):
            return False

        def create_time(self):
            return 1.0

    monkeypatch.setattr(
        execution_context,
        "psutil",
        SimpleNamespace(
            NoSuchProcess=type("NoSuchProcess", (Exception,), {}),
            ZombieProcess=type("ZombieProcess", (Exception,), {}),
            AccessDenied=type("AccessDenied", (Exception,), {}),
            Error=Exception,
        ),
    )
    owned = execution_context.OwnedProcess(
        popen=SimpleNamespace(pid=102),
        identity=GoneIdentity(),
        pid=102,
        creation_time=1.0,
        process_group_id=None,
    )

    assert execution_context.terminate_process_tree(owned) is True


def test_identity_bound_descendants_are_terminated_and_waited(monkeypatch):
    import charlie.execution_context as execution_context

    calls = []

    class Identity:
        def __init__(self, pid, created=1.0):
            self.pid = pid
            self.created = created
            self.alive = True
            self.descendants = []

        def is_running(self):
            return self.alive

        def create_time(self):
            return self.created

        def children(self, recursive=False):
            return list(self.descendants)

        def terminate(self):
            calls.append(("terminate", self.pid))
            self.alive = False

        def kill(self):
            calls.append(("kill", self.pid))
            self.alive = False

    root = Identity(103)
    child = Identity(104)
    root.descendants = [child]
    fake_psutil = SimpleNamespace(
        NoSuchProcess=type("NoSuchProcess", (Exception,), {}),
        ZombieProcess=type("ZombieProcess", (Exception,), {}),
        AccessDenied=type("AccessDenied", (Exception,), {}),
        Error=Exception,
        wait_procs=lambda processes, timeout: (list(processes), []),
    )
    monkeypatch.setattr(execution_context, "psutil", fake_psutil)
    owned = execution_context.OwnedProcess(
        popen=SimpleNamespace(pid=103),
        identity=root,
        pid=103,
        creation_time=1.0,
        process_group_id=None,
    )

    assert execution_context.terminate_process_tree(owned) is True
    assert calls == [("terminate", 104), ("terminate", 103)]


def test_posix_identity_mismatch_does_not_signal_process_group(monkeypatch):
    import charlie.execution_context as execution_context

    class Identity:
        pid = 105

        def is_running(self):
            return True

        def create_time(self):
            return 9.0

    monkeypatch.setattr(execution_context.sys, "platform", "linux")
    monkeypatch.setattr(
        execution_context,
        "psutil",
        SimpleNamespace(
            NoSuchProcess=type("NoSuchProcess", (Exception,), {}),
            ZombieProcess=type("ZombieProcess", (Exception,), {}),
            AccessDenied=type("AccessDenied", (Exception,), {}),
            Error=Exception,
        ),
    )
    owned = execution_context.OwnedProcess(
        popen=SimpleNamespace(pid=105),
        identity=Identity(),
        pid=105,
        creation_time=1.0,
        process_group_id=105,
    )

    assert execution_context.terminate_process_tree(owned) is False


@pytest.mark.asyncio
async def test_real_shell_cancellation_quiesces_owned_process_tree():
    from collections import OrderedDict

    import psutil

    import charlie.core as core
    from charlie.config import Config
    from charlie.resource_locks import default_lease_manager

    default_lease_manager.manual_takeover(["terminal"])
    brain = core.Brain(
        Config(llm_url="http://localhost:11434", llm_key="no-key", llm_model="dummy"),
        register_panic_hotkey=False,
    )
    pid = None

    async def approve(*_args, **_kwargs):
        return True

    class Bus:
        async def emit(self, *_args, **_kwargs):
            return None

    def wait_pid(path: Path) -> int:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                return int(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, ValueError):
                time.sleep(0.01)
        raise AssertionError("shell child PID was not published")

    with tempfile.TemporaryDirectory(prefix="charlie-r2-tree-") as temp_dir:
        temp_root = Path(temp_dir)
        pid_file = temp_root / "child.pid"
        script = temp_root / "parent.py"
        script.write_text(
            "import pathlib, subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid), encoding='utf-8')\n"
            "time.sleep(30)\n",
            encoding="utf-8",
        )
        command = (
            subprocess.list2cmdline([shutil.which("python") or sys.executable, str(script)])
            if sys.platform == "win32"
            else " ".join(shlex.quote(value) for value in (shutil.which("python") or sys.executable, str(script)))
        )
        registry = main._EventBusSubmissionRegistry()
        loop = asyncio.get_running_loop()
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(brain, "request_tool_approval", approve)
        task = registry.submit_task(
            main._handle_terminal_command_request(
                brain,
                Bus(),
                request_id="terminal-r2-real-tree",
                terminal_session_id="primary",
                command=command,
                result_cache=OrderedDict(),
                in_flight={},
            ),
            loop,
        )
        try:
            pid = await asyncio.to_thread(wait_pid, pid_file)
            assert psutil.pid_exists(pid)
            registry.close()
            await main._drain_event_bus_submissions(registry, loop=loop, timeout=1.0)
            assert task.done()
            assert not psutil.pid_exists(pid)
        finally:
            monkeypatch.undo()
            if pid is not None and psutil.pid_exists(pid):
                process = psutil.Process(pid)
                for child in process.children(recursive=True):
                    child.kill()
                process.kill()
        await brain.close()


@pytest.mark.asyncio
async def test_shell_timeout_quiesces_process_before_failed_envelope(monkeypatch):
    import psutil

    import charlie.core as core
    from charlie.config import Config
    from charlie.resource_locks import default_lease_manager

    default_lease_manager.manual_takeover(["terminal"])
    brain = core.Brain(
        Config(llm_url="http://localhost:11434", llm_key="no-key", llm_model="dummy"),
        register_panic_hotkey=False,
    )
    pid = None

    async def approve(*_args, **_kwargs):
        return True

    def wait_pid(path: Path) -> int:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                return int(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, ValueError):
                time.sleep(0.01)
        raise AssertionError("shell PID was not published")

    with tempfile.TemporaryDirectory(prefix="charlie-r2-timeout-") as temp_dir:
        temp_root = Path(temp_dir)
        pid_file = temp_root / "process.pid"
        script = temp_root / "sleep.py"
        script.write_text(
            "import pathlib, os, time\n"
            f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()), encoding='utf-8')\n"
            "time.sleep(30)\n",
            encoding="utf-8",
        )
        command = (
            subprocess.list2cmdline([shutil.which("python") or sys.executable, str(script)])
            if sys.platform == "win32"
            else " ".join(shlex.quote(value) for value in (shutil.which("python") or sys.executable, str(script)))
        )
        monkeypatch.setattr(core, "_tool_timeout", lambda *_args: 0.1)
        monkeypatch.setattr(brain, "request_tool_approval", approve)

        recovery_calls = []

        async def no_recovery(*_args, **_kwargs):
            recovery_calls.append((_args, _kwargs))
            return None

        monkeypatch.setattr("charlie.recovery.recover_tool", no_recovery)
        try:
            operation = asyncio.create_task(
                brain.execute_tool_operation(
                    "shell_execute",
                    {"command": command},
                    request=command,
                    task_id="terminal-r2-timeout",
                    session_id=None,
                )
            )
            pid = await asyncio.to_thread(wait_pid, pid_file)
            assert psutil.pid_exists(pid)
            result = await operation
            assert result.status == "failed"
            assert result.data["failure_kind"] == "timeout"
            assert not psutil.pid_exists(pid)
            assert recovery_calls == []
        finally:
            if pid is not None and psutil.pid_exists(pid):
                process = psutil.Process(pid)
                for child in process.children(recursive=True):
                    child.kill()
                process.kill()
            await brain.close()


@pytest.mark.asyncio
async def test_canonical_shell_execute_normal_success_preserves_output():
    import charlie.core as core
    from charlie.config import Config
    from charlie.resource_locks import default_lease_manager

    default_lease_manager.manual_takeover(["terminal"])
    brain = core.Brain(
        Config(llm_url="http://localhost:11434", llm_key="no-key", llm_model="dummy"),
        register_panic_hotkey=False,
    )

    async def approve(*_args, **_kwargs):
        return True

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(brain, "request_tool_approval", approve)
    try:
        result = await brain.execute_tool_operation(
            "shell_execute",
            {"command": "echo r2-normal"},
            request="echo r2-normal",
            task_id="terminal-r2-normal",
            session_id=None,
        )
    finally:
        monkeypatch.undo()
        await brain.close()

    assert result.status == "completed"
    assert "r2-normal" in result.result


# ---------------------------------------------------------------------------
# Test 31: Full-mode frontend build failure returns canonical code 1
# ---------------------------------------------------------------------------
def test_full_mode_frontend_failure_returns_canonical_code_1(monkeypatch):
    def failing_build(*args):
        raise RuntimeError("Vite compilation exploded")

    monkeypatch.setattr(run, "check_and_build_frontend", failing_build)
    exit_code = run.cli_main([])
    assert exit_code == 1


# ---------------------------------------------------------------------------
# Test 32: Web-only frontend build failure returns canonical code 1
# ---------------------------------------------------------------------------
def test_web_only_frontend_failure_returns_canonical_code_1(monkeypatch):
    def failing_build(*args):
        raise RuntimeError("Vite compilation exploded")

    monkeypatch.setattr(run, "check_and_build_frontend", failing_build)
    exit_code = run.cli_main(["--web-only"])
    assert exit_code == 1


# ---------------------------------------------------------------------------
# Test 33: KeyboardInterrupt during launcher preflight returns graceful 0
# ---------------------------------------------------------------------------
def test_keyboard_interrupt_during_launcher_preflight_returns_graceful_zero(monkeypatch):
    def interrupted_build(*args):
        raise KeyboardInterrupt()

    monkeypatch.setattr(run, "check_and_build_frontend", interrupted_build)
    assert run.cli_main([]) == 0
    assert run.cli_main(["--web-only"]) == 0


# ---------------------------------------------------------------------------
# Test 34: main.py does not call or import frontend build authority
# ---------------------------------------------------------------------------
def test_main_does_not_call_or_import_frontend_build_authority():
    main_path = Path("main.py")
    content = main_path.read_text(encoding="utf-8")
    assert "check_and_build_frontend" not in content
    assert "_ensure_frontend_runtime" not in content
    assert not hasattr(main, "_ensure_frontend_runtime")


# ---------------------------------------------------------------------------
# Test 35: main.py and web_server.py have no reverse import of run.py
# ---------------------------------------------------------------------------
def test_main_and_web_server_have_no_reverse_import_of_run():
    import ast

    for rel_path in ("main.py", "charlie/web_server.py"):
        tree = ast.parse(Path(rel_path).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "run", f"{rel_path} has 'import run'"
            elif isinstance(node, ast.ImportFrom):
                assert node.module != "run", f"{rel_path} has 'from run import ...'"


# ---------------------------------------------------------------------------
# Test 36: run.py -> main.py remains the supported dependency direction
# ---------------------------------------------------------------------------
def test_run_to_main_supported_direction():
    import ast

    run_tree = ast.parse(Path("run.py").read_text(encoding="utf-8"))
    imports_main = any(
        (isinstance(node, ast.Import) and any(a.name == "main" for a in node.names))
        or (isinstance(node, ast.ImportFrom) and node.module == "main")
        for node in ast.walk(run_tree)
    )
    assert imports_main, "run.py must import main"


# ---------------------------------------------------------------------------
# Test 37: Loop exception handler is restored across multiple invocations
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_loop_exception_handler_restored_without_stacking(monkeypatch):
    loop = asyncio.get_running_loop()
    sentinel_called = 0

    def sentinel_handler(lp, ctx):
        nonlocal sentinel_called
        sentinel_called += 1

    loop.set_exception_handler(sentinel_handler)
    initial_handler = loop.get_exception_handler()
    assert initial_handler is sentinel_handler

    monkeypatch.setattr(main, "_runtime_health", HealthRegistry(_all_subsystems()))
    monkeypatch.setattr(main, "SessionStore", lambda path: _FakeStore("session"))
    import charlie.audit_store as audit_store_module
    monkeypatch.setattr(audit_store_module, "AuditStore", lambda path: _FakeStore("audit"))
    monkeypatch.setattr(main, "_compose_memory_dependencies", lambda cfg: (_FakeStore("graph"), None, object()))
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
    monkeypatch.setattr(main, "_log_port_release", lambda *a: None)

    orig_gather = main.asyncio.gather
    async def fast_gather(*args, **kwargs):
        if kwargs.get("return_exceptions"):
            return await orig_gather(*args, **kwargs)
        for a in args:
            if asyncio.iscoroutine(a):
                a.close()
            elif isinstance(a, asyncio.Task) and not a.done():
                a.cancel()
        return []

    monkeypatch.setattr(main.asyncio, "gather", fast_gather)

    # First run
    await main.main()
    assert loop.get_exception_handler() is initial_handler

    # Second run
    await main.main()
    assert loop.get_exception_handler() is initial_handler

    loop.set_exception_handler(None)


# ---------------------------------------------------------------------------
# Test 38: H2 health causal guard authority remains intact
# ---------------------------------------------------------------------------
def test_h2_health_causal_guard_authority_remains_intact():
    import threading

    from charlie.core import Brain
    from charlie.subsystem_health import HealthStatus
    brain = Brain.__new__(Brain)
    brain._primary_llm_lock = threading.Lock()
    brain._primary_llm_dispatch_generation = 2
    brain._primary_llm_applied_generation = 0
    notified_statuses = []
    brain._on_llm_health = lambda st, det: notified_statuses.append((st, det))

    # Older generation 1 outcome must be suppressed
    applied = brain._notify_primary_llm_health(1, HealthStatus.DEGRADED, "error")
    assert applied is False
    assert brain._primary_llm_applied_generation == 0
    assert notified_statuses == []

    # Current generation 2 outcome must apply
    applied = brain._notify_primary_llm_health(2, HealthStatus.RUNNING, "Ready")
    assert applied is True
    assert brain._primary_llm_applied_generation == 2
    assert notified_statuses == [(HealthStatus.RUNNING, "Ready")]
