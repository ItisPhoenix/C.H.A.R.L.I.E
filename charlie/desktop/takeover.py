"""Low-Overhead Windows User Takeover Detector for Charlie V1.

Monitors physical input devices during automated mouse/keyboard control sessions.
If genuine user interaction is detected:
1. Halts automated control immediately (no fighting for the cursor).
2. Revokes canonical desktop capability ownership.
3. Sets a clean cancellation flag.

Windows reports one shared ``GetLastInputInfo`` tick for physical and injected input
alike, so ticks Charlie caused are subtracted at the source: the dispatch path records
its own ticks and those ticks are not read back as a human.

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

# How long after a Charlie dispatch its ticks keep counting as Charlie's, in the same
# boot-tick domain as GetLastInputInfo. It only has to cover input the host delivers
# after the call returns -- keystroke tails, multi-click and drag releases -- so 1.5s is
# generous by two orders of magnitude over that. Wider would start eating a human who
# grabs the mouse right after Charlie finishes, which is the takeover this exists for.
CHARLIE_INPUT_GRACE_MS = 1500


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


@dataclass
class TakeoverHalt:
    """Truthful outcome of a takeover halt -- never claimed without evidence."""

    dispatch_halted: bool
    fenced_owners: tuple[str, ...]
    fenced_capabilities: tuple[str, ...]
    error: Optional[str] = None


class UserTakeoverDetector:
    """Detects user physical mouse/keyboard takeover without invasive hooking or keylogging."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._is_active = False
        self._takeover_triggered = False
        self._session_start_tick = 0
        self._session_owner: Optional[str] = None
        self._last_checked_tick = 0
        self._self_input_dispatching = False
        self._self_input_tick = 0
        self._self_input_begin_tick = 0
        self._self_input_grace_until = 0.0

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

    def begin_self_input(self) -> None:
        """Mark that Charlie is dispatching host input right now."""
        with self._lock:
            self._self_input_dispatching = True
            self._self_input_begin_tick = self.get_last_input_tick()

    def note_self_input(self) -> None:
        """Record the tick Charlie's own last input dispatch produced."""
        with self._lock:
            end_tick = self.get_last_input_tick()
            self._self_input_dispatching = False
            # Unchanged from dispatch start means nothing at all arrived, so the newest
            # tick predates this dispatch and belongs to a human. Anchor nothing rather
            # than lend someone else's event Charlie's grace.
            self._self_input_tick = (
                0 if end_tick == self._self_input_begin_tick else end_tick
            )
            self._self_input_grace_until = (
                time.monotonic() + CHARLIE_INPUT_GRACE_MS / 1000.0
            )

    def _explained_by_charlie(self, current_tick: int) -> bool:
        if self._self_input_dispatching:
            return True
        if self._self_input_tick <= 0:
            return False
        # The tick comparison alone never expires: GetLastInputInfo only advances on new
        # input, so a human who grabs once and holds still stays muted for good. The
        # wall-clock bound expires the grace even when the tick never moves again.
        if time.monotonic() >= self._self_input_grace_until:
            return False
        return current_tick <= self._self_input_tick + CHARLIE_INPUT_GRACE_MS

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
            if self._explained_by_charlie(current_tick):
                return False
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

    def _trigger_halt(self) -> TakeoverHalt:
        """Cancel physical control and fence the canonical desktop lease.

        Two independent postconditions, both required:
        1. dispatch is halted -- ``actions.halt()`` makes every effector raise
           ``DesktopHalted``, so no further synthetic input reaches the OS;
        2. the capability is fenced, not transferred -- ``manual_takeover``
           revokes admission so a second holder cannot be admitted while the
           prior owner's work is still unwinding.

        Returns what actually happened so callers and logs never claim a
        postcondition that was not reached.
        """
        from charlie.resource_locks import current_owner, get_revocations

        errors: list[str] = []
        dispatch_halted = False
        try:
            from charlie.desktop import actions

            actions.halt()
            dispatch_halted = actions.is_halted()
            if not dispatch_halted:
                raise RuntimeError("desktop actions did not enter the halted state")
        except Exception as exc:
            errors.append(f"desktop dispatch halt failed: {exc}")
            logger.error("Failed to halt desktop actions during user takeover", exc_info=True)

        try:
            from charlie.resource_locks import default_lease_manager

            default_lease_manager.manual_takeover(("desktop",))
        except Exception as exc:
            errors.append(f"desktop lease fence failed: {exc}")
            logger.error("Failed to revoke canonical desktop lease during user takeover", exc_info=True)

        revocations = get_revocations()
        revocation = revocations.get("desktop")
        owner = current_owner("desktop")

        outcome = TakeoverHalt(
            dispatch_halted=dispatch_halted,
            fenced_owners=(owner,) if owner else (),
            fenced_capabilities=("desktop",) if revocation is not None else (),
            error="; ".join(errors) or None,
        )
        logger.warning(
            "Takeover halt complete: dispatch_halted=%s fenced_owners=%s fenced=%s error=%s",
            outcome.dispatch_halted,
            outcome.fenced_owners,
            outcome.fenced_capabilities,
            outcome.error,
        )
        return outcome

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
