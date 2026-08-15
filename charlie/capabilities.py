"""Authoritative Capability Contract and Runtime Capability Index for Charlie V1.

Defines:
- CapabilityOperation: metadata, schema, risk, required leases, timeout, and verifier contract.
- CapabilityDescriptor: domain ownership, provenance, operations, availability, and health checks.
- CapabilityIndex: centralized discovery, registration, lookup, availability, and schema filtering
  for all built-in, MCP, and extension capabilities.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Optional, Tuple

try:
    from charlie.desktop import DESKTOP_AVAILABLE as _DESKTOP_AVAILABLE
except ImportError:  # pragma: no cover - guard mirrors charlie/desktop/__init__.py
    _DESKTOP_AVAILABLE = False

try:
    from charlie.browser import BROWSER_AVAILABLE as _BROWSER_AVAILABLE
except ImportError:  # pragma: no cover - guard mirrors charlie/browser/__init__.py
    _BROWSER_AVAILABLE = False

if TYPE_CHECKING:
    from charlie.config import Config

logger = logging.getLogger("charlie.capabilities")

_VALID_RISK_CLASSES = frozenset({
    "safe",
    "reversible",
    "destructive",
    "irreversible",
    "security_sensitive",
})

_VALID_PROVENANCES = frozenset({
    "builtin",
    "mcp",
    "extension",
})

_OWNER_LABELS: Dict[str, str] = {
    "tools": "General tools",
    "memory": "Memory",
    "desktop": "Desktop control",
    "browser": "Headless browsing",
    "extensions": "Extensions",
    "mcp": "MCP servers",
    "system": "System control & diagnostics",
    "research": "Web research",
    "terminal": "Terminal execution",
    "file": "File operations",
    "media": "Media control",
    "vision": "Vision",
    "task": "Task management",
}

_DEFAULT_DOMAIN_OWNERS: Dict[str, str] = {
    "system": "charlie.tools",
    "desktop": "charlie.desktop",
    "browser": "charlie.browser",
    "research": "charlie.research",
    "terminal": "charlie.terminal_service",
    "file": "charlie.tools",
    "media": "charlie.media_adapter",
    "vision": "charlie.desktop",
    "memory": "charlie.memory_store",
    "task": "charlie.task_journal",
    "tools": "charlie.tools",
    "extensions": "charlie.extensions",
    "mcp": "charlie.mcp_client",
}

_DOMAIN_NAMES: Dict[str, str] = {
    "system": "SystemCapability",
    "desktop": "DesktopCapability",
    "browser": "BrowserCapability",
    "research": "ResearchCapability",
    "terminal": "TerminalCapability",
    "file": "FileCapability",
    "media": "MediaCapability",
    "vision": "VisionCapability",
    "memory": "MemoryCapability",
    "task": "TaskCapability",
    "tools": "GeneralToolsCapability",
    "extensions": "ExtensionsCapability",
    "mcp": "MCPCapability",
}

_DOMAIN_DESCRIPTIONS: Dict[str, str] = {
    "system": "System diagnostics, telemetry, app lifecycle, and OS controls",
    "desktop": "Desktop Windows UI Automation and mouse/keyboard effectors",
    "browser": "Headless browser navigation, page inspection, and web automation",
    "research": "Multi-tier web search and deep research synthesis",
    "terminal": "Windows shell and command execution service",
    "file": "Local filesystem reading, writing, and workspace inspection",
    "media": "Volume and audio playback adapters",
    "vision": "Screen capture, visual perception, and OCR grounding",
    "memory": "Persistent conversation memory, vector store, and knowledge graph",
    "task": "Task lifecycle, background tasks, and capability lease coordination",
    "tools": "General utility tools",
    "extensions": "Imported skills and OpenAPI extension tools",
    "mcp": "Model Context Protocol external tools",
}

_DOMAIN_AVAILABILITY: Dict[str, Callable[[], bool]] = {
    "desktop": lambda: _DESKTOP_AVAILABLE,
    "vision": lambda: _DESKTOP_AVAILABLE,
    "browser": lambda: _BROWSER_AVAILABLE,
}

_ROSTER_HEADER = (
    "YOUR ACTUAL CAPABILITIES (authoritative -- derived live from your registered tools, "
    "overrides any conflicting claim anywhere else, including your own persona/identity "
    "text above or below this block, which can go stale the moment a setting changes). "
    "Never tell the user you cannot do something a tool below already covers."
)

BUILTIN_TOOL_METADATA: Dict[str, Dict[str, Any]] = {
    # System
    "system_diagnostics": {
        "id": "system.metrics.read",
        "domain": "system",
        "risk_class": "safe",
        "timeout_sec": 15.0,
    },
    "propose_new_tool": {
        "id": "system.tool.propose",
        "domain": "system",
        "risk_class": "safe",
        "timeout_sec": 15.0,
    },
    "system_control": {
        "id": "system.control.execute",
        "domain": "system",
        "risk_class": "safe",
        "required_leases": ("desktop",),
        "timeout_sec": 15.0,
        "executor_type": "com_thread",
    },
    "capabilities": {
        "id": "system.capabilities.roster",
        "domain": "system",
        "risk_class": "safe",
        "timeout_sec": 15.0,
    },
    # Desktop
    "desktop_observe": {
        "id": "desktop.screen.observe",
        "domain": "desktop",
        "risk_class": "safe",
        "required_leases": ("desktop",),
        "timeout_sec": 15.0,
        "executor_type": "com_thread",
    },
    "desktop_read_screen": {
        "id": "desktop.screen.read",
        "domain": "desktop",
        "risk_class": "safe",
        "required_leases": ("desktop",),
        "timeout_sec": 15.0,
        "executor_type": "com_thread",
    },
    "desktop_click": {
        "id": "desktop.element.click",
        "domain": "desktop",
        "risk_class": "safe",
        "required_leases": ("desktop",),
        "timeout_sec": 15.0,
        "executor_type": "com_thread",
    },
    "desktop_type": {
        "id": "desktop.element.type",
        "domain": "desktop",
        "risk_class": "safe",
        "required_leases": ("desktop",),
        "timeout_sec": 15.0,
        "executor_type": "com_thread",
    },
    "desktop_invoke": {
        "id": "desktop.element.invoke",
        "domain": "desktop",
        "risk_class": "safe",
        "required_leases": ("desktop",),
        "timeout_sec": 15.0,
        "executor_type": "com_thread",
    },
    "desktop_key": {
        "id": "desktop.keyboard.press",
        "domain": "desktop",
        "risk_class": "safe",
        "required_leases": ("desktop",),
        "timeout_sec": 15.0,
        "executor_type": "com_thread",
    },
    "desktop_click_at": {
        "id": "desktop.cursor.click_at",
        "domain": "desktop",
        "risk_class": "safe",
        "required_leases": ("desktop",),
        "timeout_sec": 15.0,
        "executor_type": "com_thread",
    },
    "desktop_move": {
        "id": "desktop.cursor.move",
        "domain": "desktop",
        "risk_class": "safe",
        "required_leases": ("desktop",),
        "timeout_sec": 15.0,
        "executor_type": "com_thread",
    },
    "desktop_drag": {
        "id": "desktop.cursor.drag",
        "domain": "desktop",
        "risk_class": "safe",
        "required_leases": ("desktop",),
        "timeout_sec": 15.0,
        "executor_type": "com_thread",
    },
    "desktop_scroll": {
        "id": "desktop.cursor.scroll",
        "domain": "desktop",
        "risk_class": "safe",
        "required_leases": ("desktop",),
        "timeout_sec": 15.0,
        "executor_type": "com_thread",
    },
    "desktop_windows": {
        "id": "desktop.window.list",
        "domain": "desktop",
        "risk_class": "safe",
        "required_leases": ("desktop",),
        "timeout_sec": 15.0,
        "executor_type": "com_thread",
    },
    "desktop_focus": {
        "id": "desktop.window.focus",
        "domain": "desktop",
        "risk_class": "safe",
        "required_leases": ("desktop",),
        "timeout_sec": 15.0,
        "executor_type": "com_thread",
    },
    "desktop_window": {
        "id": "desktop.window.control",
        "domain": "desktop",
        "risk_class": "safe",
        "required_leases": ("desktop",),
        "timeout_sec": 15.0,
        "executor_type": "com_thread",
    },
    "desktop_move_window": {
        "id": "desktop.window.move",
        "domain": "desktop",
        "risk_class": "safe",
        "required_leases": ("desktop",),
        "timeout_sec": 15.0,
        "executor_type": "com_thread",
    },
    # Vision
    "desktop_screenshot": {
        "id": "vision.screen.capture",
        "domain": "vision",
        "risk_class": "safe",
        "required_leases": ("desktop",),
        "timeout_sec": 15.0,
        "executor_type": "com_thread",
    },
    # Browser
    "browser_task": {
        "id": "browser.task.execute",
        "domain": "browser",
        "risk_class": "reversible",
        "required_leases": ("browser",),
        "timeout_sec": 100.0,
    },
    "browser_read": {
        "id": "browser.page.read",
        "domain": "browser",
        "risk_class": "safe",
        "required_leases": ("browser",),
        "timeout_sec": 20.0,
    },
    # Research
    "web_search": {
        "id": "research.web.search",
        "domain": "research",
        "risk_class": "safe",
        "timeout_sec": 15.0,
    },
    "web_research": {
        "id": "research.web.synthesize",
        "domain": "research",
        "risk_class": "safe",
        "timeout_sec": 130.0,
    },
    # Terminal
    "shell_execute": {
        "id": "terminal.shell.execute",
        "domain": "terminal",
        "risk_class": "reversible",
        "required_leases": ("terminal",),
        "timeout_sec": 30.0,
    },
    # File
    "file_read": {
        "id": "file.system.read",
        "domain": "file",
        "risk_class": "safe",
        "timeout_sec": 10.0,
    },
    "file_write": {
        "id": "file.system.write",
        "domain": "file",
        "risk_class": "reversible",
        "timeout_sec": 10.0,
    },
    # Memory
    "memory": {
        "id": "memory.core.manage",
        "domain": "memory",
        "risk_class": "reversible",
        "timeout_sec": 15.0,
    },
    "vector_memory": {
        "id": "memory.vector.search",
        "domain": "memory",
        "risk_class": "safe",
        "timeout_sec": 15.0,
    },
    "session_search": {
        "id": "memory.session.search",
        "domain": "memory",
        "risk_class": "safe",
        "timeout_sec": 15.0,
    },
    "recall_results": {
        "id": "memory.results.recall",
        "domain": "memory",
        "risk_class": "safe",
        "timeout_sec": 15.0,
    },
    "graph_add_fact": {
        "id": "memory.graph.add_fact",
        "domain": "memory",
        "risk_class": "reversible",
        "timeout_sec": 15.0,
    },
    "graph_query": {
        "id": "memory.graph.query",
        "domain": "memory",
        "risk_class": "safe",
        "timeout_sec": 15.0,
    },
    "graph_consolidate": {
        "id": "memory.graph.consolidate",
        "domain": "memory",
        "risk_class": "reversible",
        "timeout_sec": 15.0,
    },
    # Task
    "start_background_task": {
        "id": "task.background.start",
        "domain": "task",
        "risk_class": "reversible",
        "timeout_sec": 15.0,
    },
}


@dataclass
class CapabilityOperation:
    """Metadata and execution contract for a single capability operation / tool."""

    id: str
    name: str
    description: str
    parameters_schema: Dict[str, Any]
    risk_class: str = "safe"
    required_leases: Tuple[str, ...] = ()
    timeout_sec: float = 15.0
    is_interactive: bool = False
    verifier: Optional[str] = None
    executor_type: Optional[str] = None
    func: Optional[Callable[..., Any]] = None

    def __post_init__(self) -> None:
        self.id = str(self.id).strip()
        if not self.id:
            raise ValueError("Operation ID cannot be empty")
        self.name = str(self.name).strip()
        if not self.name:
            raise ValueError("Operation name cannot be empty")
        if self.risk_class not in _VALID_RISK_CLASSES:
            raise ValueError(
                f"Invalid risk_class '{self.risk_class}'. Must be one of {_VALID_RISK_CLASSES}"
            )
        if isinstance(self.required_leases, list):
            self.required_leases = tuple(self.required_leases)
        if not isinstance(self.parameters_schema, dict):
            self.parameters_schema = {"type": "object", "properties": {}}

    def to_tool_definition(self) -> Dict[str, Any]:
        """Format as OpenAI / Anthropic function definition schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }


