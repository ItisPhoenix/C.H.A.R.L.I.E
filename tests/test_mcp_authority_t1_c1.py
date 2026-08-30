from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from charlie.events import EventMeta, EventSource, build_event


class _FakeRegistry:
    """Small registry double that exposes only main's snapshot surface."""

    def __init__(self) -> None:
        self.tools: dict[str, dict[str, Any]] = {}

    def list_metadata(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "description": item["description"],
                "owner": item["owner"],
                "risk_class": item["risk_class"],
            }
            for name, item in self.tools.items()
        ]

    def register_tool(
        self,
        name: str,
        description: str,
        schema: dict[str, Any],
        *,
        owner: str = "",
        risk_class: str | None = None,
    ):
        self.tools[name] = {
            "description": description,
            "owner": owner,
            "risk_class": risk_class,
            "schema": schema,
        }

        def decorator(func):
            self.tools[name]["func"] = func
            return func

        return decorator

    def unregister_tool(self, name: str) -> bool:
        return self.tools.pop(name, None) is not None


class _Brain:
    def __init__(self) -> None:
        self.rebuilds = 0

    def rebuild_stable_tier(self) -> None:
        self.rebuilds += 1


class _FakeMcpClient:
    def __init__(self) -> None:
        self.servers: dict[str, dict[str, Any]] = {
            "demo": {
                "command": "python",
                "args": ["server.py"],
                "running": False,
                "tools": [],
            }
        }
        self.calls: list[tuple[str, str, Any]] = []
        self.fail_on: str | None = None
        self.partial_on: str | None = None

    def list_servers_detailed(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "command": server["command"],
                "args": list(server["args"]),
                "running": server["running"],
                "status": "connected" if server["running"] else "disconnected",
                "tools_count": len(server["tools"]),
                "tools": [dict(tool) for tool in server["tools"]],
            }
            for name, server in self.servers.items()
        ]

    def _check_failure(self, operation: str) -> None:
        if self.fail_on == operation:
            raise RuntimeError(f"controlled {operation} failure")

    def _register_demo_tool(self, registry: _FakeRegistry, name: str) -> None:
        registry.register_tool(
            name=name,
            description="MCP read tool",
            schema={"type": "object"},
            owner="mcp",
            risk_class="reversible",
        )(lambda: "ok")

    def _connect_state(self, registry: _FakeRegistry, name: str) -> None:
        server = self.servers[name]
        server["running"] = True
        tool_name = f"mcp_{name}_read"
        self._register_demo_tool(registry, tool_name)
        server["tools"] = [{"name": "read", "description": "MCP read tool"}]

    def _disconnect_state(self, registry: _FakeRegistry, name: str) -> None:
        server = self.servers[name]
        registry.unregister_tool(f"mcp_{name}_read")
        server["tools"] = []
        server["running"] = False

    def add_server(self, config: Any) -> None:
        self.calls.append(("add", config.name, config))
        self._check_failure("add")
        self.servers[config.name] = {
            "command": config.command,
            "args": list(config.args),
            "running": False,
            "tools": [],
        }

    def enable_server(self, registry: _FakeRegistry, name: str) -> list[str]:
        self.calls.append(("connect", name, registry))
        if name not in self.servers:
            raise KeyError(name)
        self._check_failure("connect")
        self._connect_state(registry, name)
        if self.partial_on == "connect":
            raise RuntimeError("controlled partial connect failure")
        return [f"mcp_{name}_read"]

    def disable_server(self, registry: _FakeRegistry, name: str) -> bool:
        self.calls.append(("disconnect", name, registry))
        if name not in self.servers:
            return False
        self._check_failure("disconnect")
        self._disconnect_state(registry, name)
        if self.partial_on == "disconnect":
            raise RuntimeError("controlled partial disconnect failure")
        return True

    def restart_server(self, registry: _FakeRegistry, name: str) -> bool:
        self.calls.append(("restart", name, registry))
        if name not in self.servers:
            return False
        self._check_failure("restart")
        self._disconnect_state(registry, name)
        self._connect_state(registry, name)
        if self.partial_on == "restart":
            raise RuntimeError("controlled partial restart failure")
        return True

    def remove_server(self, registry: _FakeRegistry, name: str) -> bool:
        self.calls.append(("delete", name, registry))
        if name not in self.servers:
            return False
        self._check_failure("delete")
        if self.servers[name]["running"]:
            self._disconnect_state(registry, name)
        self.servers.pop(name)
        if self.partial_on == "delete":
            raise RuntimeError("controlled partial delete failure")
        return True


