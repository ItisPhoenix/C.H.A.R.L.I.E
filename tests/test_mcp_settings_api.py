import pytest

import charlie.web_server as web_server
from charlie.mcp_client import MCPClient, MCPServerConfig, MCPTool
from charlie.tools import registry


class _MockManagedServer:
    def __init__(self, config):
        self.config = config
        self._running = False

    def start(self):
        self._running = True

    def stop(self):
        self._running = False

    def is_running(self):
        return self._running

    def list_tools(self):
        return [
            MCPTool(
                name="test_tool",
                description="A test tool",
                input_schema={"type": "object"},
                server_name=self.config.name,
            )
        ]


def test_mcp_client_server_management_methods():
    client = MCPClient()
    cfg = MCPServerConfig(name="mock_srv", command="echo", args=["hello"])
    client._servers["mock_srv"] = _MockManagedServer(cfg)

    detailed = client.list_servers_detailed()
    assert len(detailed) == 1
    assert detailed[0]["name"] == "mock_srv"
    assert detailed[0]["status"] == "disconnected"
    assert detailed[0]["tools"] == []

    client.enable_server(registry, "mock_srv")
    detailed = client.list_servers_detailed()
    assert detailed[0]["status"] == "connected"
    assert len(detailed[0]["tools"]) == 1
    assert detailed[0]["tools"][0]["name"] == "test_tool"

    assert client.restart_server(registry, "mock_srv") is True
    assert client.list_servers_detailed()[0]["status"] == "connected"

    assert client.disable_server(registry, "mock_srv") is True
    assert client.list_servers_detailed()[0]["status"] == "disconnected"

    assert client.remove_server(registry, "mock_srv") is True
    assert len(client.list_servers_detailed()) == 0


@pytest.mark.asyncio
async def test_web_server_mcp_endpoints_read_main_projection_only(monkeypatch):
    monkeypatch.setattr(
        web_server,
        "_mcp_snapshot",
        {
            "authority": "main_runtime",
            "enabled": True,
            "servers": [
                {
                    "name": "test_srv",
                    "command": "echo",
                    "args": ["hi"],
                    "running": True,
                    "status": "connected",
                    "tools_count": 1,
                    "tools": [{"name": "test_tool", "description": "A test tool"}],
                }
            ],
        },
    )

    res = await web_server.get_mcp_servers()
    assert res["synchronized"] is True
    assert res["servers"][0]["name"] == "test_srv"
    assert res["servers"][0]["tools"][0]["name"] == "test_tool"

    status = await web_server.get_mcp_status()
    assert status["servers"] == {"test_srv": True}
