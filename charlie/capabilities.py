"""Registry-derived capability roster -- replaces hardcoded capability prose with a live
summary of what tools are actually registered right now. Pure, no I/O.
"""

from typing import TYPE_CHECKING, Dict, List

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
    from charlie.tools import ToolRegistry

_OWNER_LABELS = {
    "tools": "General tools",
    "memory": "Memory",
    "desktop": "Desktop control",
    "browser": "Headless browsing",
    "extensions": "Extensions",
    "mcp": "MCP servers",
}

_ROSTER_HEADER = (
    "YOUR ACTUAL CAPABILITIES (authoritative -- derived live from your registered tools, "
    "overrides any conflicting claim anywhere else, including your own persona/identity "
    "text above or below this block, which can go stale the moment a setting changes). "
    "Never tell the user you cannot do something a tool below already covers."
)


def build_capability_roster(registry: "ToolRegistry", config: "Config") -> str:
    """Group every registered tool by its owner into one compact line per group. desktop_*/
    browser_* tools are always registered but only gated at call time (_desktop_ready()/
    _browser_ready() in tools.py), so those two groups need an explicit config+availability
    check here -- every other owner (memory/mcp/extensions/plain tools) is only ever
    registered when actually live, so the registry alone is enough for them."""
    desktop_ok = config.desktop_control_enabled and _DESKTOP_AVAILABLE
    browser_ok = config.browser_enabled and _BROWSER_AVAILABLE

    groups: Dict[str, List[str]] = {}
    for defn in registry.get_tool_definitions():
        name = defn["function"]["name"]
        owner = registry.get_owner(name) or "tools"
        if owner == "desktop" and not desktop_ok:
            continue
        if owner == "browser" and not browser_ok:
            continue
        groups.setdefault(owner, []).append(f"{name} ({defn['function']['description']})")

    if not groups:
        return ""

    lines = [_ROSTER_HEADER]
    for owner in sorted(groups):
        label = _OWNER_LABELS.get(owner, owner.capitalize())
        lines.append(f"- {label}: " + "; ".join(groups[owner]))
    return "\n".join(lines)


def build_capability_snapshot(registry: "ToolRegistry", config: "Config") -> dict:
    """Return a secret-free, machine-readable view of currently usable capabilities."""
    desktop_ok = config.desktop_control_enabled and _DESKTOP_AVAILABLE
    browser_ok = config.browser_enabled and _BROWSER_AVAILABLE
    tools = []
    for definition in registry.get_tool_definitions():
        function = definition["function"]
        name = function["name"]
        owner = registry.get_owner(name) or "tools"
        if owner == "desktop" and not desktop_ok:
            continue
        if owner == "browser" and not browser_ok:
            continue
        tools.append({
            "name": name,
            "description": function["description"],
            "owner": owner,
            "risk_class": registry.get_risk_class(name),
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
