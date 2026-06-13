import os
import time

import requests

from charlie.config import settings
from charlie.security.tiers import RiskTier, risk_tier
from charlie.utils.logger import get_logger

logger = get_logger(__name__)


class Messenger:
    def __init__(self):
        self.bot_token = settings.supervisor.telegram_token
        self.chat_id = settings.supervisor.telegram_chat_id
        # Use absolute path for security checks
        self.reports_path = os.path.abspath(settings.supervisor.reports_path)
        os.makedirs(self.reports_path, exist_ok=True)
        self.last_send_time = 0
        self.rate_limit = 1.5  # Seconds between messages

        # Validate configuration immediately
        if self.bot_token == "REPLACE_ME":
            logger.warning("messenger_init | telegram_token_missing")
        if not self.chat_id:
            logger.warning("messenger_init | chat_id_missing")

    @risk_tier(RiskTier.TIER_0)
    def send_telegram(self, message):
        """Sends a message via Telegram bot with rate limiting."""
        if self.bot_token == "REPLACE_ME" or not self.chat_id:
            logger.warning("telegram_send_aborted | not_configured")
            return "Telegram error: Bot not configured. Set token and chat_id in settings."

        # Simple Rate Limiting
        elapsed = time.time() - self.last_send_time
        if elapsed < self.rate_limit:
            wait_time = self.rate_limit - elapsed
            logger.debug(f"telegram_rate_limit_active | waiting={wait_time:.2f}s")
            time.sleep(wait_time)

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": message, "parse_mode": "Markdown"}
        try:
            r = requests.post(url, json=payload, timeout=10)
            self.last_send_time = time.time()

            if r.status_code == 200:
                logger.info("telegram_sent_successfully")
                return "Message sent to Sir via Telegram."
            elif r.status_code == 429:
                logger.warning("telegram_too_many_requests")
                return "Telegram error: Too many requests (429)."
            else:
                logger.error(f"telegram_failed | code={r.status_code} | resp={r.text}")
                return f"Telegram failed: {r.text}"
        except Exception as e:
            logger.error("telegram_exception", error=str(e))
            return f"Telegram exception: {str(e)}"

    @risk_tier(RiskTier.TIER_0)
    def save_report(self, content, filename=None):
        """Saves content to a local report file with traversal protection."""
        try:
            if not filename:
                filename = f"report_{int(time.time())}.md"

            # Explicit check for traversal symbols before sanitization
            if ".." in filename or "/" in filename or "\\" in filename:
                logger.warning(f"messenger_traversal_blocked | input={filename}")
                return "Error: Path traversal or directory symbols detected in filename."

            # 1. Sanitize filename
            filename = "".join([c for c in filename if c.isalnum() or c in "._-"]).strip()
            if not filename:
                filename = f"report_{int(time.time())}.md"
            if "." not in filename:
                filename += ".md"

            # 2. Prevent Path Traversal
            target_path = os.path.abspath(os.path.join(self.reports_path, filename))
            if not target_path.startswith(self.reports_path):
                logger.error(f"report_traversal_attempt: {filename}")
                return "Error: Path traversal detected."

            with open(target_path, "w", encoding="utf-8") as f:
                f.write(content)

            logger.info(f"report_saved: {filename}")
            return f"Report saved successfully as {filename}."
        except Exception as e:
            logger.error("report_save_failed", error=str(e))
            return f"Failed to save report: {str(e)}"
