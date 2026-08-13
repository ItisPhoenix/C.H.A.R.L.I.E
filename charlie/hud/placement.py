"""Region -> screen-rect placement math for HUD surfaces. Pure, no Qt import, unit-testable."""
import logging
from typing import Tuple

logger = logging.getLogger("charlie.hud.placement")

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


def get_primary_screen_rect() -> Rect:
    """Primary monitor's work area (taskbar excluded) -- must match Qt's
    QScreen.availableGeometry(), which is what every SurfaceWindow is positioned relative to.
    GetSystemMetrics(SM_CXSCREEN/SM_CYSCREEN) would return the full screen including the
    taskbar strip, silently clipping bottom-anchored widgets against the smaller real window."""
    try:
        import ctypes

        class _RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        SPI_GETWORKAREA = 0x0030
        rect = _RECT()
        if not ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
            raise OSError("SystemParametersInfoW(SPI_GETWORKAREA) failed")
        return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
    except Exception as e:
        logger.warning("SPI_GETWORKAREA failed, falling back to 1920x1080: %s", e)
        return (0, 0, 1920, 1080)
