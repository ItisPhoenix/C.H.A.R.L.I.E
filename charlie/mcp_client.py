"""MCP Client for Charlie -- local Model Context Protocol integration.

Provides a lightweight MCP client that can:
1. Connect to MCP servers via stdio transport (subprocess)
2. List available tools from a server
3. Call tools on a server
4. Manage multiple server connections

This is a minimal MCP client implementation focused on local tool
discovery and invocation. It does not implement the full MCP protocol
spec -- just enough for Charlie to extend its toolset via MCP servers.
"""

import json
import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("charlie.mcp_client")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class MCPTool:
    """A tool exposed by an MCP server."""
    name: str
    description: str
    input_schema: Dict[str, Any] = field(default_factory=dict)
    server_name: str = ""


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server."""
    name: str
    command: str  # e.g. "npx" or "python"
    args: List[str] = field(default_factory=list)  # e.g. ["-m", "my_mcp_server"]
    env: Dict[str, str] = field(default_factory=dict)
    timeout: float = 30.0


# ---------------------------------------------------------------------------
# MCP Client
# ---------------------------------------------------------------------------

class MCPClient:
    """Manages connections to MCP servers and provides tool discovery/invocation.

    Usage::

        client = MCPClient()
        client.add_server(MCPServerConfig(
            name="filesystem",
            command="npx",
            args=["-y", "@anthropic/mcp-filesystem-server", "/tmp"],
        ))
        client.start()
        tools = client.list_tools()
        result = client.call_tool("filesystem", "list_directory", {"path": "/tmp"})
        client.stop()
    """

    def __init__(self) -> None:
        self._servers: Dict[str, _ManagedServer] = {}
        self._tools: Dict[str, MCPTool] = {}  # "server_name:tool_name" -> tool
        self._tool_call_log: List[Dict[str, Any]] = []
        self._max_log: int = 100

    def add_server(self, config: MCPServerConfig) -> None:
        """Register a server (does not start it)."""
        if config.name in self._servers:
            logger.warning("Server '%s' already registered, skipping", config.name)
            return
        self._servers[config.name] = _ManagedServer(config)
        logger.info("Registered MCP server: %s", config.name)

    def start(self) -> None:
        """Start all registered servers and discover tools."""
        for name, server in self._servers.items():
            try:
                server.start()
                tools = server.list_tools()
                for tool in tools:
                    tool.server_name = name
                    key = f"{name}:{tool.name}"
                    self._tools[key] = tool
                logger.info(
                    "MCP server '%s' started, discovered %d tools",
                    name,
                    len(tools),
                )
            except Exception:
                logger.warning(
                    "Failed to start MCP server '%s'", name, exc_info=True
                )

    def stop(self) -> None:
        """Stop all servers and clean up."""
        for name, server in self._servers.items():
            try:
                server.stop()
            except Exception:
                logger.debug("Error stopping server '%s'", name, exc_info=True)
        self._tools.clear()

    def list_tools(self) -> List[MCPTool]:
        """Return all discovered tools across all servers."""
        return list(self._tools.values())

    def get_tools_for_prompt(self) -> str:
        """Format discovered tools as a system prompt snippet.

        Returns a string suitable for injecting into the system prompt.
        """
        tools = self.list_tools()
        if not tools:
            return ""
        lines = ["Available MCP tools:"]
        for tool in tools:
            schema_str = ""
            if tool.input_schema:
                props = tool.input_schema.get("properties", {})
                if props:
                    schema_str = " Params: " + ", ".join(
                        f"{k} ({v.get('type', '?')})" for k, v in props.items()
                    )
            lines.append(f"- {tool.server_name}:{tool.name}: {tool.description}{schema_str}")
        return "\n".join(lines)

    def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Call a tool on a specific server.

        Returns:
            {"success": True, "result": ...} or {"success": False, "error": ...}
        """
        key = f"{server_name}:{tool_name}"
        tool = self._tools.get(key)
        if not tool:
            return {"success": False, "error": f"Tool '{tool_name}' not found on server '{server_name}'"}

        server = self._servers.get(server_name)
        if not server or not server.is_running():
            return {"success": False, "error": f"Server '{server_name}' is not running"}

        start = time.monotonic()
        try:
            result = server.call_tool(tool_name, arguments or {})
            elapsed_ms = round((time.monotonic() - start) * 1000)
            self._log_call(server_name, tool_name, arguments, True, elapsed_ms)
            return {"success": True, "result": result, "elapsed_ms": elapsed_ms}
        except Exception as exc:
            elapsed_ms = round((time.monotonic() - start) * 1000)
            self._log_call(server_name, tool_name, arguments, False, elapsed_ms, str(exc))
            return {"success": False, "error": str(exc), "elapsed_ms": elapsed_ms}

    def get_call_log(self) -> List[Dict[str, Any]]:
        """Return recent tool call log."""
        return list(self._tool_call_log)

    def _log_call(
        self,
        server: str,
        tool: str,
        args: Optional[Dict[str, Any]],
        success: bool,
        elapsed_ms: int,
        error: str = "",
    ) -> None:
        entry: Dict[str, Any] = {
            "server": server,
            "tool": tool,
            "args": args,
            "success": success,
            "elapsed_ms": elapsed_ms,
        }
        if error:
            entry["error"] = error
        self._tool_call_log.append(entry)
        if len(self._tool_call_log) > self._max_log:
            self._tool_call_log = self._tool_call_log[-self._max_log:]


