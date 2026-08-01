"""Top-level window enumeration and management via user32.

Perception (which windows exist, what's their content) stays in uia.py;
this module only answers "what windows exist" and focuses/moves/resizes
them by title substring. hwnds are session-transient -- always re-enumerate,
never cache one across calls.
"""

import ctypes
import ctypes.wintypes
import logging
import sys
from typing import Dict, List, Optional

logger = logging.getLogger("charlie.desktop.windows")

# ctypes.windll only exists on win32 (backend CI job runs on ubuntu-latest,
# see .github/workflows/ci.yml); guard so importing this module never raises
# there, mirroring charlie/desktop/__init__.py's DESKTOP_AVAILABLE pattern.
_user32 = ctypes.windll.user32 if sys.platform == "win32" else None
_kernel32 = ctypes.windll.kernel32 if sys.platform == "win32" else None

try:
    import pyautogui
    _HAS_PYAUTOGUI = True
except ImportError:
    _HAS_PYAUTOGUI = False

_SW_MINIMIZE, _SW_MAXIMIZE, _SW_RESTORE = 6, 3, 9
_WM_CLOSE = 0x0010


def _enum_raw() -> List[tuple]:
    """(hwnd, title, is_visible) for every top-level window."""
    out: List[tuple] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def cb(hwnd, _lparam):
        length = _user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buf, length + 1)
        out.append((hwnd, buf.value, bool(_user32.IsWindowVisible(hwnd))))
        return True

    _user32.EnumWindows(cb, 0)
    return out


def list_windows() -> List[Dict]:
    return [{"hwnd": h, "title": t} for h, t, vis in _enum_raw() if vis and t]


def find_window(title_substr: str) -> Optional[Dict]:
    needle = title_substr.lower()
    for w in list_windows():
        if needle in w["title"].lower():
            return w
    return None


def focus_window(title_substr: str) -> str:
    w = find_window(title_substr)
    if w is None:
        return f"Error: no window matching '{title_substr}'."
    hwnd = w["hwnd"]
    _user32.ShowWindow(hwnd, _SW_RESTORE)
    if _user32.SetForegroundWindow(hwnd):
        return f"Focused window: {w['title']}"
    # Windows blocks cross-process focus steals unless the caller shares the
    # foreground thread's input state -- AttachThreadInput grants that.
    fg_thread = _user32.GetWindowThreadProcessId(_user32.GetForegroundWindow(), None)
    current_thread = _kernel32.GetCurrentThreadId()
    can_attach = bool(fg_thread) and fg_thread != current_thread
    attached = can_attach and _user32.AttachThreadInput(current_thread, fg_thread, True)
    try:
        _user32.BringWindowToTop(hwnd)
        ok = _user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            _user32.AttachThreadInput(current_thread, fg_thread, False)
    if not ok and _HAS_PYAUTOGUI:
        pyautogui.press("alt")
        ok = _user32.SetForegroundWindow(hwnd)
    if ok:
        return f"Focused window: {w['title']}"
    logger.warning("Could not bring '%s' to foreground.", w["title"])
    return f"Warning: could not bring '{w['title']}' to foreground."


def manage_window(title_substr: str, action: str) -> str:
    w = find_window(title_substr)
    if w is None:
        return f"Error: no window matching '{title_substr}'."
    ops = {"minimize": _SW_MINIMIZE, "maximize": _SW_MAXIMIZE, "restore": _SW_RESTORE}
    if action in ops:
        _user32.ShowWindow(w["hwnd"], ops[action])
        return f"{action.capitalize()}d: {w['title']}"
    if action == "close":
        _user32.PostMessageW(w["hwnd"], _WM_CLOSE, 0, 0)
        return f"Sent close to: {w['title']}"
    return f"Error: unknown action '{action}'. Valid: minimize, maximize, restore, close."


def move_resize_window(title_substr: str, x: int, y: int, width: int, height: int) -> str:
    w = find_window(title_substr)
    if w is None:
        return f"Error: no window matching '{title_substr}'."
    _user32.MoveWindow(w["hwnd"], x, y, width, height, True)
    return f"Moved '{w['title']}' to ({x},{y}) size {width}x{height}."
