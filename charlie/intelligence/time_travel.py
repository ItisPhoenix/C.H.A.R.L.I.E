"""Time-travel briefing — query memory and activity logs for temporal context."""

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta

logger = logging.getLogger("charlie.intelligence.time_travel")


class TimeTravelEngine:
    """Answers temporal questions like 'what was I doing this time last week?'."""

    def __init__(self):
        self.db_path = os.path.join(os.getcwd(), "scratch", "outcomes.db")

    def query_last_week(self) -> str:
        """What happened this time last week?"""
        return self._query_relative(days_ago=7)

    def query_yesterday(self, hour: int | None = None) -> str:
        """What happened yesterday at this time (or a specific hour)?"""
        return self._query_relative(days_ago=1, hour=hour)

    def query_relative(self, days_ago: int, hour: int | None = None) -> str:
        """Query activity from N days ago."""
        return self._query_relative(days_ago, hour)

    def _query_relative(self, days_ago: int, hour: int | None = None) -> str:
        """Query activity from N days ago at a specific hour (or current hour)."""
        try:
            target_time = datetime.now() - timedelta(days=days_ago)
            if hour is not None:
                target_time = target_time.replace(hour=hour, minute=0, second=0)
            start_ts = int(target_time.timestamp()) - 1800  # 30 min before
            end_ts = int(target_time.timestamp()) + 1800  # 30 min after

            results = []

            # Query outcome tracker
            outcomes = self._query_outcomes(start_ts, end_ts)
            if outcomes:
                results.append(f"📊 *{len(outcomes)} tool executions:*")
                for o in outcomes[:5]:
                    results.append(f"  • {o.get('tool_name', 'unknown')} — {o.get('status', '?')}")

            # Query conversation history
            convos = self._query_conversations(start_ts, end_ts)
            if convos:
                results.append(f"\n💬 *{len(convos)} messages:*")
                for c in convos[:5]:
                    role = c.get("role", "?")
                    content = str(c.get("content", ""))[:80]
                    results.append(f"  • [{role}] {content}")

            if not results:
                date_str = target_time.strftime("%B %d at %H:%M")
                return f"No activity found for {date_str}."

            date_str = target_time.strftime("%B %d at %H:%M")
            return f"📅 *{date_str}*\n\n" + "\n".join(results)

        except Exception as e:
            logger.error(f"time_travel_err | {e}")
            return "Failed to query historical data."

    def _query_outcomes(self, start_ts: int, end_ts: int) -> list:
        """Query outcome tracker for tool executions in a time range."""
        try:
            if not os.path.exists(self.db_path):
                return []
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM outcomes WHERE timestamp BETWEEN ? AND ? ORDER BY timestamp", (start_ts, end_ts)
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def _query_conversations(self, start_ts: int, end_ts: int) -> list:
        """Query conversation history for messages in a time range."""
        try:
            history_path = os.path.join(os.getcwd(), "scratch", "conversation_history.json")
            if not os.path.exists(history_path):
                return []
            with open(history_path, "r") as f:
                messages = json.load(f)
            if not isinstance(messages, list):
                return []
            return [m for m in messages if m.get("timestamp", 0) >= start_ts and m.get("timestamp", 0) <= end_ts]
        except Exception:
            return []

    def search_memory(self, query: str) -> str:
        """Search memory graph for relevant context."""
        try:
            from charlie.intelligence.memory_graph import MemoryGraph

            mg = MemoryGraph()
            results = mg.search(query, limit=5)
            if not results:
                return f"No memory results for '{query}'."
            lines = [f"🧠 *Memory search: '{query}'*\n"]
            for r in results:
                content = str(r)[:200]
                lines.append(f"  • {content}")
            return "\n".join(lines)
        except Exception as e:
            logger.debug(f"memory_search_err | {e}")
            return f"Memory search unavailable: {e}"
