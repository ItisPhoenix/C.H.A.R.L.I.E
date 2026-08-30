"""E1 regressions: main owns extension activation; web only mirrors ACKs."""

from __future__ import annotations

import ast
import asyncio
import os
import textwrap
from types import SimpleNamespace
from typing import Any

import pytest

from charlie.events import CONTRACT_VERSION, EventMeta, EventSource
from charlie.extensions import ExtensionManager, InstalledExtension, build_skill_card

MAIN_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
MAIN_SOURCE = open(MAIN_PATH, "r", encoding="utf-8").read()

_SKILL_TEXT = """---
name: demo-skill
description: demo
scripts:
  - scripts/run.py
---
Use the demo skill.
"""


class _StopCommandLoop(BaseException):
    pass


class _BridgeBus:
    """Small IPC double that drives web_server's real event bridge."""

    _STOP = object()

    def __init__(self) -> None:
        self.commands: list[dict[str, Any]] = []
        self.events: asyncio.Queue[Any] = asyncio.Queue()
        self.command_seen = asyncio.Event()

    async def send_command(self, command: dict[str, Any]) -> None:
        self.commands.append(command)
        self.command_seen.set()

    async def consume_events(self, callback) -> None:
        while True:
            event = await self.events.get()
            if event is self._STOP:
                return
            await callback(event)


def _result_event(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "extension_operation_result",
        "version": CONTRACT_VERSION,
        "id": "extension-result-event",
        "timestamp": "2026-08-30T00:00:00+00:00",
        "source": EventSource.BRAIN.value,
        "replay": False,
        "payload": payload,
    }


def _extension(name: str = "calendar", *, enabled: bool = True) -> InstalledExtension:
    card = build_skill_card(name, "plugin", ["plugin_cal_list_events"], name)
    return InstalledExtension(
        name=name,
        kind="plugin",
        source="plugin",
        card=card,
        enabled=enabled,
        tool_names=["plugin_cal_list_events"],
    )


@pytest.fixture(autouse=True)
def _fresh_web_extension_state(monkeypatch: pytest.MonkeyPatch):
    from charlie import web_server

    monkeypatch.setattr(web_server, "_extension_manager", ExtensionManager())
    monkeypatch.setattr(web_server, "event_bus", None)
    monkeypatch.setattr(web_server, "active_connections", set())
    monkeypatch.setattr(web_server, "_pending_extension_operations", {}, raising=False)
    monkeypatch.setattr(web_server, "EXTENSION_OPERATION_TIMEOUT_SECONDS", 0.05, raising=False)
    yield


async def _stop_bridge(bus: _BridgeBus, bridge: asyncio.Task) -> None:
    await bus.events.put(bus._STOP)
    await bridge


async def _start_bridge(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, _BridgeBus, asyncio.Task]:
    from charlie import web_server

    bus = _BridgeBus()
    monkeypatch.setattr(web_server, "event_bus", bus)
    bridge = asyncio.create_task(web_server._event_bridge())
    return web_server, bus, bridge


@pytest.mark.asyncio
async def test_proposal_creation_requires_no_main_authority() -> None:
    from charlie import web_server

    result = await web_server.propose_extension({"kind": "skill", "name": "demo-skill", "raw_text": _SKILL_TEXT})

    assert result["status"] == "ok"
    assert result["pending_id"]


