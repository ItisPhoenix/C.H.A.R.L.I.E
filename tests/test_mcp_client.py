"""Tests for charlie.mcp_client -- MCP Client module."""

import logging
import sys
from typing import Any, Callable, Dict, List
from unittest.mock import MagicMock, patch

from charlie.mcp_client import (
    MCPClient,
    MCPServerConfig,
    MCPTool,
    _ManagedServer,
    parse_server_spec,
    start_mcp,
)


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


class _FakeRegistry:
    """Minimal ToolRegistry stand-in recording register_tool calls."""

    def __init__(self) -> None:
        self._tools: Dict[str, Dict[str, Any]] = {}

    def register_tool(self, name: str, description: str, schema: Dict[str, Any]) -> Callable:
        def decorator(func: Callable) -> Callable:
            self._tools[name] = {
                "name": name,
                "description": description,
                "schema": schema,
                "func": func,
            }
            return func

        return decorator


class _FakeServer:
    """Stand-in for MCPServerProcess; no real subprocess is spawned."""

    def __init__(self, server_config: MCPServerConfig) -> None:
        self.config = server_config
        self._process = MagicMock()
        self._process.poll.return_value = None
        self._tools: List[MCPTool] = []

    def start(self) -> None:
        self._tools = [
            MCPTool(name="read", description="Read a file", server_name=self.config.name),
            MCPTool(name="write", description="Write a file", server_name=self.config.name),
        ]

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def list_tools(self) -> List[MCPTool]:
        return self._tools

    def stop(self) -> None:
        self._process = None


def test_parse_server_spec():
    spec = "files|python -m server|/tmp,verbose"
    cfg = parse_server_spec(spec)
    assert cfg.name == "files"
    assert cfg.command == "python -m server"
    assert cfg.args == ["/tmp", "verbose"]


def test_parse_server_spec_requires_name_and_command():
    import pytest as _pytest

    with _pytest.raises(ValueError):
        parse_server_spec("|command")
    with _pytest.raises(ValueError):
        parse_server_spec("name|")


def test_start_mcp_disabled_registers_nothing():
    from types import SimpleNamespace

    cfg = SimpleNamespace(mcp_enabled=False, mcp_servers=["files|echo"])
    assert start_mcp(cfg) is None


def test_start_mcp_registers_into_registry(monkeypatch):
    from types import SimpleNamespace

    import charlie.mcp_client as mcp_mod

    monkeypatch.setattr(mcp_mod, "_ManagedServer", _FakeServer)

    fake = _FakeRegistry()
    # Patch start_mcp's registry import indirectly by wrapping register_tools_into
    # to target our fake recorder.
    captured: Dict[str, _FakeRegistry] = {}
    original = mcp_mod.MCPClient.register_tools_into

    def _fake_register(self, registry, prefix="mcp_"):  # type: ignore[no-untyped-def]
        captured["reg"] = fake
        return original(self, fake, prefix)

    monkeypatch.setattr(mcp_mod.MCPClient, "register_tools_into", _fake_register)

    cfg = SimpleNamespace(mcp_enabled=True, mcp_servers=["files|python -m server"])
    client = start_mcp(cfg)

    assert client is not None
    assert len(fake._tools) == 2
    names = list(fake._tools.keys())
    assert names[0].startswith("mcp_files_")
    assert "read" in names[0] and "write" in names[1]


def test_start_mcp_enabled_without_servers_returns_none():
    from types import SimpleNamespace

    cfg = SimpleNamespace(mcp_enabled=True, mcp_servers=[])
    assert start_mcp(cfg) is None


# ---------------------------------------------------------------------------
# _ManagedServer: single-reader-thread regression tests (real subprocess)
# ---------------------------------------------------------------------------

_STUB_SERVER_SCRIPT = """
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    mid = msg.get("id")
    method = msg.get("method")
    if method == "initialize":
        # Stray notification interleaved before the real response, to prove
        # the reader demuxes by id/method instead of assuming line order.
        print(json.dumps({"jsonrpc": "2.0", "method": "log", "params": {"msg": "starting"}}))
        sys.stdout.flush()
        print(json.dumps({"jsonrpc": "2.0", "id": mid, "result": {"ok": True}}))
        sys.stdout.flush()
    elif method == "ping":
        print(json.dumps({"jsonrpc": "2.0", "id": mid, "result": {"pong": True}}))
        sys.stdout.flush()
"""


class TestManagedServerSingleReader:
    """Regression tests for the dual-stdout-reader race.

    Before this fix, _ManagedServer had both a background _read_loop thread
    and a second synchronous reader inside _send_request/_raw_exchange,
    both consuming the same subprocess stdout pipe. Whichever one happened
    to read a line first could steal the JSON-RPC response another call was
    waiting for, so initialize/tools/list/tools/call would time out
    unpredictably. Now there is exactly one reader thread, and responses are
    routed to the correct waiting caller by request id.
    """

    def _make_server(self) -> _ManagedServer:
        config = MCPServerConfig(
            name="stub",
            command=sys.executable,
            args=["-c", _STUB_SERVER_SCRIPT],
            timeout=5.0,
        )
        return _ManagedServer(config)

    def test_initialize_succeeds_despite_interleaved_notification(self, caplog):
        server = self._make_server()
        try:
            with caplog.at_level(logging.WARNING, logger="charlie.mcp_client"):
                server.start()
            assert server.is_running()
            assert "init failed" not in caplog.text
        finally:
            server.stop()

    def test_sequential_requests_get_correctly_matched_responses(self):
        server = self._make_server()
        try:
            server.start()
            assert server.is_running()

            resp1 = server._send_request("ping", {})
            assert resp1 is not None
            assert resp1["result"]["pong"] is True

            resp2 = server._send_request("ping", {})
            assert resp2 is not None
            assert resp2["result"]["pong"] is True
        finally:
            server.stop()

    def test_unknown_method_times_out_without_hanging(self):
        """A request the stub script never answers must time out cleanly
        (not hang forever, and not crash the reader thread)."""
        config = MCPServerConfig(
            name="stub",
            command=sys.executable,
            args=["-c", _STUB_SERVER_SCRIPT],
            timeout=0.5,
        )
        server = _ManagedServer(config)
        try:
            server.start()
            resp = server._send_request("no_such_method", {})
            assert resp is None
            # Reader thread must still be alive/functional for later calls.
            resp2 = server._send_request("ping", {})
            assert resp2 is not None
            assert resp2["result"]["pong"] is True
        finally:
            server.stop()