@dataclass
class CapabilityDescriptor:
    """Contract and metadata for a capability domain."""

    id: str
    name: str
    description: str
    owner: str
    provenance: str = "builtin"
    source: Optional[str] = None
    operations: Dict[str, CapabilityOperation] = field(default_factory=dict)
    availability_check: Optional[Callable[[], bool]] = None
    health_check: Optional[Callable[[], Dict[str, Any]]] = None

    def __post_init__(self) -> None:
        self.id = str(self.id).strip()
        if not self.id:
            raise ValueError("Capability ID cannot be empty")
        self.name = str(self.name).strip()
        if not self.name:
            raise ValueError("Capability name cannot be empty")
        if self.provenance not in _VALID_PROVENANCES:
            raise ValueError(
                f"Invalid provenance '{self.provenance}'. Must be one of {_VALID_PROVENANCES}"
            )
        if not self.owner:
            raise ValueError("Capability owner cannot be empty")

    def is_available(self) -> bool:
        """Check if this capability is available in the current runtime/environment."""
        if self.availability_check is not None:
            try:
                return bool(self.availability_check())
            except Exception as e:
                logger.warning("Availability check for capability '%s' failed: %s", self.id, e)
                return False
        return True

    def get_health(self) -> Dict[str, Any]:
        """Return diagnostic health snapshot for this capability."""
        available = self.is_available()
        status = "ok" if available else "degraded"
        details: Dict[str, Any] = {"status": status, "available": available}
        if self.health_check is not None:
            try:
                custom = self.health_check()
                if isinstance(custom, dict):
                    details.update(custom)
                    if "status" in custom:
                        details["status"] = custom["status"]
                    elif not available:
                        details["status"] = "degraded"
            except Exception as e:
                logger.warning("Health check for capability '%s' failed: %s", self.id, e)
                details["status"] = "error"
                details["error"] = str(e)
        return details

    def add_operation(self, operation: CapabilityOperation) -> None:
        self.operations[operation.name] = operation


