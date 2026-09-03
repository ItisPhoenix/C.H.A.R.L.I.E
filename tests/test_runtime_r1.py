"""Focused regression coverage for runtime operational-state authority."""

from __future__ import annotations

import pytest

import main
from charlie.events import CONTRACT_VERSION, EventMeta, EventSource, build_event
from charlie.subsystem_health import HealthRegistry, HealthStatus
from charlie.task_journal import TaskJournal, TaskStatus


def _task(task_id: str, status: str = "running", *, title: str = "Task") -> dict:
    return {
        "id": task_id,
        "title": title,
        "status": status,
        "current_step": 1,
        "total_steps": 2,
        "origin": "background",
        "priority": "normal",
        "session_id": "session-r1",
        "progress": 0.5,
        "current_action": "Inspect state",
        "capability_requirements": ["browser"],
    }


def _snapshot_event(tasks: list[dict]) -> dict:
    return build_event(
        "task_snapshot",
        {"tasks": tasks},
        meta=EventMeta(source=EventSource.TASK),
    )


class _RecordingBus:
    def __init__(self):
        self.events = []

    async def emit(self, event_type, payload, meta=None):
        self.events.append((event_type, payload, meta))


class _BridgeBus:
    def __init__(self, events):
        self.events = events

    async def consume_events(self, callback):
        for event in self.events:
            await callback(event)


async def _run_web_event_bridge(monkeypatch, events, cache):
    from charlie import web_server

    monkeypatch.setattr(web_server, "event_bus", _BridgeBus(events))
    monkeypatch.setattr(web_server, "_background_tasks", cache)
    monkeypatch.setattr(web_server, "active_connections", set())
    await web_server._event_bridge()


@pytest.mark.asyncio
async def test_runtime_state_replay_publishes_health_and_canonical_task_snapshot(monkeypatch):
    import main

    health = HealthRegistry(("voice",))
    health.set("voice", HealthStatus.RUNNING)
    journal = TaskJournal()
    journal.create_task("Canonical task", task_id="task-r1", status=TaskStatus.RUNNING)

    monkeypatch.setattr(main, "_runtime_health", health)
    monkeypatch.setattr(main, "get_task_journal", lambda: journal)

    bus = _RecordingBus()
    await main._publish_runtime_state(bus)

    assert [event[0] for event in bus.events] == [
        "subsystem_health",
        "task_snapshot",
        "tool_snapshot",
        "mcp_snapshot",
    ]
    assert bus.events[0][1] == health.snapshot()
    assert bus.events[1][1]["tasks"][0]["id"] == "task-r1"
    assert bus.events[1][1]["tasks"][0]["status"] == "running"
    assert bus.events[1][2].source is EventSource.TASK
    assert bus.events[2][1]["authority"] == "main_runtime"
    assert bus.events[3][1] == {"authority": "main_runtime", "enabled": False, "servers": []}


@pytest.mark.asyncio
async def test_runtime_state_request_dispatch_publishes_health_and_task_snapshot(monkeypatch):
    import main

    health = HealthRegistry(("voice",))
    health.set("voice", HealthStatus.RUNNING)
    journal = TaskJournal()
    journal.create_task("Dispatched task", task_id="task-dispatch", status=TaskStatus.RUNNING)
    monkeypatch.setattr(main, "_runtime_health", health)
    monkeypatch.setattr(main, "get_task_journal", lambda: journal)

    bus = _RecordingBus()
    handled = await main._handle_runtime_state_request("runtime_state_request", bus)

    assert handled is True
    assert [event[0] for event in bus.events] == [
        "subsystem_health",
        "task_snapshot",
        "tool_snapshot",
        "mcp_snapshot",
    ]
    assert bus.events[1][1]["tasks"][0]["id"] == "task-dispatch"


@pytest.mark.asyncio
async def test_runtime_state_request_uses_actual_command_consumer_dispatch_seam(monkeypatch):
    import main

    health = HealthRegistry(("voice",))
    health.set("voice", HealthStatus.RUNNING)
    journal = TaskJournal()
    journal.create_task("Command seam task", task_id="task-command-seam", status=TaskStatus.RUNNING)
    monkeypatch.setattr(main, "_runtime_health", health)
    monkeypatch.setattr(main, "get_task_journal", lambda: journal)

    bus = _RecordingBus()
    dispatcher = getattr(main, "_dispatch_web_command", None)
    assert dispatcher is not None
    handled = await dispatcher({"type": "runtime_state_request"}, bus)

    assert handled is True
    assert [event[0] for event in bus.events] == [
        "subsystem_health",
        "task_snapshot",
        "tool_snapshot",
        "mcp_snapshot",
    ]
    assert bus.events[1][1]["tasks"][0]["id"] == "task-command-seam"


