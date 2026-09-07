"""Windows desktop control (UI Automation) -- optional, Windows-only.

Guarded so importing this package (or anything that imports it) never raises
on non-Windows platforms or when uiautomation isn't installed; callers must
check DESKTOP_AVAILABLE before using charlie.desktop.uia/actions.
"""

import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

try:
    import uiautomation  # noqa: F401
    _HAS_UIA = True
except ImportError:
    _HAS_UIA = False

DESKTOP_AVAILABLE = sys.platform == "win32" and _HAS_UIA
logger = logging.getLogger("charlie.desktop")


def _init_com_thread() -> None:
    """Initialize COM once on the dedicated UIA worker thread.

    uiautomation/comtypes need a COM-initialized apartment thread. The shared
    asyncio default executor hands work to whichever pool thread is idle with
    no such guarantee, which segfaults comtypes on Windows. core.py routes
    every desktop_* tool call through this single, COM-initialized thread
    instead of the default pool.
    """
    import comtypes
    comtypes.CoInitialize()


UIA_EXECUTOR: Optional[ThreadPoolExecutor] = None
_UIA_EXECUTOR_LOCK = threading.Lock()
_UIA_EXECUTOR_SHUTDOWN = False


def get_uia_executor() -> Optional[ThreadPoolExecutor]:
    """Return the lazily-created, process-local COM executor for UIA work."""
    global UIA_EXECUTOR
    if not DESKTOP_AVAILABLE:
        return None
    with _UIA_EXECUTOR_LOCK:
        if _UIA_EXECUTOR_SHUTDOWN:
            raise RuntimeError("UIA executor is shut down")
        if UIA_EXECUTOR is None:
            UIA_EXECUTOR = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="charlie-uia",
                initializer=_init_com_thread,
            )
        return UIA_EXECUTOR


def _uninitialize_com_thread() -> None:
    try:
        import comtypes

        comtypes.CoUninitialize()
    except Exception:
        logger.warning("UIA worker COM uninitialization failed", exc_info=True)


def shutdown_uia_executor() -> None:
    """Idempotently uninitialize and stop the dedicated UIA worker."""
    global _UIA_EXECUTOR_SHUTDOWN
    with _UIA_EXECUTOR_LOCK:
        if _UIA_EXECUTOR_SHUTDOWN:
            return
        _UIA_EXECUTOR_SHUTDOWN = True
        executor = UIA_EXECUTOR
    if executor is None:
        return
    try:
        executor.submit(_uninitialize_com_thread).result()
    except RuntimeError:
        logger.debug("UIA executor was already closed", exc_info=True)
    except Exception:
        logger.warning("UIA worker shutdown task failed", exc_info=True)
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
