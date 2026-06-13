import logging
import os
import re

import numpy as np

logger = logging.getLogger("charlie.privacy")

# Singleton instance
_redactor_instance = None


def get_redactor() -> "PrivacyRedactor":
    """Return the singleton PrivacyRedactor instance."""
    global _redactor_instance
    if _redactor_instance is None:
        _redactor_instance = PrivacyRedactor()
    return _redactor_instance


class PrivacyRedactor:
    def __init__(self):
        # List of (compiled_pattern, replacement) tuples
        self.patterns = [
            # API Keys, Tokens, Passwords (min 12 chars)
            (
                re.compile(
                    r"(api[_-]?key|token|secret|password|passwd|auth)[^a-z0-9]{1,10}[a-z0-9_-]{12,128}", re.IGNORECASE
                ),
                "[REDACTED_CREDENTIAL]",
            ),
            # AWS Access Keys
            (re.compile(r"AKIA[0-9A-Z]{16}", re.IGNORECASE), "[AWS_ACCESS_KEY]"),
            # URLs with credentials
            (re.compile(r"https?://[a-zA-Z0-9_-]+:[a-zA-Z0-9_-]+@", re.IGNORECASE), "https://[USER:PASS]@"),
            # Email addresses
            (re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", re.IGNORECASE), "[EMAIL_ADDRESS]"),
            # Generic secret assignment
            (re.compile(r'([a-z0-9_-]+_secret)\s*[:=]\s*["\'][^"\']+["\']', re.IGNORECASE), "\\1: [REDACTED]"),
        ]
        self._ocr_reader = None

    def _get_ocr_reader(self):
        """Lazy-initialize EasyOCR reader (heavy import)."""
        if self._ocr_reader is None:
            import easyocr

            self._ocr_reader = easyocr.Reader(["en"], gpu=True)
        return self._ocr_reader

    def redact(self, text: str) -> str:
        if not text:
            return ""

        redacted = str(text)
        for pattern, replacement in self.patterns:
            try:
                redacted = pattern.sub(replacement, redacted)
            except (re.error, TypeError) as e:
                logger.error(f"redaction_pattern_error | pattern={pattern.pattern} | {e}")
            except Exception as e:
                logger.error(f"redaction_unexpected_error | {e}", exc_info=True)

        return redacted

    def redact_image(self, image_path: str, output_path: str | None = None) -> str:
        """
        Redact PII from an image using OCR + pattern matching.

        On ANY failure, returns a path to a safe blank placeholder —
        NEVER the original unredacted image.

        Args:
            image_path: Path to the input image.
            output_path: Where to save the redacted image. Defaults to
                         same directory with _redacted suffix.

        Returns:
            Path to the redacted (or safe placeholder) image.
        """
        if output_path is None:
            base, ext = os.path.splitext(image_path)
            output_path = f"{base}_redacted{ext}"

        try:
            import cv2

            # Load image
            img = cv2.imread(image_path)
            if img is None:
                logger.error(f"redact_image_load_failed | path={image_path}")
                return self._write_safe_placeholder(output_path, 400, 600)

            h, w = img.shape[:2]

            # OCR to detect text regions
            reader = self._get_ocr_reader()
            results = reader.readtext(image_path)

            redacted_any = False
            for bbox, text, confidence in results:
                # Check if detected text contains PII
                if self._contains_pii(text):
                    # Blur the bounding box region
                    pts = np.array(bbox, dtype=np.int32)
                    x_min = max(0, int(pts[:, 0].min()))
                    x_max = min(w, int(pts[:, 0].max()))
                    y_min = max(0, int(pts[:, 1].min()))
                    y_max = min(h, int(pts[:, 1].max()))

                    if x_max > x_min and y_max > y_min:
                        roi = img[y_min:y_max, x_min:x_max]
                        # Gaussian blur with large kernel
                        blurred = cv2.GaussianBlur(roi, (99, 99), 30)
                        img[y_min:y_max, x_min:x_max] = blurred
                        redacted_any = True
                        logger.debug(
                            f"redacted_region | text='{text[:20]}...' | bbox=({x_min},{y_min},{x_max},{y_max})"
                        )

            if not redacted_any:
                logger.info(f"redact_image_no_pii | path={image_path}")

            cv2.imwrite(output_path, img)
            logger.info(f"redact_image_success | in={image_path} | out={output_path}")
            return output_path

        except Exception as e:
            logger.error(f"redact_image_failed | path={image_path} | {e}", exc_info=True)
            return self._write_safe_placeholder(output_path, 400, 600)

    def _contains_pii(self, text: str) -> bool:
        """Check if detected OCR text matches any PII pattern."""
        for pattern, _ in self.patterns:
            if pattern.search(text):
                return True
        return False

    def _write_safe_placeholder(self, output_path: str, h: int, w: int) -> str:
        """Write a black placeholder image with a redaction notice."""
        try:
            import cv2

            placeholder = np.zeros((h, w, 3), dtype=np.uint8)
            # Dark gray background
            placeholder[:] = (40, 40, 40)
            # Add notice text
            font = cv2.FONT_HERSHEY_SIMPLEX
            cv2.putText(placeholder, "[REDACTED - PII Protection]", (w // 6, h // 2), font, 0.7, (200, 200, 200), 2)
            cv2.imwrite(output_path, placeholder)
            logger.warning(f"safe_placeholder_written | path={output_path}")
            return output_path
        except Exception as e:
            logger.error(f"placeholder_write_failed | {e}")
            # Last resort: return the output path even if we couldn't write it
            return output_path