def test_task_snapshot_rehydrates_projection_after_web_reconnect():
    from charlie import web_server

    cache = {"stale": _task("stale", "completed")}
    web_server._apply_task_snapshot_event(
        cache,
        _snapshot_event([_task("task-a"), _task("task-b", "completed")]),
    )

    assert list(cache) == ["task-a", "task-b"]
    assert cache["task-a"]["status"] == "running"
    assert "stale" not in cache


def test_task_snapshot_replacement_is_deterministic():
    from charlie import web_server

    event = _snapshot_event([_task("task-a"), _task("task-b", "completed")])
    first = {"old": _task("old")}
    second = {"different": _task("different")}

    web_server._apply_task_snapshot_event(first, event)
    web_server._apply_task_snapshot_event(second, event)

    assert first == second


def test_incremental_background_task_events_update_rehydrated_projection():
    from charlie import web_server

    cache = {}
    web_server._apply_task_snapshot_event(cache, _snapshot_event([_task("task-a")]))
    web_server._apply_background_task_event(
        cache,
        {
            "type": "background_task",
            "payload": {
                "id": "task-a",
                "title": "Task",
                "status": "completed",
                "current_step": 2,
                "total_steps": 2,
            },
        },
    )
    web_server._apply_background_task_event(
        cache,
        {
            "type": "background_task",
            "payload": {
                "id": "task-c",
                "title": "Later task",
                "status": "running",
                "current_step": 0,
                "total_steps": 1,
            },
        },
    )

    assert cache["task-a"]["status"] == "completed"
    assert cache["task-a"]["current_step"] == 2
    assert cache["task-c"]["status"] == "running"


@pytest.mark.parametrize("terminal_status", ["completed", "failed", "cancelled"])
def test_terminal_projection_ignores_stale_incremental_event(terminal_status):
    from charlie import web_server

    cache = {}
    web_server._apply_task_snapshot_event(cache, _snapshot_event([_task("terminal", terminal_status)]))
    before = dict(cache["terminal"])

    web_server._apply_background_task_event(
        cache,
        {
            "type": "background_task",
            "schema_version": 1,
            "payload": {
                "id": "terminal",
                "title": "Stale update",
                "status": "running",
                "current_step": 0,
                "total_steps": 1,
            },
        },
    )

    assert cache["terminal"] == before


def test_malformed_incremental_task_event_is_ignored():
    from charlie import web_server

    cache = {"stable": _task("stable")}
    web_server._apply_background_task_event(
        cache,
        {
            "type": "background_task",
            "payload": {
                "id": "bad",
                "status": "running",
                "current_step": "not-an-int",
            },
        },
    )

    assert cache == {"stable": _task("stable")}


@pytest.mark.parametrize(
    "event",
    [
        {"type": "task_snapshot", "payload": {"tasks": []}},
        {"type": "task_snapshot", "version": 1, "payload": {}},
        {"type": "task_snapshot", "version": 1, "payload": {"tasks": {}}},
        _snapshot_event([{"id": "task-2", "status": "running", "current_step": "not-an-int"}]),
        _snapshot_event([_task("valid"), {"id": "bad", "current_step": "not-an-int"}]),
    ],
    ids=["missing-envelope-version", "missing-tasks", "wrong-tasks-type", "bad-row", "mixed-valid-and-bad"],
)
def test_malformed_task_snapshot_is_noop(event):
    from charlie import web_server

    cache = {"existing": _task("existing")}
    before = {key: dict(value) for key, value in cache.items()}

    web_server._apply_task_snapshot_event(cache, event)

    assert cache == before


def test_valid_empty_task_snapshot_clears_projection():
    from charlie import web_server

    cache = {"existing": _task("existing")}

    web_server._apply_task_snapshot_event(cache, _snapshot_event([]))

    assert cache == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event",
    [
        {"type": "task_snapshot", "payload": {"tasks": []}},
        {"type": "task_snapshot", "version": CONTRACT_VERSION + 1, "payload": {"tasks": []}},
        {"type": "task_snapshot", "version": str(CONTRACT_VERSION), "payload": {"tasks": []}},
    ],
    ids=["missing-version", "wrong-version", "non-integer-version"],
)
async def test_event_bridge_rejects_invalid_raw_task_snapshot_envelope(monkeypatch, event):
    cache = {"existing": _task("existing")}
    before = {key: dict(value) for key, value in cache.items()}

    await _run_web_event_bridge(monkeypatch, [event], cache)

    assert cache == before