# ---------------------------------------------------------------------------
# Internal: Managed MCP server process
# ---------------------------------------------------------------------------

class _ManagedServer:
    """Manages a single MCP server subprocess."""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self._process: Optional[subprocess.Popen] = None  # type: ignore[type-arg]
        self._lock = threading.Lock()
        self._request_id = 0
        self._response_cache: Dict[int, Dict[str, Any]] = {}
        self._reader_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Launch the server subprocess."""
        env = {**dict(__import__("os").environ), **self.config.env}
        self._process = subprocess.Popen(
            [self.config.command] + self.config.args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
            bufsize=1,
        )
        # Start reader thread to consume server output
        self._reader_thread = threading.Thread(
            target=self._read_loop, daemon=True, name=f"mcp-{self.config.name}-reader"
        )
        self._reader_thread.start()

        # Send initialize request
        resp = self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "charlie", "version": "0.1.0"},
        })
        if resp and "error" not in resp:
            # Send initialized notification
            self._send_notification("notifications/initialized", {})
            logger.debug("MCP server '%s' initialized", self.config.name)
        else:
            logger.warning("MCP server '%s' init failed: %s", self.config.name, resp)
            self._log_stderr()

    def stop(self) -> None:
        """Stop the server subprocess."""
        self._log_stderr()
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None

    def _log_stderr(self) -> None:
        """Emit captured server stderr to logs if present."""
        if not self._process or not self._process.stderr:
            return
        try:
            err = self._process.stderr.read()
        except Exception:
            logger.debug("Failed to read stderr for '%s'", self.config.name, exc_info=True)
            return
        if err and err.strip():
            logger.warning("MCP server '%s' stderr:\n%s", self.config.name, err.strip())

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def list_tools(self) -> List[MCPTool]:
        """Discover tools from the server."""
        resp = self._send_request("tools/list", {})
        if not resp or "error" in resp:
            return []
        tools = []
        for t in resp.get("result", {}).get("tools", []):
            tools.append(MCPTool(
                name=t.get("name", ""),
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
            ))
        return tools

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        """Call a tool on the server."""
        resp = self._send_request("tools/call", {
            "name": name,
            "arguments": arguments,
        })
        if not resp:
            raise RuntimeError(f"No response from server for tool '{name}'")
        if "error" in resp:
            raise RuntimeError(f"Tool error: {resp['error']}")
        result = resp.get("result", {})
        # MCP tool results can be text or structured
        content = result.get("content", [])
        if content and isinstance(content, list):
            texts = [c.get("text", str(c)) for c in content if isinstance(c, dict)]
            return "\n".join(texts) if texts else result
        return result

    def _send_request(self, method: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Send a JSON-RPC request and wait for response."""
        with self._lock:
            self._request_id += 1
            req_id = self._request_id

        request = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }
        return self._raw_exchange(request, req_id)

    def _send_notification(self, method: str, params: Dict[str, Any]) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        self._write_message(notification)

    def _raw_exchange(
        self, request: Dict[str, Any], req_id: int
    ) -> Optional[Dict[str, Any]]:
        """Write a request and read the response."""
        self._write_message(request)

        # Read lines until we get our response
        deadline = time.monotonic() + self.config.timeout
        while time.monotonic() < deadline:
            line = self._read_line(timeout=max(0.1, deadline - time.monotonic()))
            if line is None:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Check if this is the response to our request
            if msg.get("id") == req_id:
                return msg
            # Otherwise it might be a notification -- skip
        return None

    def _write_message(self, msg: Dict[str, Any]) -> None:
        if not self._process or not self._process.stdin:
            return
        line = json.dumps(msg) + "\n"
        try:
            self._process.stdin.write(line)
            self._process.stdin.flush()
        except (BrokenPipeError, OSError):
            logger.warning("Failed to write to MCP server '%s'", self.config.name)

    def _read_line(self, timeout: float = 1.0) -> Optional[str]:
        if not self._process or not self._process.stdout:
            return None
        # Use a simple approach: read with a small timeout
        # For production, use select or threading
        import select
        import sys
        if sys.platform == "win32":
            # Windows: no select on pipes, use threading
            result = [None]
            def _read():
                try:
                    result[0] = self._process.stdout.readline()  # type: ignore[union-attr]
                except Exception:
                    pass
            t = threading.Thread(target=_read, daemon=True)
            t.start()
            t.join(timeout=timeout)
            return result[0] if result[0] else None
        else:
            # Unix: use select
            ready, _, _ = select.select([self._process.stdout], [], [], timeout)
            if ready:
                return self._process.stdout.readline()
            return None

    def _read_loop(self) -> None:
        """Background thread to read server notifications."""
        if not self._process or not self._process.stdout:
            return
        try:
            for line in self._process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    if "method" in msg and "id" not in msg:
                        logger.debug(
                            "MCP server '%s' notification: %s",
                            self.config.name,
                            msg.get("method"),
                        )
                except json.JSONDecodeError:
                    logger.warning(
                        "MCP server '%s' sent non-JSON line, skipping: %r",
                        self.config.name,
                        line[:200],
                    )
        except Exception:
            if self.is_running():
                logger.debug("Reader thread for '%s' exited", self.config.name)
            else:
                self._log_stderr()
