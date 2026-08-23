"""Browser effectors: navigation and mark-based interaction, on top of the observation layer.

Mirrors charlie/desktop/actions.py's role -- observation.py assigns marks, this module acts on
them. Every action runs on the controller's dedicated browser thread via controller.run().
"""

import logging
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from charlie.browser import controller, session
from charlie.browser.observation import extract_visible_text
from charlie.utils import open_url_in_browser

logger = logging.getLogger("charlie.browser")

_DEFAULT_NAV_TIMEOUT_MS = 8000
_DEFAULT_SELECTOR_TIMEOUT_MS = 5000
_READ_TIMEOUT_SEC = 15.0
_READ_MAX_CHARS = 50000


def navigate(page: Any, url: str, wait_selector: Optional[str] = None) -> None:
    """Go to url, optionally waiting for a specific selector -- never networkidle (too slow)."""
    controller.wait_host_cooldown(url)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=_DEFAULT_NAV_TIMEOUT_MS)
    except Exception as exc:
        if type(exc).__name__ not in {"TimeoutError", "PlaywrightTimeoutError"}:
            raise
        current_host = urlparse(getattr(page, "url", "")).netloc.lower()
        requested_host = urlparse(url).netloc.lower()
        if not current_host or not (current_host == requested_host or current_host.endswith(f".{requested_host}")):
            raise
        logger.debug("Navigation load timed out after reaching %s; continuing with the loaded document", page.url)
    if wait_selector:
        try:
            page.wait_for_selector(wait_selector, timeout=_DEFAULT_SELECTOR_TIMEOUT_MS)
        except Exception:
            logger.debug("wait_for_selector(%r) timed out on %s", wait_selector, url)
    session.record_navigation(page.url)
    session.record_action(f"navigate {url}")


def back(page: Any) -> None:
    page.go_back(timeout=_DEFAULT_NAV_TIMEOUT_MS)
    session.record_navigation(page.url)
    session.record_action("back")


def forward(page: Any) -> None:
    page.go_forward(timeout=_DEFAULT_NAV_TIMEOUT_MS)
    session.record_navigation(page.url)
    session.record_action("forward")


def reload(page: Any) -> None:
    page.reload(wait_until="domcontentloaded", timeout=_DEFAULT_NAV_TIMEOUT_MS)
    session.record_navigation(page.url)
    session.record_action("reload")


def _locator_for(page: Any, mark_id: int) -> Any:
    mark = session.resolve_mark(mark_id)
    return page.locator(f"aria-ref={mark.ref}"), mark


def click(page: Any, mark_id: int) -> str:
    """Click a mark; returns its role/name for the caller to log or gate on."""
    locator, mark = _locator_for(page, mark_id)
    locator.click(timeout=_DEFAULT_SELECTOR_TIMEOUT_MS)
    session.record_action(f'click [{mark_id}] {mark.role} "{mark.name}"')
    session.invalidate_observation()
    try:
        page.wait_for_load_state("domcontentloaded", timeout=_DEFAULT_SELECTOR_TIMEOUT_MS)
    except Exception:
        logger.debug("click did not settle a document navigation")
    session.record_navigation(page.url)
    return f'{mark.role} "{mark.name}"'


def type_text(page: Any, mark_id: int, text: str, submit: bool = False) -> None:
    locator, mark = _locator_for(page, mark_id)
    locator.fill(text, timeout=_DEFAULT_SELECTOR_TIMEOUT_MS)
    if submit:
        locator.press("Enter")
        session.invalidate_observation()
    else:
        session.invalidate_marks()
    session.record_action(f'type [{mark_id}] "{text}"' + (" + Enter" if submit else ""))


def press_key(page: Any, key: str) -> None:
    page.keyboard.press(key)
    session.record_action(f"press {key}")


def media_player_key(page: Any, key: str) -> None:
    """Send a keyboard shortcut through the active rendered media surface."""
    try:
        media = page.locator("video, audio")
        media.first.press(key, timeout=_DEFAULT_SELECTOR_TIMEOUT_MS)
    except Exception:
        page.keyboard.press(key)
    session.record_action(f"media player key {key}")


def media_player_state(page: Any) -> dict:
    """Read state from the active HTMLMediaElement exposed by the current page."""
    return page.evaluate(
        """() => {
            const fallbackVideo = document.querySelector('video');
            const media = [...document.querySelectorAll('video, audio')]
                .find(element => {
                    const rect = element.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                }) || fallbackVideo || document.querySelector('audio');
            const active = document.activeElement;
            return {
                media: Boolean(media),
                paused: media ? media.paused : null,
                currentTime: media ? media.currentTime : null,
                duration: media ? media.duration : null,
                muted: media ? media.muted : null,
                active: active ? `${active.tagName}#${active.id || ''}.${active.className || ''}` : '',
            };
        }"""
    )


def scroll(page: Any, direction: str = "down", amount: int = 800) -> None:
    delta = amount if direction == "down" else -amount
    page.mouse.wheel(0, delta)
    session.record_action(f"scroll {direction}")


def extract_text(page: Any) -> str:
    return extract_visible_text(page)


def screenshot(page: Any) -> bytes:
    return page.screenshot(full_page=False)


def open_in_real_browser(url: str) -> bool:
    """Open url in the user's actual browser -- called only when the request carries open-intent."""
    opened = open_url_in_browser(url)
    session.record_action(f"opened in real browser: {url}")
    return opened


def read_url(url: str) -> Dict[str, Any]:
    """Read one public URL through HTTP, Crawl4AI, then Playwright if needed.

    This is a page-reading compatibility entry point, not the primary research
    path. It deliberately has no Jina dependency.
    """
    import asyncio

    from charlie.research.crawler import crawl_document
    from charlie.research.fetch import (
        document_from_content,
        extract_text,
        fetch_document,
        validate_public_url,
    )
    from charlie.research.models import SearchResult

    result = SearchResult(title=url, url=url, provider="browser_read")
    try:
        safe_url = validate_public_url(url)
    except ValueError:
        return {"error": "Only public HTTP(S) URLs can be read."}
    try:
        document = asyncio.run(fetch_document(result, timeout_s=_READ_TIMEOUT_SEC))
        if document is None:
            document = asyncio.run(crawl_document(result, timeout_s=_READ_TIMEOUT_SEC))
    except Exception as exc:
        logger.debug("Research fetch failed for %s: %s", url, exc)
        document = None

    if document is None:
        try:
            def read_with_playwright(page):
                page.goto(safe_url, wait_until="domcontentloaded", timeout=int(_READ_TIMEOUT_SEC * 1000))
                text, method = extract_text(page.content())
                return document_from_content(result, text, extraction_method=f"playwright:{method}")

            document = controller.run(read_with_playwright, timeout=_READ_TIMEOUT_SEC)
        except Exception as exc:
            logger.debug("Playwright extraction failed for %s: %s", url, exc)

    if document is None:
        return {"error": f"Could not extract content from {url}"}
    content = document.content[:_READ_MAX_CHARS]
    return {"url": document.url or url, "content": content, "length": len(content)}
