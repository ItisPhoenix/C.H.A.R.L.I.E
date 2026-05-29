import logging
import os
import sys
from pathlib import Path
from typing import Optional

import requests

# Setup logging for the validator
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("startup_validator")

class StartupValidator:
    def __init__(self):
        self.root_dir = Path(__file__).parent.parent.parent
        self.health_report = {}

    def check_queues(self, queues: dict) -> bool:
        """Verify that essential multiprocessing queues are responsive."""
        logger.info("Validating IPC queue integrity...")
        results = {}
        for name, q in queues.items():
            try:
                # Check if queue is closed
                if hasattr(q, '_closed') and q._closed:
                    results[name] = False
                else:
                    results[name] = True
            except Exception as e:
                logger.error(f"Queue check failed for {name}: {e}")
                results[name] = False

        self.health_report["queues"] = results
        return all(results.values())

    def check_llm_server(self) -> bool:
        """Verify that the local LLM server (LM Studio/Ollama) is reachable."""
        logger.info("Verifying LLM server availability...")
        # Try common ports for local LLM servers
        ports = [1234, 11434]
        reachable = False
        for port in ports:
            try:
                response = requests.get(f"http://localhost:{port}/v1/models", timeout=2)
                if response.status_code == 200:
                    reachable = True
                    logger.info(f"LLM server detected on port {port}")
                    break
            except Exception:
                continue

        self.health_report["llm_server"] = reachable
        return reachable

    def check_hardware(self) -> bool:
        """Check availability of optical and audio sensors."""
        logger.info("Scanning hardware sensors...")
        hardware = {"camera": False, "audio": False}

        # Check Camera (OpenCV)
        try:
            import cv2
            cap = cv2.VideoCapture(0)
            if cap.isOpened():
                hardware["camera"] = True
                cap.release()
        except Exception:
            pass

        # Check Audio (sounddevice)
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            if len(devices) > 0:
                hardware["audio"] = True
        except Exception:
            pass

        self.health_report["hardware"] = hardware
        return any(hardware.values())

    def check_env_vars(self) -> bool:
        """Verify that required environment variables are set."""
        logger.info("Validating environment variables...")

        # Required for core LLM functionality (no defaults in config.py)
        required = {
            "NIM_PRIMARY_MODEL": "Primary LLM model identifier",
            "VISION_MODEL": "Vision model identifier",
            "EMBEDDING_MODEL": "Embedding model identifier",
        }

        # Optional but recommended for Telegram integration
        optional_telegram = {
            "TELEGRAM_TOKEN": "Telegram bot token",
            "TELEGRAM_CHAT_ID": "Telegram chat ID",
        }

        results = {}
        missing_required = []

        for var, desc in required.items():
            val = os.getenv(var)
            if val:
                results[var] = True
            else:
                results[var] = False
                missing_required.append(f"{var} ({desc})")

        for var, desc in optional_telegram.items():
            val = os.getenv(var)
            results[var] = val is not None

        self.health_report["env_vars"] = results

        if missing_required:
            logger.error(f"Missing required env vars: {', '.join(missing_required)}")
            return False

        return True

    def validate_all(self, queues: Optional[dict] = None) -> bool:
        """Run all validation checks and return overall status."""
        logger.info("--- CHARLIE STARTUP VALIDATION ---")

        checks = [
            self.check_env_vars(),
            self.check_llm_server(),
            self.check_hardware()
        ]

        if queues:
            checks.append(self.check_queues(queues))

        logger.info("Validation complete.")
        for category, status in self.health_report.items():
            logger.info(f"{category.upper()}: {'PASSED' if (isinstance(status, bool) and status) or (isinstance(status, dict) and all(status.values())) else 'FAILED/PARTIAL'}")
            if isinstance(status, dict):
                for sub, val in status.items():
                    logger.info(f"  - {sub}: {'OK' if val else 'FAIL'}")

        return all(checks)

if __name__ == "__main__":
    validator = StartupValidator()
    if validator.validate_all():
        logger.info("System integrity verified. Ready for neural link ignition.")
        sys.exit(0)
    else:
        logger.warning("System health issues detected. Proceeding with caution.")
        sys.exit(1)
