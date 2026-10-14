"""Brief, click-through focus cue for Charlie's current native desktop target."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import queue
import sys
import threading
from typing import Any, Optional

logger = logging.getLogger("charlie.computer.focus_overlay")

_WM_QUIT = 0x0012
_WM_APP_UPDATE = 0x8001
_WS_POPUP = 0x80000000
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_NOACTIVATE = 0x08000000
_SWP_NOACTIVATE = 0x0010
_SWP_SHOWWINDOW = 0x0040
_SW_SHOWNA = 8
_SW_HIDE = 0
_LWA_ALPHA = 0x2
_RGN_DIFF = 4
_HTTRANSPARENT = -1
_WM_NCHITTEST = 0x0084
_WM_MOUSEACTIVATE = 0x0021
_MA_NOACTIVATE = 3
_BORDER = 5


class _NativeOverlay:
    """Own its Win32 window on a short lived message-pump thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._thread_id = 0
        self._rect: Optional[dict[str, int]] = None
        self._ready = threading.Event()

    def show(self, rect: dict[str, int]) -> None:
        if sys.platform != "win32":
            return
        with self._lock:
            self._rect = rect
            if self._thread and self._thread.is_alive():
                thread_id = self._thread_id
            else:
                self._ready.clear()
                self._thread = threading.Thread(target=self._run, daemon=True, name="CharlieDesktopFocusCue")
                self._thread.start()
                thread_id = 0
        if thread_id:
            ctypes.windll.user32.PostThreadMessageW(thread_id, _WM_APP_UPDATE, 0, 0)
        else:
            if not self._ready.wait(1.0):
                self.hide()

    def hide(self) -> None:
        with self._lock:
            thread = self._thread
            thread_id = self._thread_id
            self._thread = None
            self._thread_id = 0
            self._rect = None
        if thread and thread.is_alive():
            ctypes.windll.user32.PostThreadMessageW(thread_id, _WM_QUIT, 0, 0)
            if thread is not threading.current_thread():
                thread.join(timeout=1.0)

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        kernel32 = ctypes.windll.kernel32
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.RegisterClassW.argtypes = [ctypes.c_void_p]
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.COLORREF, wintypes.BYTE, wintypes.DWORD]
        user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.PeekMessageW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
        user32.PeekMessageW.restype = wintypes.BOOL
        user32.GetMessageW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype = ctypes.c_int
        user32.TranslateMessage.argtypes = [ctypes.c_void_p]
        user32.DispatchMessageW.argtypes = [ctypes.c_void_p]
        gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
        gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
        gdi32.CreateRoundRectRgn.restype = wintypes.HRGN
        gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        gdi32.CombineRgn.argtypes = [wintypes.HRGN, wintypes.HRGN, wintypes.HRGN, ctypes.c_int]
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        thread_id = kernel32.GetCurrentThreadId()
        class_name = f"CharlieFocusCue_{thread_id}"

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", ctypes.c_uint),
                ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", ctypes.c_void_p),
                ("hIcon", ctypes.c_void_p),
                ("hCursor", ctypes.c_void_p),
                ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", ctypes.c_wchar_p),
                ("lpszClassName", ctypes.c_wchar_p),
            ]

        wndproc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        @wndproc_type
        def wndproc(hwnd, message, wparam, lparam):
            if message == _WM_NCHITTEST:
                return _HTTRANSPARENT
            if message == _WM_MOUSEACTIVATE:
                return _MA_NOACTIVATE
            return user32.DefWindowProcW(hwnd, message, wparam, lparam)

        instance = kernel32.GetModuleHandleW(None)
        brush = gdi32.CreateSolidBrush(0x00FF873A)  # COLORREF: restrained blue.
        wc = WNDCLASSW()
        wc.lpfnWndProc = ctypes.cast(wndproc, ctypes.c_void_p).value
        wc.hInstance = instance
        wc.hbrBackground = brush
        wc.lpszClassName = class_name
        hwnd = None
        try:
            if not user32.RegisterClassW(ctypes.byref(wc)):
                return
            hwnd = user32.CreateWindowExW(
                _WS_EX_LAYERED | _WS_EX_TRANSPARENT | _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE,
                class_name,
                "",
                _WS_POPUP,
                0,
                0,
                1,
                1,
                None,
                None,
                instance,
                None,
            )
            if not hwnd:
                return
            user32.SetLayeredWindowAttributes(hwnd, 0, 132, _LWA_ALPHA)
            message = wintypes.MSG()
            user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
            with self._lock:
                self._thread_id = thread_id
            self._ready.set()
            self._position(hwnd, self._current_rect(), user32, gdi32)
            while True:
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0:
                    break
                if message.message == _WM_APP_UPDATE:
                    self._position(hwnd, self._current_rect(), user32, gdi32)
                else:
                    user32.TranslateMessage(ctypes.byref(message))
                    user32.DispatchMessageW(ctypes.byref(message))
        except Exception:
            logger.debug("Desktop focus cue could not be displayed", exc_info=True)
        finally:
            self._ready.set()
            if hwnd:
                user32.DestroyWindow(hwnd)
            user32.UnregisterClassW(class_name, instance)
            gdi32.DeleteObject(brush)

    def _current_rect(self) -> Optional[dict[str, int]]:
        with self._lock:
            return dict(self._rect) if self._rect else None

    @staticmethod
    def _position(hwnd: int, rect: Optional[dict[str, int]], user32: Any, gdi32: Any) -> None:
        if not rect:
            user32.ShowWindow(hwnd, _SW_HIDE)
            return
        x, y, width, height = (rect[key] for key in ("x", "y", "width", "height"))
        if width <= _BORDER * 2 or height <= _BORDER * 2:
            user32.ShowWindow(hwnd, _SW_HIDE)
            return
        outer = gdi32.CreateRoundRectRgn(0, 0, width, height, 18, 18)
        inner = gdi32.CreateRoundRectRgn(_BORDER, _BORDER, width - _BORDER, height - _BORDER, 12, 12)
        if not outer or not inner:
            if outer:
                gdi32.DeleteObject(outer)
            if inner:
                gdi32.DeleteObject(inner)
            user32.ShowWindow(hwnd, _SW_HIDE)
            return
        if gdi32.CombineRgn(outer, outer, inner, _RGN_DIFF) == 0:
            gdi32.DeleteObject(outer)
            gdi32.DeleteObject(inner)
            user32.ShowWindow(hwnd, _SW_HIDE)
            return
        gdi32.DeleteObject(inner)
        if not user32.SetWindowRgn(hwnd, outer, True):
            gdi32.DeleteObject(outer)
            user32.ShowWindow(hwnd, _SW_HIDE)
            return
        if not user32.SetWindowPos(hwnd, ctypes.c_void_p(-1), x, y, width, height, _SWP_NOACTIVATE | _SWP_SHOWWINDOW):
            user32.ShowWindow(hwnd, _SW_HIDE)
            return
        user32.ShowWindow(hwnd, _SW_SHOWNA)


_overlay = _NativeOverlay()
_FOCUS_TOOLS = {
    "desktop_click", "desktop_type", "desktop_invoke", "desktop_key",
    "desktop_click_at", "desktop_move", "desktop_drag", "desktop_scroll",
    "desktop_focus", "desktop_window", "desktop_move_window",
}


def is_desktop_focus_tool(tool_name: str) -> bool:
    return tool_name in _FOCUS_TOOLS


def show_desktop_focus(tool_name: str, arguments: Any) -> bool:
    """Show a cue only when the canonical backend confirms exact live bounds."""
    if not is_desktop_focus_tool(tool_name) or sys.platform != "win32":
        return False
    try:
        from .backend import get_backend

        hint = arguments.get("window") if isinstance(arguments, dict) else None
        rect = get_backend().focus_overlay_rect(window_hint=hint if isinstance(hint, str) else None)
        if not rect:
            return False
        _overlay.show(rect)
        return True
    except Exception:
        logger.debug("Desktop focus cue setup failed", exc_info=True)
        return False


def hide_desktop_focus() -> None:
    try:
        _overlay.hide()
    except Exception:
        logger.debug("Desktop focus cue cleanup failed", exc_info=True)
