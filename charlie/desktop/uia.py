"""Windows UI Automation perception layer -- set-of-marks text serialization.

Perception (this module) and effectors (charlie.desktop.actions) share one
mark_id contract: snapshot_tree() assigns ids, serialize_marks() renders them
as plain text for the model, resolve_mark() hands the live control back to
an effector. This is the one place that walks the accessibility tree.
"""

import logging
import threading
import time
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("charlie.desktop.uia")

try:
    import uiautomation as _uia
    _HAS_UIA = True
except ImportError:
    _uia = None
    _HAS_UIA = False

_MAX_DEPTH_DEFAULT = 25
_MAX_MARKS_DEFAULT = 120  # bounds prompt size/walk time -- depth alone doesn't (Chrome/Electron/WinUI nest past 8)

# Control types worth exposing to the model -- generic containers (pane/group/window/custom) skipped as noise.
_INTERESTING_CONTROL_TYPES = {
    "ButtonControl", "EditControl", "CheckBoxControl", "RadioButtonControl",
    "ComboBoxControl", "ListItemControl", "MenuItemControl", "TabItemControl",
    "HyperlinkControl", "TreeItemControl", "TextControl", "DocumentControl",
    "SplitButtonControl", "MenuControl", "SliderControl", "DataItemControl",
    "HeaderItemControl", "ImageControl",
}


@dataclass
class Element:
    mark_id: int
    name: str
    control_type: str
    bounds: Tuple[int, int, int, int]
    is_password: bool
    is_offscreen: bool


# Per-turn mark cache: mark_id -> live UIA control handle, rebuilt on every
# desktop_observe call. Desktop tools run serialized behind core.py's
# interactive lock, so this module-level cache is never touched concurrently.
_controls: Dict[int, Any] = {}
_lock = threading.Lock()

# Screen rect of the most recent ocr.capture() grab, for image-to-screen coord mapping,
# plus the monotonic clock reading at which that grab was taken.
_LAST_CAPTURE_BOUNDS: Optional[Tuple[int, int, int, int]] = None
_LAST_CAPTURE_AT: Optional[float] = None

# How long an image-space coordinate mapping stays valid. Image coords are only
# meaningful against the exact screen rect they were captured from: once a window
# moves, resizes, or the screen layout changes, an old rect silently turns a
# click into a miss (or worse, a click on whatever now occupies those pixels).
# The default is sized for the slowest legitimate path -- capture, a full LLM
# reasoning round-trip, then dispatch -- with margin, while still bounding how
# long a stale rect can be acted on. Refusing a stale capture costs one
# re-observe; trusting one costs a mis-click the user has to undo.
CAPTURE_STALENESS_SECONDS = 30.0


def set_last_capture_bounds(bounds: Optional[Tuple[int, int, int, int]]) -> None:
    global _LAST_CAPTURE_BOUNDS, _LAST_CAPTURE_AT
    _LAST_CAPTURE_BOUNDS = bounds
    # Clearing the bounds must clear the clock too, or a later capture-less read
    # would see a fresh-looking timestamp.
    _LAST_CAPTURE_AT = None if bounds is None else time.monotonic()


def get_last_capture_bounds() -> Optional[Tuple[int, int, int, int]]:
    return _LAST_CAPTURE_BOUNDS


def capture_age_seconds() -> Optional[float]:
    """Monotonic age of the recorded capture, or None if there is no capture."""
    if _LAST_CAPTURE_BOUNDS is None or _LAST_CAPTURE_AT is None:
        return None
    return max(0.0, time.monotonic() - _LAST_CAPTURE_AT)


def last_capture_staleness() -> Optional[str]:
    """None when the recorded capture is usable; otherwise why it is not.

    Returned as an explainable reason so a refusal can be traced to the capture
    rather than surfacing as an opaque "no capture bounds".
    """
    if _LAST_CAPTURE_BOUNDS is None or _LAST_CAPTURE_AT is None:
        return "no capture recorded"
    age = capture_age_seconds()
    if age is not None and age > CAPTURE_STALENESS_SECONDS:
        return (
            f"capture is stale ({age:.1f}s old, limit {CAPTURE_STALENESS_SECONDS:.0f}s) -- "
            "screen may have moved or resized"
        )
    return None


def image_to_screen(x: int, y: int) -> Optional[Tuple[int, int]]:
    """Translate captured-image pixel coords to absolute screen coords.

    Returns None for a missing *or* stale capture, so click_at/move_to/drag
    cannot dispatch against a screen rect that no longer describes the display.
    """
    reason = last_capture_staleness()
    if reason is not None:
        logger.warning("Refusing image-space coordinate (%s,%s): %s", x, y, reason)
        return None
    left, top, _right, _bottom = _LAST_CAPTURE_BOUNDS
    return left + x, top + y