class _RecordingBus:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def emit(self, event_type: str, payload: dict[str, Any], meta: EventMeta | None = None) -> None:
        self.events.append(build_event(event_type, payload, meta=meta))


class _BridgeBus:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events

    async def consume_events(self, callback) -> None:
        for event in self.events:
            await callback(event)


class _RuntimeBus:
    """In-process production seam: web command -> main dispatch -> IPC events."""

    _STOP = object()

    def __init__(self, client: _FakeMcpClient, registry: _FakeRegistry, brain: _Brain) -> None:
        self.client = client
        self.registry = registry
        self.brain = brain
        self.commands: list[dict[str, Any]] = []
        self.events: asyncio.Queue[Any] = asyncio.Queue()

    async def emit(self, event_type: str, payload: dict[str, Any], meta: EventMeta | None = None) -> None:
        await self.events.put(build_event(event_type, payload, meta=meta))

    async def send_command(self, command: dict[str, Any]) -> bool:
        self.commands.append(command)
        if command.get("type") == "mcp_operation":
            import main

            _result, self.client = await main._dispatch_mcp_operation(
                command.get("payload", {}),
                self,
                mcp_client=self.client,
                brain=self.brain,
                tool_registry=self.registry,
            )
        return True

    async def consume_events(self, callback) -> None:
        while True:
            event = await self.events.get()
            if event is self._STOP:
                return
            await callback(event)


