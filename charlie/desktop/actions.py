"""Windows UI Automation effectors -- click/type/invoke/key.

Perception (charlie.desktop.uia) hands off live control handles by mark_id;
this module turns them into actual mouse/keyboard motion. Every effector
checks the halt flag first so a panic hotkey or anomaly auto-halt (wired in
charlie.core) stops motion within one action, never mid-action.
"""

import logging
import re
import threading
from typing import Tuple

logger = logging.getLogger("charlie.desktop.actions")

try:
    import pyautogui
    pyautogui.FAILSAFE = True  # mouse-corner abort
    _HAS_PYAUTOGUI = True
except ImportError:
    _HAS_PYAUTOGUI = False

_HALT = threading.Event()

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


def _center(bounds: Tuple[int, int, int, int]) -> Tuple[int, int]:
    left, top, right, bottom = bounds
    return (left + right) // 2, (top + bottom) // 2


def click_mark(mark_id: int) -> str:
    _check_halt()
    if not _HAS_PYAUTOGUI:
        return "Error: pyautogui is not installed -- desktop control unavailable."
    from charlie.desktop.uia import resolve_bounds
    try:
        x, y = _center(resolve_bounds(mark_id))
        pyautogui.click(x, y)
        return f"Clicked mark [{mark_id}]."
    except KeyError as e:
        return f"Error: {e}"
    except DesktopHalted:
        raise
    except Exception as e:
        logger.warning("desktop_click failed for mark %s", mark_id, exc_info=True)
        return f"Error clicking mark [{mark_id}]: {e}"


def type_text(mark_id: int, text: str) -> str:
    _check_halt()
    if not _HAS_PYAUTOGUI:
        return "Error: pyautogui is not installed -- desktop control unavailable."
    from charlie.desktop.uia import resolve_bounds, resolve_is_password, resolve_mark, resolve_name
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

    try:
        x, y = _center(resolve_bounds(mark_id))
        pyautogui.click(x, y)
        pyautogui.typewrite(text, interval=0.02)
        return f"Typed into mark [{mark_id}]."
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
        if isinstance(control, Element):
            return (
                f"Error: mark [{mark_id}] is OCR-sourced text with no invoke action "
                "-- use desktop_click instead."
            )
        control.GetInvokePattern().Invoke()
        return f"Invoked mark [{mark_id}]."
    except KeyError as e:
        return f"Error: {e}"
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
