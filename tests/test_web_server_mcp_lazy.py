"""Regression tests proving web never creates a local MCP runtime owner."""

import pytest

import charlie.web_server as web_server


def test_web_server_has_no_mcp_runtime_initializer_or_client() -> None:
    assert not hasattr(web_server, "mcp_client")
    assert not hasattr(web_server, "_ensure_mcp_client")
    assert not hasattr(web_server, "_ensure_mcp_client_async")


@pytest.mark.asyncio
async def test_mcp_reads_before_projection_are_unavailable_without_starting_mcp(monkeypatch):
    monkeypatch.setattr(web_server, "_mcp_snapshot", None)
    monkeypatch.setattr(web_server, "_mcp_snapshot_event", None)

    def fail_if_started(_config):
        raise AssertionError("web must not start an MCP client")

    monkeypatch.setattr("charlie.mcp_client.start_mcp", fail_if_started)

    status = await web_server.get_mcp_status()
    servers = await web_server.get_mcp_servers()

    assert status["status"] == "unavailable"
    assert status["synchronized"] is False
    assert servers["status"] == "unavailable"
    assert servers["synchronized"] is False


@pytest.mark.asyncio
async def test_read_only_ipc_mcp_projection_feeds_introspection(monkeypatch):
    from charlie.events import EventMeta, EventSource, build_event

    event = build_event(
        "mcp_snapshot",
        {
            "authority": "main_runtime",
            "enabled": True,
            "servers": [
                {
                    "name": "main-owned",
                    "command": "python",
                    "args": [],
                    "running": True,
                    "status": "connected",
                    "tools_count": 0,
                    "tools": [],
                }
            ],
        },
        meta=EventMeta(source=EventSource.RUNTIME),
    )
    assert web_server._apply_mcp_snapshot_event(event) is True

    info = web_server._runtime_introspector.get_mcp_info()

    assert info["configured_servers"] == 1
    assert info["connected_servers"] == 1
    assert info["servers"][0]["name"] == "main-owned"
