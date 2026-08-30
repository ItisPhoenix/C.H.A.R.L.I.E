from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from charlie.events import CONTRACT_VERSION, EventMeta, EventSource, build_event
from charlie.plugins import PluginManager
from charlie.tools import ToolRegistry


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


def _tool_event(tools: list[dict[str, Any]], **payload_overrides: Any) -> dict[str, Any]:
    payload = {"authority": "main_runtime", "tools": tools}
    payload.update(payload_overrides)
    return build_event(
        "tool_snapshot",
        payload,
        meta=EventMeta(source=EventSource.RUNTIME),
    )


async def _run_bridge(monkeypatch: pytest.MonkeyPatch, events: list[dict[str, Any]]) -> None:
    from charlie import web_server

    monkeypatch.setattr(web_server, "event_bus", _BridgeBus(events))
    monkeypatch.setattr(web_server, "active_connections", set())
    await web_server._event_bridge()


@pytest.fixture(autouse=True)
def _fresh_projection(monkeypatch: pytest.MonkeyPatch):
    from charlie import web_server

    monkeypatch.setattr(web_server, "_tool_snapshot", None)
    monkeypatch.setattr(web_server, "_tool_snapshot_event", None)
    monkeypatch.setattr(web_server, "event_bus", None)
    monkeypatch.setattr(web_server, "active_connections", set())


def test_main_snapshot_uses_canonical_registry_and_safe_metadata() -> None:
    import main

    canonical_registry = SimpleNamespace(
        list_metadata=lambda: [
            {
                "name": "main_only",
                "description": "Canonical tool",
                "owner": "tools",
                "risk_class": "safe",
                "schema": {"secret": "must not cross IPC"},
                "func": object(),
            }
        ]
    )

    snapshot = main._build_tool_snapshot(canonical_registry)

    assert snapshot["authority"] == "main_runtime"
    assert snapshot["tools"] == [
        {
            "name": "main_only",
            "description": "Canonical tool",
            "owner": "tools",
            "risk_class": "safe",
        }
    ]
    assert "schema" not in snapshot["tools"][0]
    assert "func" not in snapshot["tools"][0]


@pytest.mark.asyncio
async def test_runtime_state_request_replays_main_tool_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    import charlie.tools as tools_module
    import main

    canonical_registry = SimpleNamespace(list_metadata=lambda: [{"name": "main_runtime_tool"}])
    monkeypatch.setattr(tools_module, "registry", canonical_registry)
    bus = _RecordingBus()

    handled = await main._handle_runtime_state_request("runtime_state_request", bus)

    assert handled is True
    snapshot = next(event for event in bus.events if event["type"] == "tool_snapshot")
    assert snapshot["payload"] == {
        "authority": "main_runtime",
        "tools": [{"name": "main_runtime_tool"}],
    }


@pytest.mark.asyncio
async def test_main_snapshot_crosses_actual_web_event_bridge_and_api_reads_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main
    from charlie import web_server

    canonical_registry = SimpleNamespace(
        list_metadata=lambda: [{"name": "main_tool", "description": "Main", "owner": "tools"}]
    )
    bus = _RecordingBus()
    await main._publish_tool_snapshot(bus, canonical_registry)
    await _run_bridge(monkeypatch, bus.events)

    result = await web_server.get_tools()

    assert result["status"] == "ok"
    assert result["authority"] == "main_runtime"
    assert result["tools"] == [{"name": "main_tool", "description": "Main", "owner": "tools"}]


@pytest.mark.asyncio
async def test_api_tools_is_explicitly_unavailable_and_never_falls_back_to_web_local_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import charlie.tools as tools_module
    from charlie import web_server

    monkeypatch.setattr(tools_module, "registry", SimpleNamespace(list_metadata=lambda: [{"name": "web_only"}]))

    result = await web_server.get_tools()

    assert result["status"] == "unavailable"
    assert result["runtime_status"] == "unavailable"
    assert result["synchronized"] is False
    assert result["authority"] == "main_runtime"
    assert result["tools"] == []


@pytest.mark.asyncio
async def test_valid_snapshot_atomically_replaces_old_roster(monkeypatch: pytest.MonkeyPatch) -> None:
    from charlie import web_server

    assert web_server._apply_tool_snapshot_event(_tool_event([{"name": "old_tool"}])) is True
    assert web_server._apply_tool_snapshot_event(_tool_event([{"name": "new_tool"}])) is True

    result = await web_server.get_tools()

    assert [tool["name"] for tool in result["tools"]] == ["new_tool"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event",
    [
        {"type": "tool_snapshot", "version": CONTRACT_VERSION, "payload": {}},
        _tool_event([{"name": "valid"}, {"name": ""}]),
        _tool_event([{"name": "valid"}, {"name": "valid"}]),
        _tool_event([{"name": "valid", "description": {"unsafe": True}}]),
        _tool_event([{"name": "valid", "schema": {"secret": "ignored"}}], authority="web_local"),
    ],
    ids=["missing-tools", "invalid-name", "duplicate-name", "invalid-metadata", "wrong-authority"],
)
async def test_malformed_snapshot_cannot_erase_or_poison_prior_projection(
    monkeypatch: pytest.MonkeyPatch,
    event: dict[str, Any],
) -> None:
    from charlie import web_server

    valid = _tool_event([{"name": "prior_tool", "owner": "tools"}])
    assert web_server._apply_tool_snapshot_event(valid) is True
    before = await web_server.get_tools()

    await _run_bridge(monkeypatch, [event])

    assert await web_server.get_tools() == before


