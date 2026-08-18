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
