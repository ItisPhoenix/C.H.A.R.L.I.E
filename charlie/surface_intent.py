"""Pure, allowlisted voice surface requests for the canonical React HUD."""

import re
from dataclasses import dataclass
from typing import Optional

_ACTION_RE = re.compile(
    r"^\s*(?:charlie[,:]?\s*)?(show|open|hide|close)\s+"
    r"(?:me\s+)?(?:the\s+)?(.+?)\s*[.!?]*\s*$",
    re.IGNORECASE,
)
_SURFACE_ALIASES = {
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
class SurfaceRequest:
    """One allowlisted request to show or dismiss a React HUD surface."""

    action: str
    surface_id: str


def match_surface_request(text: str) -> Optional[SurfaceRequest]:
    """Return a registered surface request, or None for normal conversation."""
    match = _ACTION_RE.match(text)
    if match is None:
        return None
    action, target = match.groups()
    surface_id = _SURFACE_ALIASES.get(target.lower().strip())
    if surface_id is None:
        return None
    return SurfaceRequest("show" if action.lower() in {"show", "open"} else "hide", surface_id)
