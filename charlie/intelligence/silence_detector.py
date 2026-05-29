"""Intelligent silence detector — calendar-based and pattern-based busy detection."""

import json
import logging
import os
import time
from datetime import datetime

logger = logging.getLogger("charlie.intelligence.silence")


class SilenceDetector:
    """Detects when the user is likely busy/silent based on calendar and patterns."""

    def __init__(self):
        self.quiet_hours_start = 23  # 11 PM
        self.quiet_hours_end = 7     # 7 AM
        self.interaction_history = []  # list of timestamps
        self._load_settings()

    def _load_settings(self):
        """Load silence settings from charlie_config.json."""
        try:
            config_path = os.path.join(os.getcwd(), "charlie_config.json")
            if os.path.exists(config_path):
                with open(config_path, "r") as f:
                    cfg = json.load(f)
                tg = cfg.get("telegram", {})
                self.quiet_hours_start = tg.get("quiet_hours_start", 23)
                self.quiet_hours_end = tg.get("quiet_hours_end", 7)
        except Exception:
            pass

    def record_interaction(self):
        """Record a user interaction timestamp."""
        self.interaction_history.append(time.time())
        # Keep last 500
        if len(self.interaction_history) > 500:
            self.interaction_history = self.interaction_history[-500:]

    def is_quiet_hours(self) -> bool:
        """Check if current time is within quiet hours."""
        hour = datetime.now().hour
        if self.quiet_hours_start > self.quiet_hours_end:
            # Wraps midnight (e.g., 23-7)
            return hour >= self.quiet_hours_start or hour < self.quiet_hours_end
        else:
            return self.quiet_hours_start <= hour < self.quiet_hours_end

    def is_calendar_busy(self) -> bool:
        """Check if user has a calendar event right now."""
        try:
            from charlie.integrations.google_calendar import GoogleCalendarIntegration
            cal = GoogleCalendarIntegration()
            if hasattr(cal, 'get_current_events'):
                events = cal.get_current_events()
                return bool(events)
        except Exception:
            pass
        return False

    def is_pattern_silent(self) -> bool:
        """Analyze interaction patterns to predict if user is likely busy."""
        if len(self.interaction_history) < 10:
            return False

        now = time.time()
        hour = datetime.now().hour

        # Check if user typically interacts at this hour
        recent_same_hour = [
            t for t in self.interaction_history
            if datetime.fromtimestamp(t).hour == hour and (now - t) < 7 * 86400
        ]

        # If user rarely interacts at this hour (< 2 times in past week), likely busy
        if len(recent_same_hour) < 2:
            return True

        return False

    def should_be_silent(self) -> bool:
        """Main check: should CHARLIE be silent right now?"""
        return self.is_quiet_hours() or self.is_calendar_busy() or self.is_pattern_silent()

    def get_silence_reason(self) -> str | None:
        """Return the reason for silence, or None if not silent."""
        if self.is_quiet_hours():
            return f"quiet_hours ({self.quiet_hours_start}:00-{self.quiet_hours_end}:00)"
        if self.is_calendar_busy():
            return "calendar_event"
        if self.is_pattern_silent():
            return "pattern_silent"
        return None
