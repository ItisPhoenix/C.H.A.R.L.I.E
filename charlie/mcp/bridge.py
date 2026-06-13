"""C.H.A.R.L.I.E. — MCP Tool Bridge
Translates MCP tool schemas into Charlie's ToolHandler registry format
and registers them into the unified Tool_Catalog.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from charlie.mcp.manager import MCPManager

if TYPE_CHECKING:
    from charlie.brain.tool_handler import ToolHandler
    from charlie.tools.tool_registry import ToolRegistry

logger = logging.getLogger("charlie.mcp.bridge")

# Default per-tool timeout (seconds). Bounds the time we'll wait for an
# MCP server to respond so a hung server can't freeze the caller.
_MCP_TOOL_TIMEOUT_S = 30.0


class MCPToolBridge:
    """Bridges MCP tools into Charlie's ToolHandler registry and the unified catalog."""

    def __init__(self, manager: MCPManager):
        self.manager = manager
        self._registered_tools: dict[str, dict] = {}  # prefixed_name → tool_info
        self._tool_registry: "ToolRegistry | None" = None

    def set_registry(self, registry: "ToolRegistry") -> None:
        """Set the unified tool registry for MCP tool registration (Req 9.3).

        Called during Brain initialization so that discovered MCP tools are
        registered into the unified catalog with proper source, calling
        convention, and risk tier.
        """
        self._tool_registry = registry

    async def register_tools(self, tool_handler: ToolHandler) -> int:
        """Discover and register all MCP tools into the ToolHandler registry.

        Returns the number of tools registered.
        """
        count = 0
        for name, client in self.manager.servers.items():
            if not client.enabled:
                continue

            # Lazy connect: connect on first registration if not already connected
            if not client.connected:
                try:
                    await self.manager.start_server(name)
                except Exception as e:
                    logger.error(f"mcp_bridge_connect_failed | server={name} | error={e}")
                    continue

            for tool in client.tools:
                prefixed_name = f"mcp_{name}_{tool['name']}"

                # Create wrapper function
                wrapper = self._create_wrapper(client, tool["name"])

                # Register in tool handler
                tool_handler.registry[prefixed_name] = wrapper

                # Register into the unified catalog (Req 9.3)
                self._register_in_catalog(prefixed_name, tool, name, wrapper)

                # Store tool info for system prompt generation
                self._registered_tools[prefixed_name] = {
                    "server": name,
                    "original_name": tool["name"],
                    "description": tool["description"],
                    "input_schema": tool["input_schema"],
                    "source": f"mcp:{name}",
                }
                count += 1

        if count > 0:
            logger.info(f"mcp_bridge_registered | tools={count}")
        return count

    def _register_in_catalog(
        self,
        prefixed_name: str,
        tool: dict,
        server_name: str,
        wrapper,
    ) -> None:
        """Register a single MCP tool into the unified Tool_Catalog (Req 9.3).

        MCP tools are external and gated at TIER_2 by default.
        """
        if self._tool_registry is None:
            return

        from charlie.security.tiers import RiskTier

        self._tool_registry.register(
            name=prefixed_name,
            description=tool.get("description", ""),
            parameters=tool.get("input_schema", {}),
            handler=wrapper,
            risk_tier=RiskTier.TIER_2,
            category="mcp",
            source=f"mcp:{server_name}",
            calling_convention="ARGS_DICT",
        )

    def _create_wrapper(self, client, tool_name: str):
        """Create a sync wrapper function for an async MCP tool call.

        The wrapper matches Charlie's tool signature: (args: dict) -> str
        and handles lazy connection + async-to-sync bridging via the
        manager's dedicated MCP event loop (Req 9.4).

        Calls are bounded by ``_MCP_TOOL_TIMEOUT_S`` (default 30s) so a
        hung MCP server can't freeze the caller (Req 2.10).
        """
        manager = self.manager

        def wrapper(args: dict[str, Any]) -> str:
            # Lazy init: connect if not already connected
            if not client.connected:
                try:
                    manager.run_coroutine(
                        manager.ensure_connected(client.name),
                        timeout=_MCP_TOOL_TIMEOUT_S,
                    )
                except Exception as e:
                    return f"Error: MCP server '{client.name}' not available: {e}"

            # Call the tool via the dedicated MCP event loop
            try:
                return manager.run_coroutine(
                    client.call_tool(tool_name, args),
                    timeout=_MCP_TOOL_TIMEOUT_S,
                )
            except Exception as e:
                return f"Error calling MCP tool '{tool_name}': {e}"

        # Set metadata for the wrapper
        wrapper.__name__ = f"mcp_{client.name}_{tool_name}"
        wrapper.__doc__ = f"MCP tool: {tool_name} (server: {client.name})"
        wrapper._mcp_tool = True
        wrapper._mcp_server = client.name
        wrapper._mcp_tool_name = tool_name

        return wrapper

    def build_system_prompt_tools(self) -> str:
        """Generate a description block of MCP tools for the LLM system prompt."""
        if not self._registered_tools:
            return ""

        lines = ["\n## MCP Tools (external servers)"]
        by_server: dict[str, list] = {}
        for prefixed, info in self._registered_tools.items():
            server = info["server"]
            if server not in by_server:
                by_server[server] = []
            by_server[server].append((prefixed, info))

        for server, tools in by_server.items():
            lines.append(f"\n### Server: {server}")
            for prefixed, info in tools:
                desc = info["description"]
                schema = info["input_schema"]
                params = ""
                if schema and "properties" in schema:
                    props = schema["properties"]
                    param_parts = []
                    for pname, pinfo in props.items():
                        ptype = pinfo.get("type", "any")
                        param_parts.append(f"{pname}: {ptype}")
                    params = f" ({', '.join(param_parts)})"
                lines.append(f"- `{prefixed}`{params}: {desc}")

        return "\n".join(lines)

    def get_registered_tools(self) -> dict[str, dict]:
        """Return all registered MCP tool info."""
        return dict(self._registered_tools)

    def register_lazy_wrappers(self, tool_handler: ToolHandler) -> int:
        """Register lazy MCP tool wrappers that connect on first call.

        This is synchronous — no connection is made. Tools connect when invoked.
        Used during Brain._discover_tools() for lazy initialization.

        Registers BOTH into tool_handler.registry (for backward compat during
        transition) AND into the unified catalog.
        """
        count = 0
        for name, client in self.manager.servers.items():
            if not client.enabled:
                continue

            wrapper = self._create_lazy_dispatcher(client, tool_handler)
            prefixed = f"mcp_{name}"
            tool_handler.registry[prefixed] = wrapper

            if self._tool_registry is not None:
                from charlie.security.tiers import RiskTier

                self._tool_registry.register(
                    name=prefixed,
                    description=f"MCP server dispatcher: {name} (lazy — connects on first call)",
                    parameters={"type": "object", "properties": {}},
                    handler=wrapper,
                    risk_tier=RiskTier.TIER_2,
                    category="mcp",
                    source=f"mcp:{name}",
                    calling_convention="ARGS_DICT",
                )

            count += 1

        if count > 0:
            logger.info(f"mcp_lazy_wrappers_registered | servers={count}")
        return count

    def _create_lazy_dispatcher(self, client, tool_handler: ToolHandler):
        """Create a dispatcher that connects to the MCP server on first call,
        discovers tools, registers them, and then dispatches to the correct tool."""
        manager = self.manager
        bridge = self

        def dispatcher(args: dict[str, Any]) -> str:
            # Connect and discover on first call via the dedicated MCP event loop
            if not client.connected:
                try:
                    manager.run_coroutine(
                        bridge._lazy_setup(client, tool_handler),
                        timeout=_MCP_TOOL_TIMEOUT_S,
                    )
                except Exception as e:
                    return f"Error: MCP server '{client.name}' failed to start: {e}"

            # After setup, this dispatcher should have been replaced.
            # If still called, it means there was an issue.
            return f"Error: MCP server '{client.name}' tools not properly registered"

        dispatcher.__name__ = f"mcp_{client.name}_lazy"
        dispatcher.__doc__ = f"Lazy dispatcher for MCP server: {client.name}"
        return dispatcher

    async def _lazy_setup(self, client, tool_handler: ToolHandler) -> None:
        """Connect to server and register its tools (replaces lazy dispatcher)."""
        await self.manager.start_server(client.name)

        # Now register individual tool wrappers
        for tool in client.tools:
            prefixed_name = f"mcp_{client.name}_{tool['name']}"
            wrapper = self._create_wrapper(client, tool["name"])
            tool_handler.registry[prefixed_name] = wrapper

            # Register into the unified catalog (Req 9.3)
            self._register_in_catalog(prefixed_name, tool, client.name, wrapper)

            self._registered_tools[prefixed_name] = {
                "server": client.name,
                "original_name": tool["name"],
                "description": tool["description"],
                "input_schema": tool["input_schema"],
                "source": f"mcp:{client.name}",
            }

        # Remove the lazy dispatcher entry from both legacy registry and unified catalog
        lazy_key = f"mcp_{client.name}"
        if lazy_key in tool_handler.registry:
            del tool_handler.registry[lazy_key]
        if self._tool_registry is not None:
            self._tool_registry.unregister(lazy_key)

    async def unregister_all(self, tool_handler: ToolHandler) -> None:
        """Remove all MCP tools from the registry."""
        for name in list(tool_handler.registry.keys()):
            if name.startswith("mcp_"):
                del tool_handler.registry[name]
        self._registered_tools.clear()
        logger.info("mcp_bridge_unregistered_all")

    def get_tools_openai_format(self, server_filter: str | None = None) -> list[dict]:
        """Return MCP tools in OpenAI function-calling format.

        Args:
            server_filter: If provided, only return tools from this server.
        """
        tools = []
        for prefixed_name, info in self._registered_tools.items():
            if server_filter and info["server"] != server_filter:
                continue

            tool_def = {
                "type": "function",
                "function": {
                    "name": prefixed_name,
                    "description": info["description"],
                    "parameters": info.get("input_schema", {}),
                },
            }
            tools.append(tool_def)
        return tools

    def get_tool_count(self) -> int:
        """Return the number of registered MCP tools."""
        return len(self._registered_tools)

    def get_tools_by_server(self, server_name: str) -> list[dict]:
        """Return all tools from a specific server."""
        return [
            {"name": info["original_name"], "description": info["description"]}
            for info in self._registered_tools.values()
            if info["server"] == server_name
        ]
