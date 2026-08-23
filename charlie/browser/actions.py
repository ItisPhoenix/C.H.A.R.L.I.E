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
        reached_host = bool(
            current_host and (current_host == requested_host or current_host.endswith(f".{requested_host}"))
        )
        if not reached_host:
            try:
                page.goto(url, wait_until="commit", timeout=_DEFAULT_SELECTOR_TIMEOUT_MS)
            except Exception as commit_exc:
                if type(commit_exc).__name__ not in {"TimeoutError", "PlaywrightTimeoutError"}:
                    raise
            current_host = urlparse(getattr(page, "url", "")).netloc.lower()
            reached_host = bool(
                current_host and (current_host == requested_host or current_host.endswith(f".{requested_host}"))
            )
            if not reached_host:
                raise exc
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


_MEDIA_STATE_SCRIPT = """requestedIndex => {
    const elements = [...document.querySelectorAll('video, audio')];
    const evidence = elements.map((element, index) => {
        const rect = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        const visible = rect.width > 0 && rect.height > 0
            && style.visibility !== 'hidden' && style.display !== 'none' && Number(style.opacity || 1) > 0;
        const duration = Number.isFinite(element.duration) ? element.duration : null;
        let score = 0;
        if (visible) score += 40 + Math.min(40, (rect.width * rect.height) / 10000);
        if (!element.paused && !element.ended) score += 100;
        if (element.currentTime > 0) score += 25;
        if (duration !== null && duration > 0) score += 15;
        if (document.visibilityState === 'visible') score += 5;
        if (document.activeElement === element) score += 25;
        if (element.closest('main, article, figure, [role="main"]')) score += 10;
        if (element.getAttribute('aria-label') || element.getAttribute('title')) score += 5;
        return {element, index, score, visible, width: rect.width, height: rect.height, duration};
    });
    let selected = null;
    let ambiguous = false;
    if (Number.isInteger(requestedIndex) && evidence[requestedIndex]) {
        selected = evidence[requestedIndex];
    } else if (evidence.length === 1) {
        selected = evidence[0];
    } else if (evidence.length > 1) {
        evidence.sort((left, right) => right.score - left.score);
        ambiguous = Math.abs(evidence[0].score - evidence[1].score) < 5;
        if (!ambiguous) selected = evidence[0];
    }
    const media = selected && selected.element;
    return {
        media: Boolean(media),
        ambiguous,
        count: elements.length,
        index: selected ? selected.index : null,
        tag: media ? media.tagName.toLowerCase() : null,
        paused: media ? media.paused : null,
        currentTime: media ? media.currentTime : null,
        duration: media && Number.isFinite(media.duration) ? media.duration : null,
        muted: media ? media.muted : null,
        volume: media ? media.volume : null,
        visible: selected ? selected.visible : false,
        width: selected ? selected.width : 0,
        height: selected ? selected.height : 0,
    };
}"""


def media_player_state(page: Any, media_index: Optional[int] = None) -> dict:
    """Read the uniquely supported active HTMLMediaElement from runtime evidence."""
    if media_index is None:
        return page.evaluate(_MEDIA_STATE_SCRIPT)
    return page.evaluate(_MEDIA_STATE_SCRIPT, media_index)


def media_player_action(page: Any, command: str, value: Optional[float] = None) -> dict:
    """Mutate one evidenced HTMLMediaElement and freshly verify its postcondition."""
    before = media_player_state(page)
    if not before.get("media"):
        return {
            "verified": False,
            "reason": "media-identity-ambiguous" if before.get("ambiguous") else "media-element-missing",
            "before": before,
            "after": before,
        }
    index = int(before["index"])
    try:
        dispatched = page.evaluate(
            """async payload => {
                const media = [...document.querySelectorAll('video, audio')][payload.index];
                if (!media) return {ok: false, reason: 'media-element-stale'};
                try {
                    if (payload.command === 'play') await media.play();
                    else if (payload.command === 'pause') media.pause();
                    else if (payload.command === 'seek') {
                        const requested = media.currentTime + payload.value;
                        media.currentTime = Number.isFinite(media.duration)
                            ? Math.max(0, Math.min(media.duration, requested))
                            : Math.max(0, requested);
                    } else if (payload.command === 'mute') media.muted = true;
                    else if (payload.command === 'unmute') media.muted = false;
                    else if (payload.command === 'volume') media.volume = Math.max(0, Math.min(1, payload.value));
                    else return {ok: false, reason: 'media-command-unsupported'};
                    return {ok: true};
                } catch (error) {
                    return {ok: false, reason: error && error.name ? error.name : 'media-action-rejected'};
                }
            }""",
            {"command": command, "index": index, "value": float(value or 0)},
        )
    except Exception as exc:
        return {"verified": False, "reason": type(exc).__name__, "before": before, "after": before}
    after = media_player_state(page, index)
    target_time = (before.get("currentTime") or 0) + float(value or 0)
    duration = before.get("duration")
    if duration is not None:
        target_time = max(0.0, min(float(duration), target_time))
    verified = bool(dispatched.get("ok")) and (
        (command == "pause" and after.get("paused") is True)
        or (command == "play" and after.get("paused") is False)
        or (command == "seek" and abs(float(after.get("currentTime") or 0) - target_time) <= 2.0)
        or (command == "mute" and after.get("muted") is True)
        or (command == "unmute" and after.get("muted") is False)
        or (command == "volume" and abs(float(after.get("volume") or 0) - float(value or 0)) <= 0.02)
    )
    session.invalidate_observation()
    session.record_action(f"media {command}" + (f" {value}" if value is not None else ""))
    return {
        "verified": verified,
        "reason": "verified" if verified else dispatched.get("reason", "media-action-unverified"),
        "before": before,
        "after": after,
    }


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
