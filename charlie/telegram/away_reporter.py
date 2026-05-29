"""Away reporter for Telegram — activity digests, catch-up, exception alerts, scheduled reports."""

import json
import logging
import os
import time

logger = logging.getLogger("charlie.telegram.away")

DIGEST_THRESHOLD_SECONDS = 1800  # 30 minutes


class AwayReporter:
    """Tracks activity and generates digests for Telegram delivery."""

    def __init__(self, telegram_q):
        self.telegram_q = telegram_q
        self.last_interaction = time.time()
        self.activity_log = []  # list of {timestamp, type, summary}
        self.alerts = []  # list of {timestamp, type, message}

    def record_interaction(self):
        """Call when user interacts via Telegram."""
        self.last_interaction = time.time()

    def record_activity(self, activity_type: str, summary: str):
        """Record a brain/tool/task activity for digest."""
        self.activity_log.append({
            "timestamp": time.time(),
            "type": activity_type,
            "summary": summary,
        })
        # Keep last 200 entries
        if len(self.activity_log) > 200:
            self.activity_log = self.activity_log[-200:]

    def record_alert(self, alert_type: str, message: str):
        """Record an exception/alert for immediate or digest delivery."""
        self.alerts.append({
            "timestamp": time.time(),
            "type": alert_type,
            "message": message,
        })

    def seconds_since_last_interaction(self) -> float:
        return time.time() - self.last_interaction

    def should_send_digest(self) -> bool:
        """Check if enough time has passed to warrant a digest."""
        return self.seconds_since_last_interaction() > DIGEST_THRESHOLD_SECONDS

    def generate_digest(self, clear: bool = True) -> str | None:
        """Generate a structured digest of recent activity. Returns markdown or None if nothing noteworthy."""
        # Filter activities since last interaction
        recent = [a for a in self.activity_log if a["timestamp"] > self.last_interaction]
        recent_alerts = [a for a in self.alerts if a["timestamp"] > self.last_interaction]

        if not recent and not recent_alerts:
            return None

        sections = []

        # Activity summary
        if recent:
            tasks = [a for a in recent if a["type"] == "task"]
            tools = [a for a in recent if a["type"] == "tool"]
            errors = [a for a in recent if a["type"] == "error"]
            other = [a for a in recent if a["type"] not in ("task", "tool", "error")]

            sections.append(f"📊 *Activity While Away* ({len(recent)} events)")

            if tasks:
                sections.append(f"\n✅ *Tasks ({len(tasks)}):*")
                for t in tasks[-5:]:  # Last 5
                    sections.append(f"  • {t['summary']}")
                if len(tasks) > 5:
                    sections.append(f"  ... and {len(tasks) - 5} more")

            if tools:
                sections.append(f"\n🔧 *Tools ({len(tools)}):*")
                # Group by tool name
                tool_counts = {}
                for t in tools:
                    name = t["summary"].split(":")[0] if ":" in t["summary"] else t["summary"]
                    tool_counts[name] = tool_counts.get(name, 0) + 1
                for name, count in sorted(tool_counts.items(), key=lambda x: -x[1])[:5]:
                    sections.append(f"  • {name} (×{count})")

            if errors:
                sections.append(f"\n❌ *Errors ({len(errors)}):*")
                for e in errors[-3:]:
                    sections.append(f"  • {e['summary']}")

            if other:
                sections.append(f"\n📌 *Other ({len(other)}):*")
                for o in other[-3:]:
                    sections.append(f"  • {o['summary']}")

        # Alerts
        if recent_alerts:
            sections.append(f"\n🚨 *Alerts ({len(recent_alerts)}):*")
            for a in recent_alerts[-5:]:
                sections.append(f"  • [{a['type']}] {a['message']}")

        # Idle duration
        idle_min = int(self.seconds_since_last_interaction() / 60)
        if idle_min > 60:
            sections.append(f"\n⏱ You've been away for {idle_min // 60}h {idle_min % 60}m")
        else:
            sections.append(f"\n⏱ You've been away for {idle_min}m")

        if clear:
            self.activity_log = []
            self.alerts = []

        return "\n".join(sections)

    def generate_startup_catchup(self) -> str | None:
        """Generate a catch-up digest on CHARLIE startup. Returns markdown or None."""
        try:
            state_path = os.path.join(os.getcwd(), "scratch", "telegram_away_state.json")
            if not os.path.exists(state_path):
                return None

            with open(state_path, "r") as f:
                state = json.load(f)

            last_shutdown = state.get("last_shutdown", 0)
            if not last_shutdown:
                return None

            gap_min = int((time.time() - last_shutdown) / 60)
            if gap_min < 5:
                return None  # Too short to matter

            events = state.get("events", [])
            if not events:
                return f"🔄 *CHARLIE back online* — offline for {gap_min}m. No activity to report."

            sections = [f"🔄 *CHARLIE back online* — offline for {gap_min // 60}h {gap_min % 60}m"]
            sections.append("\n📋 *Last session summary:*")

            tasks = [e for e in events if e.get("type") == "task"]
            errors = [e for e in events if e.get("type") == "error"]

            if tasks:
                sections.append(f"\n✅ Completed {len(tasks)} task(s)")
                for t in tasks[-3:]:
                    sections.append(f"  • {t.get('summary', 'unknown')}")

            if errors:
                sections.append(f"\n❌ {len(errors)} error(s) occurred")
                for e in errors[-3:]:
                    sections.append(f"  • {e.get('summary', 'unknown')}")

            return "\n".join(sections)

        except Exception as e:
            logger.debug(f"startup_catchup_err | {e}")
            return None

    def save_state(self):
        """Persist state for startup catch-up."""
        try:
            os.makedirs("scratch", exist_ok=True)
            state_path = os.path.join(os.getcwd(), "scratch", "telegram_away_state.json")
            state = {
                "last_shutdown": time.time(),
                "events": self.activity_log[-50:],  # Save last 50
            }
            with open(state_path, "w") as f:
                json.dump(state, f)
        except Exception as e:
            logger.debug(f"save_away_state_err | {e}")

    def send_alert(self, message: str):
        """Send an immediate alert to Telegram."""
        try:
            self.telegram_q.put({
                "type": "CHAT_MSG",
                "content": f"🚨 *ALERT*\n\n{message}",
                "speaker": "CHARLIE",
            })
        except Exception as e:
            logger.debug(f"send_alert_err | {e}")
