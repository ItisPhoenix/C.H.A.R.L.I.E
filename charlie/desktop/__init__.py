"""Windows desktop control (UI Automation) -- optional, Windows-only.

Guarded so importing this package (or anything that imports it) never raises
on non-Windows platforms or when uiautomation isn't installed; callers must
check DESKTOP_AVAILABLE before using charlie.desktop.uia/actions.
"""

import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

try:
    import uiautomation  # noqa: F401
    _HAS_UIA = True
except ImportError:
    _HAS_UIA = False

DESKTOP_AVAILABLE = sys.platform == "win32" and _HAS_UIA


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


UIA_EXECUTOR: Optional[ThreadPoolExecutor] = (
    ThreadPoolExecutor(max_workers=1, thread_name_prefix="charlie-uia", initializer=_init_com_thread)
    if DESKTOP_AVAILABLE
    else None
)
