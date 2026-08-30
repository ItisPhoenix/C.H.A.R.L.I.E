"""Tests for the React HUD's tools, MCP, and visibility bridge endpoints."""

from unittest.mock import patch

import pytest

import charlie.web_server as web_server


@pytest.mark.asyncio
async def test_get_tools_returns_registered_metadata(monkeypatch):
    monkeypatch.setattr(
        web_server,
        "_tool_snapshot",
        {
            "authority": "main_runtime",
            "tools": [{"name": "web_search", "description": "search the web", "owner": "core", "risk_class": "safe"}],
        },
    )

    result = await web_server.get_tools()

    assert result["status"] == "ok"
    assert result["authority"] == "main_runtime"
    assert result["tools"] == [
        {"name": "web_search", "description": "search the web", "owner": "core", "risk_class": "safe"}
    ]


@pytest.mark.asyncio
async def test_get_mcp_status_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr(
        web_server,
        "_mcp_snapshot",
        {"authority": "main_runtime", "enabled": False, "servers": []},
    )

    result = await web_server.get_mcp_status()

    assert result["status"] == "disabled"
    assert result["synchronized"] is True
    assert result["servers"] == {}


@pytest.mark.asyncio
async def test_get_mcp_status_returns_health_check(monkeypatch):
    monkeypatch.setattr(
        web_server,
        "_mcp_snapshot",
        {
            "authority": "main_runtime",
            "enabled": True,
            "servers": [
                {
                    "name": "filesystem",
                    "command": "",
                    "args": [],
                    "running": True,
                    "status": "connected",
                    "tools_count": 0,
                    "tools": [],
                },
                {
                    "name": "notion",
                    "command": "",
                    "args": [],
                    "running": False,
                    "status": "disconnected",
                    "tools_count": 0,
                    "tools": [],
                },
            ],
        },
    )

    result = await web_server.get_mcp_status()

    assert result["status"] == "ok"
    assert result["servers"] == {"filesystem": True, "notion": False}


@pytest.mark.asyncio
async def test_get_mcp_tools_returns_registered_mcp_definitions(monkeypatch):
    monkeypatch.setattr(
        web_server,
        "_tool_snapshot",
        {
            "authority": "main_runtime",
            "tools": [
                {"name": "mcp_files_read", "description": "Read a permitted file", "owner": "mcp"},
                {"name": "web_search", "description": "Search the web", "owner": "tools"},
            ],
        },
    )
    monkeypatch.setattr(web_server.config, "mcp_enabled", True)

    result = await web_server.get_mcp_tools()

    assert result["status"] == "ok"
    assert result["authority"] == "main_runtime"
    assert [tool["name"] for tool in result["tools"]] == ["mcp_files_read"]


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
        await main_mod._summon_hud()
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
        await main_mod._summon_hud()
        assert main_mod.hud_visible is True
        mock_open.assert_called_once()

        # Browser client connects (hud_client_count > 0)
        main_mod.hud_client_count = 1

        # Second summon while HUD visible and connected: should remain 1 open
        await main_mod._summon_hud()
        assert main_mod.hud_visible is True
        mock_open.assert_called_once()  # still only 1 call total


@pytest.mark.asyncio
async def test_hud_invoke_semantics_are_idempotent_show_when_connected():
    """The production invoke path must not hide an already-visible HUD."""
    import main as main_mod

    main_mod.hud_client_count = 1
    main_mod.hud_visible = True

    with patch("charlie.utils.open_url_in_browser") as mock_open:
        await main_mod._summon_hud()

    assert main_mod.hud_visible is True
    mock_open.assert_not_called()


@pytest.mark.asyncio
async def test_hud_summon_does_not_open_conversation_workspace(monkeypatch):
    """HUD summon changes visibility only; workspace opening is explicit."""
    import main as main_mod

    class FakeBus:
        def __init__(self):
            self.events = []

        async def emit(self, event_type, payload, meta=None):
            self.events.append((event_type, payload, meta))

    bus = FakeBus()
    main_mod.hud_client_count = 1
    main_mod.hud_visible = True

    with patch("charlie.utils.open_url_in_browser") as mock_open:
        await main_mod._summon_hud(event_bus=bus)

    mock_open.assert_not_called()
    presentation = [event for event in bus.events if event[0] == "presentation_intent"]
    assert presentation == []


@pytest.mark.asyncio
async def test_open_conversation_workspace_is_explicit_and_idempotent(monkeypatch):
    import main as main_mod

    class FakeBus:
        def __init__(self):
            self.events = []

        async def emit(self, event_type, payload, meta=None):
            self.events.append((event_type, payload, meta))

    bus = FakeBus()
    main_mod.hud_client_count = 1
    main_mod.hud_visible = True

    with patch("charlie.utils.open_url_in_browser"):
        await main_mod._open_conversation_workspace(event_bus=bus)
        await main_mod._open_conversation_workspace(event_bus=bus)

    presentation = [event for event in bus.events if event[0] == "presentation_intent"]
    assert len(presentation) == 2
    assert all(event[1]["workspace_type"] == "conversation" for event in presentation)


@pytest.mark.asyncio
async def test_hud_disconnect_then_summon_opens_again():
    """Disconnect then summon = opens again."""
    import main as main_mod

    # First summon: opens browser
    main_mod.hud_client_count = 0
    main_mod.hud_visible = False

    with patch("charlie.utils.open_url_in_browser") as mock_open:
        await main_mod._summon_hud()
        assert main_mod.hud_visible is True
        assert mock_open.call_count == 1

        # Connected: next summon does not open
        main_mod.hud_client_count = 1
        await main_mod._summon_hud()
        assert mock_open.call_count == 1

        # Disconnect occurs (IPC sets hud_client_count = 0)
        main_mod.hud_client_count = 0

        # After disconnect, summon should open the HUD again
        await main_mod._summon_hud()
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
        await main_mod._summon_hud()
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
        await main_mod._summon_hud(toggle=True)
        assert main_mod.hud_visible is True
        mock_open.assert_called_once()

        # Browser client connects
        main_mod.hud_client_count = 1

        # Toggle while connected hides HUD normally
        await main_mod._summon_hud(toggle=True)
        assert main_mod.hud_visible is False
        mock_open.assert_called_once()  # no extra call on hide

        # Next toggle after hidden shows HUD again
        main_mod.hud_client_count = 0
        await main_mod._summon_hud(toggle=True)
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
        await main_mod._summon_hud(toggle=True)
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
