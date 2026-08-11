"""Desktop session arbitration: exclusive-owner mutex + user-idle detection.

The physical mouse/keyboard is one shared resource. Foreground chat turns
and background operator tasks must acquire it; background tasks also
require the user to be idle (GetLastInputInfo) and pause the moment real
input arrives. The mutex itself is charlie.resource_locks, keyed "desktop" --
this module is a thin, capability-named wrapper so existing callers keep
their exact acquire_desktop/release_desktop/current_owner signatures.

Limitation: pyautogui's own synthetic input also bumps GetLastInputInfo's
timestamp on Windows, so user_idle_seconds() alone cannot tell "the user
just moved the mouse" apart from "our own automation just moved it."
external_input_since() compares a fresh _last_input_tick_ms() read against
the automation loop's own last-action tick (charlie.desktop.actions.
last_action_tick_ms()) to detect real external input.

ctypes.windll only exists on Windows -- guarded the same way as
charlie/desktop/windows.py's _user32/_kernel32 so importing this module
(e.g. from tests collected on CI's ubuntu-latest runner) never raises.
charlie.desktop is Windows-only in practice (see __init__.py's
DESKTOP_AVAILABLE), so _kernel32 being None off-Windows is fine -- the
functions below are never actually called there.
"""
import ctypes
import sys
from typing import Optional

from charlie import resource_locks

_CAPABILITY = "desktop"

_kernel32 = ctypes.windll.kernel32 if sys.platform == "win32" else None
if _kernel32 is not None:
    _kernel32.GetTickCount.restype = ctypes.c_uint


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def _last_input_tick_ms() -> int:
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
    return info.dwTime


def _now_tick_ms() -> int:
    return _kernel32.GetTickCount()


def user_idle_seconds() -> float:
    return max(0.0, (_now_tick_ms() - _last_input_tick_ms()) / 1000.0)


def external_input_since(tick_ms: int) -> bool:
    """True if real user input landed after `tick_ms` (an automation
    action's own last-recorded tick, from charlie.desktop.actions.
    last_action_tick_ms()). Distinguishes the user's own mouse/keyboard from
    pyautogui's synthetic input, which also bumps GetLastInputInfo -- see the
    module docstring."""
    return _last_input_tick_ms() > tick_ms


def acquire_desktop(owner_id: str) -> bool:
    return resource_locks.acquire(_CAPABILITY, owner_id)


def release_desktop(owner_id: str) -> None:
    resource_locks.release(_CAPABILITY, owner_id)


def current_owner() -> Optional[str]:
    return resource_locks.current_owner(_CAPABILITY)