@pytest.mark.asyncio
async def test_event_bridge_applies_valid_canonical_empty_snapshot(monkeypatch):
    cache = {"existing": _task("existing")}

    await _run_web_event_bridge(monkeypatch, [_snapshot_event([])], cache)

    assert cache == {}


@pytest.mark.asyncio
async def test_event_bridge_rehydrates_valid_snapshot_from_raw_ipc(monkeypatch):
    cache = {"stale": _task("stale", "completed")}

    await _run_web_event_bridge(monkeypatch, [_snapshot_event([_task("task-a")])], cache)

    assert cache == {"task-a": _task("task-a")}


@pytest.mark.asyncio
async def test_event_bridge_ignores_malformed_snapshot_row_atomically(monkeypatch):
    cache = {"existing": _task("existing")}
    malformed = _snapshot_event([_task("valid"), {"id": "bad", "status": "running", "current_step": "nope"}])
    before = {key: dict(value) for key, value in cache.items()}

    await _run_web_event_bridge(monkeypatch, [malformed], cache)

    assert cache == before


@pytest.mark.asyncio
async def test_event_bridge_ignores_malformed_incremental_and_continues(monkeypatch):
    cache = {}
    malformed = {
        "type": "background_task",
        "payload": {
            "id": "bad",
            "title": "Bad task",
            "status": "running",
            "current_step": "not-an-int",
            "total_steps": 1,
        },
    }
    valid = {
        "type": "background_task",
        "payload": {
            "id": "task-after-bad",
            "title": "Valid task",
            "status": "running",
            "current_step": 1,
            "total_steps": 1,
        },
    }

    await _run_web_event_bridge(monkeypatch, [malformed, valid], cache)

    assert "bad" not in cache
    assert cache["task-after-bad"]["status"] == "running"
    assert cache["task-after-bad"]["current_step"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["completed", "failed", "cancelled"])
async def test_event_bridge_ignores_stale_incremental_after_terminal(monkeypatch, terminal_status):
    cache = {}
    stale = {
        "type": "background_task",
        "payload": {
            "id": "terminal",
            "title": "Stale update",
            "status": "running",
            "current_step": 0,
            "total_steps": 1,
        },
    }

    await _run_web_event_bridge(monkeypatch, [_snapshot_event([_task("terminal", terminal_status)]), stale], cache)

    assert cache["terminal"]["status"] == terminal_status


@pytest.mark.asyncio
async def test_event_bridge_rejects_unknown_status_atomically_and_ignores_unknown_incremental(monkeypatch):
    cache = {"existing": _task("existing")}
    unknown_snapshot = _snapshot_event([_task("valid"), _task("unknown", "bogus")])
    unknown_incremental = {
        "type": "background_task",
        "payload": {
            "id": "unknown-incremental",
            "title": "Unknown task",
            "status": "bogus",
            "current_step": 0,
            "total_steps": 1,
        },
    }
    valid_incremental = {
        "type": "background_task",
        "payload": {
            "id": "after-unknown",
            "title": "Valid task",
            "status": "running",
            "current_step": 0,
            "total_steps": 1,
        },
    }

    await _run_web_event_bridge(
        monkeypatch,
        [unknown_snapshot, unknown_incremental, valid_incremental],
        cache,
    )

    assert cache == {
        "existing": _task("existing"),
        "after-unknown": {
            "id": "after-unknown",
            "title": "Valid task",
            "status": "running",
            "current_step": 0,
            "total_steps": 1,
        },
    }


def test_task_snapshot_accepts_only_canonical_statuses_and_legacy_aliases():
    from charlie import web_server

    cache = {}
    web_server._apply_task_snapshot_event(
        cache,
        _snapshot_event([_task("done", "done"), _task("approval", "awaiting_approval")]),
    )

    assert cache["done"]["status"] == "completed"
    assert cache["approval"]["status"] == "approval_required"