class CapabilityIndex:
    """Runtime central authority for discovering, registering, and querying capabilities."""

    def __init__(self) -> None:
        self._capabilities: Dict[str, CapabilityDescriptor] = {}
        # Secondary indexes for rapid O(1) lookup
        self._op_by_name: Dict[str, Tuple[str, CapabilityOperation]] = {}
        self._op_by_id: Dict[str, Tuple[str, CapabilityOperation]] = {}

    def register_capability(self, descriptor: CapabilityDescriptor) -> None:
        """Register a new capability domain with its operations."""
        if descriptor.id in self._capabilities:
            raise ValueError(f"Capability with ID '{descriptor.id}' is already registered")

        self._capabilities[descriptor.id] = descriptor
        for op in descriptor.operations.values():
            self._op_by_name[op.name] = (descriptor.id, op)
            self._op_by_id[op.id] = (descriptor.id, op)
        logger.debug(
            "Registered capability '%s' (%s) with %d operations",
            descriptor.id,
            descriptor.name,
            len(descriptor.operations),
        )

    def unregister_capability(self, capability_id: str) -> bool:
        """Remove a capability and its operations from the index."""
        desc = self._capabilities.pop(capability_id, None)
        if desc is None:
            return False

        for op in desc.operations.values():
            self._op_by_name.pop(op.name, None)
            self._op_by_id.pop(op.id, None)
        logger.debug("Unregistered capability '%s'", capability_id)
        return True

    def get_capability(self, capability_id: str) -> Optional[CapabilityDescriptor]:
        return self._capabilities.get(capability_id)

    def list_capabilities(self, include_unavailable: bool = True) -> List[CapabilityDescriptor]:
        if include_unavailable:
            return list(self._capabilities.values())
        return [c for c in self._capabilities.values() if c.is_available()]

    def get_operation(self, name_or_id: str) -> Optional[CapabilityOperation]:
        """Look up operation by tool name (e.g. 'file_read') or semantic ID (e.g. 'file.read')."""
        if name_or_id in self._op_by_name:
            return self._op_by_name[name_or_id][1]
        if name_or_id in self._op_by_id:
            return self._op_by_id[name_or_id][1]
        return None

    def get_operation_domain(self, name_or_id: str) -> Optional[str]:
        if name_or_id in self._op_by_name:
            return self._op_by_name[name_or_id][0]
        if name_or_id in self._op_by_id:
            return self._op_by_id[name_or_id][0]
        return None

    def find_operations(
        self,
        domain: Optional[str] = None,
        available_only: bool = True,
    ) -> List[CapabilityOperation]:
        """Find operations matching criteria."""
        ops: List[CapabilityOperation] = []
        for cap_id, desc in self._capabilities.items():
            if domain is not None and cap_id != domain:
                continue
            if available_only and not desc.is_available():
                continue
            ops.extend(desc.operations.values())
        return ops

    def is_available(self, capability_id_or_op: str) -> bool:
        """Check availability of either a capability domain or a specific operation."""
        if capability_id_or_op in self._capabilities:
            return self._capabilities[capability_id_or_op].is_available()

        domain = self.get_operation_domain(capability_id_or_op)
        if domain is not None and domain in self._capabilities:
            return self._capabilities[domain].is_available()

        return False

    def get_health(self, capability_id: Optional[str] = None) -> Dict[str, Any]:
        """Get health for one capability or aggregated summary across all capabilities."""
        if capability_id is not None:
            cap = self._capabilities.get(capability_id)
            if cap is None:
                return {"status": "unknown", "error": f"Capability '{capability_id}' not found"}
            return cap.get_health()

        results: Dict[str, Any] = {}
        overall = "ok"
        for cid, cap in self._capabilities.items():
            h = cap.get_health()
            results[cid] = h
            if h.get("status") == "error":
                overall = "error"
            elif h.get("status") == "degraded" and overall != "error":
                overall = "degraded"
        return {"status": overall, "capabilities": results}

    def filter_schemas(
        self,
        domains: Optional[Iterable[str]] = None,
        available_only: bool = True,
    ) -> List[Dict[str, Any]]:
        """Return OpenAI-compatible function definition schemas for matching active capabilities."""
        domain_set = set(domains) if domains is not None else None
        schemas: List[Dict[str, Any]] = []
        for cap_id, desc in self._capabilities.items():
            if domain_set is not None and cap_id not in domain_set:
                continue
            if available_only and not desc.is_available():
                continue
            for op in desc.operations.values():
                schemas.append(op.to_tool_definition())
        return schemas