def _walk(
    control: Any, marks: List[Element], controls: Dict[int, Any], depth: int, max_depth: int,
    max_marks: int = _MAX_MARKS_DEFAULT,
) -> None:
    if control is None or depth > max_depth or len(marks) >= max_marks:
        return
    try:
        control_type = control.ControlTypeName
        offscreen = bool(control.IsOffscreen)
        if not offscreen and control_type in _INTERESTING_CONTROL_TYPES:
            name = (control.Name or "").strip()
            if name or control_type in ("EditControl", "DocumentControl"):
                rect = control.BoundingRectangle
                mark_id = len(marks) + 1
                marks.append(Element(
                    mark_id=mark_id,
                    name=name or "(unlabeled)",
                    control_type=control_type.replace("Control", ""),
                    bounds=(rect.left, rect.top, rect.right, rect.bottom),
                    is_password=bool(getattr(control, "IsPassword", False)),
                    is_offscreen=offscreen,
                ))
                controls[mark_id] = control
    except Exception:
        logger.debug("Skipping control during UIA walk", exc_info=True)
        return

    try:
        children = control.GetChildren()
    except Exception:
        return
    for child in children:
        if len(marks) >= max_marks:
            return
        _walk(child, marks, controls, depth + 1, max_depth, max_marks)


def control_from_hwnd(hwnd: int) -> Optional[Any]:
    """Build a UIA control handle for a specific window hwnd, for snapshot_tree(root=...)."""
    if not _HAS_UIA:
        return None
    try:
        return _uia.ControlFromHandle(hwnd)
    except Exception:
        logger.warning("ControlFromHandle failed for hwnd %s", hwnd, exc_info=True)
        return None


def snapshot_tree(
    max_depth: int = _MAX_DEPTH_DEFAULT, root: Optional[Any] = None, max_marks: int = _MAX_MARKS_DEFAULT
) -> List[Element]:
    """Walk the foreground window (never the whole desktop) and return marked elements, capped at max_marks."""
    if not _HAS_UIA:
        return []
    try:
        window = root if root is not None else _uia.GetForegroundControl()
        if window is None:
            return []
        marks: List[Element] = []
        controls: Dict[int, Any] = {}
        _walk(window, marks, controls, depth=0, max_depth=max_depth, max_marks=max_marks)
        with _lock:
            _controls.clear()
            _controls.update(controls)
        return marks
    except Exception:
        logger.warning("UIA snapshot failed", exc_info=True)
        return []


def serialize_marks(elements: List[Element]) -> str:
    """Render marks as text, e.g. `[3] Button "Save"` -- identical for local and cloud models."""
    lines = [f'[{e.mark_id}] {e.control_type} "{e.name}"' for e in elements]
    return "\n".join(lines) if lines else "(no marked elements)"


def resolve_mark(mark_id: int) -> Any:
    """Look up a live control handle (or OCR Element) from the most recent
    desktop_observe call."""
    with _lock:
        control = _controls.get(mark_id)
    if control is None:
        raise KeyError(f"Mark id {mark_id} not found -- call desktop_observe again.")
    return control


def merge_ocr_elements(uia_elements: List[Element], ocr_elements: List[Element]) -> List[Element]:
    """Append OCR-sourced Elements to a UIA snapshot, continuing the mark_id
    sequence and registering them in the same per-turn cache resolve_mark()
    reads from -- so desktop_click/type/invoke resolve OCR marks unchanged."""
    start = len(uia_elements)
    merged = list(uia_elements)
    with _lock:
        for i, e in enumerate(ocr_elements):
            renumbered = replace(e, mark_id=start + i + 1)
            merged.append(renumbered)
            _controls[renumbered.mark_id] = renumbered
    return merged


def resolve_bounds(mark_id: int) -> Tuple[int, int, int, int]:
    """Bounding box for a mark, whether it's a live UIA control or an OCR Element."""
    handle = resolve_mark(mark_id)
    if isinstance(handle, Element):
        return handle.bounds
    rect = handle.BoundingRectangle
    return (rect.left, rect.top, rect.right, rect.bottom)


def resolve_is_password(mark_id: int) -> bool:
    handle = resolve_mark(mark_id)
    if isinstance(handle, Element):
        return handle.is_password
    return bool(getattr(handle, "IsPassword", False))


def is_low_confidence_mark(mark_id: int) -> bool:
    """True if a mark has no live UIA control handle (OCR/vision-sourced) or doesn't resolve."""
    try:
        handle = resolve_mark(mark_id)
    except KeyError:
        return True
    return isinstance(handle, Element)


def resolve_name(mark_id: int) -> str:
    handle = resolve_mark(mark_id)
    if isinstance(handle, Element):
        return handle.name
    return getattr(handle, "Name", "") or ""
