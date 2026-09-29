"""Trust-level tagging for tool results.

Deterministic, out-of-band classification by tool name and registered
capability provenance -- not something the LLM can influence -- so retrieved
file, session, graph, web, MCP, screen, and extension content can be told
apart from what the user typed in this turn. Everything else (config, user
turns) is trusted by default.
"""

from typing import Literal, Optional

TrustLevel = Literal["config", "user_turn", "tool_external"]

# Tools whose results carry attacker-influenceable text.
_EXTERNAL_TOOL_NAMES = frozenset(
    {
        "file_read",
        "session_search",
        "graph_query",
        "web_search",
        "web_research",
        "browser_read",
        "browser_task",
        "desktop_read_screen",
        "desktop_observe",
        "vector_memory",
    }
)
# MCP tools are registered with this prefix (see mcp_client.py register_tools_into).
_EXTERNAL_TOOL_PREFIXES = ("mcp_", "plugin_")
_DYNAMIC_EXTERNAL_TOOL_PREFIXES = ("skill_", "api_")


def _registered_capability_provenance(tool_name: str) -> Optional[str]:
    """Return registered capability provenance, when this tool is indexed."""
    try:
        from charlie.capabilities import capability_index

        domain = capability_index.get_operation_domain(tool_name)
        capability = capability_index.get_capability(domain) if domain else None
        return capability.provenance if capability is not None else None
    except (ImportError, AttributeError):
        return None


def trust_level_for_tool(tool_name: str) -> TrustLevel:
    """Classify a tool result from its name and registered provenance."""
    if tool_name in _EXTERNAL_TOOL_NAMES or tool_name.startswith(_EXTERNAL_TOOL_PREFIXES):
        return "tool_external"
    provenance = _registered_capability_provenance(tool_name)
    if provenance == "extension":
        return "tool_external"
    if provenance == "builtin":
        return "user_turn"
    if tool_name.startswith(_DYNAMIC_EXTERNAL_TOOL_PREFIXES):
        return "tool_external"
    return "user_turn"
