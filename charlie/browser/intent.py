"""Deterministic phrase detection for the browser subsystem -- no LLM, matches core.py's
_detect_open_app style of fast-path (CLAUDE.md 11.2: local/remote models both ignore prompted
tool instructions on latency-critical paths more often than a plain keyword check misses).
"""

_OPEN_VERBS = ("play", "watch", "listen", "put on")
_OPEN_PHRASES = ("open it", "show me", "in my browser", "pull it up", "show it to me", "open that")
_BARE_FOLLOWUP_PHRASES = {"open it", "open that", "show me", "show me that", "show it", "pull it up", "play it"}
_FRESHNESS_KEYWORDS = ("price", "news", "latest", "today", "current", "now", "live", "score", "weather")


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
