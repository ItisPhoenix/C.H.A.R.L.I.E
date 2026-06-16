import json
import logging
import os
from typing import Any, Dict, Tuple
from contextlib import AbstractAsyncContextManager
from mcp import ClientSession, StdioServerParameters, stdio_client

logger = logging.getLogger("charlie.mcp")


class CharlieMCPClient:
    """Manages connections to MCP (Model Context Protocol) servers.

    Reads ``mcp_config.json`` at startup, connects to each server, and
    exposes their tools for LLM tool-use prompts.
    """

    def __init__(self, config_path: str):
        self.config_path = config_path
        self._sessions: Dict[str, ClientSession] = {}
        self._tools: Dict[str, Any] = {}  # "server/tool_name" -> tool metadata
        self._available = False
        # Track context managers that must stay alive for sessions to work
        self._contexts: Dict[str, Tuple[AbstractAsyncContextManager, AbstractAsyncContextManager]] = {}

    async def start(self):
        """Load config, connect to servers, discover tools."""
        if not self.config_path or not os.path.exists(self.config_path):
            logger.info("MCP config not found at %s — skipping MCP.", self.config_path)
            return

        try:
            with open(self.config_path, "r") as f:
                config = json.load(f)
        except Exception as e:
            logger.warning("MCP config load failed: %s", e)
            return

        servers = config.get("mcpServers", {})
        if not servers:
            logger.info("No MCP servers defined in config.")
            return

        for name, server_cfg in servers.items():
            if server_cfg.get("disabled", False) or not server_cfg.get("enabled", True):
                logger.info("MCP server '%s' is disabled — skipping.", name)
                continue
            try:
                await self._connect_server(name, server_cfg)
            except Exception as e:
                logger.warning("MCP server '%s' connection failed: %s", name, e)

        if self._tools:
            self._available = True
            logger.info("MCP client ready: %d tools from %d servers.", len(self._tools), len(self._sessions))

    async def _connect_server(self, name: str, cfg: dict):
        command = cfg.get("command", "")
        args = cfg.get("args", [])
        env = cfg.get("env", None)

        logger.info("Connecting MCP server '%s': %s %s", name, command, " ".join(args))

        server_params = StdioServerParameters(
            command=command,
            args=args,
            env=env,
        )

        # Enter context managers manually — we MUST keep them alive
        # for the session to remain usable. Using `async with` would
        # close the transport when _connect_server returns.
        transport_ctx = stdio_client(server_params)
        read, write = await transport_ctx.__aenter__()

        session_ctx = ClientSession(read, write)
        session = await session_ctx.__aenter__()
        await session.initialize()

        self._contexts[name] = (transport_ctx, session_ctx)

        result = await session.list_tools()
        for tool in result.tools:
            key = f"{name}/{tool.name}"
            self._tools[key] = {
                "server": name,
                "name": tool.name,
                "description": tool.description or "",
                "inputSchema": tool.inputSchema or {},
            }
            logger.info("MCP tool registered: %s", key)

        self._sessions[name] = session

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Execute a tool and return its result (truncated to prevent context explosion)."""
        key = tool_name  # already in "server/name" format
        if key not in self._tools:
            return f"Tool '{tool_name}' not found."

        tool_info = self._tools[key]
        server_name = tool_info["server"]
        short_name = tool_info["name"]
        session = self._sessions.get(server_name)
        if not session:
            return f"Server '{server_name}' for tool '{tool_name}' is not connected."

        try:
            result = await session.call_tool(short_name, arguments)
            text = str(result.content) if result.content else ""
            # Payload truncation — prevent LLM context explosion
            if len(text) > 2000:
                text = text[:2000] + "\n[TRUNCATED]"
            return text
        except Exception as e:
            logger.error("MCP tool call '%s' failed: %s", tool_name, e)
            return f"Tool '{tool_name}' error: {e}"

    def get_tools_for_prompt(self) -> str:
        """Return a formatted string of all available tools for the system prompt."""
        if not self._tools:
            return ""

        lines = ["\n\nAvailable MCP tools (use TOOL: server/tool_name({...}) to invoke):"]
        # Payload limit: only inject first 15 tools to stay under Groq/TPM limits
        MAX_TOOLS = 15
        for key, info in sorted(self._tools.items())[:MAX_TOOLS]:
            desc = info["description"][:120] if info["description"] else "No description"
            schema = info["inputSchema"]
            params = json.dumps(schema.get("properties", {})) if schema else "{}"
            lines.append(f"  - {key}: {desc} | params: {params}")
        lines.append(
            'Format: TOOL: server/tool_name({"param": "value"})\n'
            "After tool execution, the result will be provided as OBSERVATION."
        )
        return "\n".join(lines)

    @property
    def is_available(self) -> bool:
        return self._available

    async def close(self):
        # Exit context managers in reverse order (session first, then transport)
        for name, (transport_ctx, session_ctx) in self._contexts.items():
            try:
                await session_ctx.__aexit__(None, None, None)
            except Exception:
                pass
            try:
                await transport_ctx.__aexit__(None, None, None)
            except Exception:
                pass
        self._contexts.clear()
        self._sessions.clear()
        self._tools.clear()
        self._available = False
        logger.info("MCP client shut down.")