@pytest.mark.asyncio
async def test_approved_install_waits_for_main_before_mutating_web_mirror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    web_server, bus, bridge = await _start_bridge(monkeypatch)
    proposal = await web_server.propose_extension({"kind": "plugin", "name": "calendar"})
    operation = asyncio.create_task(
        web_server.confirm_extension(
            {"pending_id": proposal["pending_id"], "approved": True, "kind": "plugin"}
        )
    )

    await bus.command_seen.wait()
    assert not operation.done()
    assert await web_server.list_extensions() == {"extensions": []}
    command = bus.commands[0]
    assert command["type"] == "extension_operation"
    assert command["payload"]["operation"] == "install"
    assert command["payload"]["request_id"]

    await bus.events.put(
        _result_event(
            {
                "request_id": command["payload"]["request_id"],
                "operation": "install",
                "kind": "plugin",
                "name": "calendar",
                "success": True,
                "tool_names": ["main_calendar_tool"],
            }
        )
    )
    result = await operation
    await _stop_bridge(bus, bridge)

    assert result == {
        "status": "ok",
        "installed": True,
        "request_id": command["payload"]["request_id"],
        "tool_names": ["main_calendar_tool"],
    }
    assert (await web_server.list_extensions())["extensions"][0]["tool_names"] == ["main_calendar_tool"]


@pytest.mark.asyncio
async def test_failed_main_install_leaves_web_extension_uninstalled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    web_server, bus, bridge = await _start_bridge(monkeypatch)
    proposal = await web_server.propose_extension({"kind": "plugin", "name": "calendar"})
    operation = asyncio.create_task(
        web_server.confirm_extension(
            {"pending_id": proposal["pending_id"], "approved": True, "kind": "plugin"}
        )
    )
    await bus.command_seen.wait()
    request_id = bus.commands[0]["payload"]["request_id"]
    await bus.events.put(
        _result_event(
            {
                "request_id": request_id,
                "operation": "install",
                "kind": "plugin",
                "name": "calendar",
                "success": False,
                "tool_names": [],
                "error": "main plugin owner rejected install",
            }
        )
    )
    result = await operation
    await _stop_bridge(bus, bridge)

    assert result["status"] == "error"
    assert result["request_id"] == request_id
    assert result["runtime_status"] == "failed"
    assert (await web_server.list_extensions())["extensions"] == []


@pytest.mark.asyncio
async def test_unavailable_main_cannot_claim_install_success() -> None:
    from charlie import web_server

    proposal = await web_server.propose_extension({"kind": "plugin", "name": "calendar"})

    result = await web_server.confirm_extension(
        {"pending_id": proposal["pending_id"], "approved": True, "kind": "plugin"}
    )

    assert result["status"] == "error"
    assert result["runtime_status"] == "unavailable"
    assert (await web_server.list_extensions())["extensions"] == []


@pytest.mark.asyncio
async def test_timeout_main_cannot_claim_install_success(monkeypatch: pytest.MonkeyPatch) -> None:
    from charlie import web_server

    monkeypatch.setattr(web_server, "EXTENSION_OPERATION_TIMEOUT_SECONDS", 0.001)
    bus = _BridgeBus()
    monkeypatch.setattr(web_server, "event_bus", bus)
    proposal = await web_server.propose_extension({"kind": "plugin", "name": "calendar"})

    result = await web_server.confirm_extension(
        {"pending_id": proposal["pending_id"], "approved": True, "kind": "plugin"}
    )

    assert result["status"] == "error"
    assert result["runtime_status"] == "timeout"
    assert (await web_server.list_extensions())["extensions"] == []


@pytest.mark.asyncio
async def test_enable_commits_web_state_only_after_main_success(monkeypatch: pytest.MonkeyPatch) -> None:
    web_server, bus, bridge = await _start_bridge(monkeypatch)
    web_server._extension_manager.record(_extension(enabled=False))
    operation = asyncio.create_task(web_server.enable_extension("calendar"))
    await bus.command_seen.wait()
    assert not operation.done()
    assert (await web_server.list_extensions())["extensions"][0]["enabled"] is False
    command = bus.commands[0]
    await bus.events.put(
        _result_event(
            {
                "request_id": command["payload"]["request_id"],
                "operation": "enable",
                "kind": "plugin",
                "name": "calendar",
                "success": True,
                "tool_names": ["main_calendar_tool"],
            }
        )
    )
    result = await operation
    await _stop_bridge(bus, bridge)

    assert result["status"] == "ok"
    assert (await web_server.list_extensions())["extensions"][0]["enabled"] is True
    assert (await web_server.list_extensions())["extensions"][0]["tool_names"] == ["main_calendar_tool"]


