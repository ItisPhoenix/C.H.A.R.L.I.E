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
# Outer bound in case a page hangs somewhere actions.py's own goto/selector timeouts don't cover.
_RECIPE_TIMEOUT_S = 20.0


@dataclass
class BrowserResult:
    url: Optional[str] = None
    answer: Optional[str] = None
    success: bool = False
    verification: str = "unverified"
    site: Optional[str] = None
    query: Optional[str] = None


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


def _link_count(page) -> int:
    try:
        return page.locator("a[href]").count()
    except Exception:
        return 0


def _wait_for_search_results(page, before_url: str, before_links: int) -> bool:
    """Wait for navigation or a changed main-content link set instead of sleeping."""
    try:
        page.wait_for_function(
            """state => {
                const root = document.querySelector('main, [role="main"], article, #mw-content-text') || document.body;
                return location.href !== state.url || root.querySelectorAll('a[href]').length > state.links;
            }""",
            {"url": before_url, "links": before_links},
            timeout=5000,
        )
        return True
    except Exception:
        logger.debug("site_search: result condition did not settle before timeout")
        return False


def _content_root(page, preferred_selector: Optional[str] = None):
    """Prefer semantic content containers so navigation chrome is not reported as a result."""
    selectors = ([preferred_selector] if preferred_selector else []) + [
        "main", '[role="main"]', "article", "#mw-content-text"
    ]
    for selector in selectors:
        locator = page.locator(selector)
        if locator.count():
            return locator.first
    return None


def _content_text(page, preferred_selector: Optional[str] = None) -> str:
    root = _content_root(page, preferred_selector)
    if root is None:
        return ""
    try:
        return root.inner_text(timeout=1500).strip()
    except Exception:
        return ""


def _content_links(page, preferred_selector: Optional[str] = None) -> List[str]:
    root = _content_root(page, preferred_selector)
    if root is None:
        return []
    links = []
    locator = root.locator("a[href]")
    for index in range(min(locator.count(), 12)):
        try:
            name = locator.nth(index).inner_text(timeout=500).strip()
        except Exception:
            continue
        if name and name.lower() not in _CHROME_LINK_NAMES:
            links.append(name)
    return links


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
                return BrowserResult(
                    url=urljoin(page.url, mark.href),
                    success=True,
                    verification="youtube-watch-url",
                    site="youtube",
                    query=query,
                )
        for mark in candidates:
            if long_enough(mark):
                return BrowserResult(
                    url=urljoin(page.url, mark.href),
                    success=True,
                    verification="youtube-watch-url",
                    site="youtube",
                    query=query,
                )
        return None

    try:
        return controller.run(run, timeout=_RECIPE_TIMEOUT_S)
    except Exception:
        logger.warning("Tier 1 youtube_play recipe failed for %r", query, exc_info=True)
        return None


def site_search(site_url: str, query: str, site_name: Optional[str] = None) -> Optional[BrowserResult]:
    """Tier 2: works on any site with a visible search box -- no site-specific knowledge needed."""
    def run(page):
        is_wikipedia = bool(site_name and site_name.lower() == "wikipedia") or "wikipedia.org" in site_url
        if is_wikipedia:
            search_url = f"https://en.wikipedia.org/w/index.php?search={quote(query)}"
            actions.navigate(page, search_url, wait_selector="#mw-content-text")
            content = _content_text(page, "#mw-content-text")
            title = page.title().strip()
            query_words = [word.lower() for word in query.split() if len(word) > 2]
            lowered_content = f"{title}\n{content}".lower()
            if len(content) >= 80 and query_words and all(word in lowered_content for word in query_words):
                return BrowserResult(
                    url=page.url,
                    answer=f"{title}: {content[:500]}",
                    success=True,
                    verification="wikipedia-content",
                    site=site_name,
                    query=query,
                )
            return BrowserResult(
                url=page.url,
                answer=f"I reached Wikipedia, but couldn't verify an article for '{query}'.",
                verification="wikipedia-content-not-found",
                site=site_name,
                query=query,
            )

        actions.navigate(page, site_url)
        marks = _observe(page)
        before_url = page.url
        before_links = _link_count(page)
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
        settled = _wait_for_search_results(page, before_url, before_links)
        if not settled:
            return BrowserResult(
                url=page.url,
                answer=f"I reached {site_name or site_url}, but couldn't verify search results for '{query}'.",
                verification="search-not-settled",
                site=site_name,
                query=query,
            )
        links = _content_links(page)[:5]
        if not links:
            return BrowserResult(
                url=page.url,
                answer=f"I reached {site_name or site_url}, but couldn't verify search results for '{query}'.",
                verification="content-not-found",
                site=site_name,
                query=query,
            )
        content = _content_text(page)
        if len(content) < 40:
            return BrowserResult(
                url=page.url,
                answer=f"I reached {site_name or site_url}, but couldn't verify search results for '{query}'.",
                verification="content-too-short",
                site=site_name,
                query=query,
            )
        return BrowserResult(
            url=page.url,
            answer="; ".join(links)[:400],
            success=True,
            verification="content-links",
            site=site_name,
            query=query,
        )

    try:
        return controller.run(run, timeout=_RECIPE_TIMEOUT_S)
    except Exception:
        logger.warning("Tier 2 site_search failed for %s %r", site_url, query, exc_info=True)
        return None
