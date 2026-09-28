"""Trust-level tagging for tool results.

Deterministic, out-of-band classification by tool name -- not something the
LLM can influence -- so retrieved file, session, graph, web, MCP, and screen
content can be told apart from what the user typed in this turn. Everything
else (config, user turns) is trusted by default.
"""

from typing import Literal

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
_EXTERNAL_TOOL_PREFIXES = ("mcp_",)


def trust_level_for_tool(tool_name: str) -> TrustLevel:
    """Classify a tool result's trust level from its tool name alone."""
    if tool_name in _EXTERNAL_TOOL_NAMES or tool_name.startswith(_EXTERNAL_TOOL_PREFIXES):
        return "tool_external"
    return "user_turn"
