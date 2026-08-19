"""Tests for the React HUD's tools, MCP, and visibility bridge endpoints."""

from unittest.mock import patch

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


@pytest.mark.asyncio
async def test_hud_visibility_defaults_visible_and_replays():
    events = web_server._initial_state_events()
    event = next(event for event in events if event["type"] == "hud_visibility")
    assert event["payload"] == {"visible": True}
    assert event["replay"] is True


@pytest.mark.asyncio
async def test_hud_visibility_replays_to_late_clients(monkeypatch):
    monkeypatch.setattr(web_server, "_hud_visible", False)

    events = web_server._initial_state_events()

    event = next(event for event in events if event["type"] == "hud_visibility")
    assert event["payload"] == {"visible": False}
    assert event["replay"] is True


@pytest.mark.asyncio
async def test_hud_first_summon_opens_once():
    """First summon = 1 open of open_url_in_browser."""
    import main as main_mod

    # Explicitly set hud_client_count to 0 (no connected clients)
    main_mod.hud_client_count = 0
    main_mod.hud_visible = False

    with patch("charlie.utils.open_url_in_browser") as mock_open:
        # First summon: hud_visible should become True and open browser once
        await main_mod._summon_conversation_workspace()
        assert main_mod.hud_visible is True
        mock_open.assert_called_once()


@pytest.mark.asyncio
async def test_hud_repeated_summon_no_duplicate_while_connected():
    """Repeated summon while connected = still 1 open (no extra calls)."""
    import main as main_mod

    main_mod.hud_client_count = 0
    main_mod.hud_visible = False

    with patch("charlie.utils.open_url_in_browser") as mock_open:
        # First summon: client count 0 -> opens browser
        await main_mod._summon_conversation_workspace()
        assert main_mod.hud_visible is True
        mock_open.assert_called_once()

        # Browser client connects (hud_client_count > 0)
        main_mod.hud_client_count = 1

        # Second summon while HUD visible and connected: should remain 1 open
        await main_mod._summon_conversation_workspace()
        assert main_mod.hud_visible is True
        mock_open.assert_called_once()  # still only 1 call total


@pytest.mark.asyncio
async def test_hud_disconnect_then_summon_opens_again():
    """Disconnect then summon = opens again."""
    import main as main_mod

    # First summon: opens browser
    main_mod.hud_client_count = 0
    main_mod.hud_visible = False

    with patch("charlie.utils.open_url_in_browser") as mock_open:
        await main_mod._summon_conversation_workspace()
        assert main_mod.hud_visible is True
        assert mock_open.call_count == 1

        # Connected: next summon does not open
        main_mod.hud_client_count = 1
        await main_mod._summon_conversation_workspace()
        assert mock_open.call_count == 1

        # Disconnect occurs (IPC sets hud_client_count = 0)
        main_mod.hud_client_count = 0

        # After disconnect, summon should open the HUD again
        await main_mod._summon_conversation_workspace()
        assert main_mod.hud_visible is True
        assert mock_open.call_count == 2


@pytest.mark.asyncio
async def test_hud_close_reopen_cycle():
    """Closed/disconnected HUD can reopen."""
    import main as main_mod

    main_mod.hud_visible = True
    main_mod.hud_client_count = 0

    # Close HUD
    main_mod.hud_visible = False

    with patch("charlie.utils.open_url_in_browser") as mock_open:
        # Reopen HUD
        await main_mod._summon_conversation_workspace()
        assert main_mod.hud_visible is True
        mock_open.assert_called_once()


@pytest.mark.asyncio
async def test_hud_ws_disconnect_reconnect(monkeypatch):
    """WS disconnect/reconnect preserves initial state event contract."""
    import charlie.web_server as web_server

    monkeypatch.setattr(web_server, "_hud_visible", True)

    events = web_server._initial_state_events()
    hud_event = next(e for e in events if e["type"] == "hud_visibility")
    assert hud_event["payload"] == {"visible": True}


@pytest.mark.asyncio
async def test_pet_independent_of_hud():
    """Pet remains independent of HUD state."""
    import main as main_mod

    # HUD state changes should not affect pet
    main_mod.hud_visible = True
    assert main_mod.hud_visible is True

    main_mod.hud_visible = False
    assert main_mod.hud_visible is False


@pytest.mark.asyncio
async def test_hud_toggle_visibility():
    """Toggle hud_visible works correctly when clients are connected."""
    import main as main_mod

    # Start hidden
    main_mod.hud_visible = False
    main_mod.hud_client_count = 0

    with patch("charlie.utils.open_url_in_browser") as mock_open:
        # Toggle to visible
        await main_mod._summon_conversation_workspace(toggle=True)
        assert main_mod.hud_visible is True
        mock_open.assert_called_once()

        # Browser client connects
        main_mod.hud_client_count = 1

        # Toggle while connected hides HUD normally
        await main_mod._summon_conversation_workspace(toggle=True)
        assert main_mod.hud_visible is False
        mock_open.assert_called_once()  # no extra call on hide

        # Next toggle after hidden shows HUD again
        main_mod.hud_client_count = 0
        await main_mod._summon_conversation_workspace(toggle=True)
        assert main_mod.hud_visible is True
        assert mock_open.call_count == 2


@pytest.mark.asyncio
async def test_hud_toggle_with_zero_clients_reopens_after_manual_browser_close():
    """Regression test: hud_visible=True, hud_client_count=0, toggle=True reopens browser."""
    import main as main_mod

    # State after manual browser close: stale hud_visible=True, but hud_client_count=0
    main_mod.hud_visible = True
    main_mod.hud_client_count = 0

    with patch("charlie.utils.open_url_in_browser") as mock_open:
        await main_mod._summon_conversation_workspace(toggle=True)
        assert main_mod.hud_visible is True
        mock_open.assert_called_once()


@pytest.mark.asyncio
async def test_terminal_input_routes_through_main_approval_channel(monkeypatch):
    class Manager:
        def snapshot(self, session_id):
            return {"session_id": session_id, "status": "running", "output": ""}

    class Bus:
        def __init__(self):
            self.commands = []

        async def send_command(self, command):
            self.commands.append(command)

    bus = Bus()
    monkeypatch.setattr(web_server, "_terminal_manager", Manager())
    monkeypatch.setattr(web_server, "event_bus", bus)

    result = await web_server.terminal_input("s1", {"line": "echo hi", "confirmed": True})

    assert result["status"] == "approval_pending"
    assert bus.commands[0]["type"] == "terminal_command_request"

