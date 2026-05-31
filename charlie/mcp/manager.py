"""C.H.A.R.L.I.E. — MCP Manager
Server lifecycle management for MCP connections.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

from charlie.mcp.client import MCPClient
from charlie.utils.logger import get_logger

logger = get_logger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "charlie_config.json"


class MCPManager:
    """Manages MCP server lifecycles: start, stop, health checks.

    Owns a persistent asyncio event loop running in a dedicated daemon thread
    (``self._loop`` / ``self._loop_thread``).  All MCP coroutines must be
    submitted to this loop via ``asyncio.run_coroutine_threadsafe`` so that the
    stdio transport's receive loop is driven by a single, always-running loop
    rather than whatever loop happens to be current in the calling thread.
    """

    def __init__(self):
        self.servers: dict[str, MCPClient] = {}
        self._tool_to_server: dict[str, MCPClient] = {}
        self._config: dict[str, Any] = {}

        # Dedicated event loop for all MCP I/O (Req 9.4)
        self._loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        self._loop_thread: threading.Thread = threading.Thread(
            target=self._run_loop,
            name="mcp-event-loop",
            daemon=True,
        )
        self._loop_thread.start()

        self._load_config()

    # ── Event-loop thread ────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """Run the MCP event loop forever in its own daemon thread."""
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run_coroutine(self, coro) -> Any:
        """Submit *coro* to the MCP event loop and block until it completes.

        Raises the coroutine's exception if it fails.  Use this from sync
        contexts (e.g. ``MCPToolBridge`` wrappers) instead of
        ``asyncio.get_event_loop()``.
        """
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()  # blocks the calling thread

    def _load_config(self) -> None:
        """Load MCP server configs from charlie_config.json."""
        try:
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._config = data.get("mcp_servers", {})
            else:
                self._config = {}
        except Exception as e:
            logger.error("mcp_config_load_failed | error=%s", e)
            self._config = {}

        # Create MCPClient instances for each configured server
        for name, server_config in self._config.items():
            if isinstance(server_config, dict):
                self.servers[name] = MCPClient(name, server_config)

        logger.info(
            "mcp_config_loaded | servers=%d | names=%s",
            len(self.servers),
            list(self.servers.keys()),
        )

    async def start_server(self, name: str) -> list[dict]:
        """Connect a single MCP server and discover its tools. Returns tool list."""
        if name not in self.servers:
            raise ValueError(
                f"MCP server '{name}' not configured. "
                f"Add it to charlie_config.json under 'mcp_servers'."
            )

        client = self.servers[name]
        if client.connected:
            return client.tools

        if not client.enabled:
            logger.info(f"mcp_server_disabled | server={name}")
            return []

        await client.connect()

        # Register tool→server mapping
        for tool in client.tools:
            prefixed = f"mcp_{name}_{tool['name']}"
            self._tool_to_server[prefixed] = client

        return client.tools

    async def start_all(self) -> None:
        """Connect all enabled MCP servers."""
        for name, client in self.servers.items():
            if client.enabled and not client.connected:
                try:
                    await self.start_server(name)
                except Exception as e:
                    logger.error(f"mcp_start_failed | server={name} | error={e}")

    async def stop_server(self, name: str) -> None:
        """Disconnect a single MCP server."""
        if name in self.servers:
            client = self.servers[name]
            # Remove tool mappings
            to_remove = [
                k for k, v in self._tool_to_server.items() if v is client
            ]
            for k in to_remove:
                del self._tool_to_server[k]
            await client.disconnect()

    async def stop_all(self) -> None:
        """Disconnect all MCP servers."""
        for name in list(self.servers.keys()):
            await self.stop_server(name)
        logger.info("mcp_all_stopped")

    def stop_all_sync(self) -> None:
        """Synchronous wrapper for ``stop_all`` — safe to call from any thread.

        Submits ``stop_all()`` to the dedicated MCP event loop and waits for
        completion, then stops the loop and joins the loop thread.  Intended to
        be called from ``Brain._shutdown_async`` (via a registered shutdown
        hook) so that MCP sessions are torn down cleanly on Brain exit (Req 9.7).
        """
        try:
            future = asyncio.run_coroutine_threadsafe(self.stop_all(), self._loop)
            future.result(timeout=15)
        except Exception as e:
            logger.warning("mcp_stop_all_sync_error | %s", e)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._loop_thread.join(timeout=5)

    def get_client_for_tool(self, tool_name: str) -> MCPClient | None:
        """Find which MCP client owns a given tool."""
        return self._tool_to_server.get(tool_name)

    def get_all_tools(self) -> dict[str, MCPClient]:
        """Return all tool→client mappings."""
        return dict(self._tool_to_server)

    async def ensure_connected(self, name: str) -> MCPClient:
        """Ensure a server is connected (lazy init). Returns the client."""
        if name not in self.servers:
            raise ValueError(
                f"MCP server '{name}' not configured. "
                f"Add it to charlie_config.json under 'mcp_servers'."
            )

        client = self.servers[name]
        if not client.connected and client.enabled:
            await self.start_server(name)
        return client

    async def health_check(self) -> dict[str, bool]:
        """Check health of all connected servers. Returns {name: is_alive}."""
        results = {}
        for name, client in self.servers.items():
            if client.connected:
                try:
                    # Try listing tools as a health check
                    await client._session.list_tools()
                    results[name] = True
                except Exception:
                    results[name] = False
                    logger.warning(f"mcp_health_failed | server={name}")
            else:
                results[name] = False
        return results

    def reload_config(self) -> None:
        """Reload config from disk. Does not reconnect existing servers."""
        self._load_config()

    def add_server(self, name: str, config: dict[str, Any]) -> None:
        """Add a new MCP server configuration.

        Args:
            name: Server name
            config: Server config dict with keys: command, args, env, url, enabled
        """
        if name in self.servers:
            raise ValueError(f"MCP server '{name}' already exists")

        self.servers[name] = MCPClient(name, config)
        self._config[name] = config
        logger.info("mcp_server_added | server=%s", name)

    async def remove_server(self, name: str) -> None:
        """Remove an MCP server. Disconnects first if connected."""
        if name not in self.servers:
            raise ValueError(f"MCP server '{name}' not found")

        await self.stop_server(name)
        del self.servers[name]
        if name in self._config:
            del self._config[name]
        logger.info("mcp_server_removed | server=%s", name)

    def get_server_status(self, name: str) -> dict[str, Any]:
        """Get detailed status for an MCP server."""
        if name not in self.servers:
            return {"error": f"Server '{name}' not found"}

        client = self.servers[name]
        return {
            "name": name,
            "enabled": client.enabled,
            "connected": client.connected,
            "tool_count": len(client.tools),
            "tools": [t["name"] for t in client.tools],
        }

    def get_all_status(self) -> list[dict[str, Any]]:
        """Get status for all configured MCP servers."""
        return [self.get_server_status(name) for name in self.servers]

    def save_config(self) -> None:
        """Save current MCP server configs back to charlie_config.json."""
        try:
            existing: dict[str, Any] = {}
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    existing = json.load(f)

            # Build config from current servers
            mcp_config: dict[str, Any] = {}
            for name, client in self.servers.items():
                cfg: dict[str, Any] = {
                    "command": client.command,
                    "args": client.args,
                    "env": client.env,
                    "url": client.url,
                    "enabled": client.enabled,
                }
                if client.token:
                    cfg["token"] = client.token
                if client.headers:
                    cfg["headers"] = client.headers
                mcp_config[name] = cfg

            existing["mcp_servers"] = mcp_config

            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2, ensure_ascii=False)

            self._config = mcp_config
            logger.info("mcp_config_saved | servers=%d", len(mcp_config))
        except Exception as e:
            logger.error("mcp_config_save_failed | error=%s", e)
