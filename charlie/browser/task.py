"""Tier-cascade orchestration for the browser_task tool.

Tiers 0-2 (fastpath/recipes) need no LLM and run inline; tier 3 needs one, so the caller
(Brain.browser_task in core.py) supplies complete/describe_image/approve_click. Never imports
Brain/core.py -- same constraint as agent.py, core.py imports this lazily instead.
"""

import asyncio
import logging
import time
from typing import Optional

from charlie import resource_locks
from charlie.browser import agent, fastpath, intent, recipes, session, stealth
from charlie.browser.recipes import BrowserResult
from charlie.known_apps import APP_REGISTRY
from charlie.utils import make_id

logger = logging.getLogger("charlie.browser")

_CAPABILITY = "browser"
_LOCK_POLL_INTERVAL_S = 0.5


async def _acquire_browser(owner_id: str, max_wait_s: float) -> bool:
    """Poll for the browser capability; fails open after max_wait_s so a stuck lock can't hang a caller forever."""
    elapsed = 0.0
    while not resource_locks.acquire(_CAPABILITY, owner_id):
        if elapsed >= max_wait_s:
            logger.warning("Browser capability lock wait timed out after %.1fs, proceeding anyway", max_wait_s)
            return False
        await asyncio.sleep(_LOCK_POLL_INTERVAL_S)
        elapsed += _LOCK_POLL_INTERVAL_S
    return True


def _resolve_known_site(task: str) -> Optional[str]:
    """First known website name mentioned in task, as a whole word -- for tier 2's site_search."""
    words = task.lower().split()
    for name, entry in APP_REGISTRY.items():
        if entry.is_website and name in words:
            return entry.open_cmd
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
        return await _resolve_inner(
            task, complete, describe_image, approve_click, max_steps, deadline_s, on_progress
        )
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
    if not freshness_sensitive:
        cached = session.cache_get(task)
        if cached is not None:
            return cached

    wait_start = time.monotonic()
    owner_id = make_id()
    acquired = await _acquire_browser(owner_id, max_wait_s=deadline_s)
    try:
        # deadline_s is a total budget -- subtract lock-wait time already spent, or a slow lock can double it.
        remaining_deadline_s = max(0.0, deadline_s - (time.monotonic() - wait_start))
        loop = asyncio.get_running_loop()
        result: Optional[BrowserResult] = None
        lowered = task.lower()

        if "youtube" in lowered:
            url = await loop.run_in_executor(None, fastpath.youtube_play, task)
            result = BrowserResult(url=url) if url else await loop.run_in_executor(
                None, recipes.youtube_play, task
            )

        if result is None:
            site = _resolve_known_site(task)
            if site:
                result = await loop.run_in_executor(None, recipes.site_search, site, task)

        if result is None:
            result = await agent.run_task(
                task, complete, describe_image, approve_click, max_steps, remaining_deadline_s, on_progress
            )
            if result.answer == "blocked":
                blocked_url = session.get_session().last_url
                retried = (
                    await loop.run_in_executor(None, stealth.retry_blocked, blocked_url)
                    if blocked_url else None
                )
                result = retried or BrowserResult(answer="That site blocked me and I couldn't get through.")

        if result is not None and not freshness_sensitive:
            session.cache_set(task, result)
        return result
    finally:
        if acquired:
            resource_locks.release(_CAPABILITY, owner_id)
