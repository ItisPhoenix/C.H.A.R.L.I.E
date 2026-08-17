"""MCP tool extension adapter using MCPClient and CapabilityIndex."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

from charlie.self_extension.models import ExtensionKind
from charlie.self_extension.registry import ExtensionEntry, ExtensionRegistry

logger = logging.getLogger("charlie.self_extension.mcp_adapter")


class MCPAdapterResult:
    def __init__(self, success: bool, message: str, server_name: str, tools: Optional[List[str]] = None):
        self.success = success
        self.message = message
        self.server_name = server_name
        self.tools = tools or []


class MCPAdapter:
    """Configures and registers MCP servers with CapabilityIndex and ExtensionRegistry."""

    def __init__(
        self,
        registry: Optional[ExtensionRegistry] = None,
        capability_index: Optional[Any] = None,
        mcp_client: Optional[Any] = None,
    ) -> None:
        self._registry = registry or ExtensionRegistry(capability_index=capability_index)
        self._capability_index = capability_index
        self._mcp_client = mcp_client

    def register_mcp_server(
        self,
        name: str,
        command: str,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        declared_tools: Optional[List[str]] = None,
    ) -> MCPAdapterResult:
        """Register MCP server entry and sync to capabilities."""
        args = list(args or [])
        env = dict(env or {})
        declared = list(declared_tools or [])
        raw_spec = json.dumps({"name": name, "command": command, "args": args, "env": env})
        content_hash = hashlib.sha256(raw_spec.encode("utf-8")).hexdigest()[:16]

        ext_id = f"mcp_{name}"
        entry = ExtensionEntry(
            extension_id=ext_id,
            name=name,
            kind=ExtensionKind.MCP_TOOL,
            source=f"{command} {' '.join(args)}",
            content_hash=content_hash,
            enabled=True,
            declared_tools=declared,
            metadata={"command": command, "args": args, "env": env},
        )
        self._registry.register(entry)

        # Register in CapabilityIndex
        if self._capability_index:
            try:
                from charlie.capabilities import CapabilityDescriptor, CapabilityOperation

                ops = {}
                for t in declared:
                    op_id = f"mcp.{name}.{t}"
                    ops[t] = CapabilityOperation(
                        id=op_id,
                        name=t,
                        description=f"[{name}] MCP tool",
                        parameters_schema={"type": "object"},
                        risk_class="reversible",
                    )

                desc = CapabilityDescriptor(
                    id=ext_id,
                    name=name,
                    description=f"MCP server '{name}'",
                    owner="mcp",
                    provenance="mcp",
                    operations=ops,
                    availability_check=lambda: True,
                )
                self._capability_index.register_capability(desc)
            except Exception as e:
                logger.warning("Failed registering capability for MCP server %s: %s", name, e)

        return MCPAdapterResult(
            success=True,
            message=f"MCP server '{name}' registered successfully.",
            server_name=name,
            tools=declared,
        )

    def rollback_mcp_server(self, name: str) -> MCPAdapterResult:
        """Unregister MCP server and remove from capability index."""
        ext_id = f"mcp_{name}"
        self._registry.unregister(ext_id)

        if self._capability_index:
            self._capability_index._capabilities.pop(ext_id, None)

        return MCPAdapterResult(
            success=True,
            message=f"MCP server '{name}' unregistered and rolled back.",
            server_name=name,
        )
