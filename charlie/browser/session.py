"""Single browser session state: mark cache, last resolved URL, visited/action history.

v1 is single-page (no tabs), so this is one module-level session tied to the controller's
browser process lifetime -- not per-turn or per-chat-session state. Mirrors the resolve_mark
contract of charlie/desktop/uia.py.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

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
    current_url: Optional[str] = None
    current_domain: Optional[str] = None
    page_type: Optional[str] = None
    search_query: Optional[str] = None
    page_capabilities: set[str] = field(default_factory=set)
    active_constraints: Dict[str, Any] = field(default_factory=dict)
    sort_state: Optional[Dict[str, str]] = None
    last_verified_results: List[Dict[str, Any]] = field(default_factory=list)
    selected_result: Optional[Dict[str, Any]] = None
    page_state: Dict[str, Any] = field(default_factory=dict)
    navigation_version: int = 0
    observation_version: int = 0
    observed_url: Optional[str] = None
    observed_signature: Optional[str] = None
    visited_urls: List[str] = field(default_factory=list)
    action_history: List[str] = field(default_factory=list)
    marks: Dict[int, Mark] = field(default_factory=dict)

    def invalidate_page_state(self) -> None:
        self.marks.clear()
        self.page_type = None
        self.search_query = None
        self.page_capabilities.clear()
        self.active_constraints.clear()
        self.sort_state = None
        self.last_verified_results.clear()
        self.selected_result = None
        self.page_state.clear()
        self.observed_url = None
        self.observed_signature = None


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


def invalidate_observation() -> None:
    """Discard page-derived state after a material DOM or navigation change."""
    current = get_session()
    current.marks.clear()
    current.last_verified_results.clear()
    current.selected_result = None
    current.page_state.clear()
    current.active_constraints.clear()
    current.sort_state = None
    current.observed_url = None
    current.observed_signature = None
    current.observation_version += 1


def resolve_mark(mark_id: int) -> Mark:
    mark = get_session().marks.get(mark_id)
    if mark is None:
        raise MarkNotFound(f"Mark id {mark_id} not found -- observe the page again.")
    return mark


def record_action(description: str) -> None:
    get_session().action_history.append(description)


def record_navigation(url: str) -> None:
    session = get_session()
    parsed = urlparse(url or "")
    query = parse_qs(parsed.query)
    search_query = next(
        (
            values[0].strip()
            for key in ("q", "query", "search_query", "search")
            for values in (query.get(key, []),)
            if values and values[0].strip()
        ),
        None,
    )
    session.invalidate_page_state()
    session.last_url = url
    session.current_url = url
    session.current_domain = (parsed.hostname or "").lower() or None
    session.search_query = search_query
    session.navigation_version += 1
    session.visited_urls.append(url)


def record_observation(
    url: str,
    *,
    page_type: Optional[str] = None,
    capabilities: Optional[List[str]] = None,
    search_query: Optional[str] = None,
    results: Optional[List[Dict[str, Any]]] = None,
    selected_result: Optional[Dict[str, Any]] = None,
    signature: Optional[str] = None,
) -> None:
    """Store facts from one fresh page observation, never from a prior site recipe."""
    current = get_session()
    if url != current.current_url:
        record_navigation(url)
        current = get_session()
    elif current.observed_signature and signature and current.observed_signature != signature:
        invalidate_observation()
        current = get_session()
    current.current_url = url
    current.last_url = url
    current.observed_url = url
    current.observation_version += 1
    if page_type is not None:
        current.page_type = page_type
    if capabilities is not None:
        current.page_capabilities = set(capabilities)
    if search_query is not None:
        current.search_query = search_query
    if results is not None:
        current.last_verified_results = [dict(result) for result in results]
    if selected_result is not None:
        current.selected_result = dict(selected_result)
    current.observed_signature = signature


def set_constraint(name: str, value: Any) -> None:
    """Record one constraint only after its current rendered result set verifies it."""
    get_session().active_constraints[name] = value


def clear_constraint(name: str) -> None:
    get_session().active_constraints.pop(name, None)


def clear_verified_state() -> None:
    """Clear generic page facts; useful on browser reset and deterministic test setup."""
    current = get_session()
    current.invalidate_page_state()


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
