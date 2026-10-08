"""Playwright lifecycle for private headless and visible user-browser profiles.

All state below is only ever touched from BROWSER_EXECUTOR's single worker thread. Both profiles
share one thread-affine Playwright runtime but have separate persistent contexts.
"""

import logging
import os
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
_headless_mode: Optional[bool] = None
_user_context = None
_user_page = None
_user_profile_path: Optional[str] = None
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


def _user_page_is_alive() -> bool:
    if _user_page is None or _user_context is None:
        return False
    try:
        if _user_page.is_closed():
            return False
        browser = getattr(_user_context, "browser", None)
        if browser is not None and hasattr(browser, "is_connected") and not browser.is_connected():
            return False
        return bool(_user_context.pages)
    except Exception:
        return False


def _dispose_stale() -> None:
    """Best-effort disposal for dead Playwright objects, always on browser thread."""
    global _playwright, _context, _page, _headless_mode
    from charlie.browser.session import reset_session
    reset_session()
    for resource in (_context, _playwright if _user_context is None else None):
        if resource is None:
            continue
        try:
            resource.close() if resource is _context else resource.stop()
        except Exception:
            logger.debug("Ignoring stale browser cleanup failure", exc_info=True)
    if _user_context is None:
        _playwright = None
    _context = None
    _page = None
    _headless_mode = None


def _block_heavy_resources(route: Any) -> None:
    if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:
        route.abort()
    else:
        route.continue_()


def _ensure_playwright() -> None:
    global _playwright
    if _playwright is not None:
        return
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