@pytest.mark.asyncio
async def test_failed_enable_preserves_disabled_web_state(monkeypatch: pytest.MonkeyPatch) -> None:
    web_server, bus, bridge = await _start_bridge(monkeypatch)
    web_server._extension_manager.record(_extension(enabled=False))
    operation = asyncio.create_task(web_server.enable_extension("calendar"))
    await bus.command_seen.wait()
    payload = bus.commands[0]["payload"]
    await bus.events.put(
        _result_event(
            {
                "request_id": payload["request_id"],
                "operation": "enable",
                "kind": "plugin",
                "name": "calendar",
                "success": False,
                "tool_names": [],
                "error": "enable failed",
            }
        )
    )
    result = await operation
    await _stop_bridge(bus, bridge)

    assert result["status"] == "error"
    assert (await web_server.list_extensions())["extensions"][0]["enabled"] is False


@pytest.mark.asyncio
async def test_disable_commits_web_state_only_after_main_success(monkeypatch: pytest.MonkeyPatch) -> None:
    web_server, bus, bridge = await _start_bridge(monkeypatch)
    web_server._extension_manager.record(_extension(enabled=True))
    operation = asyncio.create_task(web_server.disable_extension("calendar"))
    await bus.command_seen.wait()
    assert not operation.done()
    assert (await web_server.list_extensions())["extensions"][0]["enabled"] is True
    command = bus.commands[0]
    await bus.events.put(
        _result_event(
            {
                "request_id": command["payload"]["request_id"],
                "operation": "disable",
                "kind": "plugin",
                "name": "calendar",
                "success": True,
                "tool_names": ["main_calendar_tool"],
            }
        )
    )
    result = await operation
    await _stop_bridge(bus, bridge)

    assert result["status"] == "ok"
    assert (await web_server.list_extensions())["extensions"][0]["enabled"] is False


@pytest.mark.asyncio
async def test_failed_disable_preserves_enabled_web_state(monkeypatch: pytest.MonkeyPatch) -> None:
    web_server, bus, bridge = await _start_bridge(monkeypatch)
    web_server._extension_manager.record(_extension(enabled=True))
    operation = asyncio.create_task(web_server.disable_extension("calendar"))
    await bus.command_seen.wait()
    payload = bus.commands[0]["payload"]
    await bus.events.put(
        _result_event(
            {
                "request_id": payload["request_id"],
                "operation": "disable",
                "kind": "plugin",
                "name": "calendar",
                "success": False,
                "tool_names": [],
                "error": "disable failed",
            }
        )
    )
    result = await operation
    await _stop_bridge(bus, bridge)

    assert result["status"] == "error"
    assert (await web_server.list_extensions())["extensions"][0]["enabled"] is True


@pytest.mark.asyncio
async def test_uninstall_commits_web_removal_only_after_main_success(monkeypatch: pytest.MonkeyPatch) -> None:
    web_server, bus, bridge = await _start_bridge(monkeypatch)
    web_server._extension_manager.record(_extension(enabled=True))
    operation = asyncio.create_task(web_server.uninstall_extension("calendar"))
    await bus.command_seen.wait()
    assert not operation.done()
    assert len((await web_server.list_extensions())["extensions"]) == 1
    command = bus.commands[0]
    assert command["payload"]["operation"] == "uninstall"
    await bus.events.put(
        _result_event(
            {
                "request_id": command["payload"]["request_id"],
                "operation": "uninstall",
                "kind": "plugin",
                "name": "calendar",
                "success": True,
                "tool_names": [],
            }
        )
    )
    result = await operation
    await _stop_bridge(bus, bridge)

    assert result["status"] == "ok"
    assert (await web_server.list_extensions())["extensions"] == []


