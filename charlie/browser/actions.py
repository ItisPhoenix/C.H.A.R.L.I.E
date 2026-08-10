"""Browser effectors: navigation and mark-based interaction, on top of the observation layer.

Mirrors charlie/desktop/actions.py's role -- observation.py assigns marks, this module acts on
them. Every action runs on the controller's dedicated browser thread via controller.run().
"""

import logging
from typing import Any, Dict, Optional

from charlie.browser import controller, session
from charlie.browser.observation import extract_visible_text
from charlie.utils import open_url_in_browser

logger = logging.getLogger("charlie.browser")

_DEFAULT_NAV_TIMEOUT_MS = 8000
_DEFAULT_SELECTOR_TIMEOUT_MS = 5000
_READ_TIMEOUT_SEC = 15.0
_READ_MAX_CHARS = 50000
_MIN_EXTRACTED_CHARS = 200
_JINA_TIMEOUT_SEC = 20.0


def navigate(page: Any, url: str, wait_selector: Optional[str] = None) -> None:
    """Go to url, optionally waiting for a specific selector -- never networkidle (too slow)."""
    controller.wait_host_cooldown(url)
    page.goto(url, wait_until="domcontentloaded", timeout=_DEFAULT_NAV_TIMEOUT_MS)
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
    return f'{mark.role} "{mark.name}"'


def type_text(page: Any, mark_id: int, text: str, submit: bool = False) -> None:
    locator, mark = _locator_for(page, mark_id)
    locator.fill(text, timeout=_DEFAULT_SELECTOR_TIMEOUT_MS)
    if submit:
        locator.press("Enter")
    session.record_action(f'type [{mark_id}] "{text}"' + (" + Enter" if submit else ""))


def press_key(page: Any, key: str) -> None:
    page.keyboard.press(key)
    session.record_action(f"press {key}")


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
    """Fetch and extract one page's text, no headless browser -- trafilatura first, Jina Reader
    fallback for JS-rendered shells. Moved from the deleted BrowserPlugin._fetch unchanged."""
    import httpx
    import trafilatura

    text = ""
    try:
        resp = httpx.get(
            url, timeout=_READ_TIMEOUT_SEC, follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; CharlieBot/1.0)"},
        )
        resp.raise_for_status()
        text = trafilatura.extract(resp.text) or ""
    except Exception as exc:
        logger.debug("Direct fetch failed for %s: %s", url, exc)

    if len(text.strip()) < _MIN_EXTRACTED_CHARS:
        try:
            jina_resp = httpx.get(f"https://r.jina.ai/{url}", timeout=_JINA_TIMEOUT_SEC)
            if jina_resp.status_code == 200 and jina_resp.text.strip():
                text = jina_resp.text
        except Exception as exc:
            logger.debug("Jina Reader fallback failed for %s: %s", url, exc)

    if not text.strip():
        return {"error": f"Could not extract content from {url}"}
    content = text[:_READ_MAX_CHARS]
    return {"url": url, "content": content, "length": len(content)}
