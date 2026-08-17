"""MCP tool extension adapter using the canonical MCPClient API."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

from charlie.self_extension.models import ExtensionKind
from charlie.self_extension.registry import ExtensionEntry, ExtensionRegistry

logger = logging.getLogger("charlie.self_extension.mcp_adapter")


class MCPAdapterResult:
    def __init__(
        self,
        success: bool,
        message: str,
        server_name: str,
        tools: Optional[List[str]] = None,
    ):
        self.success = success
        self.message = message
        self.server_name = server_name
        self.tools = tools or []


class MCPAdapter:
    """Configures and registers MCP servers using the canonical MCPClient API."""

    def __init__(
        self,
        registry: Optional[ExtensionRegistry] = None,
        capability_index: Optional[Any] = None,
        mcp_client: Optional[Any] = None,
        tool_registry: Optional[Any] = None,
    ) -> None:
        self._registry = registry or ExtensionRegistry(capability_index=capability_index)
        self._capability_index = capability_index
        self._mcp_client = mcp_client
        self._tool_registry = tool_registry

    def register_mcp_server(
        self,
        name: str,
        command: str,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        declared_tools: Optional[List[str]] = None,
    ) -> MCPAdapterResult:
        args = list(args or [])
        env = dict(env or {})

        if self._mcp_client is None:
            return MCPAdapterResult(
                success=False,
                message="MCPClient unavailable; refusing registry-only MCP installation.",
                server_name=name,
            )

        if self._tool_registry is None:
            return MCPAdapterResult(
                success=False,
                message="MCP registration requires tool_registry for enable_server().",
                server_name=name,
            )

        from charlie.mcp_client import MCPServerConfig

        config = MCPServerConfig(name=name, command=command, args=args, env=env)
        try:
            self._mcp_client.add_server(config)
        except Exception as exc:
            return MCPAdapterResult(
                success=False,
                message=f"Failed to register MCP server '{name}': {exc}",
                server_name=name,
            )

        discovered_names: List[str] = []
        try:
            self._mcp_client.enable_server(self._tool_registry, name)
            discovered_names = [
                t.name
                for t in self._mcp_client.list_tools()
                if getattr(t, "server_name", "") == name
            ]
            if not discovered_names and not self._mcp_client.health_check().get(name, False):
                raise RuntimeError(
                    f"Server '{name}' connected but reports unhealthy and discovered no tools."
                )
        except Exception as exc:
            try:
                self._mcp_client.remove_server(self._tool_registry, name)
            except Exception:
                pass
            return MCPAdapterResult(
                success=False,
                message=f"MCP server '{name}' connect/discover failed, rolled back: {exc}",
                server_name=name,
            )

        raw_spec = json.dumps({"name": name, "command": command, "args": args, "env": env})
        content_hash = hashlib.sha256(raw_spec.encode()).hexdigest()[:16]
        ext_id = f"mcp_{name}"
        entry = ExtensionEntry(
            extension_id=ext_id,
            name=name,
            kind=ExtensionKind.MCP_TOOL,
            source=f"{command} {' '.join(args)}",
            content_hash=content_hash,
            enabled=True,
            declared_tools=discovered_names,
            metadata={"command": command, "args": args, "env": {}},
        )
        self._registry.register(entry)

        if self._capability_index:
            self._register_capability(name, ext_id, discovered_names)

        return MCPAdapterResult(
            success=True,
            message=f"MCP server '{name}' connected and {len(discovered_names)} tools discovered.",
            server_name=name,
            tools=discovered_names,
        )

    def _register_capability(self, name: str, ext_id: str, tool_names: List[str]) -> None:
        try:
            from charlie.capabilities import CapabilityDescriptor, CapabilityOperation

            _client = self._mcp_client
            _name = name

            def _availability() -> bool:
                if _client is None:
                    return False
                return _client.health_check().get(_name, False)

            ops: Dict[str, CapabilityOperation] = {}
            for t in tool_names:
                op_id = f"mcp.{name}.{t}"
                _t = t

                def _invoke(_t: str = _t, **kwargs: Any) -> Any:
                    if _client is None:
                        raise RuntimeError("No MCP client available")
                    result = _client.call_tool(_name, _t, kwargs)
                    if result.get("success"):
                        return result.get("result", "")
                    raise RuntimeError(result.get("error", "MCP tool call failed"))

                ops[t] = CapabilityOperation(
                    id=op_id,
                    name=t,
                    description=f"[{name}] MCP tool",
                    parameters_schema={"type": "object"},
                    risk_class="reversible",
                    func=_invoke,
                )

            desc = CapabilityDescriptor(
                id=ext_id,
                name=name,
                description=f"MCP server '{name}'",
                owner="mcp",
                provenance="mcp",
                operations=ops,
                availability_check=_availability,
            )
            self._capability_index.register_capability(desc)
        except Exception as exc:
            logger.warning("Failed registering capability for MCP server %s: %s", name, exc)

    def rollback_mcp_server(self, name: str) -> MCPAdapterResult:
        ext_id = f"mcp_{name}"

        if self._mcp_client is not None and self._tool_registry is not None:
            try:
                self._mcp_client.remove_server(self._tool_registry, name)
            except Exception as exc:
                logger.warning("MCPClient rollback for '%s' partial: %s", name, exc)

        self._registry.unregister(ext_id)

        if self._capability_index:
            self._capability_index._capabilities.pop(ext_id, None)

        return MCPAdapterResult(
            success=True,
            message=f"MCP server '{name}' disconnected and rolled back.",
            server_name=name,
        )
