"""Region -> screen-rect placement math for HUD surfaces. Pure, no Qt import, unit-testable."""
from typing import List, Tuple

Rect = Tuple[int, int, int, int]  # x, y, width, height

_MARGIN = 16
_SIZES = {
    "widget": (340, 180),
    "notification": (340, 180),
    "floating": (340, 180),
    "modal": (480, 320),
    "workspace": (900, 640),
}


def surface_size(mode: str, scale: float = 1.0) -> Tuple[int, int]:
    """Base pixel size for a presentation mode, scaled for DPI."""
    w, h = _SIZES.get(mode, _SIZES["widget"])
    return int(w * scale), int(h * scale)


def region_to_rect(
    region: str, screen: Rect, mode: str, scale: float = 1.0, stack_index: int = 0
) -> Rect:
    """Map an abstract region (top_right/bottom_right/top_left/bottom_left/center) to a real screen rect."""
    sx, sy, sw, sh = screen
    w, h = surface_size(mode, scale)
    margin = int(_MARGIN * scale)
    stack_offset = stack_index * (h + margin)

    if region == "center":
        return (sx + (sw - w) // 2, sy + (sh - h) // 2, w, h)
    if region == "bottom_right":
        return (sx + sw - w - margin, sy + sh - h - margin - stack_offset, w, h)
    if region == "top_left":
        return (sx + margin, sy + margin + stack_offset, w, h)
    if region == "bottom_left":
        return (sx + margin, sy + sh - h - margin - stack_offset, w, h)
    # top_right and any unrecognized region fall back here (ponytail: add strict validation if ever needed)
    return (sx + sw - w - margin, sy + margin + stack_offset, w, h)


def primary_screen_rect(screens: List[Rect]) -> Rect:
    """Pick which monitor hosts surfaces. ponytail: primary only, add real picking when multi-monitor is tested."""
    return screens[0]
