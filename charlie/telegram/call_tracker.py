"""Call tracker — Tasker integration, caller intelligence, analytics, smart callbacks."""

import logging
import os
import sqlite3
import time
from datetime import datetime

logger = logging.getLogger("charlie.telegram.calls")

# Tasker message format: CALL|type|number|timestamp|duration
TASKER_PREFIX = "CALL|"


class CallTracker:
    """Tracks phone calls via Tasker auto-forward and manual reporting."""

    def __init__(self):
        self.db_path = os.path.join(os.getcwd(), "scratch", "call_tracker.db")
        self._init_db()

    def _init_db(self):
        """Initialize the call tracking database."""
        try:
            os.makedirs("scratch", exist_ok=True)
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    call_type TEXT NOT NULL,
                    number TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    duration INTEGER DEFAULT 0,
                    caller_name TEXT,
                    notes TEXT,
                    source TEXT DEFAULT 'manual'
                )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"call_db_init_err | {e}")

    def parse_tasker_message(self, text: str) -> dict | None:
        """Parse a Tasker-formatted call message. Returns dict or None."""
        if not text.startswith(TASKER_PREFIX):
            return None
        parts = text.split("|")
        if len(parts) < 4:
            return None
        return {
            "call_type": parts[1],  # incoming, outgoing, missed
            "number": parts[2],
            "timestamp": float(parts[3]) if parts[3] else time.time(),
            "duration": int(parts[4]) if len(parts) > 4 and parts[4] else 0,
            "source": "tasker",
        }

    def parse_manual_report(self, text: str) -> dict | None:
        """Parse a manual call report from user text. Returns dict or None."""
        text_lower = text.lower()
        call_type = "unknown"
        if "missed" in text_lower:
            call_type = "missed"
        elif "incoming" in text_lower or "received" in text_lower:
            call_type = "incoming"
        elif "outgoing" in text_lower or "called" in text_lower or "dialed" in text_lower:
            call_type = "outgoing"

        # Extract phone number (simple pattern)
        import re
        number_match = re.search(r'[\+]?[\d\s\-\(\)]{7,}', text)
        number = number_match.group(0).strip() if number_match else "unknown"

        return {
            "call_type": call_type,
            "number": number,
            "timestamp": time.time(),
            "duration": 0,
            "source": "manual",
        }

    def record_call(self, call_data: dict):
        """Record a call to the database."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO calls (call_type, number, timestamp, duration, caller_name, source) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        call_data.get("call_type", "unknown"),
                        call_data.get("number", "unknown"),
                        call_data.get("timestamp", time.time()),
                        call_data.get("duration", 0),
                        call_data.get("caller_name"),
                        call_data.get("source", "manual"),
                    ),
                )
            logger.info(f"call_recorded | {call_data.get('call_type')} from {call_data.get('number')}")
        except Exception as e:
            logger.error(f"call_record_err | {e}")

    def get_caller_intelligence(self, number: str) -> str:
        """Generate intelligence about a caller. Returns formatted string."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM calls WHERE number = ? ORDER BY timestamp DESC LIMIT 10",
                    (number,),
                ).fetchall()

            if not rows:
                return f"📱 Unknown caller: {number}\n  No history on file."

            calls = [dict(r) for r in rows]
            total = len(calls)
            missed = sum(1 for c in calls if c["call_type"] == "missed")
            last_call = calls[0]
            last_time = datetime.fromtimestamp(last_call["timestamp"]).strftime("%B %d at %H:%M")

            lines = [f"📱 *Caller Intelligence: {number}*"]
            lines.append(f"  Total calls: {total}")
            lines.append(f"  Missed: {missed}")
            lines.append(f"  Last contact: {last_time}")

            # Call frequency
            if total >= 3:
                lines.append(f"  ⚠️ Frequent caller ({total} calls)")

            # Recent pattern
            recent_types = [c["call_type"] for c in calls[:5]]
            if all(t == "missed" for t in recent_types):
                lines.append("  🔴 All recent calls were missed — may be urgent")

            return "\n".join(lines)

        except Exception as e:
            logger.error(f"caller_intel_err | {e}")
            return f"📱 Caller: {number} (intelligence unavailable)"

    def get_analytics(self) -> str:
        """Generate call analytics summary."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row

                total = conn.execute("SELECT COUNT(*) as c FROM calls").fetchone()["c"]
                if total == 0:
                    return "📊 No call data recorded yet."

                by_type = conn.execute(
                    "SELECT call_type, COUNT(*) as c FROM calls GROUP BY call_type"
                ).fetchall()

                top_callers = conn.execute(
                    "SELECT number, COUNT(*) as c FROM calls GROUP BY number ORDER BY c DESC LIMIT 5"
                ).fetchall()

                peak_hours = conn.execute(
                    "SELECT CAST(strftime('%H', timestamp, 'unixepoch') AS INTEGER) as hour, COUNT(*) as c "
                    "FROM calls GROUP BY hour ORDER BY c DESC LIMIT 3"
                ).fetchall()

            lines = [f"📊 *Call Analytics* ({total} total calls)\n"]

            lines.append("*By type:*")
            for row in by_type:
                lines.append(f"  • {row['call_type']}: {row['c']}")

            lines.append("\n*Top callers:*")
            for row in top_callers:
                lines.append(f"  • {row['number']}: {row['c']} calls")

            if peak_hours:
                lines.append("\n*Peak hours:*")
                for row in peak_hours:
                    lines.append(f"  • {row['hour']}:00 — {row['c']} calls")

            return "\n".join(lines)

        except Exception as e:
            logger.error(f"call_analytics_err | {e}")
            return "📊 Call analytics unavailable."

    def suggest_callback(self, number: str) -> str | None:
        """Suggest a callback time based on patterns. Returns suggestion or None."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT CAST(strftime('%H', timestamp, 'unixepoch') AS INTEGER) as hour, COUNT(*) as c "
                    "FROM calls WHERE number = ? GROUP BY hour ORDER BY c DESC LIMIT 1",
                    (number,),
                ).fetchall()

            if rows:
                peak_hour = rows[0]["hour"]
                return f"💡 This number usually calls around {peak_hour}:00. Consider calling back then."

        except Exception:
            pass
        return None
