"""Tier-cascade orchestration for the browser_task tool.

Tiers 0-2 (fastpath/recipes) need no LLM and run inline; tier 3 needs one, so the caller
(Brain.browser_task in core.py) supplies complete/describe_image/approve_click. Never imports
Brain/core.py -- same constraint as agent.py, core.py imports this lazily instead.
"""

import asyncio
import logging
import re
import time
from typing import Optional
from urllib.parse import urlparse

from charlie import resource_locks
from charlie.browser import agent, controller, fastpath, intent, recipes, session, stealth
from charlie.browser.recipes import BrowserResult
from charlie.known_apps import APP_REGISTRY, resolve_website_url
from charlie.utils import make_id

logger = logging.getLogger("charlie.browser")

_CAPABILITY = "browser"
_LOCK_POLL_INTERVAL_S = 0.5
_NON_CACHEABLE_NAV_WORDS = (
    "search",
    "find",
    "look up",
    "open",
    "go back",
    "navigate",
    "filter",
    "show only",
    "exclude shorts",
)


def _cacheable(task: str, freshness_sensitive: bool) -> bool:
    lowered = task.lower()
    return not freshness_sensitive and not any(word in lowered for word in _NON_CACHEABLE_NAV_WORDS)


async def _acquire_browser(owner_id: str, max_wait_s: float) -> bool:
    """Poll for the browser capability without allowing concurrent page mutations."""
    elapsed = 0.0
    while not resource_locks.acquire(_CAPABILITY, owner_id):
        if elapsed >= max_wait_s:
            logger.warning("Browser capability lock wait timed out after %.1fs", max_wait_s)
            return False
        await asyncio.sleep(_LOCK_POLL_INTERVAL_S)
        elapsed += _LOCK_POLL_INTERVAL_S
    return True


def _resolve_known_site(task: str) -> Optional[str]:
    """Resolve a site hint or arbitrary HTTP(S) target from current intent text."""
    url_match = re.search(r"https?://[^\s<>\"']+", task, re.IGNORECASE)
    if url_match:
        return resolve_website_url(url_match.group(0))
    site_match = re.search(r"\bon\s+([a-z0-9][\w.-]*)", task, re.IGNORECASE)
    if site_match:
        resolved = resolve_website_url(site_match.group(1))
        if resolved:
            return resolved
    lowered = task.casefold()
    for name, entry in sorted(APP_REGISTRY.items(), key=lambda item: len(item[0]), reverse=True):
        if entry.is_website and re.search(rf"\b{re.escape(name)}\b", lowered):
            return resolve_website_url(name)
    return None


async def resolve(
    task: str,
    complete: agent.Complete,
    describe_image: Optional[agent.DescribeImage] = None,
    approve_click: Optional[agent.ApproveClick] = None,
    max_steps: int = 3,
    deadline_s: float = 25.0,
    on_progress=None,
) -> BrowserResult:
    start_time = time.perf_counter()
    outcome = "success"
    try:
        return await _resolve_inner(task, complete, describe_image, approve_click, max_steps, deadline_s, on_progress)
    except Exception as e:
        outcome = f"error: {type(e).__name__}"
        raise
    finally:
        elapsed = (time.perf_counter() - start_time) * 1000
        logger.debug(f"browser.task.resolve took {elapsed:.2f}ms, outcome: {outcome}")


