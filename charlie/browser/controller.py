"""Playwright lifecycle: lazy launch, warm/idle-shutdown, and the one dedicated browser thread.

All state below is only ever touched from BROWSER_EXECUTOR's single worker thread -- launch,
navigation and shutdown all run as submitted callables, so there is no cross-thread access to
the thread-affine Playwright objects and no lock is needed around them.
"""

import logging
import sys
import threading
import time
from typing import Any, Callable, Dict, Optional, TypeVar

from charlie.browser import BROWSER_EXECUTOR
from charlie.browser.errors import BrowserUnavailable
from charlie.config import config

logger = logging.getLogger("charlie.browser")

T = TypeVar("T")

# a policy swap is process-global; this lock keeps two concurrent launches (there should never
# be more than one, since BROWSER_EXECUTOR has one worker) from racing the swap-back
_POLICY_SWAP_LOCK = threading.Lock()

_BLOCKED_RESOURCE_TYPES = {"image", "font", "media"}
_NAV_HOST_COOLDOWN_S = 1.0

_playwright = None
_context = None
_page = None
_last_used_at = 0.0
_resources_blocked = True
_idle_timer: Optional[threading.Timer] = None
_last_nav_by_host: Dict[str, float] = {}
_activity_lock = threading.Lock()
_active_task_leases = 0
_active_operations = 0


def _page_is_alive() -> bool:
    """Return False when Playwright retained a page object after its transport died."""
    if _page is None or _context is None or _playwright is None:
        return False
    try:
        if _page.is_closed():
            return False
        browser = getattr(_context, "browser", None)
        if browser is not None and hasattr(browser, "is_connected") and not browser.is_connected():
            return False
        return bool(_context.pages)
    except Exception:
        return False


def _dispose_stale() -> None:
    """Best-effort disposal for dead Playwright objects, always on browser thread."""
    global _playwright, _context, _page
    from charlie.browser.session import reset_session
    reset_session()
    for resource in (_context, _playwright):
        if resource is None:
            continue
        try:
            resource.close() if resource is _context else resource.stop()
        except Exception:
            logger.debug("Ignoring stale browser cleanup failure", exc_info=True)
    _playwright = None
    _context = None
    _page = None


def _block_heavy_resources(route: Any) -> None:
    if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:
        route.abort()
    else:
        route.continue_()


def _launch() -> None:
    """Start Playwright and open the one persistent page. Must run on the browser thread."""
    global _playwright, _context, _page
    if not BROWSER_EXECUTOR:
        raise BrowserUnavailable("playwright is not installed")
    import asyncio

    from playwright.sync_api import sync_playwright
    if sys.platform == "win32":
        with _POLICY_SWAP_LOCK:
            prior_policy = asyncio.get_event_loop_policy()
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            try:
                _playwright = sync_playwright().start()
            finally:
                asyncio.set_event_loop_policy(prior_policy)
    else:
        _playwright = sync_playwright().start()
    launch_kwargs = dict(
        user_data_dir=config.browser_profile_path,
        headless=config.browser_headless,
        viewport={"width": 1280, "height": 900},
    )
    launch_mode = "bundled Chromium"
    try:
        _context = _playwright.chromium.launch_persistent_context(**launch_kwargs)
    except Exception:
        logger.warning("Bundled Chromium unavailable, falling back to the installed browser", exc_info=True)
        _context = _playwright.chromium.launch_persistent_context(channel="chrome", **launch_kwargs)
        launch_mode = "installed Chrome fallback"
    _context.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
    _page = _context.new_page()
    _page.route("**/*", _block_heavy_resources)
    try:
        import importlib.metadata
        playwright_version = importlib.metadata.version("playwright")
    except Exception:
        playwright_version = "unknown"
    try:
        browser_version = _context.browser.version if _context.browser is not None else "unknown"
    except Exception:
        browser_version = "unknown"
    logger.info(
        "Browser controller launched: %s, playwright=%s, browser=%s",
        launch_mode,
        playwright_version,
        browser_version,
    )


def _ensure_launched() -> Any:
    if _page is None:
        _launch()
    elif not _page_is_alive():
        logger.warning("Stale browser state detected; relaunching once")
        _dispose_stale()
        _launch()
    return _page


