"""Unified ToolRegistry — single catalog for native, MCP, and agent tools."""

import importlib
import pkgutil
from pathlib import Path
from typing import Any

from charlie.utils.logger import get_logger

logger = get_logger(__name__)


class ToolEntry:
    """A single tool entry in the registry."""

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict,
        handler: callable,
        risk_tier: str = "TIER_0",
        category: str = "general",
        source: str = "native",
        timeout: int = 30,
    ):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.handler = handler
        self.risk_tier = risk_tier
        self.category = category
        self.source = source
        self.timeout = timeout

    def to_openai_format(self) -> dict:
        """Convert to OpenAI function-calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "risk_tier": self.risk_tier,
            "category": self.category,
            "source": self.source,
        }


class ToolRegistry:
    """Single unified tool catalog for ALL tools (native, MCP, agent)."""

    def __init__(self):
        self._tools: dict[str, ToolEntry] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: dict,
        handler: callable,
        risk_tier: str = "TIER_0",
        category: str = "general",
        source: str = "native",
        timeout: int = 30,
    ) -> None:
        """Register a tool in the registry."""
        # Collision handling: if name exists and source differs, prefix
        if name in self._tools:
            existing = self._tools[name]
            if existing.source != source:
                # Prefix with source
                prefixed = f"{source.replace(':', '_')}_{name}"
                logger.warning("tool_name_collision | name=%s | renamed=%s", name, prefixed)
                name = prefixed
            else:
                # Same source, overwrite
                logger.info("tool_overwritten | name=%s | source=%s", name, source)

        self._tools[name] = ToolEntry(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            risk_tier=risk_tier,
            category=category,
            source=source,
            timeout=timeout,
        )
        logger.debug("tool_registered | name=%s | source=%s", name, source)

    def unregister(self, name: str) -> bool:
        """Remove a tool from the registry."""
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    def get(self, name: str) -> ToolEntry | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def execute(self, name: str, args: dict) -> Any:
        """Execute a tool by name with arguments."""
        entry = self._tools.get(name)
        if not entry:
            return f"Error: Tool '{name}' not found"

        # Validate args against registered schema
        if entry.parameters:
            schema_props = entry.parameters.get("properties", {})
            schema_required = entry.parameters.get("required", [])
            # Check required params
            for req in schema_required:
                if req not in args:
                    return f"Error: Missing required parameter '{req}' for tool '{name}'"
            # Strip unknown params to prevent injection
            if schema_props:
                unknown = set(args) - set(schema_props)
                if unknown:
                    logger.warning("tool_unknown_params | name=%s | dropped=%s", name, unknown)
                    args = {k: v for k, v in args.items() if k not in unknown}

        try:
            result = entry.handler(**args)
            return result
        except Exception as e:
            logger.error("tool_execution_failed | name=%s | error=%s", name, e)
            return f"Error executing {name}: {e}"

    def get_tools_for_llm(
        self,
        categories: list[str] | None = None,
        sources: list[str] | None = None,
        tool_list: list[str] | None = None,
    ) -> list[dict]:
        """Get tools in OpenAI function-calling format with optional filtering."""
        tools = []
        for name, entry in self._tools.items():
            # Filter by explicit tool list
            if tool_list and name not in tool_list:
                continue
            # Filter by category
            if categories and entry.category not in categories:
                continue
            # Filter by source
            if sources and entry.source not in sources:
                continue
            tools.append(entry.to_openai_format())
        return tools

    def get_tools_for_agent(self, agent_spec) -> list[dict]:
        """Get tools scoped to an agent's declared tool list."""
        if not agent_spec:
            return self.get_tools_for_llm()

        agent_tools = getattr(agent_spec, "tools", [])
        return self.get_tools_for_llm(tool_list=agent_tools)

    def get_tool_names(self) -> list[str]:
        """Get all registered tool names."""
        return list(self._tools.keys())

    def get_tools_text(self) -> str:
        """Get all tools as a simple text block for system prompt injection.

        Format: "- tool_name: description"
        """
        lines = []
        for name, entry in sorted(self._tools.items()):
            lines.append(f"- {name}: {entry.description}")
        return "\n".join(lines)

    def get_tool_count(self) -> int:
        """Get the number of registered tools."""
        return len(self._tools)

    def get_tools_by_category(self, category: str) -> list[ToolEntry]:
        """Get all tools in a category."""
        return [e for e in self._tools.values() if e.category == category]

    def get_tools_by_source(self, source: str) -> list[ToolEntry]:
        """Get all tools from a source."""
        return [e for e in self._tools.values() if e.source == source]

    def clear(self) -> None:
        """Clear all tools (for testing)."""
        self._tools.clear()

    def auto_discover(self, tools_package: str = "charlie.tools") -> int:
        """Auto-discover @tool decorated functions in a package.

        Returns the number of tools discovered.
        """
        count = 0
        try:
            package = importlib.import_module(tools_package)
            package_file = getattr(package, "__file__", None)
            if not package_file:
                logger.warning("auto_discover_no_file | package=%s", tools_package)
                return 0
            package_path = Path(package_file).parent

            for importer, modname, ispkg in pkgutil.iter_modules([str(package_path)]):
                if modname.startswith("_"):
                    continue

                try:
                    module = importlib.import_module(f"{tools_package}.{modname}")
                    tools = _discover_module_tools(module)
                    for tool_meta in tools:
                        self.register(
                            name=tool_meta["name"],
                            description=tool_meta["description"],
                            parameters=tool_meta["parameters"],
                            handler=tool_meta["handler"],
                            risk_tier=tool_meta.get("risk_tier", "TIER_0"),
                            category=tool_meta.get("category", "general"),
                            source="native",
                            timeout=tool_meta.get("timeout", 30),
                        )
                        count += 1
                except Exception as e:
                    logger.warning("module_discovery_failed | module=%s | error=%s", modname, e)

        except Exception as e:
            logger.error("auto_discover_failed | package=%s | error=%s", tools_package, e)

        logger.info("auto_discover_complete | tools_found=%d", count)
        return count

    def register_from_handler(self, handler_obj) -> int:
        """Register tools from an existing ToolHandler object (backward compat).

        Scans for _tool_* methods and registers them with proper risk tiers.
        """
        from charlie.security.tiers import get_tool_tier, RiskTier

        count = 0
        for attr_name in dir(handler_obj):
            if not attr_name.startswith("_tool_"):
                continue
            method = getattr(handler_obj, attr_name)
            if not callable(method):
                continue

            tool_name = attr_name[6:]  # Remove _tool_ prefix
            doc = method.__doc__ or f"Tool: {tool_name}"

            # Read risk tier from @risk_tier decorator
            tier = get_tool_tier(method)
            risk_tier_str = tier.name if isinstance(tier, RiskTier) else "TIER_0"

            self.register(
                name=tool_name,
                description=doc.strip().split("\n")[0],
                parameters={"type": "object", "properties": {}},
                handler=lambda m=method, **kwargs: m(kwargs),
                risk_tier=risk_tier_str,
                source="native",
            )
            count += 1

        logger.info("registered_from_handler | tools=%d", count)
        return count


def _discover_module_tools(module) -> list[dict]:
    """Discover @tool decorated functions in a module."""
    tools = []
    for name in dir(module):
        obj = getattr(module, name)
        if callable(obj) and hasattr(obj, "_tool_meta"):
            tools.append(obj._tool_meta)
    return tools
