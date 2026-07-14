"""Windows UI Automation perception layer -- set-of-marks text serialization.

Perception (this module) and effectors (charlie.desktop.actions) share one
mark_id contract: snapshot_tree() assigns ids, serialize_marks() renders them
as plain text for the model, resolve_mark() hands the live control back to
an effector. This is the one place that walks the accessibility tree.
"""

import logging
import threading
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("charlie.desktop.uia")

try:
    import uiautomation as _uia
    _HAS_UIA = True
except ImportError:
    _HAS_UIA = False

_MAX_DEPTH_DEFAULT = 8

# Control types worth exposing to the model. Generic containers (pane,
# group, window, custom) are skipped -- they're not actionable and just add
# noise to the set-of-marks text.
_INTERESTING_CONTROL_TYPES = {
    "ButtonControl", "EditControl", "CheckBoxControl", "RadioButtonControl",
    "ComboBoxControl", "ListItemControl", "MenuItemControl", "TabItemControl",
    "HyperlinkControl", "TreeItemControl", "TextControl",
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


def _walk(control: Any, marks: List[Element], controls: Dict[int, Any], depth: int, max_depth: int) -> None:
    if control is None or depth > max_depth:
        return
    try:
        control_type = control.ControlTypeName
        offscreen = bool(control.IsOffscreen)
        if not offscreen and control_type in _INTERESTING_CONTROL_TYPES:
            name = (control.Name or "").strip()
            if name or control_type == "EditControl":
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
        _walk(child, marks, controls, depth + 1, max_depth)


def snapshot_tree(max_depth: int = _MAX_DEPTH_DEFAULT, root: Optional[Any] = None) -> List[Element]:
    """Walk the foreground window (never the whole desktop) and return marked elements."""
    if not _HAS_UIA:
        return []
    try:
        window = root if root is not None else _uia.GetForegroundControl()
        if window is None:
            return []
        marks: List[Element] = []
        controls: Dict[int, Any] = {}
        _walk(window, marks, controls, depth=0, max_depth=max_depth)
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


def resolve_name(mark_id: int) -> str:
    handle = resolve_mark(mark_id)
    if isinstance(handle, Element):
        return handle.name
    return getattr(handle, "Name", "") or ""
