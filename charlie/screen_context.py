"""Foreground window title monitor for screen context awareness.

Polls the active window title every 2 seconds. Windows-only via ctypes;
other platforms fall back to 'unknown'.
"""

import logging
import threading
from typing import Callable, Optional

logger = logging.getLogger("charlie.screen_context")

# Keywords for contextual awareness
ERROR_KEYWORDS = ("Error", "Exception", "Failed", "Bug", "Traceback", "CRASH")
CODE_KEYWORDS = ("VS Code", "PyCharm", "IntelliJ", "Code", "Terminal", "Sublime")
BROWSER_KEYWORDS = ("Chrome", "Firefox", "Edge", "Brave", "Opera", "Arc")
MEDIA_KEYWORDS = ("YouTube", "Netflix", "Spotify", "VLC", "Plex", "Disney")
WORK_KEYWORDS = ("Word", "Excel", "PowerPoint", "Slack", "Teams", "Outlook", "Notion", "Jira", "Confluence", "Figma", "Canva", "Docs", "Sheets")


class ScreenContextMonitor:
    """Polls foreground window title and classifies context."""

    def __init__(
        self,
        callback: Optional[Callable[[str], None]] = None,
        poll_interval: float = 2.0,
    ) -> None:
        self._callback = callback
        self._poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._current_title: str = ""
        self._last_category: str = "unknown"

    @property
    def current_title(self) -> str:
        return self._current_title

    @property
    def category(self) -> str:
        return self._last_category

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="screen-ctx")
        self._thread.start()
        logger.info("ScreenContextMonitor started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                title = self._get_foreground_title()
                if title != self._current_title:
                    self._current_title = title
                    self._last_category = self._classify(title)
                    if self._callback:
                        self._callback(title)
            except Exception as e:
                logger.debug(f"Screen poll error: {e}")
            self._stop_event.wait(self._poll_interval)

    @staticmethod
    def _get_foreground_title() -> str:
        """Get the title of the foreground window (Windows)."""
        try:
            import ctypes
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            hWnd = user32.GetForegroundWindow()
            if not hWnd:
                return ""
            length = user32.GetWindowTextLengthW(hWnd)
            if length == 0:
                return ""
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hWnd, buffer, length + 1)
            return buffer.value
        except Exception:
            return "unknown"

    @staticmethod
    def _classify(title: str) -> str:
        """Classify window title into a context category."""
        if not title:
            return "unknown"
        title_lower = title.lower()
        if any(kw.lower() in title_lower for kw in ERROR_KEYWORDS):
            return "error"
        if any(kw.lower() in title_lower for kw in CODE_KEYWORDS):
            return "coding"
        if any(kw.lower() in title_lower for kw in BROWSER_KEYWORDS):
            return "browsing"
        if any(kw.lower() in title_lower for kw in MEDIA_KEYWORDS):
            return "leisure"
        if any(kw.lower() in title_lower for kw in WORK_KEYWORDS):
            return "work"
        return "other"
