"""Screen-capture + OCR perception tier -- UIA-blind fallback.

Produces charlie.desktop.uia.Element records from recognized text, the same
shape UIA emits, so serialize_marks()/resolve_mark() are reused unchanged.
This tier only runs when snapshot_tree() found too little (charlie/tools.py).
capture() is also reused by the future vision tier -- do not duplicate it.
"""

import io
import logging
from typing import List, Optional, Tuple

from charlie.config import config
from charlie.desktop.uia import Element, set_last_capture_bounds

logger = logging.getLogger("charlie.desktop.ocr")

try:
    import mss
    import pytesseract
    from PIL import Image
    from pytesseract import Output
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

_MIN_CONF_DEFAULT = 60


def capture(
    region: Optional[Tuple[int, int, int, int]] = None,
    monitor: Optional[int] = None,
) -> bytes:
    """Screenshot as PNG bytes via mss.

    region: explicit (left, top, right, bottom) pixels to grab.
    monitor: 1-indexed physical monitor (mss.monitors[1], [2], ...) to grab
        in full, ignored if region is given. Neither given: full virtual
        screen (mss.monitors[0]), the existing default behavior.
    """
    if not OCR_AVAILABLE:
        raise RuntimeError("mss/pytesseract/Pillow not installed -- OCR unavailable.")
    with mss.MSS() as sct:
        if region is not None:
            grab_target = {
                "left": region[0],
                "top": region[1],
                "width": region[2] - region[0],
                "height": region[3] - region[1],
            }
        elif monitor is not None:
            grab_target = sct.monitors[monitor]
        else:
            grab_target = sct.monitors[0]
        if region is not None:
            bounds = region
        else:
            right = grab_target["left"] + grab_target["width"]
            bottom = grab_target["top"] + grab_target["height"]
            bounds = (grab_target["left"], grab_target["top"], right, bottom)
        set_last_capture_bounds(bounds)
        try:
            shot = sct.grab(grab_target)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        except (mss.exception.ScreenShotError, PermissionError) as e:
            logger.warning("mss capture failed, attempting dxcam fallback: %s", e)
            img = None
            try:
                import dxcam
                # Use primary monitor for fallback
                camera = dxcam.create(output_color="RGB")
                frame = camera.grab()
                if frame is not None:
                    img = Image.fromarray(frame)
                    if region is not None:
                        img = img.crop((region[0], region[1], region[2], region[3]))
                del camera
            except Exception as fallback_e:
                logger.warning("dxcam fallback also failed: %s", fallback_e, exc_info=True)

            if img is None:
                raise RuntimeError(
                    "Screen capture failed: both mss and the dxcam fallback were unable to grab a frame "
                    "(likely an exclusive-fullscreen app or a capture permission issue)."
                ) from e

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


def ocr_marks(png_bytes: bytes, min_conf: int = _MIN_CONF_DEFAULT) -> List[Element]:
    """Run Tesseract over PNG bytes; return Element records (control_type='ocr_text')."""
    if not OCR_AVAILABLE:
        return []
    if config.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = config.tesseract_cmd
    try:
        img = Image.open(io.BytesIO(png_bytes))
        data = pytesseract.image_to_data(img, output_type=Output.DICT)
    except Exception:
        logger.warning("OCR pass failed", exc_info=True)
        return []

    elements: List[Element] = []
    for i in range(len(data.get("text", []))):
        text = (data["text"][i] or "").strip()
        try:
            conf = int(float(data["conf"][i]))
        except (TypeError, ValueError):
            conf = -1
        if not text or conf < min_conf:
            continue
        left, top, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        elements.append(
            Element(
                mark_id=len(elements) + 1,
                name=text,
                control_type="ocr_text",
                bounds=(left, top, left + w, top + h),
                is_password=False,
                is_offscreen=False,
            )
        )
    return elements
