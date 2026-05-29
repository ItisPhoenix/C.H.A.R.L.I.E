import datetime
import logging
import threading
import time
from typing import Dict, List

from charlie.integrations.google_calendar import GoogleCalendarIntegration

logger = logging.getLogger("charlie.intelligence.calendar")

_CACHE_TTL = 300

class CalendarIntel:
    """
    Ambient schedule intelligence provider.

    Manages proactive alerts, schedule summarization, and caching
    of calendar provider data with thread-safe access.
    """
    def __init__(self):
        self.provider = GoogleCalendarIntegration()
        self.last_summary = []
        self.last_fetch_time = 0.0
        self.alerted_event_ids = set()
        self._lock = threading.Lock()

    def _parse_iso(self, iso_str: str) -> datetime.datetime:
        """Robust ISO date parsing with UTC standardization."""
        s = iso_str.replace("Z", "+00:00")
        if "T" not in s: s += "T00:00:00+00:00"
        return datetime.datetime.fromisoformat(s)

    def get_schedule(self, force_refresh: bool = False) -> List[Dict]:
        """
        Fetches events from the configured provider with caching.

        Args:
            force_refresh: Bypass TTL and fetch fresh data.

        Returns:
            List of event dictionaries.
        """
        with self._lock:
            now = time.time()
            if not force_refresh and now - self.last_fetch_time < _CACHE_TTL:
                return self.last_summary

            logger.info("calendar_intel | refreshing_schedule")
            all_events = self.provider.fetch()

            # Sort by start time
            all_events.sort(key=lambda x: x["start"])
            self.last_summary = all_events
            self.last_fetch_time = now
            return all_events

    def check_for_upcoming_alerts(self) -> List[Dict]:
        """Checks for meetings starting within the next 30 minutes."""
        with self._lock:
            schedule = list(self.last_summary)

        now = datetime.datetime.now(datetime.timezone.utc)
        alerts = []

        for event in schedule:
            try:
                start_dt = self._parse_iso(event["start"])
                diff = start_dt - now
                if datetime.timedelta(0) < diff < datetime.timedelta(minutes=30):
                    event_id = event.get("id", event["summary"])
                    if event_id not in self.alerted_event_ids:
                        alerts.append(event)
                        self.alerted_event_ids.add(event_id)
            except Exception as e:
                logger.debug(f"calendar_intel | alert_check_fail | {e}")

        return alerts

    def get_morning_briefing(self) -> str:
        """Returns a string summary of today's schedule."""
        schedule = self.get_schedule(force_refresh=True)
        today = datetime.date.today()

        today_events = []
        for e in schedule:
            try:
                start_dt = self._parse_iso(e["start"])
                event_date = start_dt.date()
                if event_date == today:
                    today_events.append(e)
            except Exception: continue

        if not today_events:
            return "You have a clear schedule today, Sir."

        summary = f"Sir, you have {len(today_events)} events scheduled for today:\n"
        for e in today_events:
            try:
                start_dt = self._parse_iso(e["start"])
                time_str = start_dt.strftime("%H:%M")
                summary += f"- {e['summary']} at {time_str}\n"
            except Exception:
                summary += f"- {e['summary']}\n"

        return summary
