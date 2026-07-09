"""Tests for charlie.mcp_client -- MCP Client module."""

from unittest.mock import MagicMock, patch

from charlie.mcp_client import MCPClient, MCPServerConfig, MCPTool


class TestMCPTool:
    def test_create_tool(self):
        tool = MCPTool(
            name="test_tool",
            description="A test tool",
            input_schema={"properties": {"x": {"type": "string"}}},
            server_name="test_server",
        )
        assert tool.name == "test_tool"
        assert tool.description == "A test tool"
        assert tool.server_name == "test_server"
        assert "x" in tool.input_schema["properties"]

    def test_default_schema(self):
        tool = MCPTool(name="t", description="d")
        assert tool.input_schema == {}
        assert tool.server_name == ""


class TestMCPServerConfig:
    def test_config_defaults(self):
        config = MCPServerConfig(name="test", command="python")
        assert config.args == []
        assert config.env == {}
        assert config.timeout == 30.0

    def test_config_custom(self):
        config = MCPServerConfig(
            name="myserver",
            command="npx",
            args=["-y", "my-mcp-server"],
            env={"MY_VAR": "value"},
            timeout=10.0,
        )
        assert config.command == "npx"
        assert len(config.args) == 2
        assert config.env["MY_VAR"] == "value"


class TestMCPClient:
    def test_add_server(self):
        client = MCPClient()
        config = MCPServerConfig(name="s1", command="echo")
        client.add_server(config)
        assert config.name in client._servers

    def test_add_duplicate_server(self):
        client = MCPClient()
        config = MCPServerConfig(name="s1", command="echo")
        client.add_server(config)
        client.add_server(config)  # Should log warning, not raise
        assert config.name in client._servers

    def test_list_tools_empty(self):
        client = MCPClient()
        assert client.list_tools() == []

    def test_get_tools_for_prompt_empty(self):
        client = MCPClient()
        assert client.get_tools_for_prompt() == ""

    def test_call_tool_no_server(self):
        client = MCPClient()
        result = client.call_tool("nonexistent", "tool")
        assert result["success"] is False
        assert "not found" in result["error"]

    def test_get_call_log_empty(self):
        client = MCPClient()
        assert client.get_call_log() == []

    @patch("charlie.mcp_client._ManagedServer")
    def test_start_registers_tools(self, MockServer):
        mock_instance = MagicMock()
        mock_instance.list_tools.return_value = [
            MCPTool(name="tool1", description="desc1"),
            MCPTool(name="tool2", description="desc2"),
        ]
        MockServer.return_value = mock_instance

        client = MCPClient()
        client.add_server(MCPServerConfig(name="s1", command="echo"))
        client.start()

        tools = client.list_tools()
        assert len(tools) == 2
        assert tools[0].server_name == "s1"

    def test_log_call(self):
        client = MCPClient()
        client._log_call("s1", "tool1", {"x": "y"}, True, 100)
        log = client.get_call_log()
        assert len(log) == 1
        assert log[0]["server"] == "s1"
        assert log[0]["success"] is True

    def test_log_call_max(self):
        client = MCPClient()
        client._max_log = 5
        for i in range(10):
            client._log_call("s1", f"tool{i}", {}, True, 10)
        assert len(client.get_call_log()) == 5

    def test_get_tools_for_prompt_with_tools(self):
        client = MCPClient()
        tool = MCPTool(
            name="my_tool",
            description="Does something",
            input_schema={"properties": {"query": {"type": "string"}}},
            server_name="my_server",
        )
        client._tools["my_server:my_tool"] = tool
        prompt = client.get_tools_for_prompt()
        assert "my_server:my_tool" in prompt
        assert "query" in prompt
