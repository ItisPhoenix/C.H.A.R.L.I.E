"""Deterministic phrase detection for the browser subsystem -- no LLM, matches core.py's
_detect_open_app style of fast-path (local/remote models both ignore prompted tool instructions
on latency-critical paths more often than a plain keyword check misses).
"""

import re
from dataclasses import dataclass
from typing import Optional

_OPEN_VERBS = ("play", "watch", "listen", "put on")
_OPEN_PHRASES = ("open it", "show me", "in my browser", "pull it up", "show it to me", "open that")
_BARE_FOLLOWUP_PHRASES = {"open it", "open that", "show me", "show me that", "show it", "pull it up", "play it"}
_FRESHNESS_KEYWORDS = ("price", "news", "latest", "today", "current", "now", "live", "score", "weather")

_SEARCH_QUERY_RE = re.compile(
    r"\b(?:search|find|look\s+up|browse|check)\s+(?:for\s+)?(?P<query>.+?)\s+on\s+(?P<site>[a-z0-9][\w.]*)\b",
    re.IGNORECASE,
)
_OPEN_AND_SEARCH_RE = re.compile(
    r"\b(?:search|find|look\s+up|browse|check)\s+(?:for\s+)?(?P<query>.+)$",
    re.IGNORECASE,
)
_MEDIA_QUERY_RE = re.compile(
    r"\b(?:play|watch|listen\s+to|put\s+on)\s+(?P<query>.+?)\s+(?:on\s+)?(?P<site>youtube|spotify)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BrowserIntent:
    """Normalized site task details shared by recipes and the tier cascade."""

    site: str
    query: str
    original: str


def parse_site_intent(task: str, site: str) -> Optional[BrowserIntent]:
    """Extract the user query from a site command without losing the original task."""
    original = task.strip()
    site_lower = site.lower().strip()
    search_match = _SEARCH_QUERY_RE.search(original)
    if search_match and search_match.group("site").lower().strip(".,!? ") == site_lower:
        query = search_match.group("query")
    else:
        media_match = _MEDIA_QUERY_RE.search(original)
        if media_match and media_match.group("site").lower() == site_lower:
            query = media_match.group("query")
        else:
            open_search = _OPEN_AND_SEARCH_RE.search(original)
            query = open_search.group("query") if site_lower in original.lower().split() and open_search else original
    query = re.sub(r"\s+", " ", query).strip(" .,!?\t\r\n")
    if not query:
        return None
    return BrowserIntent(site=site_lower, query=query, original=original)


def has_open_intent(task: str) -> bool:
    """True when the request implies the result should open in the user's real browser."""
    lowered = f" {task.lower().strip()} "
    if any(f" {verb} " in lowered for verb in _OPEN_VERBS):
        return True
    return any(phrase in lowered for phrase in _OPEN_PHRASES)


def is_bare_followup(task: str) -> bool:
    """True for a standalone 'open it' / 'show me that' with no new query -- reuse last_url."""
    return task.lower().strip().rstrip(".!") in _BARE_FOLLOWUP_PHRASES


def is_freshness_sensitive(task: str) -> bool:
    """True when the task looks time-sensitive enough that a cached result would be wrong."""
    lowered = task.lower()
    return any(keyword in lowered for keyword in _FRESHNESS_KEYWORDS)
