"""C.H.A.R.L.I.E. — MCP Client
Wraps the mcp Python SDK for stdio and SSE transport connections.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
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
        self.token = config.get("token")
        self.headers = config.get("headers", {})
        self.enabled = config.get("enabled", True)

        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None
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

            # Session is now entered — its receive loop is running.
            # Initialize the session and discover tools.
            await self._session.initialize()

            tools_result = await self._session.list_tools()
            self._tools = []
            for tool in tools_result.tools:
                self._tools.append(
                    {
                        "name": tool.name,
                        "description": tool.description or "",
                        "input_schema": tool.inputSchema if hasattr(tool, "inputSchema") else {},
                    }
                )

            self._connected = True
            logger.info("mcp_connected | server=%s | tools=%d", self.name, len(self._tools))

        except Exception as e:
            logger.error("mcp_connect_failed | server=%s | error=%s", self.name, e)
            # Clean up the exit stack if connection failed mid-way
            if self._exit_stack is not None:
                try:
                    await self._exit_stack.aclose()
                except Exception:
                    pass
                self._exit_stack = None
            self._session = None
            self._connected = False
            raise

    async def _connect_stdio(self) -> None:
        """Connect via stdio transport (local subprocess) using AsyncExitStack."""
        server_params = StdioServerParameters(
            command=self.command,
            args=self.args,
            env=self.env,
        )
        self._exit_stack = AsyncExitStack()
        read, write = await self._exit_stack.enter_async_context(stdio_client(server_params))
        self._session = await self._exit_stack.enter_async_context(ClientSession(read, write))
        # Session is now entered — its receive loop is running

    async def _connect_sse(self) -> None:
        """Connect via SSE transport (remote server) using AsyncExitStack."""
        from mcp.client.sse import sse_client

        # Build headers from token and/or explicit headers config
        headers = dict(self.headers) if self.headers else {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        self._exit_stack = AsyncExitStack()
        read, write = await self._exit_stack.enter_async_context(
            sse_client(url=self.url, headers=headers if headers else None)
        )
        self._session = await self._exit_stack.enter_async_context(ClientSession(read, write))
        # Session is now entered — its receive loop is running

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
            logger.error("mcp_tool_failed | server=%s | tool=%s | error=%s", self.name, name, e)
            return f"Error calling MCP tool '{name}': {e}"

    async def disconnect(self) -> None:
        """Clean shutdown of the MCP server connection.

        Uses AsyncExitStack.aclose() which correctly tears down all entered
        contexts in reverse order. Never calls __aexit__ on an unentered session
        and never double-exits.
        """
        if self._exit_stack is not None:
            try:
                await self._exit_stack.aclose()
            except Exception as e:
                logger.debug("mcp_disconnect_error | server=%s | %s", self.name, e)
            finally:
                self._exit_stack = None
                self._session = None
                self._connected = False
        logger.info("mcp_disconnected | server=%s", self.name)

    async def reconnect(self, max_retries: int = 5) -> bool:
        """Attempt to reconnect with exponential backoff.

        Tries connect() up to max_retries times with delays of 1s, 2s, 4s, 8s,
        16s (capped at 30s between attempts).
        Returns True on success, False if all attempts are exhausted.
        """
        for attempt in range(max_retries):
            try:
                # Disconnect first if still connected
                if self._connected or self._exit_stack is not None:
                    await self.disconnect()

                # Exponential backoff: 1s, 2s, 4s, 8s, 16s (max 30s)
                if attempt > 0:
                    wait_time = min(2 ** (attempt - 1), 30)
                    logger.info(
                        "mcp_reconnect_attempt | server=%s | attempt=%d | wait=%ds",
                        self.name,
                        attempt + 1,
                        wait_time,
                    )
                    await asyncio.sleep(wait_time)

                await self.connect()
                logger.info(
                    "mcp_reconnect_success | server=%s | attempt=%d",
                    self.name,
                    attempt + 1,
                )
                return True

            except Exception as e:
                logger.warning(
                    "mcp_reconnect_failed | server=%s | attempt=%d | error=%s",
                    self.name,
                    attempt + 1,
                    e,
                )

        logger.error("mcp_reconnect_exhausted | server=%s | attempts=%d", self.name, max_retries)
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
