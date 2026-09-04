"""Vision-LLM element grounding for surfaces UIA + OCR can't see into.

Reuses the existing VISION_LLM_URL/KEY/MODEL endpoint (charlie/core.py's
vision tier) that desktop_screenshot already sends SoM images to -- this is
the same model answering a different question ("find clickable regions"
instead of "which numbered mark do I click"), so no new dependency, no new
config, and switching to a cloud vision endpoint later is just an .env edit.

This is a fallback, not a third always-on perception source: it only runs
when UIA+OCR come back too sparse to be useful (see tools.py:_grounding_marks).
"""

import io
import json
import logging
import re
from typing import Any, List, Tuple

import httpx

from charlie import resource_locks
from charlie.desktop.uia import Element
from charlie.desktop.vision import to_data_url
from charlie.utils import build_auth_headers, make_id

logger = logging.getLogger("charlie.desktop.grounding")

_PROMPT = (
    "Locate every clickable UI element in this screenshot (buttons, icons, "
    "links, inputs, tabs, menu items). Respond with ONLY a JSON array, no "
    "other text: "
    '[{"bbox_2d": [x1, y1, x2, y2], "label": "short description"}, ...]. '
    "Coordinates are on a 0-1000 scale relative to image width/height."
)

_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)
_TIMEOUT_SEC = 30.0


def detect(png_bytes: bytes, config: Any, *, owner_id: str | None = None) -> List[Element]:
    """Ask the configured vision LLM to find clickable regions in `png_bytes`.

    Returns provisional Elements (mark_id starting at 0) -- merge_ocr_elements
    (charlie/desktop/uia.py:151) renumbers them into the real mark sequence.
    Returns [] on any failure or when vision isn't configured; this is a
    best-effort fallback, never the only perception source.
    """
    if not (config.vision_enabled and config.vision_llm_url):
        return []
    payload = {
        "model": config.vision_llm_model,
        "stream": False,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": _PROMPT},
                {"type": "image_url", "image_url": {"url": to_data_url(png_bytes)}},
            ],
        }],
    }
    grounding_owner = owner_id or f"grounding:{make_id(8)}"
    if not resource_locks.acquire("vision_gpu", grounding_owner):
        logger.info(
            "Grounding vision skipped: vision_gpu is held by %s",
            resource_locks.current_owner("vision_gpu"),
        )
        return []
    try:
        try:
            response = httpx.post(
                f"{config.vision_llm_url.rstrip('/')}/chat/completions",
                json=payload,
                headers=build_auth_headers(config.vision_llm_key),
                timeout=_TIMEOUT_SEC,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        except Exception:
            logger.warning("Grounding vision call failed", exc_info=True)
            return []
        width, height = _image_size(png_bytes)
        return _parse_elements(content, width, height)
    finally:
        resource_locks.release("vision_gpu", grounding_owner)


def _clamp01000(value: float) -> float:
    return max(0, min(1000, value))


def _image_size(png_bytes: bytes) -> Tuple[int, int]:
    from PIL import Image
    with Image.open(io.BytesIO(png_bytes)) as img:
        return img.size


def _parse_elements(content: str, width: int, height: int) -> List[Element]:
    """Parse a (possibly prose-wrapped) JSON bbox array into provisional Elements."""
    match = _JSON_ARRAY_RE.search(content)
    if not match:
        logger.warning("Grounding response had no JSON array: %r", content[:200])
        return []
    try:
        boxes = json.loads(match.group(0))
    except json.JSONDecodeError:
        logger.warning("Grounding response JSON invalid: %r", content[:200])
        return []
    elements = []
    for i, box in enumerate(boxes):
        if not isinstance(box, dict):
            continue
        bbox = box.get("bbox_2d")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        x1, y1, x2, y2 = bbox
        # A VLM can hand back swapped (x1>x2) or out-of-range (outside the
        # documented 0-1000 scale) coordinates -- normalize both, same
        # defense charlie/desktop/vision.py applies to UIA's own bounds.
        x1, x2 = sorted((_clamp01000(x1), _clamp01000(x2)))
        y1, y2 = sorted((_clamp01000(y1), _clamp01000(y2)))
        elements.append(Element(
            mark_id=i,
            name=str(box.get("label", "element"))[:80],
            control_type="GroundedElement",
            bounds=(
                round(x1 / 1000 * width),
                round(y1 / 1000 * height),
                round(x2 / 1000 * width),
                round(y2 / 1000 * height),
            ),
            is_password=False,
            is_offscreen=False,
        ))
    return elements