@pytest.mark.asyncio
async def test_failed_uninstall_preserves_installed_web_state(monkeypatch: pytest.MonkeyPatch) -> None:
    web_server, bus, bridge = await _start_bridge(monkeypatch)
    web_server._extension_manager.record(_extension(enabled=True))
    operation = asyncio.create_task(web_server.uninstall_extension("calendar"))
    await bus.command_seen.wait()
    payload = bus.commands[0]["payload"]
    await bus.events.put(
        _result_event(
            {
                "request_id": payload["request_id"],
                "operation": "uninstall",
                "kind": "plugin",
                "name": "calendar",
                "success": False,
                "tool_names": [],
                "error": "uninstall failed",
            }
        )
    )
    result = await operation
    await _stop_bridge(bus, bridge)

    assert result["status"] == "error"
    assert len((await web_server.list_extensions())["extensions"]) == 1


@pytest.mark.asyncio
async def test_result_request_id_correlation_is_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    web_server, bus, bridge = await _start_bridge(monkeypatch)
    proposal = await web_server.propose_extension({"kind": "plugin", "name": "calendar"})
    operation = asyncio.create_task(
        web_server.confirm_extension(
            {"pending_id": proposal["pending_id"], "approved": True, "kind": "plugin"}
        )
    )
    await bus.command_seen.wait()
    payload = bus.commands[0]["payload"]
    await bus.events.put(
        _result_event(
            {
                "request_id": "another-request",
                "operation": "install",
                "kind": "plugin",
                "name": "calendar",
                "success": True,
                "tool_names": ["wrong"],
            }
        )
    )
    await asyncio.sleep(0)
    assert not operation.done()
    await bus.events.put(
        _result_event(
            {
                "request_id": payload["request_id"],
                "operation": "install",
                "kind": "plugin",
                "name": "calendar",
                "success": True,
                "tool_names": ["right"],
            }
        )
    )
    result = await operation
    await _stop_bridge(bus, bridge)

    assert result["tool_names"] == ["right"]


def _function_source(name: str) -> str:
    module = ast.parse(MAIN_SOURCE)
    matches = [
        node
        for node in ast.walk(module)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]
    assert matches, f"{name} missing from main.py"
    return ast.get_source_segment(MAIN_SOURCE, matches[0]) or ""


class _NullLogger:
    def debug(self, *args: Any, **kwargs: Any) -> None:
        pass


@pytest.mark.asyncio
async def test_main_command_loop_calls_authoritative_mutation_seam() -> None:
    source = _function_source("consume_web_commands")
    calls: list[dict[str, Any]] = []

    def apply(payload: dict[str, Any], **kwargs: Any):
        calls.append({"payload": payload, "kwargs": kwargs})
        return (
            {
                "request_id": payload["request_id"],
                "operation": payload["operation"],
                "kind": payload["kind"],
                "name": payload["name"],
                "success": True,
                "tool_names": ["main_tool"],
            },
            kwargs["mcp_client"],
        )

    class CommandBus:
        def __init__(self) -> None:
            self.seen = False
            self.events: list[tuple[Any, Any, Any]] = []

        async def next_command(self):
            if self.seen:
                raise _StopCommandLoop
            self.seen = True
            return {
                "type": "extension_operation",
                "payload": {
                    "request_id": "req-1",
                    "operation": "install",
                    "kind": "plugin",
                    "name": "calendar",
                },
            }

        async def emit(self, event_type, payload, meta=None):
            self.events.append((event_type, payload, meta))

    async def publish_tool_snapshot(bus, tool_registry):
        bus.events.append(("tool_snapshot", {"registry": tool_registry}, None))

    namespace = {
        "asyncio": asyncio,
        "logger": _NullLogger(),
        "apply_extension_operation": apply,
        "_publish_tool_snapshot": publish_tool_snapshot,
        "EventMeta": EventMeta,
        "EventSource": EventSource,
    }
    wrapper = (
        "def _wrapper():\n"
        "    current_web_session_id = 'session'\n"
        "    _voice_fallback_session_id = 'fallback'\n"
        "    voice = None\n"
        "    mcp_client = None\n"
        "    plugin_manager = object()\n"
        "    config = SimpleNamespace(plugin_allow_dirs=[])\n"
        + textwrap.indent(source, "    ")
        + "\n    return consume_web_commands\n"
    )
    namespace["SimpleNamespace"] = SimpleNamespace
    exec(compile(wrapper, "<main.consume_web_commands>", "exec"), namespace)
    consume = namespace["_wrapper"]()
    bus = CommandBus()

    with pytest.raises(_StopCommandLoop):
        await consume(bus, object())

    assert len(calls) == 1
    assert calls[0]["payload"]["request_id"] == "req-1"
    assert [event[0] for event in bus.events] == ["tool_snapshot", "extension_operation_result"]
    assert bus.events[1][1]["success"] is True


