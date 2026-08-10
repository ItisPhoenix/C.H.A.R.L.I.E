"""Headless browser control (Playwright + Chrome) -- optional.

Guarded so importing this package never raises when Playwright isn't
installed; callers must check BROWSER_AVAILABLE before using
charlie.browser.controller/actions. Mirrors charlie/desktop/__init__.py.
"""

from concurrent.futures import ThreadPoolExecutor
from typing import Optional

try:
    import playwright  # noqa: F401
    _HAS_PLAYWRIGHT = True
except ImportError:
    _HAS_PLAYWRIGHT = False

BROWSER_AVAILABLE = _HAS_PLAYWRIGHT

# One worker: Playwright's sync API is greenlet/thread-affine, page+context must live on one thread.
BROWSER_EXECUTOR: Optional[ThreadPoolExecutor] = (
    ThreadPoolExecutor(max_workers=1, thread_name_prefix="charlie-browser")
    if BROWSER_AVAILABLE
    else None
)
