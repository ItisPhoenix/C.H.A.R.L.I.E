"""
charlie/tools/screen_understanding.py

Screen understanding tools: vision analysis, element finding, OCR.
"""

import json
import logging

from charlie.tools.tool_decorator import tool, RiskTier

logger = logging.getLogger("charlie.tools.screen")


@tool(
    name="see_screen",
    description="Describe what is currently on the screen using the vision model.",
    category="vision",
    risk_tier=RiskTier.TIER_0,
)
def see_screen(question: str = "What is on the screen?") -> str:
    """Capture the screen and ask the vision model to describe it."""
    from charlie.tools._vision_bridge import get_brain

    brain = get_brain()
    if not brain or not hasattr(brain, "vision_handler"):
        return "Vision handler not available"

    screenshot = brain.vision_handler.capture_screen()
    if screenshot is None:
        return "Failed to capture screen"

    result = brain.vision_handler.ask_vision(screenshot, question)
    return result or "No response from vision model"


@tool(
    name="find_element",
    description="Find a UI element on screen by description and return its coordinates.",
    category="vision",
    risk_tier=RiskTier.TIER_0,
)
def find_element(description: str) -> str:
    """Find a UI element on screen using the vision model."""
    from charlie.tools._vision_bridge import get_brain

    brain = get_brain()
    if not brain or not hasattr(brain, "vision_handler"):
        return "Vision handler not available"

    screenshot = brain.vision_handler.capture_screen()
    if screenshot is None:
        return "Failed to capture screen"

    prompt = (
        f'Find "{description}" in this screenshot. '
        f"Return ONLY valid JSON (no markdown): "
        f'{{"x": <center pixel x>, "y": <center pixel y>, '
        f'"found": true/false, "confidence": 0.0-1.0}}'
    )
    result = brain.vision_handler.ask_vision(screenshot, prompt)
    if not result:
        return "No response from vision model"

    # Parse JSON from response
    content = result.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    try:
        coords = json.loads(content)
        if coords.get("found"):
            return (
                f"Found '{description}' at ({coords['x']}, {coords['y']}) "
                f"with {coords.get('confidence', 0):.0%} confidence"
            )
        return f"Could not find '{description}' on screen"
    except json.JSONDecodeError:
        return f"Vision model response: {result[:200]}"


@tool(
    name="read_screen_text",
    description="Read text from the screen using OCR.",
    category="vision",
    risk_tier=RiskTier.TIER_0,
)
def read_screen_text(x: int = 0, y: int = 0, width: int = 0, height: int = 0) -> str:
    """Extract text from screen using OCR. If region is 0, reads full screen."""
    try:
        import pytesseract
        from PIL import ImageGrab
        import os

        if os.name == "nt":
            tesseract_path = os.getenv("TESSERACT_PATH", r"C:\Program Files\Tesseract-OCR\tesseract.exe")
            if os.path.exists(tesseract_path):
                pytesseract.pytesseract.tesseract_cmd = tesseract_path
    except ImportError:
        return "pytesseract or Pillow not installed"

    if width > 0 and height > 0:
        region = (x, y, x + width, y + height)
        screenshot = ImageGrab.grab(bbox=region)
    else:
        screenshot = ImageGrab.grab()

    text = pytesseract.image_to_string(screenshot).strip()
    if not text:
        return "No text detected on screen"
    return text[:5000]  # Limit output length