# Global authoritative capability index
capability_index = CapabilityIndex()


def register_tool_in_index(
    name: str,
    description: str,
    schema: Dict[str, Any],
    func: Optional[Callable[..., Any]] = None,
    owner: str = "tools",
    risk_class: Optional[str] = None,
    is_interactive: bool = False,
    index: Optional[CapabilityIndex] = None,
) -> CapabilityOperation:
    """Register a tool into CapabilityIndex, creating domain descriptor on-demand if needed."""
    if index is None:
        index = capability_index

    meta = BUILTIN_TOOL_METADATA.get(name, {})
    domain = meta.get("domain", owner or "tools")
    if owner.startswith("mcp"):
        domain = owner
    elif owner == "extensions":
        domain = "extensions"

    op_id = meta.get("id", f"{domain}.{name}")
    eff_risk = risk_class or meta.get("risk_class", "safe")
    required_leases = meta.get("required_leases", ())
    timeout_sec = meta.get("timeout_sec", 15.0)
    executor_type = meta.get("executor_type")
    verifier = meta.get("verifier")

    op = CapabilityOperation(
        id=op_id,
        name=name,
        description=description,
        parameters_schema=schema,
        risk_class=eff_risk,
        required_leases=required_leases,
        timeout_sec=timeout_sec,
        is_interactive=is_interactive,
        verifier=verifier,
        executor_type=executor_type,
        func=func,
    )

    cap_desc = index.get_capability(domain)
    if cap_desc is None:
        provenance = "builtin"
        source = None
        if domain.startswith("mcp"):
            provenance = "mcp"
            source = domain.split(".", 1)[1] if "." in domain else "mcp"
        elif domain == "extensions" or domain.startswith("extension"):
            provenance = "extension"

        owner_module = _DEFAULT_DOMAIN_OWNERS.get(domain, f"charlie.{domain}")
        cap_name = _DOMAIN_NAMES.get(domain, f"{domain.capitalize()}Capability")
        cap_desc = CapabilityDescriptor(
            id=domain,
            name=cap_name,
            description=_DOMAIN_DESCRIPTIONS.get(domain, f"{cap_name} domain"),
            owner=owner_module,
            provenance=provenance,
            source=source,
            operations={},
            availability_check=_DOMAIN_AVAILABILITY.get(domain),
        )
        index.register_capability(cap_desc)

    cap_desc.add_operation(op)
    # Update secondary index lookup tables
    index._op_by_name[op.name] = (domain, op)
    index._op_by_id[op.id] = (domain, op)
    return op


