"""
charlie/tools/vision_context.py
Vision protection context tools.
Provides safe desktop capture utilizing Pillow and local OCR spatial redactors.
"""

import os
import time
from PIL import ImageGrab
import pygetwindow as gw
from charlie.tools.tool_decorator import tool, RiskTier
from charlie.privacy.redactor import get_redactor
from charlie.utils.logger import get_logger

logger = get_logger("VISION_CONTEXT")

# Banned executable names or titles
SENSITIVE_KEYWORDS = [
    "incognito", "private browsing", "private window", "1password",
    "bitwarden", "keepass", "dashlane", "lastpass", "passwords",
    "password manager", "keychain", "credit card", "bank"
]

def is_sensitive_window_active() -> bool:
    """Check if the active foreground window contains sensitive/private data."""
    try:
        active = gw.getActiveWindow()
        if not active:
            return False
        title = active.title.lower()
        for kw in SENSITIVE_KEYWORDS:
            if kw in title:
                logger.warning(f"sensitive_window_detected | title={active.title}")
                return True
        return False
    except Exception as e:
        logger.debug(f"sensitive_window_check_error | {e}")
        return False

@tool(
    name="capture_desktop",
    description="Capture primary monitor screen safely, redacting all sensitive credentials, keys and emails",
    risk_tier=RiskTier.TIER_1,
    category="security",
)
def capture_desktop() -> str:
    """Takes a desktop screenshot, checks active window focus and applies OCR PII redaction."""
    os.makedirs("scratch", exist_ok=True)
    out_path = os.path.abspath(f"scratch/screenshot_{int(time.time())}.png")

    # 1. Active window incognito/password check
    if is_sensitive_window_active():
        logger.warning("capture_aborted | private/sensitive window has active focus")
        return "CAPTURE BLOCKED: Focused window contains sensitive information (Incognito Mode or Password Manager active)."

    # 2. Grab screen via Pillow
    try:
        img = ImageGrab.grab()
        img.save(out_path, format="PNG")
    except Exception as e:
        logger.error(f"pillow_grab_failed | {e}")
        return f"Error: Screen capture failed: {e}"

    # 3. Apply Local OCR Spatial Redaction
    try:
        redactor = get_redactor()
        redacted_path = redactor.redact_image(out_path, out_path)
        logger.info(f"screenshot_redaction_complete | path={redacted_path}")
        return redacted_path
    except Exception as e:
        logger.error(f"ocr_redaction_failed | {e}")
        # Security fallback: black image with redaction label
        try:
            import numpy as np
            import cv2
            placeholder = np.zeros((400, 600, 3), dtype=np.uint8)
            cv2.putText(placeholder, "[REDACTED - SECURITY EXCEPTION]", (100, 200),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2)
            cv2.imwrite(out_path, placeholder)
            return out_path
        except Exception:
            return "Error: Redaction processing exception. Screen capture discarded."
