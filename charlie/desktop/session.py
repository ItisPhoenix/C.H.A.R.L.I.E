"""Desktop session arbitration: exclusive-owner mutex + user-idle detection.

The physical mouse/keyboard is one shared resource. Foreground chat turns
and background operator tasks must acquire it; background tasks also
require the user to be idle (GetLastInputInfo) and pause the moment real
input arrives.

Limitation: pyautogui's own synthetic input also bumps GetLastInputInfo's
timestamp on Windows, so user_idle_seconds() alone cannot tell "the user
just moved the mouse" apart from "our own automation just moved it." A
later task must have the automation loop record its own last-action tick
and compare a fresh _last_input_tick_ms() read against it to detect real
external input; this module only provides the raw building blocks.

No import guard here: charlie.desktop is Windows-only (see __init__.py's
DESKTOP_AVAILABLE), and ctypes.windll is only touched inside function
bodies below, so importing this module stays safe on non-Windows -- it
would only raise (AttributeError on ctypes.windll) if those functions were
actually called off-Windows, same call-time binding the rest of the package
relies on.
"""
import ctypes
import threading
from typing import Optional

_lock = threading.Lock()
_owner: Optional[str] = None


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def _last_input_tick_ms() -> int:
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
    return info.dwTime


def _now_tick_ms() -> int:
    return ctypes.windll.kernel32.GetTickCount()


def user_idle_seconds() -> float:
    return max(0.0, (_now_tick_ms() - _last_input_tick_ms()) / 1000.0)


def acquire_desktop(owner_id: str) -> bool:
    global _owner
    with _lock:
        if _owner is None or _owner == owner_id:
            _owner = owner_id
            return True
        return False


def release_desktop(owner_id: str) -> None:
    global _owner
    with _lock:
        if _owner == owner_id:
            _owner = None


def current_owner() -> Optional[str]:
    return _owner