def unregister_tool_from_index(name: str, index: Optional[CapabilityIndex] = None) -> bool:
    """Remove a tool from CapabilityIndex."""
    if index is None:
        index = capability_index
    if name not in index._op_by_name:
        return False
    domain, op = index._op_by_name.pop(name)
    index._op_by_id.pop(op.id, None)
    cap = index.get_capability(domain)
    if cap and name in cap.operations:
        cap.operations.pop(name, None)
    return True


# ---------------------------------------------------------------------------
# Roster & Snapshot views
# ---------------------------------------------------------------------------


def build_capability_roster(
    registry_or_index: Any,
    config: "Config",
) -> str:
    """Group every registered tool by its owner/domain into one compact line per group.

    Supports either legacy ToolRegistry or CapabilityIndex.
    """
    desktop_ok = config.desktop_control_enabled and _DESKTOP_AVAILABLE
    browser_ok = config.browser_enabled and _BROWSER_AVAILABLE

    groups: Dict[str, List[str]] = {}

    if hasattr(registry_or_index, "get_tool_definitions"):
        for defn in registry_or_index.get_tool_definitions():
            name = defn["function"]["name"]
            owner = registry_or_index.get_owner(name) or "tools"
            if owner == "desktop" and not desktop_ok:
                continue
            if owner == "browser" and not browser_ok:
                continue
            groups.setdefault(owner, []).append(f"{name} ({defn['function']['description']})")
    elif isinstance(registry_or_index, CapabilityIndex):
        for desc in registry_or_index.list_capabilities(include_unavailable=True):
            if desc.id == "desktop" and not desktop_ok:
                continue
            if desc.id == "browser" and not browser_ok:
                continue
            for op in desc.operations.values():
                owner = desc.id
                groups.setdefault(owner, []).append(f"{op.name} ({op.description})")

    if not groups:
        return ""

    lines = [_ROSTER_HEADER]
    for owner in sorted(groups):
        label = _OWNER_LABELS.get(owner, owner.capitalize())
        lines.append(f"- {label}: " + "; ".join(groups[owner]))
    return "\n".join(lines)


