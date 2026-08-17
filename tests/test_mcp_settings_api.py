import pytest
from charlie.mcp_client import MCPClient, MCPServerConfig, MCPTool
import charlie.web_server as web_server


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
        return [MCPTool(name="test_tool", description="A test tool", input_schema={"type": "object"}, server_name=self.config.name)]


def test_mcp_client_server_management_methods():
    client = MCPClient()
    cfg = MCPServerConfig(name="mock_srv", command="echo", args=["hello"])
    client._servers["mock_srv"] = _MockManagedServer(cfg)
    
    # Detailed list when disconnected
    detailed = client.list_servers_detailed()
    assert len(detailed) == 1
    assert detailed[0]["name"] == "mock_srv"
    assert detailed[0]["status"] == "disconnected"
    assert detailed[0]["tools"] == []

    # Connect
    assert client.connect_server("mock_srv") is True
    detailed = client.list_servers_detailed()
    assert detailed[0]["status"] == "connected"
    assert len(detailed[0]["tools"]) == 1
    assert detailed[0]["tools"][0]["name"] == "test_tool"

    # Restart
    assert client.restart_server("mock_srv") is True
    assert client.list_servers_detailed()[0]["status"] == "connected"

    # Disconnect
    assert client.disconnect_server("mock_srv") is True
    assert client.list_servers_detailed()[0]["status"] == "disconnected"

    # Remove
    assert client.remove_server_by_name("mock_srv") is True
    assert len(client.list_servers_detailed()) == 0


@pytest.mark.asyncio
async def test_web_server_mcp_endpoints(monkeypatch):
    client = MCPClient()
    cfg = MCPServerConfig(name="test_srv", command="echo", args=["hi"])
    client._servers["test_srv"] = _MockManagedServer(cfg)
    
    monkeypatch.setattr(web_server, "mcp_client", client)
    
    # 1. GET /api/mcp/servers
    res = await web_server.get_mcp_servers()
    assert "servers" in res
    assert len(res["servers"]) == 1
    assert res["servers"][0]["name"] == "test_srv"
    
    # 2. POST /api/mcp/servers/{name}/connect
    conn_res = await web_server.connect_mcp_server("test_srv")
    assert conn_res["status"] == "ok"
    
    # Verify tools updated
    res2 = await web_server.get_mcp_servers()
    assert res2["servers"][0]["status"] == "connected"
    assert len(res2["servers"][0]["tools"]) == 1
    
    # 3. POST /api/mcp/servers/{name}/restart
    restart_res = await web_server.restart_mcp_server("test_srv")
    assert restart_res["status"] == "ok"
    
    # 4. POST /api/mcp/servers/{name}/disconnect
    disc_res = await web_server.disconnect_mcp_server("test_srv")
    assert disc_res["status"] == "ok"
    
    # 5. POST /api/mcp/servers (add new server)
    add_res = await web_server.add_mcp_server({
        "name": "new_srv",
        "command": "python",
        "args": ["-m", "server"],
    })
    assert add_res["status"] == "ok"
    assert len(client.list_servers_detailed()) == 2
    
    # 6. DELETE /api/mcp/servers/{name}
    del_res = await web_server.delete_mcp_server("test_srv")
    assert del_res["status"] == "ok"
    assert len(client.list_servers_detailed()) == 1
