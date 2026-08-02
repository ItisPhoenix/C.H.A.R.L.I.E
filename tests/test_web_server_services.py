"""Tests for /api/tools and /api/services/status -- both used to lie:
/api/mcp/tools under-reported the registry (MCP-prefixed subset only) and
/api/services/status hardcoded every service as "online" unconditionally.
These endpoints now report real state."""

import time

import pytest

import charlie.web_server as web_server
from charlie.tools import registry


@pytest.fixture
def _fake_tool():
    registry.register_tool(
        name="test_fake_tool",
        description="test-only tool",
        schema={"type": "object", "properties": {}},
    )(lambda **_: "ok")
    yield
    registry.unregister_tool("test_fake_tool")


@pytest.mark.asyncio
class TestGetRegisteredTools:
    async def test_returns_full_registry_not_just_mcp_prefixed(self, _fake_tool):
        result = await web_server.get_registered_tools()

        names = {t["function"]["name"] for t in result["tools"]}
        assert "test_fake_tool" in names


@pytest.mark.asyncio
class TestServicesStatus:
    async def test_voice_offline_when_no_recent_system_status(self, monkeypatch):
        monkeypatch.setattr(web_server, "_system_status_received_at", 0.0)

        result = await web_server.get_services_status()

        by_name = {s["name"]: s for s in result["services"]}
        assert by_name["Voice Pipeline Engine"]["status"] == "offline"
        assert by_name["Whisper ASR Worker"]["status"] == "offline"

    async def test_voice_online_when_system_status_recent(self, monkeypatch):
        monkeypatch.setattr(web_server, "_system_status_received_at", time.time())

        result = await web_server.get_services_status()

        by_name = {s["name"]: s for s in result["services"]}
        assert by_name["Voice Pipeline Engine"]["status"] == "online"
        assert by_name["Whisper ASR Worker"]["status"] == "online"

    async def test_web_server_always_online(self):
        result = await web_server.get_services_status()

        by_name = {s["name"]: s for s in result["services"]}
        assert by_name["FastAPI Web Server"]["status"] == "online"

    async def test_eventbus_offline_when_none(self, monkeypatch):
        monkeypatch.setattr(web_server, "event_bus", None)

        result = await web_server.get_services_status()

        by_name = {s["name"]: s for s in result["services"]}
        assert by_name["ZeroMQ EventBus Bridge"]["status"] == "offline"

    async def test_eventbus_online_when_set(self, monkeypatch):
        monkeypatch.setattr(web_server, "event_bus", object())

        result = await web_server.get_services_status()

        by_name = {s["name"]: s for s in result["services"]}
        assert by_name["ZeroMQ EventBus Bridge"]["status"] == "online"

    async def test_session_store_offline_when_query_raises(self, monkeypatch):
        def _broken_store():
            raise RuntimeError("db locked")

        monkeypatch.setattr(web_server, "_get_store", _broken_store)

        result = await web_server.get_services_status()

        by_name = {s["name"]: s for s in result["services"]}
        assert by_name["SQLite SessionStore"]["status"] == "offline"

    async def test_memory_store_online_iff_path_exists(self, monkeypatch, tmp_path):
        existing = tmp_path / "chroma"
        existing.mkdir()
        monkeypatch.setattr(web_server.config, "memory_db_path", str(existing))

        result = await web_server.get_services_status()
        by_name = {s["name"]: s for s in result["services"]}
        assert by_name["ChromaDB MemoryStore"]["status"] == "online"

        monkeypatch.setattr(web_server.config, "memory_db_path", str(tmp_path / "nope"))

        result = await web_server.get_services_status()
        by_name = {s["name"]: s for s in result["services"]}
        assert by_name["ChromaDB MemoryStore"]["status"] == "offline"