def test_main_exposes_one_authoritative_extension_mutation_seam() -> None:
    import main

    assert callable(getattr(main, "apply_extension_operation", None))
    source = _function_source("consume_web_commands")
    assert "apply_extension_operation(" in source
    assert "extension_installed" not in source
    assert "extension_enabled" not in source
    assert "extension_disabled" not in source
    assert "extension_uninstalled" not in source


def test_main_skill_transition_updates_brain_and_rebuilds_stable_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    import charlie.extensions.install as install_module
    import main

    calls: dict[str, Any] = {}

    def fake_install(*args: Any, **kwargs: Any):
        calls["args"] = args
        calls["kwargs"] = kwargs
        return ["main_skill_tool"], kwargs["mcp_client"]

    monkeypatch.setattr(install_module, "install_extension", fake_install)

    class Brain:
        def __init__(self) -> None:
            self.skill_blocks: dict[str, str] = {}
            self.rebuilds = 0

        def add_installed_skill_block(self, name: str, block: str) -> None:
            self.skill_blocks[name] = block

        def rebuild_stable_tier(self) -> None:
            self.rebuilds += 1

    brain = Brain()
    registry = object()
    payload = {
        "request_id": "skill-req",
        "operation": "install",
        "kind": "skill",
        "name": "demo-skill",
        "source": "skill-source",
        "raw_text": _SKILL_TEXT,
    }

    result, returned_mcp = main.apply_extension_operation(
        payload,
        brain=brain,
        plugin_manager=object(),
        mcp_client=None,
        runtime_config=SimpleNamespace(plugin_allow_dirs=[]),
        tool_registry=registry,
    )

    assert result["success"] is True
    assert result["tool_names"] == ["main_skill_tool"]
    assert returned_mcp is None
    assert "demo-skill" in brain.skill_blocks
    assert brain.rebuilds == 1
    assert calls["kwargs"]["registry"] is registry


def test_main_failure_becomes_correlated_failure_result(monkeypatch: pytest.MonkeyPatch) -> None:
    import charlie.extensions.install as install_module
    import main

    def fail_install(*args: Any, **kwargs: Any):
        raise RuntimeError("main owner unavailable")

    monkeypatch.setattr(install_module, "install_extension", fail_install)

    class Brain:
        def rebuild_stable_tier(self) -> None:
            raise AssertionError("failed transition must not rebuild")

    result, returned_mcp = main.apply_extension_operation(
        {"request_id": "failed-req", "operation": "install", "kind": "plugin", "name": "calendar"},
        brain=Brain(),
        plugin_manager=object(),
        mcp_client=None,
        runtime_config=SimpleNamespace(plugin_allow_dirs=[]),
        tool_registry=object(),
    )

    assert result["request_id"] == "failed-req"
    assert result["success"] is False
    assert result["tool_names"] == []
    assert "main owner unavailable" in result["error"]
    assert returned_mcp is None
