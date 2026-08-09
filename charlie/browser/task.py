"""Tier-cascade orchestration for the browser_task tool.

Tiers 0-2 (fastpath/recipes) need no LLM and run inline; tier 3 needs one, so the caller
(Brain.browser_task in core.py) supplies complete/describe_image/approve_click. Never imports
Brain/core.py -- same constraint as agent.py, core.py imports this lazily instead.
"""

import asyncio
import logging
from typing import Optional

from charlie.browser import agent, fastpath, intent, recipes, session, stealth
from charlie.browser.recipes import BrowserResult
from charlie.known_apps import APP_REGISTRY

logger = logging.getLogger("charlie.browser")


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
    """Run the tier cascade for `task`, falling through tier by tier, and cache the result."""
    freshness_sensitive = intent.is_freshness_sensitive(task)
    if not freshness_sensitive:
        cached = session.cache_get(task)
        if cached is not None:
            return cached

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
            task, complete, describe_image, approve_click, max_steps, deadline_s, on_progress
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
