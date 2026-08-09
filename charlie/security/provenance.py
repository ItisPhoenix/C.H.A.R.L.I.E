"""Trust-level tagging for tool results.

Deterministic, out-of-band classification by tool name -- not something the
LLM can influence -- so text that came from the open web, a rendered page, an
MCP server, on-screen content, or stored vector memory can be told apart from
what the user actually typed. Everything else (config, user turns) is
trusted by default.
"""

from typing import Literal

TrustLevel = Literal["config", "user_turn", "tool_external"]

# Tools whose results carry attacker-influenceable text.
_EXTERNAL_TOOL_NAMES = frozenset(
    {
        "web_search",
        "browser_read",
        "browser_task",
        "desktop_read_screen",
        "desktop_observe",
        "vector_memory",
    }
)
# MCP tools are registered with this prefix (see mcp_client.py register_tools_into).
_EXTERNAL_TOOL_PREFIXES = ("mcp_",)


def trust_level_for_tool(tool_name: str) -> TrustLevel:
    """Classify a tool result's trust level from its tool name alone."""
    if tool_name in _EXTERNAL_TOOL_NAMES or tool_name.startswith(_EXTERNAL_TOOL_PREFIXES):
        return "tool_external"
    return "user_turn"