def _launch(*, headless: Optional[bool] = None) -> None:
    """Open the private persistent page on the shared browser worker."""
    global _context, _page, _headless_mode
    _ensure_playwright()
    headless_mode = config.browser_headless if headless is None else headless
    launch_kwargs = dict(
        user_data_dir=config.browser_profile_path,
        headless=headless_mode,
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
    _headless_mode = headless_mode
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


def _launch_user() -> None:
    """Launch Charlie's visible, persistent browser profile on the browser thread."""
    global _playwright, _user_context, _user_page, _user_profile_path
    if not BROWSER_EXECUTOR:
        raise BrowserUnavailable("playwright is not installed")
    from charlie.computer.manifest import resolve_browser_executable
    from charlie.privacy_service import validate_browser_profile_path

    _ensure_playwright()
    executable = resolve_browser_executable("brave") or resolve_browser_executable("chrome")
    if not executable:
        raise BrowserUnavailable("No installed visible browser executable was found")
    profile = str(validate_browser_profile_path(config.browser_user_profile_path))
    if profile.casefold() == os.path.abspath(str(config.browser_profile_path)).casefold():
        raise BrowserUnavailable("Visible and private browser profiles must be different")
    os.makedirs(profile, exist_ok=True)
    _user_profile_path = profile
    _user_context = _playwright.chromium.launch_persistent_context(
        user_data_dir=profile,
        executable_path=executable,
        headless=False,
        viewport={"width": 1280, "height": 900},
    )
    _user_page = _user_context.pages[0] if _user_context.pages else _user_context.new_page()
    logger.info("Visible browser controller launched: profile=%s executable=%s", profile, executable)


def _ensure_launched() -> Any:
    if _page is None:
        _launch()
    elif not _page_is_alive():
        logger.warning("Stale browser state detected; relaunching once")
        _dispose_stale()
        _launch()
    return _page


def _prepare_user_visible_on_thread() -> Dict[str, Any]:
    """Expose the same Playwright page used by an interactive browser task."""
    from charlie.browser.session import get_session

    if _page is None:
        _launch(headless=False)
    elif not _page_is_alive():
        _dispose_stale()
        _launch(headless=False)
    elif _headless_mode is not False:
        current = get_session()
        page_url = str(getattr(_page, "url", ""))
        if (
            current.visited_urls
            or current.current_url not in {None, "", "about:blank"}
            or page_url not in {"", "about:blank"}
        ):
            raise BrowserUnavailable(
                "The existing Charlie browser is headless and already has state; "
                "it cannot be made visible without creating a second browser."
            )
        _shutdown_on_thread()
        _launch(headless=False)

    try:
        _page.bring_to_front()
    except Exception as exc:
        raise BrowserUnavailable("Charlie could not expose its Playwright browser window.") from exc
    return runtime_identity(_page)


def prepare_user_visible(timeout: float = 10.0) -> Dict[str, Any]:
    """Make the controller's existing page the user-visible browser surface."""
    if not BROWSER_EXECUTOR:
        raise BrowserUnavailable("playwright is not installed")
    return BROWSER_EXECUTOR.submit(_prepare_user_visible_on_thread).result(timeout=timeout)


def prepare_user_browser(timeout: float = 10.0) -> Dict[str, Any]:
    """Launch Charlie's visible persistent browser profile and return its identity."""
    if not BROWSER_EXECUTOR:
        raise BrowserUnavailable("playwright is not installed")
    return BROWSER_EXECUTOR.submit(_run_user_on_thread, runtime_user_identity).result(
        timeout=timeout
    )


def _windows_session_id(pid: Optional[int]) -> Optional[int]:
    if pid is None or sys.platform != "win32":
        return None
    try:
        import ctypes

        session_id = ctypes.c_ulong()
        if ctypes.windll.kernel32.ProcessIdToSessionId(pid, ctypes.byref(session_id)):
            return int(session_id.value)
    except Exception:
        logger.debug("Unable to resolve browser Windows SessionId", exc_info=True)
    return None


def _browser_process_pid() -> Optional[int]:
    """Find the browser process tied to Charlie's persistent profile, if exposed by the host."""
    profile = os.path.abspath(str(config.browser_profile_path)).casefold()
    browser_names = {"chrome.exe", "chromium.exe", "msedge.exe", "chrome-headless-shell.exe"}
    try:
        import psutil

        processes: list[dict[str, Any]] = []
        for process in psutil.process_iter(["pid", "ppid", "name", "cmdline", "create_time"]):
            try:
                name = str(process.info.get("name") or "").casefold()
                command_args = [str(arg) for arg in (process.info.get("cmdline") or [])]
                command_line = " ".join(command_args).casefold()
                if name not in browser_names or profile not in command_line:
                    continue
                role = next(
                    (arg.casefold() for arg in command_args if arg.casefold().startswith("--type=")),
                    None,
                )
                processes.append(
                    {
                        "pid": int(process.info["pid"]),
                        "ppid": int(process.info.get("ppid") or 0),
                        "role": role,
                        "create_time": float(process.info.get("create_time") or 0.0),
                        "remote_debugging_pipe": "--remote-debugging-pipe" in {arg.casefold() for arg in command_args},
                    }
                )
            except (OSError, psutil.Error, TypeError, ValueError):
                continue

        roots = [item for item in processes if item["role"] is None]
        pipe_roots = [item for item in roots if item["remote_debugging_pipe"]]
        if pipe_roots:
            roots = pipe_roots
        by_pid = {item["pid"]: item for item in processes}

        def descendants(root_pid: int) -> set[int]:
            found: set[int] = set()
            pending = [root_pid]
            while pending:
                parent_pid = pending.pop()
                for item in processes:
                    if item["ppid"] == parent_pid and item["pid"] not in found:
                        found.add(item["pid"])
                        pending.append(item["pid"])
            return found

        root_of_tree = []
        for root in roots:
            child_pids = descendants(root["pid"])
            if not all(pid == root["pid"] or pid in child_pids for pid in by_pid):
                continue
            if any(root["create_time"] > by_pid[pid]["create_time"] for pid in child_pids):
                continue
            root_of_tree.append(root)

        if len(root_of_tree) == 1:
            return root_of_tree[0]["pid"]
        if len(roots) == 1:
            root = roots[0]
            child_pids = descendants(root["pid"])
            if any(root["create_time"] > by_pid[pid]["create_time"] for pid in child_pids):
                logger.warning("Browser root creation order is invalid; refusing PID correlation")
                return None
            return root["pid"]
        logger.warning(
            "Unable to identify a unique Charlie browser root: candidates=%s profile=%s",
            [item["pid"] for item in roots],
            profile,
        )
    except Exception:
        logger.debug("Unable to resolve browser process provenance", exc_info=True)
    return None


def runtime_identity(page: Any) -> Dict[str, Any]:
    """Expose only identities the live Playwright/host runtime actually provides."""
    context = _context
    browser = getattr(context, "browser", None) if context is not None else None

    def guid(value: Any) -> Optional[str]:
        return getattr(getattr(value, "_impl_obj", None), "_guid", None)

    browser_pid = _browser_process_pid()
    try:
        browser_version = browser.version if browser is not None else None
    except Exception:
        browser_version = None
    return {
        "browser_pid": browser_pid,
        "windows_session_id": _windows_session_id(browser_pid),
        "browser_id": guid(browser),
        "browser_context_id": guid(context),
        "target_id": guid(page),
        "browser_version": browser_version,
        "profile_path": os.path.abspath(str(config.browser_profile_path)),
        "headless": _headless_mode,
    }


def runtime_user_identity(page: Any) -> Dict[str, Any]:
    """Expose identities for the visible persistent browser lane."""
    browser = getattr(_user_context, "browser", None) if _user_context is not None else None

    def guid(value: Any) -> Optional[str]:
        return getattr(getattr(value, "_impl_obj", None), "_guid", None)

    try:
        browser_version = browser.version if browser is not None else None
    except Exception:
        browser_version = None
    return {
        "browser_pid": None,
        "windows_session_id": None,
        "browser_id": guid(browser),
        "browser_context_id": guid(_user_context),
        "target_id": guid(page),
        "browser_version": browser_version,
        "profile_path": _user_profile_path,
        "headless": False,
    }


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


def _run_user_on_thread(fn: Callable[[Any], T]) -> T:
    global _user_page
    if _user_page is None or not _user_page_is_alive():
        _launch_user()
    return fn(_user_page)


def run_user(fn: Callable[[Any], T], timeout: Optional[float] = None) -> T:
    """Run one operation against Charlie's visible persistent browser profile."""
    if not BROWSER_EXECUTOR:
        raise BrowserUnavailable("playwright is not installed")
    future = BROWSER_EXECUTOR.submit(_run_user_on_thread, fn)
    return future.result(timeout=timeout)


def _run_user_context_on_thread(fn: Callable[[Any], T]) -> T:
    global _user_page
    if _user_page is None or not _user_page_is_alive():
        _launch_user()
    return fn(_user_context)


def run_user_context(fn: Callable[[Any], T], timeout: Optional[float] = None) -> T:
    """Run one operation against Charlie's visible browser context."""
    if not BROWSER_EXECUTOR:
        raise BrowserUnavailable("playwright is not installed")
    future = BROWSER_EXECUTOR.submit(_run_user_context_on_thread, fn)
    return future.result(timeout=timeout)


def warm() -> None:
    """Fire-and-forget launch, called on wake-word so the browser is ready before a command lands."""
    if not BROWSER_EXECUTOR or _page is not None:
        return
    BROWSER_EXECUTOR.submit(_ensure_launched)


def _shutdown_on_thread() -> None:
    global _playwright, _context, _page, _headless_mode, _user_context, _user_page, _user_profile_path
    from charlie.browser.session import reset_session
    reset_session()
    if _user_context is not None:
        try:
            _user_context.close()
        except Exception:
            logger.warning("Error closing visible browser context", exc_info=True)
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
    _headless_mode = None
    _user_context = None
    _user_page = None
    _user_profile_path = None
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
        BROWSER_EXECUTOR.submit(_dispose_stale)


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
    if BROWSER_EXECUTOR and (_page is not None or _user_page is not None):
        BROWSER_EXECUTOR.submit(_shutdown_on_thread).result(timeout=10)
