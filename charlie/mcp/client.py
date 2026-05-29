"""C.H.A.R.L.I.E. — MCP Client
Wraps the mcp Python SDK for stdio and SSE transport connections.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger("charlie.mcp.client")


class MCPClient:
    """Client for a single MCP server. Handles connect/disconnect and tool calls."""

    def __init__(self, name: str, config: dict[str, Any]):
        self.name = name
        self.command = config.get("command")
        self.args = config.get("args", [])
        self.env = config.get("env")
        self.url = config.get("url")
        self.enabled = config.get("enabled", True)

        self._session: ClientSession | None = None
        self._context_stack = None
        self._read = None
        self._write = None
        self._connected = False
        self._tools: list[dict] = []

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def tools(self) -> list[dict]:
        return self._tools

    async def connect(self) -> None:
        """Establish connection to the MCP server."""
        if self._connected:
            return

        try:
            if self.command:
                await self._connect_stdio()
            elif self.url:
                await self._connect_sse()
            else:
                raise ValueError(
                    f"MCP server '{self.name}': invalid config. Provide 'command' for local servers "
                    f"or 'url' for remote servers. Example: {{'command': 'npx', 'args': ['-y', '@mcp/server']}}"
                )

            # Initialize the session
            await self._session.initialize()

            # Discover tools
            tools_result = await self._session.list_tools()
            self._tools = []
            for tool in tools_result.tools:
                self._tools.append({
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": tool.inputSchema if hasattr(tool, "inputSchema") else {},
                })

            self._connected = True
            logger.info(
                f"mcp_connected | server={self.name} | tools={len(self._tools)}"
            )

        except Exception as e:
            logger.error(f"mcp_connect_failed | server={self.name} | error={e}")
            self._connected = False
            raise

    async def _connect_stdio(self) -> None:
        """Connect via stdio transport (local subprocess)."""
        server_params = StdioServerParameters(
            command=self.command,
            args=self.args,
            env=self.env,
        )
        # stdio_client is an async context manager that yields (read, write)
        # We need to enter it and keep it alive
        self._context_stack = stdio_client(server_params)
        read, write = await self._context_stack.__aenter__()
        self._read = read
        self._write = write
        self._session = ClientSession(read, write)

    async def _connect_sse(self) -> None:
        """Connect via SSE transport (remote server)."""
        from mcp.client.sse import sse_client

        self._context_stack = sse_client(url=self.url)
        read, write = await self._context_stack.__aenter__()
        self._read = read
        self._write = write
        self._session = ClientSession(read, write)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Call a tool on this MCP server. Returns the text result."""
        if not self._connected or not self._session:
            raise RuntimeError(f"MCP server '{self.name}' is not connected")

        try:
            result = await self._session.call_tool(name, arguments=arguments)

            # Extract text content
            if result.content:
                parts = []
                for block in result.content:
                    if hasattr(block, "text"):
                        parts.append(block.text)
                    else:
                        parts.append(str(block))
                return "\n".join(parts)
            return "Tool executed successfully (no output)."

        except Exception as e:
            logger.error(
                f"mcp_tool_failed | server={self.name} | tool={name} | error={e}"
            )
            return f"Error calling MCP tool '{name}': {e}"

    async def disconnect(self) -> None:
        """Clean shutdown of the MCP server connection."""
        if not self._connected:
            return

        try:
            if self._session:
                await self._session.__aexit__(None, None, None)
            if self._context_stack:
                await self._context_stack.__aexit__(None, None, None)
        except Exception as e:
            logger.debug(f"mcp_disconnect_error | server={self.name} | error={e}")
        finally:
            self._session = None
            self._context_stack = None
            self._read = None
            self._write = None
            self._connected = False
            logger.info(f"mcp_disconnected | server={self.name}")

    async def reconnect(self, max_attempts: int = 3) -> bool:
        """Attempt to reconnect with exponential backoff.

        Returns True if reconnection succeeded, False otherwise.
        """
        import asyncio

        for attempt in range(max_attempts):
            try:
                # Disconnect first if connected
                if self._connected:
                    await self.disconnect()

                # Wait with exponential backoff
                if attempt > 0:
                    wait_time = 2 ** attempt  # 2s, 4s, 8s
                    logger.info("mcp_reconnect_attempt | server=%s | attempt=%d | wait=%ds",
                                self.name, attempt + 1, wait_time)
                    await asyncio.sleep(wait_time)

                # Try to connect
                await self.connect()
                logger.info("mcp_reconnect_success | server=%s | attempt=%d", self.name, attempt + 1)
                return True

            except Exception as e:
                logger.warning("mcp_reconnect_failed | server=%s | attempt=%d | error=%s",
                               self.name, attempt + 1, e)

        logger.error("mcp_reconnect_exhausted | server=%s | attempts=%d", self.name, max_attempts)
        return False

    def get_tool_info(self, tool_name: str) -> dict | None:
        """Get info for a specific tool by name."""
        for tool in self._tools:
            if tool["name"] == tool_name:
                return tool
        return None

    def get_status(self) -> dict:
        """Get detailed status of this client."""
        return {
            "name": self.name,
            "enabled": self.enabled,
            "connected": self.connected,
            "tool_count": len(self._tools),
            "tools": [t["name"] for t in self._tools],
            "command": self.command,
            "url": self.url,
        }

    def __repr__(self) -> str:
        status = "connected" if self._connected else "disconnected"
        return f"MCPClient(name={self.name!r}, tools={len(self._tools)}, status={status})"