async def _resolve_inner(
    task: str,
    complete: agent.Complete,
    describe_image: Optional[agent.DescribeImage] = None,
    approve_click: Optional[agent.ApproveClick] = None,
    max_steps: int = 3,
    deadline_s: float = 25.0,
    on_progress=None,
) -> BrowserResult:
    """Run the tier cascade for `task`, falling through tier by tier, and cache the result."""
    freshness_sensitive = intent.is_freshness_sensitive(task)
    if _cacheable(task, freshness_sensitive):
        cached = session.cache_get(task)
        if cached is not None:
            return cached

    wait_start = time.monotonic()
    owner_id = make_id()
    acquired = await _acquire_browser(owner_id, max_wait_s=deadline_s)
    if not acquired:
        return BrowserResult(
            answer="The browser is busy with another task. Try again shortly.",
            verification="capability-busy",
        )
    controller_lease = False
    try:
        controller.acquire_task_lease()
        controller_lease = True
        # deadline_s is a total budget -- subtract lock-wait time already spent, or a slow lock can double it.
        remaining_deadline_s = max(0.0, deadline_s - (time.monotonic() - wait_start))
        loop = asyncio.get_running_loop()
        result: Optional[BrowserResult] = None
        lowered = task.lower()
        youtube_open_request = ("open" in lowered or "play" in lowered) and ("video" in lowered or "youtube" in lowered)

        current_url = session.get_session().last_url or ""
        if result is None and current_url:
            try:
                live_url = await loop.run_in_executor(None, lambda: controller.run(lambda page: page.url, timeout=5.0))
                if live_url:
                    current_url = live_url
            except Exception:
                pass
        parsed_intent = intent.parse_browser_intent(
            task,
            urlparse(current_url).hostname or session.get_session().current_domain or "",
        )
        if result is None and current_url and parsed_intent.operation in {
            "BACK",
            "FILTER",
            "SORT",
            "READ",
            "CURRENT_PAGE_FACT",
            "COMPARE",
            "PRODUCT_SELECT",
        }:
            result = await loop.run_in_executor(None, recipes.apply_current_page_intent, task, parsed_intent)
        if "youtube.com/watch?v=" in current_url:
            player_result = await loop.run_in_executor(None, recipes.youtube_player_control, task)
            if player_result is not None:
                result = player_result
        if result is None and "filter" in lowered and ("video" in lowered or "short" in lowered):
            if "youtube.com/results" not in current_url:
                result = BrowserResult(
                    answer="filter unavailable in current YouTube UI",
                    verification="youtube-video-filter-unavailable",
                    site="youtube",
                )
        if (
            result is None
            and "youtube.com/results" in current_url
            and ("filter" in lowered or "show only" in lowered or "exclude shorts" in lowered)
        ):
            result = await loop.run_in_executor(None, recipes.youtube_filter_videos)
        if result is None and "youtube.com/results" in current_url and (
            intent.has_open_intent(task) or "open" in lowered.split()
        ):
            result = await loop.run_in_executor(None, recipes.youtube_open_current, task)

        # Compatibility adapter only: generic current-page operations own the
        # normal path; the legacy ecommerce adapter is consulted after generic
        # controls cannot verify the live result set.
        if result is None and "flipkart.com" in current_url.lower():
            result = await loop.run_in_executor(None, recipes.flipkart_action, task)

        if result is None and "youtube" in lowered:
            if youtube_open_request:
                result = BrowserResult(
                    answer="I couldn't verify a current YouTube results page to open from.",
                    verification="youtube-open-current-page-unavailable",
                    site="youtube",
                )
            else:
                youtube_intent = intent.parse_site_intent(task, "youtube")
                query = youtube_intent.query if youtube_intent else task
                if intent.is_search_intent(task):
                    result = await loop.run_in_executor(
                        None, recipes.site_search, "https://www.youtube.com", query, "youtube"
                    )
                else:
                    url = await loop.run_in_executor(None, fastpath.youtube_play, query)
                    result = (
                        BrowserResult(
                            url=url,
                            success=True,
                            verification="youtube-watch-url",
                            site="youtube",
                            query=query,
                        )
                        if url
                        else await loop.run_in_executor(None, recipes.youtube_play, query)
                    )

        if result is None:
            site = _resolve_known_site(task)
            if site:
                site_name = next(
                    (name for name, entry in APP_REGISTRY.items() if entry.is_website and entry.open_cmd == site),
                    None,
                )
                site_intent = intent.parse_site_intent(task, site_name or "")
                if site_intent:
                    query = site_intent.query
                else:
                    parsed_site_intent = intent.parse_browser_intent(task, urlparse(site).hostname or "")
                    query = parsed_site_intent.query or task
                result = await loop.run_in_executor(None, recipes.site_search, site, query, site_name)

        if result is None:
            repository_result = await loop.run_in_executor(
                None, recipes.current_repository_search, task, session.get_session().last_url
            )
            if repository_result is not None:
                result = repository_result

        if result is None:
            result = await agent.run_task(
                task, complete, describe_image, approve_click, max_steps, remaining_deadline_s, on_progress
            )
            if result.answer == "blocked":
                blocked_url = session.get_session().last_url
                retried = await loop.run_in_executor(None, stealth.retry_blocked, blocked_url) if blocked_url else None
                result = retried or BrowserResult(answer="That site blocked me and I couldn't get through.")

        if result is not None and result.success and _cacheable(task, freshness_sensitive):
            session.cache_set(task, result)
        return result
    finally:
        if controller_lease:
            controller.release_task_lease()
        if acquired:
            resource_locks.release(_CAPABILITY, owner_id)
