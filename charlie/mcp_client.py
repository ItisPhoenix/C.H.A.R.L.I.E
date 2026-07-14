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
import os
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


def parse_server_spec(spec: str) -> MCPServerConfig:
    """Parse a "name|command|arg1,arg2,..." spec into a config.

    The pipe-separated form keeps command names unambiguous when args
    themselves contain spaces. Missing args default to an empty list.
    """
    parts = [p.strip() for p in spec.split("|")]
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise ValueError(
            f"Invalid MCP server spec '{spec}'; expected 'name|command[|args]'"
        )
    name, command = parts[0], parts[1]
    args = [a.strip() for a in parts[2].split(",") if a.strip()] if len(parts) > 2 else []
    return MCPServerConfig(name=name, command=command, args=args)


def load_config_file(path: str) -> List[MCPServerConfig]:
    """Load MCP server definitions from a standard "mcpServers" JSON config file
    (the same map format used by Claude Desktop / Cursor / VS Code):
    {"mcpServers": {"name": {"command": "...", "args": [...], "env": {...}}}}.

    Missing file returns an empty list (not an error) -- this is an optional,
    equally-valid alternative to the MCP_SERVERS env var, not a replacement for it.
    """
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read MCP config file '%s': %s", path, exc)
        return []

    configs: List[MCPServerConfig] = []
    for name, entry in data.get("mcpServers", {}).items():
        command = entry.get("command", "")
        if not name or not command:
            logger.warning("Skipping MCP config entry '%s': missing command", name)
            continue
        configs.append(
            MCPServerConfig(
                name=name,
                command=command,
                args=list(entry.get("args", [])),
                env=dict(entry.get("env", {})),
            )
        )
    return configs


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

    def register_tools_into(self, registry: Any, prefix: str = "mcp_") -> List[str]:
        """Register every discovered tool into the shared ToolRegistry.

        Each MCP tool becomes callable through the same ``execute_tool`` path
        the built-in tools use, so the LLM invokes them transparently. Tool
        names are prefixed (default ``mcp_``) to avoid colliding with built-ins.

        Returns the list of registered tool names.
        """
        registered: List[str] = []
        for tool in self.list_tools():
            full_name = f"{prefix}{tool.server_name}_{tool.name}"
            server_name = tool.server_name
            tool_name = tool.name

            def _invoke(server_name=server_name, tool_name=tool_name, **kwargs: Any) -> str:
                result = self.call_tool(server_name, tool_name, kwargs)
                if result.get("success"):
                    return str(result.get("result", ""))
                return f"MCP tool error: {result.get('error', 'unknown error')}"

            registry.register_tool(
                name=full_name,
                description=f"[{tool.server_name}] {tool.description}",
                schema=tool.input_schema or {"type": "object", "properties": {}},
            )(_invoke)
            registered.append(full_name)
        logger.info("Registered %d MCP tools into the shared registry", len(registered))
        return registered


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
        self._reader_thread: Optional[threading.Thread] = None
        # Pending requests awaiting a response, keyed by request id. The
        # reader thread (the only code that reads stdout) delivers the
        # response here and sets the event; _send_request just waits on it.
        self._pending: Dict[int, Dict[str, Any]] = {}

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
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self._process.kill()
        # Only after the process has exited: _log_stderr() does a blocking
        # full-pipe read, which would hang forever against a still-running
        # child (the pipe only reaches EOF once it's closed at exit).
        self._log_stderr()
        self._process = None

    def _log_stderr(self) -> None:
        """Emit captured server stderr to logs if present.

        Only safe to call once the process has exited (poll() is not None):
        reading a pipe with no size argument blocks until EOF, which for a
        still-running child never comes -- calling this while the process is
        alive would hang the caller indefinitely.
        """
        if not self._process or not self._process.stderr:
            return
        if self._process.poll() is None:
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
        """Send a JSON-RPC request and wait for the reader thread to deliver
        its response (matched by request id)."""
        with self._lock:
            self._request_id += 1
            req_id = self._request_id
            event = threading.Event()
            self._pending[req_id] = {"event": event, "response": None}

        request = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }
        self._write_message(request)

        got_response = event.wait(timeout=self.config.timeout)
        with self._lock:
            entry = self._pending.pop(req_id, None)
        if not got_response or entry is None:
            return None
        return entry["response"]

    def _send_notification(self, method: str, params: Dict[str, Any]) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        self._write_message(notification)

    def _write_message(self, msg: Dict[str, Any]) -> None:
        if not self._process or not self._process.stdin:
            return
        line = json.dumps(msg) + "\n"
        try:
            self._process.stdin.write(line)
            self._process.stdin.flush()
        except (BrokenPipeError, OSError):
            logger.warning("Failed to write to MCP server '%s'", self.config.name)

    def _read_loop(self) -> None:
        """The single reader thread for this server's stdout.

        This is the ONLY code that reads self._process.stdout -- a prior
        version also read it synchronously from _send_request (via a second
        thread per call), and the two readers raced for lines: whichever one
        happened to read first could steal the JSON-RPC response the other
        was waiting for, causing initialize/tools/list/tools/call to time
        out unpredictably. Responses (messages with an "id") are routed to
        the waiting _send_request call via self._pending; notifications
        (messages with a "method" and no "id") are just logged.
        """
        if not self._process or not self._process.stdout:
            return
        try:
            for line in self._process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(
                        "MCP server '%s' sent non-JSON line, skipping: %r",
                        self.config.name,
                        line[:200],
                    )
                    continue
                msg_id = msg.get("id")
                if msg_id is not None:
                    with self._lock:
                        entry = self._pending.get(msg_id)
                    if entry is not None:
                        entry["response"] = msg
                        entry["event"].set()
                    # else: response to a request we've already given up on
                    # (timed out) -- nothing waiting for it, safe to drop.
                elif "method" in msg:
                    logger.debug(
                        "MCP server '%s' notification: %s",
                        self.config.name,
                        msg.get("method"),
                    )
        except Exception:
            if self.is_running():
                logger.debug("Reader thread for '%s' exited", self.config.name)
            else:
                self._log_stderr()


def start_mcp(config: Any) -> Optional["MCPClient"]:
    """Build, start, and register MCP servers from config.

    Returns the started client, or None when MCP is disabled or there are no
    server specs. Tool discovery happens here once, at startup, and the tools
    are registered into the shared ToolRegistry so the LLM can call them.
    """
    from charlie.tools import registry

    if not config.mcp_enabled:
        logger.debug("MCP disabled (MCP_ENABLED=false)")
        return None

    # Two equally-valid, mergeable sources: the JSON config file (standard
    # "mcpServers" format, easiest for hand-editing) and the MCP_SERVERS env
    # var (pipe-spec, easiest for the web dashboard to write). Same-name
    # entries: file wins, since add_server() skips a name it's already seen.
    server_configs: List[MCPServerConfig] = load_config_file(config.mcp_config_path)
    for spec in config.mcp_servers:
        try:
            server_configs.append(parse_server_spec(spec))
        except ValueError as exc:
            logger.warning("Skipping MCP server spec: %s", exc)

    if not server_configs:
        logger.debug(
            "MCP enabled but no servers configured (MCP_SERVERS or %s)",
            config.mcp_config_path,
        )
        return None

    client = MCPClient()
    for server_config in server_configs:
        client.add_server(server_config)
    client.start()
    registered = client.register_tools_into(registry)
    logger.info(
        "MCP active: %d server(s) connected, %d tool(s) registered",
        len(client._servers),
        len(registered),
    )
    return client