@pytest.mark.asyncio
async def test_self_introspection_uses_web_projection_and_reports_lease_authority_unavailable(monkeypatch):
    from charlie import resource_locks, task_journal, web_server

    projection = {"task-ipc": _task("task-ipc", title="IPC task")}
    health_projection = {"web": {"status": "running", "detail": "Projected"}}
    monkeypatch.setattr(web_server, "_background_tasks", projection)
    monkeypatch.setattr(web_server, "_subsystem_health", health_projection)

    def local_journal_must_not_be_read(*args, **kwargs):
        raise AssertionError("web introspection must not read local task journal")

    def local_leases_must_not_be_read(*args, **kwargs):
        raise AssertionError("web introspection must not read local lease authority")

    monkeypatch.setattr(task_journal, "get_task_journal", local_journal_must_not_be_read)
    monkeypatch.setattr(resource_locks, "get_capability_lease_manager", local_leases_must_not_be_read)
    monkeypatch.setattr(resource_locks, "get_all_leases", local_leases_must_not_be_read)

    result = await web_server.get_self_introspection()

    assert result["tasks"]["total_tasks"] == 1
    assert result["tasks"]["active_tasks"] == [
        {
            "task_id": "task-ipc",
            "title": "IPC task",
            "status": "running",
            "priority": "normal",
            "origin": "background",
        }
    ]
    assert result["subsystem_health"] == health_projection
    assert result["leases"]["status"] == "unavailable"
    assert result["leases"]["active_leases"] == {}
    assert result["leases"]["leased_resources_count"] is None


@pytest.mark.asyncio
async def test_task_routes_share_ipc_projection_when_local_scheduler_is_empty(monkeypatch):
    from charlie import background_task, web_server

    projection = {"task-ipc": _task("task-ipc", title="IPC task")}
    monkeypatch.setattr(web_server, "_background_tasks", projection)

    def local_scheduler_must_not_be_read():
        raise AssertionError("web route must not read local background scheduler")

    monkeypatch.setattr(background_task, "get_current_task", local_scheduler_must_not_be_read)

    tasks_response = await web_server.list_tasks()
    current_response = await web_server.background_task_status()

    assert tasks_response == {"tasks": [projection["task-ipc"]]}
    assert current_response == {"task": projection["task-ipc"]}


@pytest.mark.asyncio
async def test_developer_diagnostics_uses_ipc_projection_and_reports_lease_unknown(monkeypatch):
    from charlie import resource_locks, task_journal, web_server

    projection = {"task-ipc": _task("task-ipc", title="IPC task")}
    monkeypatch.setattr(web_server, "_background_tasks", projection)
    monkeypatch.setattr(web_server, "config", type("Config", (), {"developer_mode_enabled": True})())

    def local_journal_must_not_be_constructed(*args, **kwargs):
        raise AssertionError("web diagnostics must not construct local TaskJournal")

    def local_leases_must_not_be_read(*args, **kwargs):
        raise AssertionError("web diagnostics must not read local lease authority")

    monkeypatch.setattr(task_journal, "TaskJournal", local_journal_must_not_be_constructed)
    monkeypatch.setattr(resource_locks, "get_all_leases", local_leases_must_not_be_read)

    result = await web_server.get_developer_diagnostics()
    diagnostics = result["diagnostics"]

    assert diagnostics["tasks"] == [projection["task-ipc"]]
    assert diagnostics["leases"] == {}
    assert diagnostics["lease_authority"]["status"] == "unavailable"


def test_main_runtime_introspector_receives_canonical_instances(monkeypatch):
    import charlie.resource_locks as resource_locks
    import charlie.runtime_introspector as runtime_introspector
    import main

    health = object()
    journal = object()
    leases = object()
    captured = {}

    class SpyIntrospector:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(main, "_runtime_health", health)
    monkeypatch.setattr(main, "get_task_journal", lambda: journal)
    monkeypatch.setattr(resource_locks, "get_capability_lease_manager", lambda: leases)
    monkeypatch.setattr(runtime_introspector, "RuntimeIntrospector", SpyIntrospector)

    result = main._build_runtime_introspector(
        config=object(),
        capability_index=object(),
        mcp_client=object(),
    )

    assert isinstance(result, SpyIntrospector)
    assert captured["health_registry"] is health
    assert captured["task_journal"] is journal
    assert captured["lease_manager"] is leases


class _WebFakeProcess:
    def __init__(self, pid: int = 4321, exit_code: int | None = None) -> None:
        self.pid = pid
        self.exit_code = exit_code
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.exit_code

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        self.exit_code = self.exit_code if self.exit_code is not None else 0
        return self.exit_code


