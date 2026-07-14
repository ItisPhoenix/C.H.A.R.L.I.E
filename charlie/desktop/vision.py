"""Set-of-marks screenshot annotation for the vision perception tier.

Reuses charlie.desktop.ocr.capture() for the screenshot -- do not duplicate
it here. Draws numbered boxes over the combined UIA + OCR mark list so a
vision model refers to targets by mark id, never raw pixel coordinates,
keeping click resolution deterministic even though perception is vision-based.
"""

import base64
import io
import logging
from typing import List

from charlie.desktop.uia import Element

logger = logging.getLogger("charlie.desktop.vision")

try:
    from PIL import Image, ImageDraw
    VISION_AVAILABLE = True
except ImportError:
    VISION_AVAILABLE = False

_BOX_COLOR = (255, 64, 64)
_LABEL_COLOR = (255, 255, 255)


def annotate_som(png_bytes: bytes, elements: List[Element]) -> bytes:
    """Draw numbered boxes over `elements` on top of `png_bytes`; return PNG bytes."""
    if not VISION_AVAILABLE:
        raise RuntimeError("Pillow not installed -- vision annotation unavailable.")
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    draw = ImageDraw.Draw(img)
    for e in elements:
        left, top, right, bottom = e.bounds
        # Real UIA elements can report negative/offscreen or swapped bounds
        # (multi-monitor negative coordinates, partially offscreen controls) --
        # normalize before drawing, since PIL requires x1>=x0 and y1>=y0.
        x0, x1 = min(left, right), max(left, right)
        y0, y1 = min(top, bottom), max(top, bottom)
        draw.rectangle((x0, y0, x1, y1), outline=_BOX_COLOR, width=2)
        label = str(e.mark_id)
        label_y0 = max(0, y0 - 14)
        label_y1 = max(label_y0, y0)
        draw.rectangle((x0, label_y0, x0 + 8 + 7 * len(label), label_y1), fill=_BOX_COLOR)
        draw.text((x0 + 2, label_y0), label, fill=_LABEL_COLOR)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def to_data_url(png_bytes: bytes) -> str:
    """Base64 `data:image/png;base64,...` URL for an LLM image_url payload."""
    encoded = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{encoded}"
