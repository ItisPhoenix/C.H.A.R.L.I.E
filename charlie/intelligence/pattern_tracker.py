import json
import logging
import os
import time
from datetime import datetime
from typing import List, Optional

logger = logging.getLogger("charlie.intelligence.patterns")


class PatternTracker:
    """
    PatternTracker: Predictive Context Engine.
    Logs user activity and identifies recurring patterns to anticipate context needs.
    """

    def __init__(self, log_path: str = "logs/patterns.jsonl"):
        self.log_path = log_path
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)

    @staticmethod
    def _tail_lines(filepath: str, n: int) -> list[str]:
        """Read the last n lines of a file efficiently without loading the entire file."""
        try:
            with open(filepath, "rb") as f:
                f.seek(0, 2)  # Seek to end
                fsize = f.tell()
                # Read chunks from end until we have enough newlines
                block_size = min(8192, fsize)
                data = b""
                pos = fsize
                while pos > 0 and data.count(b"\n") <= n:
                    read_size = min(block_size, pos)
                    pos -= read_size
                    f.seek(pos)
                    data = f.read(read_size) + data
                lines = data.decode("utf-8", errors="replace").splitlines()
                return lines[-n:]
        except Exception:
            return []

    def log_event(self, app: str, file: Optional[str], task: str):
        """
        Logs a snapshot of current activity for pattern analysis.
        """
        event = {
            "timestamp": time.time(),
            "iso": datetime.now().isoformat(),
            "app": app,
            "file": file,
            "task": task,
            "hour": datetime.now().hour,
            "weekday": datetime.now().weekday(),
        }
        try:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(event) + "\n")
        except Exception as e:
            logger.error("pattern_log_failed | %s", e)

    def predict_next_context(self) -> Optional[str]:
        """
        Analyzes logs to predict what the user might do next.
        Returns a string prediction or None.
        """
        if not os.path.exists(self.log_path):
            return None

        now = datetime.now()
        current_hour = now.hour

        try:
            lines = self._tail_lines(self.log_path, 100)
            events = [json.loads(line) for line in lines if line.strip()]

            if not events:
                return None

            # Pattern 1: Morning Routine
            if 7 <= current_hour <= 10:
                morning_apps = [e["app"] for e in events if 7 <= e["hour"] <= 10]
                if morning_apps:
                    top_app = max(set(morning_apps), key=morning_apps.count)
                    return f"morning_routine_{top_app.lower()}"

            # Pattern 2: Sequential Activity (X typically follows Y)
            # (Simple version: if last app was X, what is usually next?)
            last_app = events[-1]["app"]
            next_apps = []
            for i in range(len(events) - 1):
                if events[i]["app"] == last_app:
                    next_apps.append(events[i + 1]["app"])

            if next_apps:
                top_next = max(set(next_apps), key=next_apps.count)
                if next_apps.count(top_next) > 2:  # Significant pattern
                    return f"sequence_{top_next.lower()}"

        except Exception as e:
            logger.debug("prediction_failed | %s", e)

        return None

    def get_repeated_patterns(self, min_count: int = 3) -> List[dict]:
        """Find actions that have been repeated at least min_count times."""
        if not os.path.exists(self.log_path):
            return []

        try:
            lines = self._tail_lines(self.log_path, 200)
            events = [json.loads(line) for line in lines if line.strip()]

            if not events:
                return []

            # Count app+task combinations
            combos = {}
            for e in events:
                key = f"{e.get('app', 'unknown')}|{e.get('task', 'unknown')}"
                if key not in combos:
                    combos[key] = {"app": e["app"], "task": e["task"], "count": 0}
                combos[key]["count"] += 1

            return [
                {"app": v["app"], "task": v["task"], "count": v["count"]}
                for v in combos.values()
                if v["count"] >= min_count
            ]
        except Exception as e:
            logger.debug("get_repeated_patterns_failed | %s", e)
            return []

    def get_proactive_suggestion(self) -> Optional[str]:
        """
        Returns a human-friendly suggestion based on prediction.
        """
        prediction = self.predict_next_context()
        if not prediction:
            return None

        if prediction.startswith("morning_routine_"):
            app = prediction.replace("morning_routine_", "").title()
            return f"You typically start working with {app} around this time. Shall I prepare the context?"

        if prediction.startswith("sequence_"):
            app = prediction.replace("sequence_", "").title()
            return f"I noticed you usually open {app} after this. Should I pre-load those project files?"

        return None