class _StartupExitSentinel(BaseException):
    pass


class _StartupFakeStore:
    def close(self) -> None:
        pass


class _StartupFakeBrain:
    async def close(self) -> None:
        pass


def _web_build_identity() -> dict[str, object]:
    return {
        "build_id": "build-1",
        "input_fingerprint": "inputs-1",
        "git_sha": "source-1",
        "dirty": True,
    }


def _web_status(process: _WebFakeProcess, launch_id: str, build: dict[str, object]) -> dict[str, object]:
    return {
        "launch_id": launch_id,
        "pid": process.pid,
        "source_identity": build.get("git_sha"),
        "frontend_build": build,
    }


def _patch_web_spawn(monkeypatch, process: _WebFakeProcess) -> None:
    monkeypatch.setattr(main, "_web_port_is_listening", lambda host, port: False)
    monkeypatch.setattr(main.subprocess, "Popen", lambda *args, **kwargs: process)


def test_socket_probe_distinguishes_bound_and_free_ports():
    listener = main.socket.socket(main.socket.AF_INET, main.socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        assert main._web_port_is_listening("127.0.0.1", port) is True
    finally:
        listener.close()

    assert main._web_port_is_listening("127.0.0.1", port) is False


def test_free_port_requires_owned_runtime_identity_before_success(monkeypatch):
    process = _WebFakeProcess()
    build = _web_build_identity()
    launch_id = "launch-current"
    health = HealthRegistry(("web",))
    monkeypatch.setattr(main, "_runtime_health", health)
    _patch_web_spawn(monkeypatch, process)
    monkeypatch.setattr(main, "_expected_frontend_build_identity", lambda: build)
    monkeypatch.setattr(main, "_fetch_web_status", lambda host, port: _web_status(process, launch_id, build))

    result = main._start_web_subprocess(
        ("python", "web_server_entry.py"),
        {"CHARLIE_LAUNCH_ID": launch_id},
        host="127.0.0.1",
        port=8000,
        launch_id=launch_id,
    )

    assert result is process
    assert main._runtime_health.snapshot()["web"]["status"] == "running"
    assert process.terminated is False


def test_web_identity_accepts_the_real_child_of_the_launcher(monkeypatch):
    process = _WebFakeProcess(pid=4321)
    build = _web_build_identity()
    monkeypatch.setattr(main, "_web_process_owns_pid", lambda current, reported: reported == 8765)

    status = _web_status(process, "launch-current", build) | {"pid": 8765}
    assert main._web_identity_error(status, process, "launch-current", build) is None


@pytest.mark.asyncio
async def test_main_startup_failure_exits_nonzero_instead_of_succeeding(monkeypatch, caplog):
    import charlie.audit_store as audit_store_module
    import charlie.plugins as plugins_module
    import charlie.tools as tools_module

    health = HealthRegistry(("brain", "plugins", "mcp", "web"))
    monkeypatch.setattr(main, "_runtime_health", health)
    monkeypatch.setattr(main, "SessionStore", lambda path: _StartupFakeStore())
    monkeypatch.setattr(audit_store_module, "AuditStore", lambda path: _StartupFakeStore())
    monkeypatch.setattr(main, "_compose_memory_dependencies", lambda runtime_config: (object(), None, object()))
    monkeypatch.setattr(main, "_ensure_frontend_runtime", lambda: None)
    monkeypatch.setattr(main, "Brain", lambda *args, **kwargs: _StartupFakeBrain())
    monkeypatch.setattr(main, "_wire_memory_service", lambda service: None)
    monkeypatch.setattr(tools_module, "register_plugin_tools", lambda runtime_config: None)
    monkeypatch.setattr(plugins_module, "PluginManager", lambda: object())
    monkeypatch.setattr(main.config, "mcp_enabled", False)
    monkeypatch.setattr(
        main,
        "_start_web_subprocess",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("synthetic web ownership failure")),
    )

    exit_codes = []

    def fake_exit(code):
        exit_codes.append(code)
        raise _StartupExitSentinel

    monkeypatch.setattr(main.os, "_exit", fake_exit)

    with pytest.raises(_StartupExitSentinel):
        await main.main()

    assert exit_codes == [1]
    assert "Charlie runtime startup failed before voice initialization" in caplog.text


