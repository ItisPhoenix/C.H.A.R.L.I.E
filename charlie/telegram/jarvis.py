"""JARVIS-style features for Telegram — morning briefing, email intel, finance, tracking."""

import json
import logging
import os
import time
from datetime import datetime

logger = logging.getLogger("charlie.telegram.jarvis")


class JarvisFeatures:
    """JARVIS-style intelligent features delivered via Telegram."""

    def __init__(self, telegram_q, brain_task_q):
        self.telegram_q = telegram_q
        self.brain_task_q = brain_task_q
        self.tracking_items = []  # {id, type, query, last_status, added_at}
        self._load_tracking()

    def _load_tracking(self):
        """Load tracking items from config."""
        try:
            config_path = os.path.join(os.getcwd(), "charlie_config.json")
            if os.path.exists(config_path):
                with open(config_path, "r") as f:
                    cfg = json.load(f)
                self.tracking_items = cfg.get("telegram", {}).get("tracking", [])
        except Exception:
            pass

    def _save_tracking(self):
        """Save tracking items to config with file locking."""
        try:
            import msvcrt
            config_path = os.path.join(os.getcwd(), "charlie_config.json")
            lock_path = config_path + ".lock"
            with open(lock_path, "w") as lock_f:
                msvcrt.locking(lock_f.fileno(), msvcrt.LK_NBLCK, 1)
                try:
                    cfg = {}
                    if os.path.exists(config_path):
                        with open(config_path, "r") as f:
                            cfg = json.load(f)
                    cfg.setdefault("telegram", {})["tracking"] = self.tracking_items
                    # Atomic write
                    tmp_path = config_path + ".tmp"
                    with open(tmp_path, "w") as f:
                        json.dump(cfg, f, indent=2)
                    os.replace(tmp_path, config_path)
                finally:
                    msvcrt.locking(lock_f.fileno(), msvcrt.LK_UNLCK, 1)
        except Exception:
            pass

    def generate_morning_briefing(self) -> str:
        """Generate a comprehensive morning briefing."""
        now = datetime.now()
        sections = [f"☀️ *Good morning! — {now.strftime('%A, %B %d')}*\n"]

        # Calendar events
        try:
            from charlie.intelligence.calendar_intel import CalendarIntel
            cal = CalendarIntel()
            if hasattr(cal, 'get_todays_events'):
                events = cal.get_todays_events()
                if events:
                    sections.append("📅 *Today's calendar:*")
                    for e in events[:5]:
                        sections.append(f"  • {e}")
                else:
                    sections.append("📅 No calendar events today.")
        except Exception:
            sections.append("📅 Calendar unavailable.")

        # Active tasks
        sections.append("\n📋 *Active tasks:*")
        sections.append("  (Querying brain...)")

        # Weather (if available)
        try:
            sections.append("\n🌤 *Weather:*")
            sections.append("  (Weather integration not configured)")
        except Exception:
            pass

        # Mood check-in
        sections.append("\n💭 How are you feeling today?")

        return "\n".join(sections)

    def generate_email_digest(self) -> str:
        """Generate an email digest from Gmail integration."""
        try:
            from charlie.integrations.gmail import GmailIntegration
            gmail = GmailIntegration()
            if hasattr(gmail, 'get_recent_emails'):
                emails = gmail.get_recent_emails(max_results=10)
                if not emails:
                    return "📧 No new emails."

                sections = [f"📧 *Email Digest* ({len(emails)} new)\n"]
                for email in emails[:7]:
                    sender = email.get("from", "unknown")
                    subject = email.get("subject", "no subject")
                    snippet = email.get("snippet", "")[:60]
                    sections.append(f"  • *{sender}*: {subject}")
                    if snippet:
                        sections.append(f"    {snippet}")
                return "\n".join(sections)
        except Exception as e:
            logger.debug(f"email_digest_err | {e}")
        return "📧 Email digest unavailable."

    def generate_finance_update(self) -> str:
        """Generate a financial monitoring update."""
        try:
            config_path = os.path.join(os.getcwd(), "charlie_config.json")
            if os.path.exists(config_path):
                with open(config_path, "r") as f:
                    cfg = json.load(f)
                watchlist = cfg.get("telegram", {}).get("finance_watchlist", [])
                if not watchlist:
                    return "💰 No stocks/crypto in watchlist. Add some in charlie_config.json → telegram.finance_watchlist"

                sections = ["💰 *Financial Update*\n"]
                for item in watchlist:
                    sections.append(f"  • {item}: (market data integration needed)")
                return "\n".join(sections)
        except Exception:
            pass
        return "💰 Financial monitoring unavailable."

    def add_tracking(self, item_type: str, query: str) -> str:
        """Add an item to track (package, flight, delivery)."""
        item_id = f"track_{int(time.time())}"
        self.tracking_items.append({
            "id": item_id,
            "type": item_type,
            "query": query,
            "last_status": "pending",
            "added_at": time.time(),
        })
        self._save_tracking()
        return f"📦 Now tracking: {query} ({item_id})"

    def remove_tracking(self, item_id: str) -> str:
        """Remove a tracking item."""
        self.tracking_items = [t for t in self.tracking_items if t["id"] != item_id]
        self._save_tracking()
        return f"📦 Stopped tracking: {item_id}"

    def list_tracking(self) -> str:
        """List all tracked items."""
        if not self.tracking_items:
            return "📦 No items being tracked."

        sections = [f"📦 *Tracking ({len(self.tracking_items)} items):*\n"]
        for item in self.tracking_items:
            age_days = int((time.time() - item.get("added_at", 0)) / 86400)
            sections.append(f"  • [{item['type']}] {item['query']} — {item.get('last_status', 'unknown')} ({age_days}d ago)")
        return "\n".join(sections)

    def check_tracking_updates(self) -> str | None:
        """Check for tracking updates. Returns update message or None."""
        if not self.tracking_items:
            return None

        updates = []
        for item in self.tracking_items:
            # This would integrate with shipping APIs
            # For now, just report items that have been tracked for a while
            age_days = int((time.time() - item.get("added_at", 0)) / 86400)
            if age_days > 7 and item.get("last_status") == "pending":
                updates.append(f"  • {item['query']} — tracked for {age_days} days, no updates")

        if updates:
            return "📦 *Tracking Updates:*\n" + "\n".join(updates)
        return None
