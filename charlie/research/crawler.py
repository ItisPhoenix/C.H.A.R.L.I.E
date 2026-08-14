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