def _server(
    name: str = "demo",
    *,
    running: bool = False,
    tools: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    safe_tools = list(tools or [])
    return {
        "name": name,
        "command": "python",
        "args": ["server.py"],
        "running": running,
        "status": "connected" if running else "disconnected",
        "tools_count": len(safe_tools),
        "tools": safe_tools,
    }


def _mcp_event(servers: list[dict[str, Any]], *, enabled: bool = True) -> dict[str, Any]:
    return build_event(
        "mcp_snapshot",
        {"authority": "main_runtime", "enabled": enabled, "servers": servers},
        meta=EventMeta(source=EventSource.RUNTIME),
    )


def _tool_event(tools: list[dict[str, Any]]) -> dict[str, Any]:
    return build_event(
        "tool_snapshot",
        {"authority": "main_runtime", "tools": tools},
        meta=EventMeta(source=EventSource.RUNTIME),
    )


@pytest.fixture(autouse=True)
def _fresh_c1_state(monkeypatch: pytest.MonkeyPatch):
    import charlie.web_server as web_server
    import main

    monkeypatch.setattr(main.config, "mcp_enabled", True)
    monkeypatch.setattr(web_server.config, "mcp_enabled", True)
    monkeypatch.setattr(web_server, "_mcp_snapshot", None)
    monkeypatch.setattr(web_server, "_mcp_snapshot_event", None)
    monkeypatch.setattr(web_server, "_tool_snapshot", None)
    monkeypatch.setattr(web_server, "_tool_snapshot_event", None)
    monkeypatch.setattr(web_server, "event_bus", None)
    monkeypatch.setattr(web_server, "active_connections", set())
    monkeypatch.setattr(web_server, "_pending_mcp_operations", {}, raising=False)
    yield


async def _run_bridge(monkeypatch: pytest.MonkeyPatch, events: list[dict[str, Any]]) -> None:
    import charlie.web_server as web_server

    monkeypatch.setattr(web_server, "event_bus", _BridgeBus(events))
    await web_server._event_bridge()


@pytest.mark.asyncio
async def test_main_mcp_command_dispatch_covers_all_operations_and_orders_ack_last() -> None:
    import main

    registry = _FakeRegistry()
    client = _FakeMcpClient()
    brain = _Brain()
    bus = _RecordingBus()

    operations = [
        {"request_id": "add-1", "operation": "add", "server_name": "extra", "command": "python", "args": ["x.py"]},
        {"request_id": "connect-1", "operation": "connect", "server_name": "demo"},
        {"request_id": "disconnect-1", "operation": "disconnect", "server_name": "demo"},
        {"request_id": "restart-1", "operation": "restart", "server_name": "demo"},
        {"request_id": "delete-1", "operation": "delete", "server_name": "demo"},
    ]
    expected_events = {
        "add": ["mcp_snapshot", "mcp_operation_result"],
        "connect": ["tool_snapshot", "mcp_snapshot", "mcp_operation_result"],
        "disconnect": ["tool_snapshot", "mcp_snapshot", "mcp_operation_result"],
        "restart": ["tool_snapshot", "mcp_snapshot", "mcp_operation_result"],
        "delete": ["tool_snapshot", "mcp_snapshot", "mcp_operation_result"],
    }

    for payload in operations:
        bus.events.clear()
        result, client = await main._dispatch_mcp_operation(
            payload,
            bus,
            mcp_client=client,
            brain=brain,
            tool_registry=registry,
        )
        assert result["success"] is True
        assert [event["type"] for event in bus.events] == expected_events[payload["operation"]]
        assert bus.events[-1]["type"] == "mcp_operation_result"
        assert {
            key: bus.events[-1]["payload"][key]
            for key in ("request_id", "operation", "success", "server_name")
        } == {
            "request_id": payload["request_id"],
            "operation": payload["operation"],
            "success": True,
            "server_name": payload["server_name"],
        }

    assert [call[0] for call in client.calls] == ["add", "connect", "disconnect", "restart", "delete"]
    assert all(call[2] is registry for call in client.calls[1:])
    assert brain.rebuilds == 4


def test_main_command_consumer_routes_mcp_operations_through_authoritative_seam() -> None:
    import inspect

    import main

    source = inspect.getsource(main.main)
    assert 'cmd_type == "mcp_operation"' in source
    assert "_dispatch_mcp_operation(" in source


def test_main_mcp_snapshot_is_safe_and_sanitizes_secret_arguments() -> None:
    import main

    client = SimpleNamespace(
        list_servers_detailed=lambda: [
            {
                "name": "safe",
                "command": "python",
                "args": ["server.py", "--api-key=do-not-leak", "--token", "secret", "--mode", "stdio"],
                "running": False,
                "status": "disconnected",
                "tools_count": 1,
                "tools": [
                    {
                        "name": "read",
                        "description": "Read data",
                        "input_schema": {"default": "sensitive schema must not cross"},
                    }
                ],
                "env": {"SECRET": "must not cross"},
                "process": object(),
            }
        ]
    )

    snapshot = main._build_mcp_snapshot(client, enabled=True)

    assert snapshot == {
        "authority": "main_runtime",
        "enabled": True,
        "servers": [
            {
                "name": "safe",
                "command": "python",
                "args": ["server.py", "--api-key=<redacted>", "--token", "<redacted>", "--mode", "stdio"],
                "running": False,
                "status": "disconnected",
                "tools_count": 1,
                "tools": [{"name": "read", "description": "Read data"}],
            }
        ],
    }


@pytest.mark.asyncio
async def test_web_mcp_routes_send_correlated_commands_and_never_touch_web_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import charlie.tools as tools_module
    import charlie.web_server as web_server

    registry = _FakeRegistry()
    local_registry = _FakeRegistry()
    client = _FakeMcpClient()
    brain = _Brain()
    bus = _RuntimeBus(client, registry, brain)
    monkeypatch.setattr(web_server, "event_bus", bus)
    monkeypatch.setattr(tools_module, "registry", local_registry)
    bridge = asyncio.create_task(web_server._event_bridge())

    try:
        add_result = await web_server.add_mcp_server(
            {"name": "extra", "command": "python", "args": ["extra.py"]}
        )
        connect_result = await web_server.connect_mcp_server("demo")
        disconnect_result = await web_server.disconnect_mcp_server("demo")
        restart_result = await web_server.restart_mcp_server("demo")
        delete_result = await web_server.delete_mcp_server("demo")
    finally:
        await bus.events.put(bus._STOP)
        await bridge

    assert all(result["status"] == "ok" for result in [
        add_result,
        connect_result,
        disconnect_result,
        restart_result,
        delete_result,
    ])
    assert [command["type"] for command in bus.commands] == [
        "mcp_operation",
        "mcp_operation",
        "mcp_operation",
        "mcp_operation",
        "mcp_operation",
    ]
    assert [command["payload"]["operation"] for command in bus.commands] == [
        "add",
        "connect",
        "disconnect",
        "restart",
        "delete",
    ]
    assert all(command["payload"]["request_id"] for command in bus.commands)
    assert local_registry.tools == {}
    assert (await web_server.get_mcp_servers())["servers"] == [
        {
            "name": "extra",
            "command": "python",
            "args": ["extra.py"],
            "running": False,
            "status": "disconnected",
            "tools_count": 0,
            "tools": [],
        }
    ]


@pytest.mark.asyncio
async def test_failed_operation_preserves_projection_and_partial_failure_is_truthful(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import charlie.web_server as web_server
    import main

    registry = _FakeRegistry()
    client = _FakeMcpClient()
    brain = _Brain()
    seed_bus = _RecordingBus()
    await main._publish_tool_snapshot(seed_bus, registry)
    await main._publish_mcp_snapshot(seed_bus, client, enabled=True)
    await _run_bridge(monkeypatch, seed_bus.events)
    before_tools = await web_server.get_tools()
    before_servers = await web_server.get_mcp_servers()

    client.fail_on = "connect"
    failure_bus = _RecordingBus()
    failed, _ = await main._dispatch_mcp_operation(
        {"request_id": "failed", "operation": "connect", "server_name": "demo"},
        failure_bus,
        mcp_client=client,
        brain=brain,
        tool_registry=registry,
    )
    assert failed["success"] is False
    assert "partial" not in failed
    assert [event["type"] for event in failure_bus.events] == ["mcp_operation_result"]
    assert await web_server.get_tools() == before_tools
    assert await web_server.get_mcp_servers() == before_servers

    client.fail_on = None
    client.partial_on = "connect"
    partial_bus = _RecordingBus()
    partial, _ = await main._dispatch_mcp_operation(
        {"request_id": "partial", "operation": "connect", "server_name": "demo"},
        partial_bus,
        mcp_client=client,
        brain=brain,
        tool_registry=registry,
    )
    assert partial["success"] is False
    assert partial["partial"] is True
    assert [event["type"] for event in partial_bus.events] == [
        "tool_snapshot",
        "mcp_snapshot",
        "mcp_operation_result",
    ]
    await _run_bridge(monkeypatch, partial_bus.events)
    assert (await web_server.get_mcp_status())["servers"] == {"demo": True}
    assert [tool["name"] for tool in (await web_server.get_mcp_tools())["tools"]] == ["mcp_demo_read"]


@pytest.mark.asyncio
async def test_mcp_web_transport_failures_are_not_reported_as_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import charlie.web_server as web_server

    class _SendFailureBus:
        async def send_command(self, command):
            return False

    monkeypatch.setattr(web_server, "event_bus", _SendFailureBus())
    result = await web_server.connect_mcp_server("demo")
    assert result["status"] == "error"
    assert result["runtime_status"] == "unavailable"

    class _NoAckBus:
        async def send_command(self, command):
            return True

    monkeypatch.setattr(web_server, "event_bus", _NoAckBus())
    monkeypatch.setattr(web_server, "MCP_OPERATION_TIMEOUT_SECONDS", 0.001)
    timeout_result = await web_server.connect_mcp_server("demo")
    assert timeout_result["status"] == "error"
    assert timeout_result["runtime_status"] == "timeout"


@pytest.mark.asyncio
async def test_mcp_snapshot_projection_status_servers_empty_and_malformed_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import charlie.web_server as web_server

    valid = _mcp_event([_server("demo", running=True, tools=[{"name": "read", "description": "Read"}])])
    assert web_server._apply_mcp_snapshot_event(valid) is True
    status = await web_server.get_mcp_status()
    servers = await web_server.get_mcp_servers()
    assert status["synchronized"] is True
    assert status["servers"] == {"demo": True}
    assert servers["servers"][0]["tools_count"] == 1

    empty = _mcp_event([])
    assert web_server._apply_mcp_snapshot_event(empty) is True
    assert (await web_server.get_mcp_status())["servers"] == {}
    assert (await web_server.get_mcp_servers())["servers"] == []
    assert (await web_server.get_mcp_status())["synchronized"] is True

    assert web_server._apply_mcp_snapshot_event(valid) is True
    before = await web_server.get_mcp_servers()
    malformed = _mcp_event([_server("demo"), _server("demo")])
    assert web_server._apply_mcp_snapshot_event(malformed) is False
    assert await web_server.get_mcp_servers() == before

    monkeypatch.setattr(web_server, "_mcp_snapshot", None)
    monkeypatch.setattr(web_server, "_mcp_snapshot_event", None)
    unavailable_status = await web_server.get_mcp_status()
    unavailable_servers = await web_server.get_mcp_servers()
    assert unavailable_status["synchronized"] is False
    assert unavailable_status["status"] == "unavailable"
    assert unavailable_servers["synchronized"] is False
    assert unavailable_servers["status"] == "unavailable"


@pytest.mark.asyncio
async def test_mcp_snapshot_replays_through_runtime_state_and_actual_event_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import charlie.web_server as web_server
    import main

    client = _FakeMcpClient()
    bus = _RecordingBus()
    assert await main._handle_runtime_state_request("runtime_state_request", bus, client) is True
    snapshot = next(event for event in bus.events if event["type"] == "mcp_snapshot")
    assert snapshot["payload"]["authority"] == "main_runtime"
    assert snapshot["payload"]["servers"][0]["name"] == "demo"

    await _run_bridge(monkeypatch, bus.events)
    assert (await web_server.get_mcp_status())["synchronized"] is True
    replay = next(event for event in web_server._initial_state_events() if event["type"] == "mcp_snapshot")
    assert replay["replay"] is True
    assert replay["payload"]["servers"][0]["name"] == "demo"


@pytest.mark.asyncio
async def test_mcp_tools_uses_owner_provenance_not_name_prefix() -> None:
    import charlie.web_server as web_server

    assert web_server._apply_tool_snapshot_event(
        _tool_event(
            [
                {"name": "mcp_fake_plugin", "owner": "plugin"},
                {"name": "owned_without_prefix", "owner": "mcp"},
                {"name": "mcp_real", "owner": "mcp"},
            ]
        )
    ) is True
    assert web_server._apply_mcp_snapshot_event(_mcp_event([])) is True

    result = await web_server.get_mcp_tools()
    assert [tool["name"] for tool in result["tools"]] == ["owned_without_prefix", "mcp_real"]
    assert "mcp_fake_plugin" not in [tool["name"] for tool in result["tools"]]


def test_web_has_no_mcp_runtime_client_or_local_mcp_initializer() -> None:
    import charlie.web_server as web_server

    assert not hasattr(web_server, "mcp_client")
    assert not hasattr(web_server, "_ensure_mcp_client")
    assert not hasattr(web_server, "_ensure_mcp_client_async")
    assert not hasattr(web_server, "MCPClient")
    assert not hasattr(web_server, "MCPServerConfig")
