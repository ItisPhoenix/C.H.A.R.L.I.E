"""Single browser session state: mark cache, last resolved URL, visited/action history.

v1 is single-page (no tabs), so this is one module-level session tied to the controller's
browser process lifetime -- not per-turn or per-chat-session state. Mirrors the resolve_mark
contract of charlie/desktop/uia.py.
"""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from charlie.browser.errors import MarkNotFound
from charlie.browser.observation import Mark
from charlie.utils import make_id

# task -> (BrowserResult, expiry) -- module-level since it outlives any one browser relaunch.
_CACHE_TTL_S = 600.0
_task_cache: Dict[str, tuple] = {}


@dataclass
class BrowserSession:
    session_id: str
    last_url: Optional[str] = None
    visited_urls: List[str] = field(default_factory=list)
    action_history: List[str] = field(default_factory=list)
    marks: Dict[int, Mark] = field(default_factory=dict)


_session: Optional[BrowserSession] = None


def get_session() -> BrowserSession:
    global _session
    if _session is None:
        _session = BrowserSession(session_id=make_id())
    return _session


def reset_session() -> None:
    """Called by the controller on relaunch -- a new browser process means stale marks/history."""
    global _session
    _session = None


def record_marks(marks: List[Mark]) -> None:
    get_session().marks = {m.mark_id: m for m in marks}


def invalidate_marks() -> None:
    """Discard references tied to the previous DOM snapshot."""
    get_session().marks.clear()


def resolve_mark(mark_id: int) -> Mark:
    mark = get_session().marks.get(mark_id)
    if mark is None:
        raise MarkNotFound(f"Mark id {mark_id} not found -- observe the page again.")
    return mark


def record_action(description: str) -> None:
    get_session().action_history.append(description)


def record_navigation(url: str) -> None:
    session = get_session()
    session.marks.clear()
    session.last_url = url
    session.visited_urls.append(url)


def cache_get(task: str):
    entry = _task_cache.get(task)
    if entry is None:
        return None
    result, expiry = entry
    if time.monotonic() > expiry:
        del _task_cache[task]
        return None
    return result


def cache_set(task: str, result) -> None:
    _task_cache[task] = (result, time.monotonic() + _CACHE_TTL_S)
