"""Windows UI Automation effectors -- click/type/invoke/key.

Perception (charlie.desktop.uia) hands off live control handles by mark_id;
this module turns them into actual mouse/keyboard motion. Every effector
checks the halt flag first so a panic hotkey or anomaly auto-halt (wired in
charlie.core) stops motion within one action, never mid-action.
"""

import ctypes
import logging
import re
import threading
from typing import Any, Optional, Tuple

logger = logging.getLogger("charlie.desktop.actions")

try:
    import pyautogui
    # Off: an idle cursor in a corner (not an abort gesture) was tripping every
    # subsequent call. The real stop mechanisms are the panic hotkey and the
    # per-turn auto-halt below, not this uncoordinated pyautogui default.
    pyautogui.FAILSAFE = False
    _HAS_PYAUTOGUI = True
except Exception:  # pyautogui also raises on headless Linux (no X display), not just ImportError
    _HAS_PYAUTOGUI = False

_HALT = threading.Event()
# Same clock as charlie.desktop.session's GetLastInputInfo reads, so external_input_since() can compare them.
_last_action_tick_ms = 0


def _record_action_tick() -> None:
    global _last_action_tick_ms
    if hasattr(ctypes, "windll"):
        _last_action_tick_ms = ctypes.windll.kernel32.GetTickCount()


def last_action_tick_ms() -> int:
    return _last_action_tick_ms

_SECURE_REFUSAL = (
    "Refusing to type into a secure field. I've handed control back to you -- "
    "fill it in, then say continue."
)
# Payment/credential field names or automation ids that hard-stop typing even
# when the UIA IsPassword flag isn't set (e.g. a card-number field is plain
# text but still sensitive).
_PAYMENT_FIELD_RE = re.compile(
    r"(password|passwd|pwd|card.?number|cvv|cvc|ssn|routing)", re.IGNORECASE
)


class DesktopHalted(Exception):
    """Raised when an effector runs while the halt flag is set."""


def halt() -> None:
    _HALT.set()


def clear_halt() -> None:
    _HALT.clear()


def is_halted() -> bool:
    return _HALT.is_set()


def _check_halt() -> None:
    if _HALT.is_set():
        raise DesktopHalted("Desktop control halted.")
    _record_action_tick()


def _center(bounds: Tuple[int, int, int, int]) -> Tuple[int, int]:
    left, top, right, bottom = bounds
    return (left + right) // 2, (top + bottom) // 2


def _try_uia_invoke(control: Any) -> bool:
    """Best-effort UIA InvokePattern.Invoke() -- no physical input, works on
    background/occluded windows. Returns False (not raises) if the control
    doesn't support it, so the caller can fall back to a physical click."""
    try:
        pattern = control.GetInvokePattern()
        if pattern is None:
            return False
        pattern.Invoke()
        return True
    except Exception:
        logger.debug("UIA InvokePattern unavailable/failed", exc_info=True)
        return False


def _try_uia_set_value(control: Any, text: str) -> bool:
    """Best-effort UIA ValuePattern.SetValue() -- no physical input, no focus
    required. Returns False (not raises) if unsupported, so the caller can
    fall back to click + typewrite."""
    try:
        pattern = control.GetValuePattern()
        if pattern is None:
            return False
        pattern.SetValue(text)
        return True
    except Exception:
        logger.debug("UIA ValuePattern unavailable/failed", exc_info=True)
        return False


def click_mark(mark_id: int) -> str:
    _check_halt()
    from charlie.desktop.uia import Element, resolve_bounds, resolve_mark
    try:
        control = resolve_mark(mark_id)
    except KeyError as e:
        return f"Error: {e}"

    if not isinstance(control, Element) and _try_uia_invoke(control):
        return f"Invoked mark [{mark_id}] via UIA. This succeeded -- no need to click it again."

    if not _HAS_PYAUTOGUI:
        return "Error: pyautogui is not installed -- desktop control unavailable."
    try:
        x, y = _center(resolve_bounds(mark_id))
        pyautogui.click(x, y)
        return f"Clicked mark [{mark_id}]. This succeeded -- no need to click it again."
    except DesktopHalted:
        raise
    except Exception as e:
        logger.warning("desktop_click failed for mark %s", mark_id, exc_info=True)
        return f"Error clicking mark [{mark_id}]: {e}"


def type_text(mark_id: int, text: str) -> str:
    _check_halt()
    from charlie.desktop.uia import Element, resolve_bounds, resolve_is_password, resolve_mark, resolve_name
    try:
        control = resolve_mark(mark_id)
    except KeyError as e:
        return f"Error: {e}"

    name = resolve_name(mark_id)
    automation_id = getattr(control, "AutomationId", "") or ""
    if (
        resolve_is_password(mark_id)
        or _PAYMENT_FIELD_RE.search(name)
        or _PAYMENT_FIELD_RE.search(automation_id)
    ):
        logger.info("secure field detected -- refusing to type")
        return _SECURE_REFUSAL

    if not isinstance(control, Element) and _try_uia_set_value(control, text):
        return (
            f"Typed {text!r} into mark [{mark_id}] via UIA. This succeeded -- do not retype it via "
            "shell_execute or any other tool."
        )

    if not _HAS_PYAUTOGUI:
        return "Error: pyautogui is not installed -- desktop control unavailable."
    try:
        x, y = _center(resolve_bounds(mark_id))
        pyautogui.click(x, y)
        pyautogui.typewrite(text, interval=0.02)
        return (
            f"Typed {text!r} into mark [{mark_id}]. This succeeded -- do not retype it via "
            "shell_execute or any other tool."
        )
    except DesktopHalted:
        raise
    except Exception as e:
        logger.warning("desktop_type failed for mark %s", mark_id, exc_info=True)
        return f"Error typing into mark [{mark_id}]: {e}"


