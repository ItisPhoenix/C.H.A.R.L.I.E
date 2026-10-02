"""MCP Client for Charlie -- local Model Context Protocol integration.

Provides a lightweight MCP client that can:
1. Connect to MCP servers via stdio or Streamable HTTP
2. List available tools from a server
3. Call tools on a server
4. Manage multiple server connections
5. Add/enable/disable a server at runtime without restarting Charlie

This is a minimal MCP client implementation focused on local tool
discovery and invocation. It does not implement the full MCP protocol
spec -- just enough for Charlie to extend its toolset via MCP servers.
"""

import asyncio
import json
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger("charlie.mcp_client")

# Cross-thread bridge (start/stop run off-loop via asyncio.to_thread) -- same pattern as charlie.tools.
_event_bus: Optional[Any] = None
_event_loop: Optional[Any] = None


def set_event_bus(bus: Any, loop: Any) -> None:
    """Wire the producer-side EventBus + its asyncio loop, called once from main.py at startup."""
    global _event_bus, _event_loop
    _event_bus = bus
    _event_loop = loop


def _emit_mcp_status(server_name: str, status: str, tool_count: int = 0) -> None:
    """Fire-and-forget: never raises, a failure here must not affect start()/stop()."""
    if _event_bus is None or _event_loop is None:
        return
    try:
        from charlie.events import EventMeta, EventSource
        payload = {"server_name": server_name, "status": status, "tool_count": tool_count}
        asyncio.run_coroutine_threadsafe(
            _event_bus.emit("mcp_status_changed", payload, meta=EventMeta(source=EventSource.BRAIN)), _event_loop
        )
    except Exception:
        logger.warning("mcp_status_changed emit failed", exc_info=True)


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
    annotations: Dict[str, Any] = field(default_factory=dict)
    """Server-declared hints. Untrusted: recorded, never used as policy authority."""


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server."""
    name: str
    command: str = ""  # e.g. "docker" or "python"
    args: List[str] = field(default_factory=list)  # e.g. ["-m", "my_mcp_server"]
    env: Dict[str, str] = field(default_factory=dict)
    timeout: float = 30.0  # Internal timeout unit is seconds.
    url: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.url:
            parsed = urlsplit(self.url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"MCP server '{self.name}' has an invalid HTTP URL")
            if self.command:
                raise ValueError(f"MCP server '{self.name}' cannot configure both command and url")
        elif not self.command:
            raise ValueError(f"MCP server '{self.name}' requires command or url")


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


def load_mcp_config(path: str) -> tuple[List[MCPServerConfig], Dict[str, str]]:
    """Load Charlie ``mcpServers`` and OpenCode ``mcp`` config entries.

    Charlie-native ``timeout`` values are seconds. OpenCode local/remote
    ``timeout`` and all ``timeout_ms`` values are milliseconds.
    Charlie's local ``mcpToolPolicies`` map is intentionally separate from server
    annotations, which are untrusted metadata.
    """
    if not path or not os.path.isfile(path):
        return [], {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read MCP config file '%s': %s", path, exc)
        return [], {}

    if not isinstance(data, dict):
        logger.warning("Failed to read MCP config file '%s': root must be an object", path)
        return [], {}

    raw_opencode = data.get("mcp", {})
    if not isinstance(raw_opencode, dict):
        logger.warning("Skipping OpenCode MCP entries in '%s': mcp must be an object", path)
        raw_opencode = {}
    if isinstance(raw_opencode.get("servers"), dict):
        raw_opencode = raw_opencode["servers"]

    raw_native = data.get("mcpServers", {})
    if not isinstance(raw_native, dict):
        logger.warning("Skipping MCP servers in '%s': mcpServers must be an object", path)
        raw_native = {}
    entries = dict(raw_opencode)
    entries.update(raw_native)  # Standard mcpServers entries win duplicate names.

    def timeout_seconds(entry: Dict[str, Any], *, opencode: bool) -> float:
        if entry.get("timeout_ms") is not None:
            timeout = float(entry["timeout_ms"]) / 1000
        elif entry.get("timeout") is not None:
            timeout = float(entry["timeout"])
            if opencode:
                timeout /= 1000
        else:
            timeout = 5.0 if opencode else 30.0
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        return timeout

    configs: List[MCPServerConfig] = []
    for name, entry in entries.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(entry, dict):
            logger.warning("Skipping invalid MCP config entry '%s'", name)
            continue
        if entry.get("enabled") is False or entry.get("disabled") is True:
            continue
        try:
            url = entry.get("url")
            command = entry.get("command", "")
            transport = str(entry.get("type", entry.get("transport", ""))).casefold().replace("_", "-")
            opencode = transport in {"local", "remote"}
            timeout = timeout_seconds(entry, opencode=opencode)
            if transport == "local":
                if not isinstance(command, list) or not command or not all(
                    isinstance(arg, str) and arg for arg in command
                ):
                    raise ValueError("OpenCode local command must be a non-empty string array")
                extra_args = entry.get("args", [])
                if not isinstance(extra_args, list):
                    raise ValueError("args must be an array")
                env = entry.get("environment", entry.get("env", {}))
                config = MCPServerConfig(
                    name=name,
                    command=command[0],
                    args=[*command[1:], *(str(arg) for arg in extra_args)],
                    env={str(k): str(v) for k, v in env.items()},
                    timeout=timeout,
                )
            elif transport == "remote":
                if not url:
                    raise ValueError("OpenCode remote entry requires url")
                config = MCPServerConfig(
                    name=name,
                    url=str(url),
                    headers={str(k): str(v) for k, v in entry.get("headers", {}).items()},
                    timeout=timeout,
                )
            elif url:
                if command:
                    raise ValueError("entry cannot configure both command and url")
                if transport and transport not in {"http", "streamable-http"}:
                    raise ValueError(f"unsupported HTTP transport '{transport}'")
                config = MCPServerConfig(
                    name=name,
                    url=str(url),
                    headers={str(k): str(v) for k, v in entry.get("headers", {}).items()},
                    timeout=timeout,
                )
            else:
                if transport and transport != "stdio":
                    raise ValueError(f"unsupported stdio transport '{transport}'")
                if not isinstance(command, str):
                    raise ValueError("command must be a string")
                config = MCPServerConfig(
                    name=name,
                    command=command,
                    args=[str(arg) for arg in entry.get("args", [])],
                    env={str(k): str(v) for k, v in entry.get("env", entry.get("environment", {})).items()},
                    timeout=timeout,
                )
            configs.append(config)
        except (AttributeError, TypeError, ValueError) as exc:
            logger.warning("Skipping MCP config entry '%s': %s", name, exc)

    raw_policies = data.get("mcpToolPolicies", {})
    policies: Dict[str, str] = {}
    if isinstance(raw_policies, dict):
        for tool_id, policy in raw_policies.items():
            if (
                isinstance(tool_id, str)
                and ":" in tool_id
                and all(part.strip() for part in tool_id.split(":", 1))
                and isinstance(policy, str)
                and policy.casefold() in {"allow", "ask", "deny"}
            ):
                policies[tool_id] = policy.casefold()
            else:
                logger.warning("Ignoring invalid MCP tool policy for %r; unknown tools require approval", tool_id)
    elif raw_policies:
        logger.warning("Ignoring invalid mcpToolPolicies in '%s'", path)
    return configs, policies


def load_config_file(path: str) -> List[MCPServerConfig]:
    """Backward-compatible server-only view of ``load_mcp_config``."""
    return load_mcp_config(path)[0]


MCP_TOOL_RISK_FLOOR = "security_sensitive"
"""Lowest risk class an MCP tool may be registered with."""

_SERVER_POLICY_KEYS = ("policy", "risk_class", "riskClass", "approval", "charlie_policy")
"""Annotation keys with which a server tries to claim its own risk/permission."""


def server_declared_policy(annotations: Any) -> Optional[str]:
    """Return the permission a server claims for itself, if it claims one.

    Advisory hints (``readOnlyHint``, ``destructiveHint``) are not permission
    claims and stay out of policy entirely. A claim is recorded so it can be
    floored, never honoured.
    """
    if not isinstance(annotations, dict):
        return None
    for key in _SERVER_POLICY_KEYS:
        if key in annotations:
            return str(annotations[key])
    return None


def mcp_tool_risk_class(policy: str, *, declared_policy: Optional[str] = None) -> str:
    """Single authority for the risk class Charlie registers an MCP tool with.

    ``safe`` means "runs without owner approval", so it is reserved for a policy
    Charlie itself declared in ``mcpToolPolicies`` / ``MCP_READ_ONLY_TOOLS``.
    A server-declared claim about its own permission is untrusted and is always
    floored, so no external declaration can remove the approval requirement.
    ``"ask"``, ``"deny"`` and unknown values are floored too.
    """
    if declared_policy is not None:
        return MCP_TOOL_RISK_FLOOR
    if str(policy or "").casefold() == "allow":
        return "safe"
    return MCP_TOOL_RISK_FLOOR


def _safe_error(exc: Exception) -> str:
    """Keep transport diagnostics useful without echoing credential-bearing URLs."""

    if isinstance(exc, httpx.HTTPStatusError):
        return f"MCP HTTP request returned status {exc.response.status_code}"
    if isinstance(exc, httpx.RequestError):
        return f"MCP HTTP transport failed ({type(exc).__name__})"
    return str(exc)[:500]


# ---------------------------------------------------------------------------
# MCP Client
# ---------------------------------------------------------------------------

class MCPClient:
    """Manages connections to MCP servers and provides tool discovery/invocation.

    Usage::

        client = MCPClient(tool_policies={"filesystem:list_directory": "allow"})
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

    def __init__(
        self,
        read_only_tools: Optional[List[str]] = None,
        *,
        tool_policies: Optional[Dict[str, str]] = None,
    ) -> None:
        """Manage MCP tools under Charlie's local per-tool policy."""
        self._servers: Dict[str, Any] = {}
        self._tools: Dict[str, MCPTool] = {}  # "server_name:tool_name" -> tool
        self._read_only_tools = {name.strip() for name in (read_only_tools or []) if name.strip()}
        self._tool_policies: Dict[str, str] = {}
        for tool_id, policy in (tool_policies or {}).items():
            normalized = str(policy).casefold()
            if isinstance(tool_id, str) and ":" in tool_id and normalized in {"allow", "ask", "deny"}:
                self._tool_policies[tool_id] = normalized
            else:
                logger.warning("Ignoring invalid MCP tool policy for %r; defaulting to approval", tool_id)
        self._server_errors: Dict[str, str] = {}
        self._tool_call_log: List[Dict[str, Any]] = []
        self._max_log: int = 100
        # server_name -> full registered tool names, so a server's tools can
        # be found again for unregistration without re-deriving the prefix.
        self._registered_tools: Dict[str, List[str]] = {}

    def add_server(self, config: MCPServerConfig) -> None:
        """Register a server (does not start it)."""
        if config.name in self._servers:
            logger.warning("Server '%s' already registered, skipping", config.name)
            return
        self._servers[config.name] = _ManagedHTTPServer(config) if config.url else _ManagedServer(config)
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
                _emit_mcp_status(name, "connected", len(tools))
            except Exception as exc:
                self._server_errors[name] = _safe_error(exc)
                try:
                    server.stop()
                except Exception:
                    logger.debug("Error cleaning up failed MCP server '%s'", name, exc_info=True)
                logger.warning(
                    "Failed to start MCP server '%s': %s", name, self._server_errors[name]
                )
                _emit_mcp_status(name, "failed")
            else:
                self._server_errors.pop(name, None)

    def stop(self) -> None:
        """Stop all servers and clean up."""
        for name, server in self._servers.items():
            try:
                server.stop()
                _emit_mcp_status(name, "stopped")
                self._server_errors.pop(name, None)
            except Exception:
                logger.debug("Error stopping server '%s'", name, exc_info=True)
        self._tools.clear()

    def health_check(self) -> Dict[str, bool]:
        """Server name -> is_running, for charlie.watchers.mcp_health_watcher."""
        return {name: server.is_running() for name, server in self._servers.items()}

    def list_tools(self) -> List[MCPTool]:
        """Return tools not denied by Charlie's local policy."""
        return [
            tool for key, tool in self._tools.items()
            if self.tool_policy(key) != "deny"
        ]

    def tool_policy(self, tool_id: str) -> str:
        """Resolve local policy; unlisted tools ask unless legacy allowlist is set."""
        if tool_id in self._tool_policies:
            return self._tool_policies[tool_id]
        if self._read_only_tools:
            return "allow" if tool_id in self._read_only_tools else "deny"
        return "ask"

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
        if self.tool_policy(key) == "deny":
            return {"success": False, "error": f"MCP tool '{key}' is denied by local policy"}

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
            self._server_errors[server_name] = _safe_error(exc)
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
        """Register every discovered tool (across all servers) into the
        shared ToolRegistry. Returns the list of registered tool names."""
        registered: List[str] = []
        for name in self._servers:
            registered.extend(self._register_server_tools(registry, name, prefix))
        logger.info("Registered %d MCP tools into the shared registry", len(registered))
        return registered

    def _register_server_tools(
        self, registry: Any, server_name: str, prefix: str = "mcp_"
    ) -> List[str]:
        """Register one server's already-discovered tools into the shared
        ToolRegistry. Each MCP tool becomes callable through the same
        ``execute_tool`` path the built-in tools use, so the LLM invokes them
        transparently. Tool names are prefixed (default ``mcp_``) to avoid
        colliding with built-ins."""
        registered: List[str] = []
        for tool in self._tools.values():
            tool_id = f"{tool.server_name}:{tool.name}"
            policy = self.tool_policy(tool_id)
            if tool.server_name != server_name or policy == "deny":
                continue
            full_name = f"{prefix}{tool.server_name}_{tool.name}"
            tool_name = tool.name

            def _invoke(server_name=server_name, tool_name=tool_name, **kwargs: Any) -> Any:
                result = self.call_tool(server_name, tool_name, kwargs)
                if result.get("success"):
                    return str(result.get("result", ""))
                raise RuntimeError(f"MCP tool error: {result.get('error', 'unknown error')}")

            registry.register_tool(
                name=full_name,
                description=f"[{tool.server_name}] {tool.description}",
                schema=tool.input_schema or {"type": "object", "properties": {}},
                owner="mcp",
                risk_class=mcp_tool_risk_class(
                    policy,
                    declared_policy=server_declared_policy(getattr(tool, "annotations", None)),
                ),
            )(_invoke)
            registered.append(full_name)

        self._registered_tools.setdefault(server_name, []).extend(registered)
        return registered

    def unregister_server_tools(self, registry: Any, name: str) -> List[str]:
        """Remove a server's previously-registered tools from a ToolRegistry.
        Returns the tool names that were removed."""
        names = self._registered_tools.pop(name, [])
        for tool_name in names:
            registry.unregister_tool(tool_name)
        return names

    def disable_server(self, registry: Any, name: str) -> bool:
        """Stop a server's subprocess and unregister its tools, keeping the
        server config so it can be re-enabled later via enable_server()
        without re-adding it. Returns whether the server existed."""
        server = self._servers.get(name)
        if server is None:
            return False
        self.unregister_server_tools(registry, name)
        self._tools = {k: t for k, t in self._tools.items() if t.server_name != name}
        try:
            server.stop()
        except Exception:
            logger.debug("Error stopping server '%s'", name, exc_info=True)
        self._server_errors.pop(name, None)
        return True

    def enable_server(self, registry: Any, name: str) -> List[str]:
        """(Re)start a registered server and register its freshly-discovered
        tools into a ToolRegistry. Works for a server added via add_server()
        that hasn't been started yet, or one previously disabled. Returns the
        registered tool names."""
        server = self._servers.get(name)
        if server is None:
            raise KeyError(f"No MCP server registered under '{name}'")
        try:
            if not server.is_running():
                server.start()
            for tool in server.list_tools():
                tool.server_name = name
                self._tools[f"{name}:{tool.name}"] = tool
            registered = self._register_server_tools(registry, name)
        except Exception as exc:
            self._server_errors[name] = _safe_error(exc)
            try:
                server.stop()
            except Exception:
                logger.debug("Error cleaning up failed MCP server '%s'", name, exc_info=True)
            raise
        self._server_errors.pop(name, None)
        return registered

    def remove_server(self, registry: Any, name: str) -> bool:
        """Unregister a server's tools, stop its subprocess, and drop its
        config entirely -- unlike disable_server(), it cannot be re-enabled
        without add_server() first. Returns whether the server existed."""
        if name not in self._servers:
            return False
        self.unregister_server_tools(registry, name)
        server = self._servers.pop(name)
        try:
            server.stop()
        except Exception:
            logger.debug("Error stopping server '%s' during removal", name, exc_info=True)
        self._server_errors.pop(name, None)
        return True

    def list_servers_detailed(self) -> List[Dict[str, Any]]:
        """Return full details of each configured server and its tools."""
        out = []
        for name, server in self._servers.items():
            running = server.is_running()
            server_tools = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                }
                for t in self._tools.values()
                if getattr(t, "server_name", "") == name
                and self.tool_policy(f"{name}:{t.name}") != "deny"
            ]
            error = self._server_errors.get(name)
            out.append({
                "name": name,
                "command": server.config.command,
                "args": server.config.args,
                "running": running,
                "status": "connected" if running else ("failed" if error else "disconnected"),
                "error": error,
                "transport": "streamable-http" if server.config.url else "stdio",
                "tools_count": len(server_tools),
                "tools": server_tools,
            })
        return out

    def restart_server(self, registry: Any, name: str) -> bool:
        """Stop and restart a server using canonical disable/enable."""
        if name not in self._servers:
            return False
        self.disable_server(registry, name)
        self.enable_server(registry, name)
        return True


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
        self._ready = False
        self._reader_thread: Optional[threading.Thread] = None
        # Pending requests awaiting a response, keyed by request id. The
        # reader thread (the only code that reads stdout) delivers the
        # response here and sets the event; _send_request just waits on it.
        self._pending: Dict[int, Dict[str, Any]] = {}

    def start(self) -> None:
        """Launch the server subprocess."""
        self._ready = False
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
        if not resp or "error" in resp:
            error = resp.get("error") if resp else "initialize timed out"
            self.stop()
            raise RuntimeError(f"MCP server '{self.config.name}' initialize failed: {error}")
        self._send_notification("notifications/initialized", {})
        self._ready = True
        logger.debug("MCP server '%s' initialized", self.config.name)

    def stop(self) -> None:
        """Stop the server subprocess."""
        self._ready = False
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
        return self._ready and self._process is not None and self._process.poll() is None

    def list_tools(self) -> List[MCPTool]:
        """Discover tools from the server."""
        tools = []
        params: Dict[str, Any] = {}
        seen_cursors = set()
        while True:
            resp = self._send_request("tools/list", params)
            if not resp or "error" in resp:
                raise RuntimeError(f"MCP server '{self.config.name}' tools/list failed: {resp or 'timed out'}")
            result = resp.get("result", {})
            for item in result.get("tools", []):
                annotations = item.get("annotations") if isinstance(item, dict) else None
                tools.append(MCPTool(
                    name=item.get("name", ""),
                    description=item.get("description", ""),
                    input_schema=item.get("inputSchema", {}),
                    # Untrusted: recorded, never used as policy authority.
                    annotations=annotations if isinstance(annotations, dict) else {},
                ))
            cursor = result.get("nextCursor")

            if not cursor:
                break
            if cursor in seen_cursors:
                raise RuntimeError(f"MCP server '{self.config.name}' repeated a tools/list cursor")
            seen_cursors.add(cursor)
            params = {"cursor": cursor}
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
        if result.get("isError") is True:
            content = result.get("content", [])
            texts = [item.get("text", "") for item in content if isinstance(item, dict)]
            message = "\n".join(text for text in texts if text).strip()
            raise RuntimeError(message or f"MCP tool '{name}' returned isError=true")
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