def set_resource_blocking(enabled: bool) -> None:
    """Toggle image/font/media blocking; the vision fallback disables it for a real screenshot."""
    global _resources_blocked
    if enabled == _resources_blocked or _page is None:
        _resources_blocked = enabled
        return
    _resources_blocked = enabled
    _page.unroute("**/*", _block_heavy_resources)
    if enabled:
        _page.route("**/*", _block_heavy_resources)


def wait_host_cooldown(url: str) -> None:
    """Sleep off any remaining per-host cooldown so a looping agent can't hammer one site."""
    from urllib.parse import urlparse
    host = urlparse(url).netloc
    last = _last_nav_by_host.get(host, 0.0)
    remaining = _NAV_HOST_COOLDOWN_S - (time.monotonic() - last)
    if remaining > 0:
        time.sleep(remaining)
    _last_nav_by_host[host] = time.monotonic()


def _run_on_thread(fn: Callable[[Any], T], retry_on_stale: bool = True) -> T:
    global _last_used_at, _active_operations
    page = _ensure_launched()
    with _activity_lock:
        _active_operations += 1
        _last_used_at = time.monotonic()
    try:
        return fn(page)
    except Exception:
        if not retry_on_stale or _page_is_alive():
            raise
        logger.warning("Browser operation lost Playwright state; relaunching once")
        _dispose_stale()
        return fn(_ensure_launched())
    finally:
        with _activity_lock:
            _active_operations -= 1
            _last_used_at = time.monotonic()
            can_schedule = _active_task_leases == 0
        if can_schedule:
            _schedule_idle_shutdown()


def run(fn: Callable[[Any], T], timeout: Optional[float] = None, retry_on_stale: bool = True) -> T:
    """Run fn(page) on the dedicated browser thread, launching on first use."""
    if not BROWSER_EXECUTOR:
        raise BrowserUnavailable("playwright is not installed")
    future = BROWSER_EXECUTOR.submit(_run_on_thread, fn, retry_on_stale)
    return future.result(timeout=timeout)


def warm() -> None:
    """Fire-and-forget launch, called on wake-word so the browser is ready before a command lands."""
    if not BROWSER_EXECUTOR or _page is not None:
        return
    BROWSER_EXECUTOR.submit(_ensure_launched)


def _shutdown_on_thread() -> None:
    global _playwright, _context, _page
    from charlie.browser.session import reset_session
    reset_session()
    if _context is not None:
        try:
            _context.close()
        except Exception:
            logger.warning("Error closing browser context", exc_info=True)
    if _playwright is not None:
        try:
            _playwright.stop()
        except Exception:
            logger.warning("Error stopping playwright", exc_info=True)
    _playwright = None
    _context = None
    _page = None
    logger.info("Browser controller shut down (idle)")


def _schedule_idle_shutdown() -> None:
    global _idle_timer
    if _idle_timer is not None:
        _idle_timer.cancel()
    _idle_timer = threading.Timer(config.browser_idle_timeout_s, _idle_check)
    _idle_timer.daemon = True
    _idle_timer.start()


def _idle_check() -> None:
    with _activity_lock:
        idle = (
            _page is not None
            and _active_task_leases == 0
            and _active_operations == 0
            and time.monotonic() - _last_used_at >= config.browser_idle_timeout_s
        )
    if idle and BROWSER_EXECUTOR:
        BROWSER_EXECUTOR.submit(_shutdown_on_thread)


def acquire_task_lease() -> None:
    """Keep the browser alive for the full duration of one browser task."""
    global _active_task_leases
    with _activity_lock:
        _active_task_leases += 1
        if _idle_timer is not None:
            _idle_timer.cancel()


def release_task_lease() -> None:
    """Release a task lease and let the normal idle timer reclaim the browser."""
    global _active_task_leases
    with _activity_lock:
        _active_task_leases = max(0, _active_task_leases - 1)
        can_schedule = _active_task_leases == 0 and _active_operations == 0 and _page is not None
    if can_schedule:
        _schedule_idle_shutdown()


def shutdown() -> None:
    """Explicit shutdown, e.g. on process exit."""
    if BROWSER_EXECUTOR and _page is not None:
        BROWSER_EXECUTOR.submit(_shutdown_on_thread).result(timeout=10)
