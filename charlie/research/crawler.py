"""Optional Crawl4AI escalation for JS-heavy public pages."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from charlie.research.fetch import document_from_content, validate_public_url
from charlie.research.models import SearchResult, SourceDocument

logger = logging.getLogger("charlie.research.crawler")


async def crawl_document(
    result: SearchResult,
    *,
    timeout_s: float = 30.0,
    max_depth: int = 1,
    max_pages: int = 4,
) -> Optional[SourceDocument]:
    """Use Crawl4AI only after ordinary HTTP extraction failed.

    Crawl4AI is optional. Missing dependency is a normal degradation path, not
    a fake success and not a reason to launch Playwright for every search.

    crawl4ai is NOT installed and is not a declared dependency (see the comment
    block above `browser = [...]` in pyproject.toml). With it absent this
    function is a fast no-op: the import below raises ImportError and we return
    None, handing the source to the Browser Executor. Measured on this machine:
    15.07ms cold, 5.22ms warm (that residual is the DNS getaddrinfo
    validate_public_url does before the import -- no page fetch is attempted).
    Installing it made every unreadable source pay a browser-launch attempt that
    fails inside crawl4ai's own `browser_manager.start()` at
    `await async_playwright().start()` -- measured at 5.181s per source on the
    live run (14:36:57.302 fetch failed -> 14:37:02.483 Crawl4AI failed) for a
    guaranteed null. Playwright itself is fine; it is this path that is not.

    NOTE (dead parameters, reported not fixed): `max_depth` and `max_pages` are
    accepted and are supplied by the caller (charlie/research/engine.py passes
    research_crawl_max_depth/research_crawl_max_pages), but neither is ever
    applied -- `CrawlerRunConfig` below sets neither `depth` nor any page cap, so
    both values are silently discarded. They are dead as configured. Left
    untouched because removing them is a signature change other agents' in-flight
    work (charlie/research/engine.py) still calls into.
    """
    try:
        url = validate_public_url(result.url)
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
    except ImportError:
        return None
    except ValueError:
        logger.info("Skipping blocked crawl URL: %s", result.url)
        return None

    try:
        browser_config = BrowserConfig(headless=True, verbose=False)
        run_config = CrawlerRunConfig(
            word_count_threshold=80,
            excluded_tags=["script", "style", "nav", "footer"],
            stream=False,
        )
        async with AsyncWebCrawler(config=browser_config) as crawler:
            crawled = await asyncio.wait_for(
                crawler.arun(url=url, config=run_config),
                timeout=timeout_s,
            )
        content = getattr(crawled, "markdown", None) or getattr(crawled, "cleaned_html", None) or ""
        title = getattr(crawled, "title", None) or result.title
        return document_from_content(result, str(content), extraction_method="crawl4ai", title=str(title))
    except Exception:
        logger.warning("Crawl4AI failed for %s", result.url, exc_info=True)
        return None
