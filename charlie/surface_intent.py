"""Natural-language extraction for registry-driven semantic HUD requests."""

import re
from dataclasses import dataclass
from typing import Optional

from charlie.presentation_registry import PresentationRegistry, get_presentation_registry

_ACTION_RE = re.compile(
    r"^\s*(?:charlie[,:]?\s*)?(show|open|hide|close)\s+"
    r"(?:me\b\s*,?\s*)?(?:the\s+)?(.+?)\s*[.!?]*\s*$",
    re.IGNORECASE,
)
_CLEAR_RE = re.compile(r"^\s*(?:charlie[,:]?\s*)?clear\s+(?:the\s+)?screen\s*[.!?]*\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class SurfaceRequest:
    """One semantic request after registry-driven target resolution."""

    action: str
    surface_id: Optional[str] = None


def match_surface_request(
    text: str,
    registry: Optional[PresentationRegistry] = None,
) -> Optional[SurfaceRequest]:
    """Extract language and resolve the target exclusively through the registry."""
    if _CLEAR_RE.match(text):
        return SurfaceRequest("clear_screen")
    match = _ACTION_RE.match(text)
    if match is None:
        return None
    action, target = match.groups()
    registry = registry or get_presentation_registry()
    resolved = registry.resolve_surface(target.strip())
    if not resolved.resolved:
        return None
    return SurfaceRequest(
        "show" if action.lower() in {"show", "open"} else "hide",
        resolved.canonical,
    )
