"""Tests for charlie.mcp_client -- MCP Client module."""

import json
import logging
import sys
from typing import Any, Callable, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from charlie.mcp_client import (
    MCPClient,
    MCPServerConfig,
    MCPTool,
    _ManagedServer,
    load_mcp_config,
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

        client = MCPClient(read_only_tools=["s1:tool1", "s1:tool2"])
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
        client = MCPClient(read_only_tools=["my_server:my_tool"])
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

    def register_tool(self, name: str, description: str, schema: Dict[str, Any], **kwargs: Any) -> Callable:
        def decorator(func: Callable) -> Callable:
            self._tools[name] = {
                "name": name,
                "description": description,
                "schema": schema,
                "func": func,
                "risk_class": kwargs.get("risk_class"),
            }
            return func

        return decorator

    def unregister_tool(self, name: str) -> bool:
        return self._tools.pop(name, None) is not None


class _FakeServer:
    """Stand-in for MCPServerProcess; no real subprocess is spawned."""

    def __init__(self, server_config: MCPServerConfig) -> None:
        self.config = server_config
        # Matches real _ManagedServer.__init__: no process until start() runs.
        self._process = None
        self._tools: List[MCPTool] = []
        self.calls: List[tuple[str, Dict[str, Any]]] = []

    def start(self) -> None:
        self._process = MagicMock()
        self._process.poll.return_value = None
        self._tools = [
            MCPTool(name="read", description="Read a file", server_name=self.config.name),
            MCPTool(name="write", description="Write a file", server_name=self.config.name),
        ]

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def list_tools(self) -> List[MCPTool]:
        return self._tools

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        self.calls.append((name, arguments))
        return f"{name} result"

    def stop(self) -> None:
        self._process = None


class TestMCPRuntimeControl:
    """Phase 5 adapter #1: add/enable/disable/unregister without restart."""

    def _client_with_server(self, monkeypatch, name: str = "s1") -> MCPClient:
        import charlie.mcp_client as mcp_mod

        monkeypatch.setattr(mcp_mod, "_ManagedServer", _FakeServer)
        client = MCPClient(read_only_tools=[f"{name}:read"])
        client.add_server(MCPServerConfig(name=name, command="echo"))
        return client

    def test_enable_server_exposes_only_allowlisted_read_tool(self, monkeypatch):
        client = self._client_with_server(monkeypatch)
        registry = _FakeRegistry()

        registered = client.enable_server(registry, "s1")

        assert registered == ["mcp_s1_read"]
        assert [tool.name for tool in client.list_tools()] == ["read"]
        assert "mcp_s1_read" in registry._tools
        assert "mcp_s1_write" not in registry._tools
        assert registry._tools["mcp_s1_read"]["risk_class"] == "safe"
        assert client.call_tool("s1", "read")["success"] is True
        denied = client.call_tool("s1", "write")
        assert denied["success"] is False
        assert "denied by local policy" in denied["error"]
        assert client._servers["s1"].calls == [("read", {})]
        assert client._servers["s1"].is_running()

    def test_enable_server_unknown_raises(self, monkeypatch):
        import pytest

        client = self._client_with_server(monkeypatch)
        with pytest.raises(KeyError):
            client.enable_server(_FakeRegistry(), "nope")

    def test_disable_server_unregisters_tools_and_stops(self, monkeypatch):
        client = self._client_with_server(monkeypatch)
        registry = _FakeRegistry()
        client.enable_server(registry, "s1")

        result = client.disable_server(registry, "s1")

        assert result is True
        assert registry._tools == {}
        assert not client._servers["s1"].is_running()
        assert "s1" in client._servers  # config kept for re-enable

    def test_disable_server_missing_returns_false(self, monkeypatch):
        client = self._client_with_server(monkeypatch)
        assert client.disable_server(_FakeRegistry(), "nope") is False

    def test_remove_server_drops_config_entirely(self, monkeypatch):
        client = self._client_with_server(monkeypatch)
        registry = _FakeRegistry()
        client.enable_server(registry, "s1")

        result = client.remove_server(registry, "s1")

        assert result is True
        assert registry._tools == {}
        assert "s1" not in client._servers

    def test_disable_then_enable_round_trip(self, monkeypatch):
        client = self._client_with_server(monkeypatch)
        registry = _FakeRegistry()
        client.enable_server(registry, "s1")
        client.disable_server(registry, "s1")

        registered = client.enable_server(registry, "s1")

        assert registered == ["mcp_s1_read"]
        assert client._servers["s1"].is_running()

    def test_unregister_server_tools_without_stopping(self, monkeypatch):
        client = self._client_with_server(monkeypatch)
        registry = _FakeRegistry()
        client.enable_server(registry, "s1")

        removed = client.unregister_server_tools(registry, "s1")

        assert removed == ["mcp_s1_read"]
        assert registry._tools == {}
        assert client._servers["s1"].is_running()  # unaffected by this call


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


def test_load_mcp_config_normalizes_charlie_and_opencode_entries(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({
        "mcp": {
            "ai_coding": {
                "type": "local",
                "command": ["docker", "mcp", "gateway", "run", "--profile", "ai_coding"],
                "environment": {"MODE": "local"},
                "enabled": True,
                "timeout": 9000000,
            },
            "remote": {
                "type": "remote",
                "url": "https://remote.test/mcp",
                "headers": {"Authorization": "Bearer local"},
                "enabled": True,
                "timeout_ms": 12000,
            },
            "disabled": {"type": "local", "command": ["should", "not", "run"], "enabled": False},
        },
        "mcpServers": {
            "stdio": {"type": "stdio", "command": "python", "args": ["-m", "server"], "timeout": 8},
            "http": {"type": "streamable-http", "url": "https://native.test/mcp", "timeout": 2.5},
        },
        "mcpToolPolicies": {"stdio:read": "ALLOW", "remote:write": "deny"},
    }), encoding="utf-8")

    servers, policies = load_mcp_config(str(path))
    by_name = {server.name: server for server in servers}

    assert set(by_name) == {"ai_coding", "remote", "stdio", "http"}
    assert (by_name["ai_coding"].command, by_name["ai_coding"].args) == (
        "docker", ["mcp", "gateway", "run", "--profile", "ai_coding"],
    )
    assert by_name["ai_coding"].timeout == 9000
    assert by_name["ai_coding"].env == {"MODE": "local"}
    assert by_name["remote"].url == "https://remote.test/mcp"
    assert by_name["remote"].headers == {"Authorization": "Bearer local"}
    assert by_name["remote"].timeout == 12
    assert by_name["stdio"].timeout == 8
    assert by_name["http"].timeout == 2.5
    assert policies == {"stdio:read": "allow", "remote:write": "deny"}


def test_streamable_http_uses_session_and_local_policy(monkeypatch):
    import httpx

    import charlie.mcp_client as mcp_mod

    requests = []
    tools = [
        {"name": "read", "description": "Read", "inputSchema": {"type": "object"},
         "annotations": {"readOnlyHint": False, "destructiveHint": True}},
        {"name": "review", "description": "Review", "inputSchema": {"type": "object"},
         "annotations": {"readOnlyHint": True, "destructiveHint": False}},
        {"name": "write", "description": "Write", "inputSchema": {"type": "object"},
         "annotations": {"readOnlyHint": True}},
    ]

    def handle(request):
        message = json.loads(request.content) if request.method == "POST" else {}
        requests.append((request.method, message, request.headers))
        method = message.get("method")
        if request.url.path == "/unavailable":
            return httpx.Response(503)
        if request.method == "DELETE":
            return httpx.Response(200)
        if method == "initialize":
            return httpx.Response(
                200,
                headers={"content-type": "application/json", "Mcp-Session-Id": "local-session"},
                json={"jsonrpc": "2.0", "id": message["id"], "result": {"protocolVersion": "2025-03-26"}},
            )
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/list":
            payload = {"jsonrpc": "2.0", "id": message["id"], "result": {"tools": tools}}
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=f"event: message\ndata: {json.dumps(payload)}\n\n",
            )
        if method == "tools/call":
            payload = {"jsonrpc": "2.0", "id": message["id"], "result": {
                "content": [{"type": "text", "text": f"{message['params']['name']} result"}],
            }}
            return httpx.Response(200, json=payload)
        return httpx.Response(400)

    real_client = httpx.Client

    def mock_client(**kwargs):
        return real_client(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(mcp_mod.httpx, "Client", mock_client)
    client = MCPClient(tool_policies={"demo:read": "allow", "demo:write": "deny"})
    registry = _FakeRegistry()
    client.add_server(MCPServerConfig(name="demo", url="https://example.test/mcp"))
    try:
        client.start()
        assert client.health_check() == {"demo": True}
        assert {tool.name for tool in client.list_tools()} == {"read", "review"}
        assert client.register_tools_into(registry) == ["mcp_demo_read", "mcp_demo_review"]
        assert registry._tools["mcp_demo_read"]["risk_class"] == "safe"
        assert registry._tools["mcp_demo_review"]["risk_class"] == "security_sensitive"
        read_result = client.call_tool("demo", "read")
        assert read_result.get("success") is True, read_result
        assert read_result["result"] == "read result"
        assert client.call_tool("demo", "review")["success"] is True
        assert client.call_tool("demo", "write")["success"] is False

        methods = [message.get("method") for method, message, _ in requests if method == "POST"]
        assert methods == ["initialize", "notifications/initialized", "tools/list", "tools/call", "tools/call"]
        for method, _, headers in requests[1:]:
            if method == "POST":
                assert headers.get("mcp-session-id") == "local-session"
                assert headers.get("mcp-protocol-version") == "2025-03-26"
        assert requests[0][2].get("accept") == "application/json, text/event-stream"
    finally:
        client.stop()
    assert requests[-1][0] == "DELETE"

    failed_client = MCPClient()
    failed_client.add_server(MCPServerConfig(name="offline", url="https://example.test/unavailable"))
    failed_client.start()
    details = failed_client.list_servers_detailed()[0]
    assert failed_client.health_check() == {"offline": False}
    assert details["status"] == "failed"
    assert "status 503" in details["error"]
    failed_client.stop()


def test_start_mcp_disabled_registers_nothing():
    from types import SimpleNamespace

    cfg = SimpleNamespace(mcp_enabled=False, mcp_servers=["files|echo"], mcp_config_path="")
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

    cfg = SimpleNamespace(
        mcp_enabled=True,
        mcp_servers=["files|python -m server"],
        mcp_config_path="",
        mcp_read_only_tools=["files:read"],
    )
    client = start_mcp(cfg)

    assert client is not None
    assert len(fake._tools) == 1
    names = list(fake._tools.keys())
    assert names == ["mcp_files_read"]
    assert fake._tools["mcp_files_read"]["risk_class"] == "safe"


def test_config_reads_mcp_read_only_tools_from_env(monkeypatch):
    from charlie.config import Config

    monkeypatch.setenv("MCP_READ_ONLY_TOOLS", "files:read, notes:list ,")

    assert Config().mcp_read_only_tools == ["files:read", "notes:list"]


def test_start_mcp_enabled_without_servers_returns_none():
    from types import SimpleNamespace

    cfg = SimpleNamespace(mcp_enabled=True, mcp_servers=[], mcp_config_path="")
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


_OPERATION_STUB_SCRIPT = """
import json, sys
mode = sys.argv[1]
for line in sys.stdin:
    msg = json.loads(line)
    request_id = msg.get("id")
    method = msg.get("method")
    if method == "initialize":
        response = {"jsonrpc": "2.0", "id": request_id, "result": {"ok": True}}
    elif method == "tools/list":
        response = {"jsonrpc": "2.0", "id": request_id, "result": {"tools": [
            {"name": "read_probe", "description": "Read-only probe", "inputSchema": {"type": "object"}}
        ]}}
    elif method == "tools/call" and mode == "rpc_error":
        response = {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": "remote denied"}}
    elif method == "tools/call" and mode == "is_error":
        response = {"jsonrpc": "2.0", "id": request_id, "result": {
            "isError": True, "content": [{"type": "text", "text": "server rejected the read"}]
        }}
    elif method == "tools/call":
        response = {"jsonrpc": "2.0", "id": request_id, "result": {
            "content": [{"type": "text", "text": "read-only value"}]
        }}
    else:
        continue
    print(json.dumps(response), flush=True)
"""


async def _execute_local_mcp_probe(mode: str, tmp_path):
    from charlie.capabilities import capability_index
    from charlie.config import Config
    from charlie.core import Brain
    from charlie.tools import registry

    client = MCPClient(read_only_tools=["g9_stdio:read_probe"])
    client.add_server(MCPServerConfig(
        name="g9_stdio",
        command=sys.executable,
        args=["-u", "-c", _OPERATION_STUB_SCRIPT, mode],
        timeout=3.0,
    ))
    observed = []
    brain = None
    try:
        client.start()
        registered = client.register_tools_into(registry)
        tool_name = "mcp_g9_stdio_read_probe"
        assert registered == [tool_name]
        operation = capability_index.get_operation(tool_name)
        assert operation is not None
        assert operation.risk_class == "safe"
        assert registry.get_owner(tool_name) == "mcp"

        brain = Brain(
            Config(
                llm_url="http://127.0.0.1:1/v1",
                llm_model="test",
                memory_file=str(tmp_path / "MEMORY.md"),
                user_file=str(tmp_path / "USER.md"),
                opinions_file=str(tmp_path / "OPINIONS.md"),
                session_db_path=str(tmp_path / "sessions.db"),
                world_model_db_path=str(tmp_path / "world-model.db"),
                memory_graph_db=str(tmp_path / "memory-graph.db"),
            ),
            register_panic_hotkey=False,
            on_operation_result=lambda name, envelope: observed.append((name, envelope)),
        )
        result = await brain.execute_tool_operation(
            tool_name,
            {},
            request="Read the local probe",
            task_id="task-g9",
            session_id="session-g9",
            turn_id="turn-g9",
        )
        assert observed == [(tool_name, result)]
        assert result.capability == "mcp"
        assert result.turn_id == "turn-g9"
        assert result.task_id == "task-g9"
        return result
    finally:
        if brain is not None:
            await brain.close()
        client.remove_server(registry, "g9_stdio")


@pytest.mark.asyncio
async def test_mcp_read_only_result_uses_brain_operation_success_path(tmp_path):
    from charlie.turn_contracts import ResultStatus

    result = await _execute_local_mcp_probe("success", tmp_path)
    assert result.status == ResultStatus.COMPLETED.value
    assert result.result == "read-only value"
    assert result.verification is None
    assert result.verification_status is None
    assert result.errors == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,error_text", [
    ("rpc_error", "remote denied"),
    ("is_error", "server rejected the read"),
])
async def test_mcp_errors_become_failed_brain_operation_envelopes(mode, error_text, tmp_path):
    from charlie.turn_contracts import ResultStatus

    result = await _execute_local_mcp_probe(mode, tmp_path)
    assert result.status == ResultStatus.FAILED.value
    assert error_text in result.result
    assert any(error_text in error for error in result.errors)
    assert result.verification is None
    assert result.verification_status is None


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