class _ManagedHTTPServer:
    """Streamable HTTP MCP connection backed by Charlie's existing httpx dependency."""

    _PROTOCOL_VERSION = "2025-03-26"

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self._client: Optional[httpx.Client] = None
        self._session_id: Optional[str] = None
        self._protocol_version = self._PROTOCOL_VERSION
        self._request_id = 0
        self._ready = False
        self._lock = threading.RLock()

    def start(self) -> None:
        if not self.config.url:
            raise ValueError("Streamable HTTP MCP server requires url")
        self._client = httpx.Client(timeout=self.config.timeout, headers=self.config.headers)
        self._ready = False
        response = self._exchange("initialize", {
            "protocolVersion": self._PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "charlie", "version": "0.1.0"},
        })
        if not response or "error" in response or not isinstance(response.get("result"), dict):
            raise RuntimeError(f"MCP server '{self.config.name}' initialize failed: {response or 'no response'}")
        self._protocol_version = str(response["result"].get("protocolVersion") or self._PROTOCOL_VERSION)
        self._exchange("notifications/initialized", {})
        self._ready = True

    def stop(self) -> None:
        self._ready = False
        client, self._client = self._client, None
        if client is None:
            return
        if self._session_id:
            try:
                client.delete(
                    self.config.url,
                    headers={"Mcp-Session-Id": self._session_id},
                    timeout=self.config.timeout,
                )
            except httpx.HTTPError:
                logger.debug("MCP session close failed for '%s'", self.config.name, exc_info=True)
        client.close()
        self._session_id = None

    def is_running(self) -> bool:
        return self._ready and self._client is not None

    def _exchange(self, method: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if self._client is None or not self.config.url:
            raise RuntimeError(f"MCP HTTP server '{self.config.name}' is not started")
        with self._lock:
            request_id: Optional[int] = None
            message: Dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
            if method != "notifications/initialized":
                self._request_id += 1
                request_id = self._request_id
                message["id"] = request_id
            headers = {"Accept": "application/json, text/event-stream"}
            if self._session_id:
                headers["Mcp-Session-Id"] = self._session_id
            if method != "initialize":
                headers["MCP-Protocol-Version"] = self._protocol_version
            try:
                response = self._client.post(self.config.url, json=message, headers=headers)
            except httpx.RequestError as exc:
                raise RuntimeError(_safe_error(exc)) from exc
            self._session_id = response.headers.get("Mcp-Session-Id", self._session_id)
            if response.status_code == 202 and request_id is None:
                return None
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise RuntimeError(_safe_error(exc)) from exc
            if not response.content:
                if request_id is None:
                    return None
                raise RuntimeError("MCP HTTP server accepted request without a response")
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
            try:
                if content_type == "application/json":
                    payload = response.json()
                elif content_type == "text/event-stream":
                    payload = self._sse_response(response.text, request_id)
                else:
                    raise RuntimeError(f"MCP HTTP server returned unsupported content type '{content_type}'")
            except (ValueError, json.JSONDecodeError) as exc:
                raise RuntimeError("MCP HTTP server returned invalid JSON") from exc
            if request_id is None:
                return None
            if not isinstance(payload, dict) or payload.get("id") != request_id:
                raise RuntimeError("MCP HTTP server response did not match the request ID")
            return payload

    @staticmethod
    def _sse_response(body: str, request_id: Optional[int]) -> Dict[str, Any]:
        data_lines: List[str] = []
        for line in [*body.splitlines(), ""]:
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
            elif not line and data_lines:
                try:
                    message = json.loads("\n".join(data_lines))
                except json.JSONDecodeError:
                    data_lines = []
                    continue
                data_lines = []
                if isinstance(message, dict) and message.get("id") == request_id:
                    return message
        raise RuntimeError("MCP HTTP event stream contained no matching response")

    def list_tools(self) -> List[MCPTool]:
        tools: List[MCPTool] = []
        params: Dict[str, Any] = {}
        seen_cursors = set()
        while True:
            response = self._exchange("tools/list", params)
            if not response or "error" in response:
                raise RuntimeError(f"MCP server '{self.config.name}' tools/list failed: {response or 'timed out'}")
            result = response.get("result", {})
            if not isinstance(result, dict):
                raise RuntimeError(f"MCP server '{self.config.name}' returned invalid tools/list result")
            for item in result.get("tools", []):
                if isinstance(item, dict) and item.get("name"):
                    # Server annotations are untrusted hints: recorded as evidence
                    # only. They never become a policy (see mcp_tool_risk_class).
                    annotations = item.get("annotations")
                    tools.append(MCPTool(
                        name=str(item["name"]),
                        description=str(item.get("description", "")),
                        input_schema=(
                            item.get("inputSchema", {})
                            if isinstance(item.get("inputSchema", {}), dict)
                            else {}
                        ),
                        annotations=(
                            annotations if isinstance(annotations, dict) else {}
                        ),
                    ))
            cursor = result.get("nextCursor")

            if not cursor:
                return tools
            if cursor in seen_cursors:
                raise RuntimeError(f"MCP server '{self.config.name}' repeated a tools/list cursor")
            seen_cursors.add(cursor)
            params = {"cursor": cursor}

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        response = self._exchange("tools/call", {"name": name, "arguments": arguments})
        if not response:
            raise RuntimeError(f"No response from MCP server for tool '{name}'")
        if "error" in response:
            raise RuntimeError(f"Tool error: {response['error']}")
        result = response.get("result", {})
        if result.get("isError") is True:
            content = result.get("content", [])
            texts = [item.get("text", "") for item in content if isinstance(item, dict)]
            message = "\n".join(text for text in texts if text).strip()
            raise RuntimeError(message or f"MCP tool '{name}' returned isError=true")
        content = result.get("content", [])
        if isinstance(content, list) and content:
            texts = [item.get("text", str(item)) for item in content if isinstance(item, dict)]
            return "\n".join(texts) if texts else result
        return result


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
    # var (pipe-spec, easiest for environment configuration). Same-name
    # entries: file wins, since add_server() skips a name it's already seen.
    server_configs, tool_policies = load_mcp_config(config.mcp_config_path)
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

    client = MCPClient(
        read_only_tools=getattr(config, "mcp_read_only_tools", []),
        tool_policies=tool_policies,
    )
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
