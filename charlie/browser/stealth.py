"""Tier 4: last-resort retry through Scrapling's StealthyFetcher after a real block.

As of scrapling 0.4.12, StealthyFetcher runs on Patchright (a stealth-patched Chromium fork),
not Camoufox -- same browser family as our main controller, just a separately-managed browser
binary (`patchright install`) fetched lazily on first actual use, never unless a block fired.
"""

import asyncio
import logging
from typing import Optional

from charlie.browser.controller import _POLICY_SWAP_LOCK
from charlie.browser.recipes import BrowserResult

logger = logging.getLogger("charlie.browser")

_STEALTH_TIMEOUT_S = 20.0


def retry_blocked(url: str) -> Optional[BrowserResult]:
    """One-shot stealth retry of a URL that tripped the block heuristic. None on failure."""
    try:
        from scrapling.fetchers import StealthyFetcher
    except ImportError:
        logger.warning("Tier 4 stealth retry unavailable -- scrapling[fetchers] not installed")
        return None
    try:
        # Patchright's sync_playwright() needs Proactor for subprocess creation, same as controller.py's _launch().
        with _POLICY_SWAP_LOCK:
            prior_policy = asyncio.get_event_loop_policy()
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            try:
                resp = StealthyFetcher.fetch(
                    url, headless=True, solve_cloudflare=True, timeout=_STEALTH_TIMEOUT_S * 1000
                )
            finally:
                asyncio.set_event_loop_policy(prior_policy)
    except Exception as exc:
        if "patchright install" in str(exc):
            logger.warning("Tier 4 stealth retry needs its browser installed -- run `patchright install`")
        else:
            logger.warning("Tier 4 stealth retry failed for %s", url, exc_info=True)
        return None
    if resp.status != 200:
        logger.warning("Tier 4 stealth retry got HTTP %s for %s", resp.status, url)
        return None
    text = resp.get_all_text(ignore_tags=("script", "style"))[:2000] if hasattr(resp, "get_all_text") else ""
    return BrowserResult(url=url, answer=text or None)
