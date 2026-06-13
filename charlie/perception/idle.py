"""Centralized idle-state detection.

Single source of truth for "how long has the user been idle." Wraps
the Windows User32 GetLastInputInfo API. Other subsystems
(`ProactivityEngine`, `AutonomyLoop`) read from this class instead
of each implementing their own idle logic.
"""

from __future__ import annotations

import ctypes
import threading
from typing import Callable

from charlie.utils.logger import get_logger

logger = get_logger("IDLE_WATCHER")


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


class IdleWatcher:
    """Tracks user idle time and fires listener callbacks on threshold crossings."""

    def __init__(self, edge_threshold_s: float = 60.0):
        self._listeners: list[Callable[[float], None]] = []
        self._lock = threading.Lock()
        self._last_idle_state: bool = False
        self._edge_threshold_s: float = edge_threshold_s
        self._check_count: int = 0

    def get_idle_duration(self) -> float:
        """System idle duration in seconds via Windows User32 API.

        Returns 0.0 on any failure (no display, non-Windows, etc.).
        """
        try:
            lii = _LASTINPUTINFO()
            lii.cbSize = ctypes.sizeof(lii)
            if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
                millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
                return max(0.0, millis / 1000.0)
        except Exception as e:
            logger.debug(f"failed_to_get_idle_duration | {e}")
        return 0.0

    def is_idle(self, threshold_s: float) -> bool:
        """True if the user has been idle for at least `threshold_s` seconds."""
        return self.get_idle_duration() >= threshold_s

    def register_listener(self, callback: Callable[[float], None]) -> None:
        """Register a callback fired when the user crosses the idle threshold.

        Callback signature: ``callback(idle_seconds: float) -> None``.
        Fires once per crossing (not on every check while still idle).
        """
        with self._lock:
            self._listeners.append(callback)

    def check_and_notify(self) -> float:
        """Call from a background loop. Notifies listeners on threshold crossings.

        Returns the current idle duration for callers that want to do their
        own per-tick logic. Listeners fire on the 0→1 idle transition only.
        """
        idle_s = self.get_idle_duration()
        self._check_count += 1
        is_now_idle = idle_s >= self._edge_threshold_s
        if is_now_idle and not self._last_idle_state:
            with self._lock:
                listeners = list(self._listeners)
            for cb in listeners:
                try:
                    cb(idle_s)
                except Exception as e:
                    logger.debug(f"idle_listener_failed | {e}")
        self._last_idle_state = is_now_idle
        return idle_s


_idle_watcher: IdleWatcher | None = None


def get_idle_watcher() -> IdleWatcher:
    """Get the singleton IdleWatcher instance."""
    global _idle_watcher
    if _idle_watcher is None:
        _idle_watcher = IdleWatcher()
    return _idle_watcher
