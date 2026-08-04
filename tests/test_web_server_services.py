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
    async def test_returns_full_registry_not_just_mcp_prefixed(self, monkeypatch, _fake_tool):
        # Isolate from ambient MCP_ENABLED -- must not spawn a real MCP
        # subprocess (and leak its registrations into the shared registry
        # for later tests) just because the local .env happens to enable it.
        monkeypatch.setattr(web_server.config, "mcp_enabled", False)

        result = await web_server.get_registered_tools()

        names = {t["function"]["name"] for t in result["tools"]}
        assert "test_fake_tool" in names


@pytest.fixture
def _fake_mcp_tool():
    registry.register_tool(
        name="mcp_filesystem_read_file",
        description="test-only mcp tool",
        schema={"type": "object", "properties": {}},
    )(lambda **_: "ok")
    yield
    registry.unregister_tool("mcp_filesystem_read_file")


@pytest.mark.asyncio
class TestMcpStatusAndTools:
    """get_tool_definitions() returns OpenAI-format {"function": {"name": ...}}
    dicts, not a top-level "name" key -- these endpoints used to check
    d.get("name") directly, which always missed, so /api/mcp/status reported
    connected: false and /api/mcp/tools returned [] even with MCP fully
    connected and tools registered."""

    async def test_status_reports_connected_when_mcp_tools_registered(self, monkeypatch, _fake_mcp_tool):
        monkeypatch.setattr(web_server.config, "mcp_enabled", True)
        monkeypatch.setattr(web_server, "_ensure_mcp_client_async", lambda: _noop())

        result = await web_server.get_mcp_status()

        assert result == {"enabled": True, "connected": True}

    async def test_tools_filters_to_mcp_prefixed_only(self, monkeypatch, _fake_tool, _fake_mcp_tool):
        monkeypatch.setattr(web_server.config, "mcp_enabled", True)
        monkeypatch.setattr(web_server, "_ensure_mcp_client_async", lambda: _noop())

        result = await web_server.get_mcp_tools()

        names = {t["function"]["name"] for t in result["tools"]}
        assert names == {"mcp_filesystem_read_file"}


async def _noop():
    return None


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


@pytest.mark.asyncio
class TestSessionChatFallback:
    """The REST /chat fallback used to persist the user turn AND forward it
    as a `chat` command -- main.py's _process() also persists on receiving
    that command, so every REST-originated message got stored twice."""

    async def test_does_not_persist_directly_only_forwards(self, monkeypatch):
        appended = []

        class _FakeStore:
            def append(self, *args, **kwargs):
                appended.append((args, kwargs))

        monkeypatch.setattr(web_server, "_get_store", lambda: _FakeStore())

        sent_commands = []

        class _FakeEventBus:
            async def send_command(self, cmd):
                sent_commands.append(cmd)

        monkeypatch.setattr(web_server, "event_bus", _FakeEventBus())

        result = await web_server.session_chat("sess_1", {"text": "hello"})

        assert result == {"status": "ok"}
        assert appended == []
        assert sent_commands == [{"type": "chat", "session_id": "sess_1", "text": "hello"}]
