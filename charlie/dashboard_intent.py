"""Pure, allowlisted show/hide intent matching for dashboard panels."""

import re
from dataclasses import dataclass
from typing import Optional

_ACTION_RE = re.compile(
    r"^\s*(?:charlie[,:]?\s*)?(show|open|hide|close)\s+"
    r"(?:me\s+)?(?:the\s+)?(.+?)\s*[.!?]*\s*$",
    re.IGNORECASE,
)
_PANEL_ALIASES = {
    "calendar": "calendar",
    "reminders": "calendar",
    "media": "media",
    "media player": "media",
    "music": "media",
    "music player": "media",
    "terminal": "terminal",
    "settings": "settings",
    "system": "system",
    "system health": "system",
    "health": "system",
    "tasks": "tasks",
    "task": "tasks",
    "chat": "chat",
    "conversation": "chat",
    "tools": "tools",
    "connections": "mcp",
    "mcp": "mcp",
}


@dataclass(frozen=True)
class DashboardPanelIntent:
    """One registered dashboard panel visibility instruction."""

    action: str
    panel_id: str


def match_dashboard_panel_intent(text: str) -> Optional[DashboardPanelIntent]:
    """Return a registered panel visibility intent, or None for normal chat."""
    match = _ACTION_RE.match(text)
    if match is None:
        return None
    action, target = match.groups()
    panel_id = _PANEL_ALIASES.get(target.lower().strip())
    if panel_id is None:
        return None
    return DashboardPanelIntent("show" if action.lower() in {"show", "open"} else "hide", panel_id)
