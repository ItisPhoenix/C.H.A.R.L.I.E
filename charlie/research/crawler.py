"""Bounded dynamic extraction for sources plain HTTP cannot read.

The normal research path still starts with :mod:`charlie.research.fetch`.
Scrapling handles a second HTTP/parser pass and dynamic pages. Crawl4AI is an
optional isolated fallback when it is installed; its browser startup must not
poison Charlie's event loop when the package is present but unhealthy.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any, Optional
from urllib.parse import urlparse

from charlie.research.fetch import (
    document_from_content,
    extract_html_title,
    validate_public_url,
    validate_public_url_async,
)
from charlie.research.models import SearchResult, SourceDocument

logger = logging.getLogger("charlie.research.crawler")


def _result_at_url(result: SearchResult, url: str) -> SearchResult:
    return SearchResult(
        title=result.title,
        url=url,
        snippet=result.snippet,
        provider=result.provider,
        rank=result.rank,
        published_at=result.published_at,
        domain=urlparse(url).netloc.lower(),
    )


def _response_text(response: Any) -> str:
    """Read visible text from a Scrapling response without trusting snippets."""
    try:
        text = response.get_all_text(
            separator="\n",
            strip=True,
            ignore_tags=("script", "style", "noscript", "nav", "footer"),
        )
    except Exception:
        text = ""
    return str(text or "").strip()


async def _scrapling_static(result: SearchResult, *, timeout_s: float) -> Optional[SourceDocument]:
    """Use Scrapling's browser-shaped HTTP fetch and parser as a cheap retry."""
    try:
        from scrapling.fetchers import AsyncFetcher
    except ImportError:
        return None

    try:
        response = await asyncio.wait_for(
            AsyncFetcher.get(
                result.url,
                stealthy_headers=True,
                follow_redirects=True,
                timeout=timeout_s,
            ),
            timeout_s,
        )
        if int(getattr(response, "status", 0)) >= 400:
            return None
        final_url = await validate_public_url_async(str(getattr(response, "url", result.url)))
        content = _response_text(response)
        body = getattr(response, "body", b"")
        if isinstance(body, bytes):
            title = extract_html_title(body.decode(getattr(response, "encoding", "utf-8"), "ignore"))
        else:
            title = None
        return document_from_content(
            _result_at_url(result, final_url),
            content,
            extraction_method="scrapling:http",
            title=title or result.title,
        )
    except Exception:
        logger.debug("Scrapling HTTP extraction failed for %s", result.url, exc_info=True)
        return None


def _scrapling_dynamic_sync(url: str, timeout_s: float, *, stealth: bool = False) -> Any:
    """Run browser-backed Scrapling in a worker thread, outside Charlie's loop."""
    from scrapling.fetchers import DynamicFetcher, StealthyFetcher
    from charlie.browser.controller import _POLICY_SWAP_LOCK

    fetcher = StealthyFetcher if stealth else DynamicFetcher
    options = {
        "headless": True,
        "load_dom": True,
        "network_idle": False,
        "disable_resources": True,
        "google_search": False,
        "timeout": max(1000, int(timeout_s * 1000)),
    }
    if stealth:
        options.update({"solve_cloudflare": True, "block_webrtc": True, "hide_canvas": True})

    if sys.platform != "win32":
        return fetcher.fetch(url, **options)

    # Patchright/Playwright browser launches need Windows' Proactor loop for
    # subprocess transport. Charlie's main loop intentionally uses Selector
    # for ZMQ compatibility, so swap only around this isolated browser call.
    with _POLICY_SWAP_LOCK:
        prior_policy = asyncio.get_event_loop_policy()
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        try:
            return fetcher.fetch(url, **options)
        finally:
            asyncio.set_event_loop_policy(prior_policy)


async def _scrapling_dynamic(
    result: SearchResult,
    *,
    timeout_s: float,
    stealth: bool = False,
) -> Optional[SourceDocument]:
    try:
        from scrapling.fetchers import DynamicFetcher, StealthyFetcher  # noqa: F401
    except ImportError:
        return None

    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(_scrapling_dynamic_sync, result.url, timeout_s, stealth=stealth),
            timeout_s,
        )
        if int(getattr(response, "status", 0)) >= 400:
            return None
        final_url = await validate_public_url_async(str(getattr(response, "url", result.url)))
        content = _response_text(response)
        body = getattr(response, "body", b"")
        title = None
        if isinstance(body, bytes):
            title = extract_html_title(body.decode(getattr(response, "encoding", "utf-8"), "ignore"))
        return document_from_content(
            _result_at_url(result, final_url),
            content,
            extraction_method="scrapling:stealth" if stealth else "scrapling:dynamic",
            title=title or result.title,
        )
    except Exception:
        logger.warning(
            "Scrapling %s extraction failed for %s",
            "stealth" if stealth else "dynamic",
            result.url,
            exc_info=True,
        )
        return None


async def _crawl4ai(result: SearchResult, *, timeout_s: float) -> Optional[SourceDocument]:
    """Try Crawl4AI in its own event loop when the optional package is present."""
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
    except ImportError:
        return None

    async def run() -> Any:
        browser_config = BrowserConfig(headless=True, verbose=False)
        run_config = CrawlerRunConfig(
            word_count_threshold=80,
            excluded_tags=["script", "style", "nav", "footer"],
            stream=False,
        )
        async with AsyncWebCrawler(config=browser_config) as crawler:
            return await crawler.arun(url=result.url, config=run_config)

    try:
        # Isolate Crawl4AI's Playwright startup from Charlie's running loop.
        crawled = await asyncio.wait_for(asyncio.to_thread(lambda: asyncio.run(run())), timeout_s)
        content = getattr(crawled, "markdown", None) or getattr(crawled, "cleaned_html", None) or ""
        title = getattr(crawled, "title", None) or result.title
        return document_from_content(result, str(content), extraction_method="crawl4ai", title=str(title))
    except Exception:
        logger.warning("Crawl4AI isolated extraction failed for %s", result.url, exc_info=True)
        return None


async def crawl_document(
    result: SearchResult,
    *,
    timeout_s: float = 30.0,
    max_depth: int = 1,
    max_pages: int = 4,
) -> Optional[SourceDocument]:
    """Escalate one unreadable source through Scrapling and optional Crawl4AI.

    ``max_depth`` and ``max_pages`` remain part of the caller contract. Search
    already bounds sources; this function reads one cited URL and never turns a
    source fetch into an unbounded site crawl.
    """
    del max_depth, max_pages
    try:
        validate_public_url(result.url)
    except ValueError:
        logger.info("Skipping blocked crawl URL: %s", result.url)
        return None

    document = await _scrapling_static(result, timeout_s=min(timeout_s, 12.0))
    if document is not None:
        return document

    document = await _crawl4ai(result, timeout_s=timeout_s)
    if document is not None:
        return document

    document = await _scrapling_dynamic(result, timeout_s=timeout_s)
    if document is not None:
        return document

    return await _scrapling_dynamic(result, timeout_s=timeout_s, stealth=True)