@pytest.mark.asyncio
async def test_missing_snapshot_does_not_appear_in_reconnect_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    from charlie import web_server

    events = web_server._initial_state_events()

    assert not any(event["type"] == "tool_snapshot" for event in events)


@pytest.mark.asyncio
async def test_valid_snapshot_is_replayed_to_late_web_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    from charlie import web_server

    event = _tool_event([{"name": "replayed_tool", "risk_class": "safe"}])
    assert web_server._apply_tool_snapshot_event(event) is True

    replay = next(item for item in web_server._initial_state_events() if item["type"] == "tool_snapshot")

    assert replay["replay"] is True
    assert replay["payload"]["tools"] == [{"name": "replayed_tool", "risk_class": "safe"}]


def _extension_payload(operation: str, request_id: str, tool_names: list[str] | None = None) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "operation": operation,
        "kind": "plugin",
        "name": "calendar",
        "source": "plugin",
        "tool_names": list(tool_names or []),
    }


class _Brain:
    def __init__(self) -> None:
        self.rebuilds = 0

    def rebuild_stable_tier(self) -> None:
        self.rebuilds += 1


@pytest.mark.asyncio
async def test_e1_successful_main_mutations_update_authoritative_roster_through_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main
    from charlie import web_server

    registry = ToolRegistry()
    plugin_manager = PluginManager()
    brain = _Brain()
    runtime_config = SimpleNamespace(plugin_allow_dirs=[])

    async def publish_roster() -> dict[str, Any]:
        bus = _RecordingBus()
        await main._publish_tool_snapshot(bus, registry)
        await _run_bridge(monkeypatch, bus.events)
        return await web_server.get_tools()

    install, _ = main.apply_extension_operation(
        _extension_payload("install", "install-1"),
        brain=brain,
        plugin_manager=plugin_manager,
        mcp_client=None,
        runtime_config=runtime_config,
        tool_registry=registry,
    )
    assert install["success"] is True
    assert "plugin_cal_list_events" in [tool["name"] for tool in (await publish_roster())["tools"]]

    disable, _ = main.apply_extension_operation(
        _extension_payload("disable", "disable-1", install["tool_names"]),
        brain=brain,
        plugin_manager=plugin_manager,
        mcp_client=None,
        runtime_config=runtime_config,
        tool_registry=registry,
    )
    assert disable["success"] is True
    assert "plugin_cal_list_events" not in [tool["name"] for tool in (await publish_roster())["tools"]]

    enable, _ = main.apply_extension_operation(
        _extension_payload("enable", "enable-1", install["tool_names"]),
        brain=brain,
        plugin_manager=plugin_manager,
        mcp_client=None,
        runtime_config=runtime_config,
        tool_registry=registry,
    )
    assert enable["success"] is True
    assert "plugin_cal_list_events" in [tool["name"] for tool in (await publish_roster())["tools"]]

    uninstall, _ = main.apply_extension_operation(
        _extension_payload("uninstall", "uninstall-1", enable["tool_names"]),
        brain=brain,
        plugin_manager=plugin_manager,
        mcp_client=None,
        runtime_config=runtime_config,
        tool_registry=registry,
    )
    assert uninstall["success"] is True
    assert "plugin_cal_list_events" not in [tool["name"] for tool in (await publish_roster())["tools"]]
    assert brain.rebuilds == 4


@pytest.mark.asyncio
async def test_failed_e1_mutation_does_not_falsely_change_projected_roster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import charlie.extensions.install as install_module
    import main
    from charlie import web_server

    registry = ToolRegistry()
    registry.register_tool("stable_tool", "Stable", {"type": "object"})(lambda: "ok")
    bus = _RecordingBus()
    await main._publish_tool_snapshot(bus, registry)
    await _run_bridge(monkeypatch, bus.events)
    before = await web_server.get_tools()

    def fail_install(*args: Any, **kwargs: Any):
        raise RuntimeError("mutation failed")

    monkeypatch.setattr(install_module, "install_extension", fail_install)
    result, _ = main.apply_extension_operation(
        _extension_payload("install", "failed-1"),
        brain=_Brain(),
        plugin_manager=PluginManager(),
        mcp_client=None,
        runtime_config=SimpleNamespace(plugin_allow_dirs=[]),
        tool_registry=registry,
    )

    assert result["success"] is False
    assert await web_server.get_tools() == before
    registry.unregister_tool("stable_tool")
