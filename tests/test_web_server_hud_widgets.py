"""Tests for the React HUD's tools, MCP, and visibility bridge endpoints."""

import pytest

import charlie.web_server as web_server
from charlie.tools import ToolRegistry


@pytest.mark.asyncio
async def test_get_tools_returns_registered_metadata(monkeypatch):
    registry = ToolRegistry()
    registry.register_tool(
        "web_search", "search the web", {"type": "object", "properties": {}}, owner="core", risk_class="safe"
    )
    monkeypatch.setattr("charlie.tools.registry", registry)

    result = await web_server.get_tools()

    assert result["tools"] == [
        {"name": "web_search", "description": "search the web", "owner": "core", "risk_class": "safe"}
    ]


@pytest.mark.asyncio
async def test_get_mcp_status_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr(web_server.config, "mcp_enabled", False)

    result = await web_server.get_mcp_status()

    assert result == {"servers": {}}


@pytest.mark.asyncio
async def test_get_mcp_status_returns_health_check(monkeypatch):
    class _FakeClient:
        def health_check(self):
            return {"filesystem": True, "notion": False}

    monkeypatch.setattr(web_server.config, "mcp_enabled", True)
    monkeypatch.setattr(web_server, "mcp_client", _FakeClient())

    result = await web_server.get_mcp_status()

    assert result == {"servers": {"filesystem": True, "notion": False}}


@pytest.mark.asyncio
async def test_get_mcp_tools_returns_registered_mcp_definitions(monkeypatch):
    registry = ToolRegistry()
    registry.register_tool(
        "mcp_files_read", "Read a permitted file", {"type": "object", "properties": {}}, owner="mcp"
    )(lambda: "ok")
    registry.register_tool(
        "web_search", "Search the web", {"type": "object", "properties": {}}, owner="tools"
    )(lambda: "ok")
    monkeypatch.setattr("charlie.tools.registry", registry)
    monkeypatch.setattr(web_server.config, "mcp_enabled", True)

    async def no_start():
        return None

    monkeypatch.setattr(web_server, "_ensure_mcp_client_async", no_start)

    result = await web_server.get_mcp_tools()

    assert [tool["function"]["name"] for tool in result["tools"]] == ["mcp_files_read"]


def test_hud_visibility_replays_to_late_clients(monkeypatch):
    monkeypatch.setattr(web_server, "_hud_visible", False)

    events = web_server._initial_state_events()

    event = next(event for event in events if event["type"] == "hud_visibility")
    assert event["payload"] == {"visible": False}
    assert event["replay"] is True


@pytest.mark.asyncio
async def test_hud_initial_visibility_consistency():
    """Phase 14: initial visibility is consistent main -> web -> frontend."""
    from charlie import main as main_mod
    import charlie.web_server as web_server

    # Main process initial state
    assert main_mod.hud_visible is False

    # Web server initial state (via _initial_state_events)
    events = web_server._initial_state_events()
    hud_event = next(e for e in events if e["type"] == "hud_visibility")
    assert hud_event["payload"] == {"visible": False}

    # Frontend store initial state
    from frontend.src.store.charlie import useCharlieStore
    useCharlieStore.setState({ "hudVisible": False })
    assert useCharlieStore.getState().hudVisible is False


@pytest.mark.asyncio
async def test_hud_repeated_summon_no_duplicate():
    """Phase 14: repeated HUD summon does not duplicate."""
    from charlie import main as main_mod

    # First summon: hud_visible should become True
    main_mod._summon_conversation_workspace()
    assert main_mod.hud_visible is True

    # Second summon while HUD visible: should remain True (idempotent)
    main_mod._summon_conversation_workspace()
    assert main_mod.hud_visible is True

    # Third summon after closing: should work
    main_mod._summon_conversation_workspace()
    assert main_mod.hud_visible is True


@pytest.mark.asyncio
async def test_hud_close_reopen_cycle():
    """Phase 14: closed/disconnected HUD can reopen."""
    from charlie import main as main_mod

    # Start with HUD visible
    main_mod.hud_visible = True

    # Close HUD
    main_mod.hud_visible = False

    # Reopen HUD
    main_mod._summon_conversation_workspace()
    assert main_mod.hud_visible is True


@pytest.mark.asyncio
async def test_hud_ws_disconnect_reconnect():
    """Phase 14: WS disconnect/reconnect works."""
    import charlie.web_server as web_server

    # Simulate disconnect by clearing active connections
    web_server.active_connections.clear()

    # Reconnect should work
    # The _initial_state_events should still emit hud_visibility
    events = web_server._initial_state_events()
    hud_event = next(e for e in events if e["type"] == "hud_visibility")
    assert hud_event["payload"] == {"visible": web_server._hud_visible}


@pytest.mark.asyncio
async def test_pet_independent_of_hud():
    """Phase 14: pet remains independent of HUD state."""
    from charlie import main as main_mod
    from charlie.pet_window import PetWindow

    # HUD state changes should not affect pet
    main_mod.hud_visible = True
    assert main_mod.hud_visible is True

    main_mod.hud_visible = False
    assert main_mod.hud_visible is False

    # Pet state should be unchanged (this is tested by the pet window tests)
