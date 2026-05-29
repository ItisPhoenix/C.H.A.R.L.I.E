"""C.H.A.R.L.I.E. — MCP Manager
Server lifecycle management for MCP connections.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from charlie.mcp.client import MCPClient
from charlie.utils.logger import get_logger

logger = get_logger(__name__)

CONFIG_PATH = Path("charlie_config.json")


class MCPManager:
    """Manages MCP server lifecycles: start, stop, health checks."""

    def __init__(self):
        self.servers: dict[str, MCPClient] = {}
        self._tool_to_server: dict[str, MCPClient] = {}
        self._config: dict[str, Any] = {}
        self._load_config()

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
            logger.error(f"mcp_config_load_failed | error={e}")
            self._config = {}

        # Create MCPClient instances for each configured server
        for name, server_config in self._config.items():
            if isinstance(server_config, dict):
                self.servers[name] = MCPClient(name, server_config)

        logger.info(
            f"mcp_config_loaded | servers={len(self.servers)} | "
            f"names={list(self.servers.keys())}"
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
                mcp_config[name] = {
                    "command": client.command,
                    "args": client.args,
                    "env": client.env,
                    "url": client.url,
                    "enabled": client.enabled,
                }

            existing["mcp_servers"] = mcp_config

            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2, ensure_ascii=False)

            self._config = mcp_config
            logger.info("mcp_config_saved | servers=%d", len(mcp_config))
        except Exception as e:
            logger.error("mcp_config_save_failed | error=%s", e)
