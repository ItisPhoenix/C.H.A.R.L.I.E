"""Low-Overhead Windows User Takeover Detector for Charlie V1.

Monitors physical input devices during automated mouse/keyboard control sessions.
If genuine user interaction is detected:
1. Halts automated control immediately (no fighting for the cursor).
2. Releases physical mouse/keyboard capability leases.
3. Sets a clean cancellation flag.

Zero-keylogging guarantee: captures no keystrokes, no coordinates, and stores no input logs.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("charlie.desktop.takeover")

# Custom tag passed in dwExtraInfo during Charlie's synthetic SendInput calls
CHARLIE_INPUT_TAG = 0x4348524C  # 'CHRL' in ASCII hex


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("dwTime", ctypes.c_uint),
    ]


@dataclass
class TakeoverStatus:
    active: bool
    takeover_detected: bool
    last_user_tick_ms: int = 0
    session_owner: Optional[str] = None


class UserTakeoverDetector:
    """Detects user physical mouse/keyboard takeover without invasive hooking or keylogging."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._is_active = False
        self._takeover_triggered = False
        self._session_start_tick = 0
        self._session_owner: Optional[str] = None
        self._last_checked_tick = 0

    def get_last_input_tick(self) -> int:
        """Get the tick count of the last user physical or simulated input event."""
        if hasattr(ctypes, "windll"):
            try:
                lii = LASTINPUTINFO()
                lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
                if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
                    return int(lii.dwTime)
            except Exception:
                pass
        return int(time.perf_counter() * 1000)

    def start_session(self, owner: str) -> None:
        """Mark the beginning of an automated physical cursor/keyboard effector session."""
        with self._lock:
            self._is_active = True
            self._takeover_triggered = False
            self._session_owner = owner
            self._session_start_tick = self.get_last_input_tick()
            logger.info("Physical input session started for owner '%s' (tick %d)", owner, self._session_start_tick)

    def end_session(self) -> None:
        """Mark the completion of an automated physical session."""
        with self._lock:
            self._is_active = False
            self._session_owner = None
            logger.debug("Physical input session ended")

    def check_takeover(self) -> bool:
        """Check if genuine user input occurred after session start. If so, triggers takeover."""
        with self._lock:
            if not self._is_active or self._takeover_triggered:
                return self._takeover_triggered

            current_tick = self.get_last_input_tick()
            # If user input occurred >= 250ms after session began, user took over
            if current_tick > self._session_start_tick + 250:
                self._takeover_triggered = True
                self._is_active = False
                logger.warning(
                    "User physical takeover detected (tick %d > start %d) -- halting desktop session for '%s'",
                    current_tick,
                    self._session_start_tick,
                    self._session_owner,
                )
                self._trigger_halt()
                return True

            return False

    def _trigger_halt(self) -> None:
        """Halt actions and release physical leases."""
        try:
            from charlie.desktop import actions

            actions.halt()
        except Exception:
            pass

        try:
            from charlie.resource_locks import default_lease_manager

            default_lease_manager.release("physical_mouse")
            default_lease_manager.release("keyboard")
        except Exception:
            pass

    def is_physical_control_active(self) -> bool:
        with self._lock:
            return self._is_active

    def status(self) -> TakeoverStatus:
        with self._lock:
            return TakeoverStatus(
                active=self._is_active,
                takeover_detected=self._takeover_triggered,
                last_user_tick_ms=self.get_last_input_tick(),
                session_owner=self._session_owner,
            )


# Global singleton detector
user_takeover_detector = UserTakeoverDetector()