def build_capability_snapshot(
    registry_or_index: Any,
    config: "Config",
) -> dict:
    """Return a secret-free, machine-readable view of currently usable capabilities."""
    desktop_ok = config.desktop_control_enabled and _DESKTOP_AVAILABLE
    browser_ok = config.browser_enabled and _BROWSER_AVAILABLE
    tools = []

    if hasattr(registry_or_index, "get_tool_definitions"):
        for definition in registry_or_index.get_tool_definitions():
            function = definition["function"]
            name = function["name"]
            owner = registry_or_index.get_owner(name) or "tools"
            if owner == "desktop" and not desktop_ok:
                continue
            if owner == "browser" and not browser_ok:
                continue
            tools.append({
                "name": name,
                "description": function["description"],
                "owner": owner,
                "risk_class": registry_or_index.get_risk_class(name),
            })
    elif isinstance(registry_or_index, CapabilityIndex):
        for desc in registry_or_index.list_capabilities(include_unavailable=True):
            if desc.id == "desktop" and not desktop_ok:
                continue
            if desc.id == "browser" and not browser_ok:
                continue
            for op in desc.operations.values():
                tools.append({
                    "name": op.name,
                    "description": op.description,
                    "owner": desc.id,
                    "risk_class": op.risk_class,
                })

    return {
        "tools": tools,
        "subsystems": {
            "desktop": {"enabled": config.desktop_control_enabled, "available": _DESKTOP_AVAILABLE},
            "browser": {"enabled": config.browser_enabled, "available": _BROWSER_AVAILABLE},
            "telegram": {
                "enabled": config.telegram_enabled,
                "configured": bool(config.telegram_bot_token and config.telegram_user_id > 0),
            },
        },
    }
