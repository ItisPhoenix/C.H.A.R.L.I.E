"""Tier 1 (known-site recipes) and tier 2 (generic searchbox flow) -- no LLM involved.

Deterministic beats prompted tool calls on latency-critical paths; these run
before the agent loop (tier 3) ever gets a turn. Each function drives its own controller.run()
call, so callers invoke them directly without wrapping.
"""

import logging
import re
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import quote, urljoin

from charlie.browser import actions, controller, session
from charlie.browser.observation import Mark, parse_snapshot, rank_and_cap
from charlie.known_apps import APP_REGISTRY

logger = logging.getLogger("charlie.browser")

_MIN_DURATION_S = 60
_SHORTS_MARKER = "/shorts/"
_SPELLED_DURATION_RE = re.compile(r"(?:(\d+)\s*hours?)?,?\s*(?:(\d+)\s*minutes?)?,?\s*(?:(\d+)\s*seconds?)?")
# ponytail: denylist, not a real chrome/content classifier -- good enough for the generic fallback
_CHROME_LINK_NAMES = {"home", "ask", "sign up", "log in", "about", "help", "settings", "skip to main content"}
_SETTLE_WAIT_MS = 800
# Outer bound in case a page hangs somewhere actions.py's own goto/selector timeouts don't cover.
_RECIPE_TIMEOUT_S = 20.0


@dataclass
class BrowserResult:
    url: Optional[str] = None
    answer: Optional[str] = None


def resolve_site(name: str) -> Optional[str]:
    """Look up a known website's URL by name, reusing the single site registry core.py uses."""
    entry = APP_REGISTRY.get(name.lower().strip())
    if entry and entry.is_website:
        return entry.open_cmd
    return None


def _observe(page) -> List[Mark]:
    marks = rank_and_cap(parse_snapshot(page.locator("body").aria_snapshot(mode="ai")))
    session.record_marks(marks)
    return marks


def _spelled_duration_to_seconds(text: str) -> Optional[int]:
    match = _SPELLED_DURATION_RE.search(text)
    if not match or not any(match.groups()):
        return None
    hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def youtube_play(query: str) -> Optional[BrowserResult]:
    """Tier 1: a real page, used when tier 0's HTTP parse (fastpath.youtube_play) found nothing."""
    def run(page):
        actions.navigate(page, f"https://www.youtube.com/results?search_query={quote(query)}",
                          wait_selector="a#video-title")
        marks = _observe(page)
        candidates = [
            m for m in marks
            if m.role == "link" and m.href and "/watch?v=" in m.href and _SHORTS_MARKER not in m.href
        ]
        query_words = {w.lower() for w in query.split() if len(w) > 2}

        def long_enough(mark: Mark) -> bool:
            seconds = _spelled_duration_to_seconds(mark.name)
            return seconds is None or seconds >= _MIN_DURATION_S

        for mark in candidates:
            if long_enough(mark) and query_words & set(mark.name.lower().split()):
                return BrowserResult(url=urljoin(page.url, mark.href))
        for mark in candidates:
            if long_enough(mark):
                return BrowserResult(url=urljoin(page.url, mark.href))
        return None

    try:
        return controller.run(run, timeout=_RECIPE_TIMEOUT_S)
    except Exception:
        logger.warning("Tier 1 youtube_play recipe failed for %r", query, exc_info=True)
        return None


def site_search(site_url: str, query: str) -> Optional[BrowserResult]:
    """Tier 2: works on any site with a visible search box -- no site-specific knowledge needed."""
    def run(page):
        actions.navigate(page, site_url)
        marks = _observe(page)
        # combobox can be a <select> (e.g. Amazon's category dropdown) that .fill() rejects -- text inputs first.
        candidates = [m for m in marks if m.role in ("textbox", "searchbox")]
        candidates += [m for m in marks if m.role == "combobox"]
        for search_mark in candidates:
            try:
                actions.type_text(page, search_mark.mark_id, query, submit=True)
                break
            except Exception:
                logger.debug("site_search: mark %d not fillable, trying next candidate", search_mark.mark_id)
        else:
            return None
        try:
            page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            logger.debug("site_search: no navigation detected after submit on %s", site_url)
        page.wait_for_timeout(_SETTLE_WAIT_MS)  # most client-rendered results settle quickly after domcontentloaded
        marks_after = _observe(page)
        links = [
            m for m in marks_after
            if m.role == "link" and m.name and m.name.lower() not in _CHROME_LINK_NAMES
        ][:5]
        if not links:
            return BrowserResult(url=page.url)
        return BrowserResult(url=page.url, answer="; ".join(m.name for m in links)[:400])

    try:
        return controller.run(run, timeout=_RECIPE_TIMEOUT_S)
    except Exception:
        logger.warning("Tier 2 site_search failed for %s %r", site_url, query, exc_info=True)
        return None