def invoke_mark(mark_id: int) -> str:
    _check_halt()
    from charlie.desktop.uia import Element, resolve_mark
    try:
        control = resolve_mark(mark_id)
    except KeyError as e:
        return f"Error: {e}"
    if isinstance(control, Element):
        # OCR-sourced marks have no invoke action -- click is the only real option, so just do it.
        logger.info("Mark %s is OCR-sourced -- auto-falling back to click.", mark_id)
        return click_mark(mark_id)
    try:
        if _try_uia_invoke(control):
            return f"Invoked mark [{mark_id}]."
        return click_mark(mark_id)
    except DesktopHalted:
        raise
    except Exception as e:
        logger.warning("desktop_invoke failed for mark %s", mark_id, exc_info=True)
        return f"Error invoking mark [{mark_id}]: {e}"


def key_press(keys: str) -> str:
    _check_halt()
    if not _HAS_PYAUTOGUI:
        return "Error: pyautogui is not installed -- desktop control unavailable."
    try:
        parts = [k.strip() for k in keys.split("+") if k.strip()]
        if not parts:
            return "Error: no keys specified."
        pyautogui.hotkey(*parts)
        return f"Sent key chord: {keys}."
    except DesktopHalted:
        raise
    except Exception as e:
        logger.warning("desktop_key failed for '%s'", keys, exc_info=True)
        return f"Error sending key chord '{keys}': {e}"


_SCROLL_UNIT = 120  # one wheel notch on Windows
_DRAG_DURATION_S = 0.4  # instant drags get ignored by many apps


def _to_screen(x: int, y: int) -> Optional[Tuple[int, int]]:
    from charlie.desktop.uia import image_to_screen
    return image_to_screen(x, y)


def click_at(x: int, y: int, button: str = "left", double: bool = False) -> str:
    _check_halt()
    if not _HAS_PYAUTOGUI:
        return "Error: pyautogui is not installed -- desktop control unavailable."
    pt = _to_screen(x, y)
    if pt is None:
        return "Error: no capture bounds -- call desktop_observe or desktop_screenshot first."
    try:
        pyautogui.click(pt[0], pt[1], button=button, clicks=2 if double else 1)
        return f"Clicked at image ({x},{y}) -> screen {pt}."
    except DesktopHalted:
        raise
    except Exception as e:
        logger.warning("click_at failed", exc_info=True)
        return f"Error clicking at ({x},{y}): {e}"


def move_to(x: int, y: int) -> str:
    _check_halt()
    if not _HAS_PYAUTOGUI:
        return "Error: pyautogui is not installed -- desktop control unavailable."
    pt = _to_screen(x, y)
    if pt is None:
        return "Error: no capture bounds -- call desktop_observe or desktop_screenshot first."
    try:
        pyautogui.moveTo(pt[0], pt[1])
        return f"Moved cursor to image ({x},{y})."
    except DesktopHalted:
        raise
    except Exception as e:
        logger.warning("move_to failed", exc_info=True)
        return f"Error moving to ({x},{y}): {e}"


def drag(x1: int, y1: int, x2: int, y2: int) -> str:
    _check_halt()
    if not _HAS_PYAUTOGUI:
        return "Error: pyautogui is not installed -- desktop control unavailable."
    p1 = _to_screen(x1, y1)
    p2 = _to_screen(x2, y2)
    if p1 is None or p2 is None:
        return "Error: no capture bounds -- call desktop_observe or desktop_screenshot first."
    try:
        pyautogui.moveTo(p1[0], p1[1])
        pyautogui.dragTo(p2[0], p2[1], duration=_DRAG_DURATION_S, button="left")
        return f"Dragged ({x1},{y1}) -> ({x2},{y2})."
    except DesktopHalted:
        raise
    except Exception as e:
        logger.warning("drag failed", exc_info=True)
        return f"Error dragging ({x1},{y1}) -> ({x2},{y2}): {e}"


def scroll(notches: int) -> str:
    _check_halt()
    if not _HAS_PYAUTOGUI:
        return "Error: pyautogui is not installed -- desktop control unavailable."
    try:
        pyautogui.scroll(notches * _SCROLL_UNIT)
        return f"Scrolled {notches} notches."
    except DesktopHalted:
        raise
    except Exception as e:
        logger.warning("scroll failed", exc_info=True)
        return f"Error scrolling {notches} notches: {e}"


_SYSTEM_ACTIONS = {
    "volume_up": "volumeup",
    "volume_down": "volumedown",
    "mute": "volumemute",
    "play_pause": "playpause",
    "next_track": "nexttrack",
    "prev_track": "prevtrack",
}


def system_control(action: str) -> str:
    _check_halt()
    if not _HAS_PYAUTOGUI:
        return "Error: pyautogui is not installed -- desktop control unavailable."
    key = _SYSTEM_ACTIONS.get(action)
    if key is None:
        return f"Error: unknown action '{action}'. Valid: {', '.join(sorted(_SYSTEM_ACTIONS))}."
    try:
        pyautogui.press(key)
        return f"Done: {action}."
    except DesktopHalted:
        raise
    except Exception as e:
        logger.warning("system_control failed for '%s'", action, exc_info=True)
        return f"Error sending system control '{action}': {e}"