def test_unrelated_listener_stops_startup_without_spawning(monkeypatch):
    health = HealthRegistry(("web",))
    monkeypatch.setattr(main, "_runtime_health", health)
    monkeypatch.setattr(main, "_web_port_is_listening", lambda host, port: True)
    monkeypatch.setattr(main, "_fetch_web_status", lambda host, port: None)
    monkeypatch.setattr(main.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("must not spawn"))

    with pytest.raises(RuntimeError, match="Port 8000 is occupied by another process"):
        main._start_web_subprocess(
            ("python", "web_server_entry.py"),
            {},
            host="127.0.0.1",
            port=8000,
            launch_id="launch-current",
        )


def test_stale_charlie_listener_stops_startup_truthfully(monkeypatch):
    process = _WebFakeProcess(pid=9999)
    health = HealthRegistry(("web",))
    monkeypatch.setattr(main, "_runtime_health", health)
    monkeypatch.setattr(main, "_web_port_is_listening", lambda host, port: True)
    monkeypatch.setattr(
        main,
        "_fetch_web_status",
        lambda host, port: {"launch_id": "launch-old", "pid": process.pid},
    )
    monkeypatch.setattr(main.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("must not spawn"))

    with pytest.raises(RuntimeError, match="another Charlie runtime"):
        main._start_web_subprocess(
            ("python", "web_server_entry.py"),
            {},
            host="127.0.0.1",
            port=8000,
            launch_id="launch-current",
        )


def test_spawned_web_exit_stops_startup_and_tears_down_child(monkeypatch):
    process = _WebFakeProcess(exit_code=17)
    build = _web_build_identity()
    health = HealthRegistry(("web",))
    monkeypatch.setattr(main, "_runtime_health", health)
    _patch_web_spawn(monkeypatch, process)
    monkeypatch.setattr(main, "_expected_frontend_build_identity", lambda: build)
    monkeypatch.setattr(main, "_fetch_web_status", lambda host, port: None)

    with pytest.raises(RuntimeError, match="exited before readiness"):
        main._start_web_subprocess(
            ("python", "web_server_entry.py"),
            {},
            host="127.0.0.1",
            port=8000,
            launch_id="launch-current",
        )

    assert process.terminated is True


def test_web_readiness_timeout_stops_startup_and_tears_down_child(monkeypatch):
    process = _WebFakeProcess()
    build = _web_build_identity()
    health = HealthRegistry(("web",))
    monkeypatch.setattr(main, "_runtime_health", health)
    _patch_web_spawn(monkeypatch, process)
    monkeypatch.setattr(main, "_expected_frontend_build_identity", lambda: build)
    monkeypatch.setattr(main, "_fetch_web_status", lambda host, port: None)

    with pytest.raises(RuntimeError, match="did not become ready"):
        main._start_web_subprocess(
            ("python", "web_server_entry.py"),
            {},
            host="127.0.0.1",
            port=8000,
            launch_id="launch-current",
            startup_timeout=0,
        )

    assert process.terminated is True


def test_wrong_launch_identity_is_rejected_and_child_is_stopped(monkeypatch):
    process = _WebFakeProcess()
    build = _web_build_identity()
    health = HealthRegistry(("web",))
    monkeypatch.setattr(main, "_runtime_health", health)
    _patch_web_spawn(monkeypatch, process)
    monkeypatch.setattr(main, "_expected_frontend_build_identity", lambda: build)
    monkeypatch.setattr(main, "_fetch_web_status", lambda host, port: _web_status(process, "launch-old", build))

    with pytest.raises(RuntimeError, match="launch identity mismatch"):
        main._start_web_subprocess(
            ("python", "web_server_entry.py"),
            {},
            host="127.0.0.1",
            port=8000,
            launch_id="launch-current",
        )

    assert process.terminated is True


def test_wrong_frontend_identity_is_rejected(monkeypatch):
    process = _WebFakeProcess()
    expected = _web_build_identity()
    served = {**expected, "input_fingerprint": "stale-inputs"}
    health = HealthRegistry(("web",))
    monkeypatch.setattr(main, "_runtime_health", health)
    _patch_web_spawn(monkeypatch, process)
    monkeypatch.setattr(main, "_expected_frontend_build_identity", lambda: expected)
    monkeypatch.setattr(main, "_fetch_web_status", lambda host, port: _web_status(process, "launch-current", served))

    with pytest.raises(RuntimeError, match="frontend build identity mismatch"):
        main._start_web_subprocess(
            ("python", "web_server_entry.py"),
            {},
            host="127.0.0.1",
            port=8000,
            launch_id="launch-current",
        )

    assert process.terminated is True
