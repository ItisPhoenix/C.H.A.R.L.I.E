"""
charlie/intelligence/suggestion_engine.py

Proactive Suggestion Engine — anticipates user needs before being asked.


The suggestion engine runs as a background task and generates
suggestions based on trigger conditions like:
- Morning briefing (user-defined hour)
- Meeting reminders (15 min before)
- Idle detection ("You were working on X, resume?")
- Pattern detection ("Shall I automate this?")
- Deadline approaching
- Repeated errors
"""

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from charlie.intelligence.pattern_tracker import PatternTracker
from charlie.intelligence.pattern_detector import PatternDetector
from charlie.intelligence.task_state import get_task_state

SCRATCH_DIR = Path("scratch")
SUGGESTIONS_FILE = SCRATCH_DIR / "suggestions.json"

logger = logging.getLogger("charlie.intelligence.suggestion_engine")


@dataclass
class Suggestion:
    """A proactive suggestion to present to the user."""
    id: str
    type: str           # morning_briefing, meeting_reminder, resume_task, automate, deadline, error_recovery
    message: str        # The suggestion text
    actions: List[Dict]  # Available actions e.g. [{"label": "Yes", "action": "do_this"}, {"label": "No", "action": "dismiss"}]
    priority: int        # 1-10, higher = more important
    dismissible: bool = True
    created_at: float = field(default_factory=time.time)
    expires_at: Optional[float] = None

    def is_expired(self) -> bool:
        """Check if suggestion has expired."""
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at


class SuggestionEngine:
    """
    Proactive suggestion engine that monitors conditions and generates suggestions.

    Runs as a background task every N minutes and checks trigger conditions.
    """

    # Default trigger conditions
    TRIGGERS = {
        "morning_briefing": {
            "enabled": True,
            "hour": 8,  # 8 AM
            "interval": 60 * 60,  # Check once per hour
        },
        "meeting_reminder": {
            "enabled": True,
            "minutes_before": 15,
            "interval": 60,  # Check every minute
        },
        "idle_resume": {
            "enabled": True,
            "idle_threshold_minutes": 30,
            "interval": 300,  # Check every 5 minutes
        },
        "pattern_automation": {
            "enabled": True,
            "min_repetitions": 3,
            "interval": 600,  # Check every 10 minutes
        },
        "deadline_alert": {
            "enabled": True,
            "hours_before": 2,
            "interval": 300,  # Check every 5 minutes
        },
        "error_recovery": {
            "enabled": True,
            "error_threshold": 3,
            "interval": 60,  # Check every minute
        },
        "predictive": {
            "enabled": True,
            "interval": 600,  # Check every 10 minutes
        },
        "tool_health": {
            "enabled": True,
            "interval": 300,  # Check every 5 minutes
        },
        "proactive_research": {
            "enabled": True,
            "interval": 3600,  # Check every hour
        },
    }

    def __init__(
        self,
        check_interval: int = 300,  # 5 minutes
        max_suggestions: int = 10,
        delivery_callback: Optional[Callable[[Suggestion], None]] = None,
        persist_path: Optional[Path] = None,
    ):
        """
        Initialize the suggestion engine.

        Args:
            check_interval: How often to check trigger conditions (seconds)
            max_suggestions: Maximum suggestions to keep in queue
            delivery_callback: Optional callback when suggestion is ready to deliver
            persist_path: Path to persist suggestions JSON (default: scratch/suggestions.json)
        """
        self.check_interval = check_interval
        self.max_suggestions = max_suggestions
        self.delivery_callback = delivery_callback
        self._persist_path = persist_path or SUGGESTIONS_FILE

        self._suggestions: List[Suggestion] = []
        self._trigger_timestamps: Dict[str, float] = {}
        self._last_check: float = 0
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Dependencies (can be injected)
        self._pattern_tracker: Optional[PatternTracker] = None
        self._pattern_detector: Optional[PatternDetector] = None
        self._outcome_tracker = None
        self._task_state = get_task_state()

        # User preferences
        self._morning_briefing_hour = 8
        self._morning_briefing_done_today = False

        # Load persisted suggestions (skip if persist_path is None)
        if self._persist_path is not None:
            self._load_suggestions()

    def set_pattern_tracker(self, tracker: PatternTracker):
        """Set the pattern tracker dependency."""
        self._pattern_tracker = tracker

    def set_brain(self, brain):
        """Set the brain reference for calendar access in meeting reminders."""
        self._brain = brain

    def set_pattern_detector(self, detector: PatternDetector):
        """Set the PatternDetector dependency."""
        self._pattern_detector = detector

    def set_outcome_tracker(self, tracker):
        """Set the OutcomeTracker dependency."""
        self._outcome_tracker = tracker

    # --- Persistence ---

    def _save_suggestions(self) -> None:
        """Persist current suggestions to disk."""
        try:
            SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
            data = []
            for s in self._suggestions:
                data.append({
                    "id": s.id,
                    "type": s.type,
                    "message": s.message,
                    "actions": s.actions,
                    "priority": s.priority,
                    "dismissible": s.dismissible,
                    "created_at": s.created_at,
                    "expires_at": s.expires_at,
                })
            with open(self._persist_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug(f"suggestions_save_failed | {e}")

    def _load_suggestions(self) -> None:
        """Load persisted suggestions from disk."""
        try:
            if not self._persist_path.exists():
                return
            with open(self._persist_path, encoding="utf-8") as f:
                data = json.load(f)
            for item in data:
                s = Suggestion(
                    id=item["id"],
                    type=item["type"],
                    message=item["message"],
                    actions=item.get("actions", []),
                    priority=item.get("priority", 5),
                    dismissible=item.get("dismissible", True),
                    created_at=item.get("created_at", 0.0),
                    expires_at=item.get("expires_at"),
                )
                if not s.is_expired():
                    self._suggestions.append(s)
            if self._suggestions:
                logger.info(f"suggestions_loaded | count={len(self._suggestions)}")
        except Exception as e:
            logger.debug(f"suggestions_load_failed | {e}")

    def start(self):
        """Start the suggestion engine background thread."""
        if self._running:
            return

        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("suggestion_engine_started")

    def stop(self):
        """Stop the suggestion engine."""
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
        logger.info("suggestion_engine_stopped")

    def _run_loop(self):
        """Main background loop."""
        while not self._stop_event.is_set():
            try:
                self._check_triggers()
            except Exception as e:
                logger.error(f"suggestion_engine_error | {e}")

            self._stop_event.wait(self.check_interval)

    def _check_triggers(self):
        """Check all enabled trigger conditions."""
        now = time.time()
        self._last_check = now

        # Reset morning briefing flag at midnight
        current_hour = datetime.now().hour
        if current_hour == 0 and self._morning_briefing_done_today:
            self._morning_briefing_done_today = False

        # Check each trigger
        if self.TRIGGERS["morning_briefing"]["enabled"]:
            self._check_morning_briefing()

        if self.TRIGGERS["meeting_reminder"]["enabled"]:
            self._check_meeting_reminder()

        if self.TRIGGERS["idle_resume"]["enabled"]:
            self._check_idle_resume()

        if self.TRIGGERS["pattern_automation"]["enabled"]:
            self._check_pattern_automation()

        if self.TRIGGERS["deadline_alert"]["enabled"]:
            self._check_deadline_alert()

        if self.TRIGGERS["error_recovery"]["enabled"]:
            self._check_error_recovery()

        if self.TRIGGERS["predictive"]["enabled"]:
            self._check_predictive()

        if self.TRIGGERS["tool_health"]["enabled"]:
            self._check_tool_health()

        if self.TRIGGERS["proactive_research"]["enabled"]:
            self._check_proactive_research()

        # Clean up expired suggestions
        self._cleanup_expired()

    def _check_morning_briefing(self):
        """Check if it's time for morning briefing."""
        if self._morning_briefing_done_today:
            return

        current_hour = datetime.now().hour
        if current_hour == self._morning_briefing_hour:
            self._morning_briefing_done_today = True

            suggestion = Suggestion(
                id=str(uuid.uuid4())[:8],
                type="morning_briefing",
                message="Good morning! Here's your briefing:",
                actions=[
                    {"label": "Show briefing", "action": "show_briefing"},
                    {"label": "Later", "action": "dismiss"}
                ],
                priority=8,
                expires_at=time.time() + 3600  # Expires in 1 hour
            )
            self._add_suggestion(suggestion)

    def _check_meeting_reminder(self):
        """Check if there's an upcoming meeting within 15 minutes."""
        from datetime import datetime, timedelta

        calendar = getattr(self, '_brain', None)
        if calendar:
            calendar = getattr(calendar, 'calendar', None)
        if not calendar:
            return

        try:
            schedule = calendar.get_schedule()
            if not schedule:
                return

            now = datetime.now()
            window = now + timedelta(minutes=15)

            for event in schedule:
                start_str = event.get('start', '')
                if not start_str:
                    continue
                try:
                    start = datetime.fromisoformat(start_str.replace('Z', '+00:00').replace('+00:00', ''))
                except (ValueError, AttributeError):
                    continue

                if now <= start <= window:
                    title = event.get('title', event.get('summary', 'Meeting'))
                    time_str = start.strftime('%H:%M')
                    minutes_until = int((start - now).total_seconds() / 60)

                    # Avoid duplicate reminders
                    event_id = event.get('id', title)
                    existing = [s for s in self._suggestions
                               if s.type == "meeting_reminder" and event_id in s.id]
                    if existing:
                        continue

                    suggestion = Suggestion(
                        id=f"meeting_{event_id}",
                        type="meeting_reminder",
                        message=f"You have '{title}' at {time_str} (in ~{minutes_until} min).",
                        actions=[
                            {"label": "Thanks", "action": "dismiss"},
                            {"label": "Prep notes", "action": "meeting_prep"},
                        ],
                        priority=8,
                        expires_at=start.timestamp()
                    )
                    self._add_suggestion(suggestion)
        except Exception as e:
            logger.debug(f"meeting_reminder_error | {e}")

    def _check_idle_resume(self):
        """Check if user was working on something and might want to resume."""
        if not self._pattern_tracker:
            return

        # Use PatternTracker to predict what user might want to resume
        suggestion_text = self._pattern_tracker.get_proactive_suggestion()
        if not suggestion_text:
            return

        # Avoid duplicate idle suggestions
        existing = [s for s in self._suggestions if s.type == "resume_task"]
        if existing:
            return

        suggestion = Suggestion(
            id=str(uuid.uuid4())[:8],
            type="resume_task",
            message=suggestion_text,
            actions=[
                {"label": "Yes", "action": "resume"},
                {"label": "No", "action": "dismiss"}
            ],
            priority=3,
            expires_at=time.time() + 1800  # Expires in 30 min
        )
        self._add_suggestion(suggestion)

    def _check_pattern_automation(self):
        """Check for repeated actions that could be automated."""
        if not self._pattern_tracker:
            return

        # Get patterns with high repetition
        patterns = self._pattern_tracker.get_repeated_patterns(
            min_count=self.TRIGGERS["pattern_automation"]["min_repetitions"]
        )

        for pattern in patterns:
            desc = f"{pattern.get('app', '?')} → {pattern.get('task', '?')} ({pattern.get('count', 0)}x)"
            # Check if we already suggested this
            existing = [s for s in self._suggestions
                      if s.type == "automate" and desc in s.message]
            if existing:
                continue

            suggestion = Suggestion(
                id=str(uuid.uuid4())[:8],
                type="automate",
                message=f"Shall I automate this? You do '{desc}' frequently.",
                actions=[
                    {"label": "Yes, automate", "action": "automate"},
                    {"label": "Not now", "action": "dismiss"}
                ],
                priority=5,
                expires_at=time.time() + 86400  # Expires in 24 hours
            )
            self._add_suggestion(suggestion)

    def _check_deadline_alert(self):
        """Check for approaching task deadlines."""
        pending_tasks = self._task_state.get_pending_tasks()

        for task in pending_tasks:
            # Check if task has a deadline
            deadline_str = task.get("deadline")
            if not deadline_str:
                continue

            try:
                deadline = datetime.fromisoformat(deadline_str)
                hours_until = (deadline - datetime.now()).total_seconds() / 3600

                if 0 < hours_until <= self.TRIGGERS["deadline_alert"]["hours_before"]:
                    # Check if we already suggested this
                    existing = [s for s in self._suggestions
                              if s.type == "deadline" and task["id"] in s.message]
                    if existing:
                        continue

                    suggestion = Suggestion(
                        id=str(uuid.uuid4())[:8],
                        type="deadline",
                        message=f"Task '{task['goal']}' is due in {int(hours_until)} hours.",
                        actions=[
                            {"label": "Show task", "action": "show_task"},
                            {"label": "Dismiss", "action": "dismiss"}
                        ],
                        priority=7,
                        expires_at=time.time() + 3600
                    )
                    self._add_suggestion(suggestion)

            except (ValueError, TypeError):
                continue

    def _check_error_recovery(self):
        """Check for repeated errors that need investigation."""
        if not self._outcome_tracker:
            return

        recent = self._outcome_tracker.get_recent_outcomes(
            event_type="tool_call", limit=50
        )
        failures = [o for o in recent if o.outcome_type == "failure"]
        if len(failures) < 3:
            return

        # Avoid duplicate suggestions
        existing = [s for s in self._suggestions if s.type == "error_recovery"]
        if existing:
            return

        failed_tools = set()
        for f in failures[-5:]:
            if f.tool_name:
                failed_tools.add(f.tool_name)

        tool_list = ", ".join(failed_tools) if failed_tools else "multiple tools"
        suggestion = Suggestion(
            id=str(uuid.uuid4())[:8],
            type="error_recovery",
            message=f"I've noticed repeated failures with {tool_list}. Want me to investigate?",
            actions=[
                {"label": "Yes, investigate", "action": "investigate_errors"},
                {"label": "Dismiss", "action": "dismiss"}
            ],
            priority=6,
            expires_at=time.time() + 3600
        )
        self._add_suggestion(suggestion)

    def _check_predictive(self):
        """Suggest based on learned behavioral patterns."""
        if not self._pattern_detector:
            return

        patterns = self._pattern_detector.detect_patterns(min_confidence=3)
        if not patterns:
            return

        for pattern in patterns:
            # Skip low confidence
            if pattern.confidence < 3:
                continue

            # Check if we already suggested this pattern
            existing = [s for s in self._suggestions
                       if s.type == "predictive" and pattern.description in s.message]
            if existing:
                continue

            suggestion = Suggestion(
                id=str(uuid.uuid4())[:8],
                type="predictive",
                message=f"Based on your patterns: {pattern.description}",
                actions=[
                    {"label": "Got it", "action": "dismiss"},
                    {"label": "Don't suggest this", "action": "suppress_pattern"}
                ],
                priority=3,
                expires_at=time.time() + 86400
            )
            self._add_suggestion(suggestion)
            break  # One predictive suggestion per cycle

    def _check_tool_health(self):
        """Suggest avoiding tools with low success rates."""
        if not self._pattern_detector:
            return

        recs = self._pattern_detector.get_tool_recommendations()
        deprecate = recs.get("deprecate", [])
        if not deprecate:
            return

        # Avoid duplicate
        existing = [s for s in self._suggestions if s.type == "tool_health"]
        if existing:
            return

        tool_list = ", ".join(deprecate[:3])
        suggestion = Suggestion(
            id=str(uuid.uuid4())[:8],
            type="tool_health",
            message=f"These tools have been unreliable: {tool_list}. I'll avoid using them.",
            actions=[
                {"label": "OK", "action": "dismiss"},
                {"label": "Show details", "action": "show_tool_health"}
            ],
            priority=4,
            expires_at=time.time() + 43200  # 12 hours
        )
        self._add_suggestion(suggestion)

    def _check_proactive_research(self):
        """Research topics the user frequently asks about.

        Uses pattern detector to identify recurring interests,
        then generates a research suggestion if a topic hasn't been
        researched recently.
        """
        if not self._pattern_detector:
            return

        # Avoid duplicate
        existing = [s for s in self._suggestions if s.type == "proactive_research"]
        if existing:
            return

        # Get user preferences/interests from pattern detector
        try:
            prefs = self._pattern_detector.get_user_preferences()
            if not prefs:
                return

            # Find the most mentioned topic
            topics = []
            for pref in prefs:
                pref_lower = pref.lower()
                # Extract topic keywords from preference statements
                if any(kw in pref_lower for kw in ["research", "search", "learn", "study", "read"]):
                    topics.append(pref)

            if not topics:
                return

            # Check if we've already suggested research recently
            recent_research = [s for s in self._suggestions
                             if s.type == "proactive_research"
                             and s.expires_at and s.expires_at > time.time()]
            if recent_research:
                return

            topic = topics[0][:100]
            suggestion = Suggestion(
                id=str(uuid.uuid4())[:8],
                type="proactive_research",
                message=f"I noticed you frequently research '{topic}'. Want me to look into the latest developments?",
                actions=[
                    {"label": "Research now", "action": "research", "topic": topic},
                    {"label": "Not now", "action": "dismiss"},
                ],
                priority=3,
                expires_at=time.time() + 86400,  # 24 hours
            )
            self._add_suggestion(suggestion)

        except Exception as e:
            logger.debug("proactive_research_check_failed | %s", e)

    def _add_suggestion(self, suggestion: Suggestion):
        """Add a suggestion to the queue."""
        self._suggestions.append(suggestion)

        # Trim to max
        if len(self._suggestions) > self.max_suggestions:
            self._suggestions = self._suggestions[-self.max_suggestions:]

        # Deliver if callback set
        if self.delivery_callback:
            self.delivery_callback(suggestion)

        self._save_suggestions()
        logger.info(f"suggestion_added | type={suggestion.type} | priority={suggestion.priority}")

    def _cleanup_expired(self):
        """Remove expired suggestions."""
        before = len(self._suggestions)
        self._suggestions = [s for s in self._suggestions if not s.is_expired()]
        if len(self._suggestions) < before:
            self._save_suggestions()
            logger.debug(f"suggestions_cleaned | removed={before - len(self._suggestions)}")

    def get_pending_suggestions(self) -> List[Suggestion]:
        """Get all pending (non-expired) suggestions."""
        return [s for s in self._suggestions if not s.is_expired()]

    def dismiss_suggestion(self, suggestion_id: str):
        """Dismiss a suggestion by ID."""
        self._suggestions = [s for s in self._suggestions if s.id != suggestion_id]
        self._save_suggestions()
        logger.info(f"suggestion_dismissed | id={suggestion_id}")

    def get_suggestion_by_id(self, suggestion_id: str) -> Optional[Suggestion]:
        """Get a specific suggestion by ID."""
        for s in self._suggestions:
            if s.id == suggestion_id:
                return s
        return None

    def trigger_now(self, trigger_type: str):
        """Manually trigger a specific check."""
        if trigger_type == "morning_briefing":
            self._check_morning_briefing()
        elif trigger_type == "deadline_alert":
            self._check_deadline_alert()
        elif trigger_type == "pattern_automation":
            self._check_pattern_automation()

    def set_morning_briefing_hour(self, hour: int):
        """Set the preferred morning briefing hour."""
        if 0 <= hour <= 23:
            self._morning_briefing_hour = hour
            logger.info(f"morning_briefing_hour_set | hour={hour}")


# Singleton instance
_suggestion_engine: Optional[SuggestionEngine] = None

def get_suggestion_engine() -> SuggestionEngine:
    """Get the singleton SuggestionEngine instance."""
    global _suggestion_engine
    if _suggestion_engine is None:
        _suggestion_engine = SuggestionEngine()
    return _suggestion_engine


if __name__ == "__main__":
    # Test the suggestion engine
    engine = SuggestionEngine(check_interval=1)

    # Add a test suggestion
    test_suggestion = Suggestion(
        id="test123",
        type="morning_briefing",
        message="Good morning! Here's your briefing:",
        actions=[{"label": "Show", "action": "show"}],
        priority=8
    )
    engine._add_suggestion(test_suggestion)

    print(f"Pending suggestions: {len(engine.get_pending_suggestions())}")

    # Test expiration
    expired_suggestion = Suggestion(
        id="expired",
        type="test",
        message="Expired",
        actions=[],
        priority=5,
        expires_at=time.time() - 1  # Already expired
    )
    engine._add_suggestion(expired_suggestion)

    engine._cleanup_expired()
    print(f"After cleanup: {len(engine.get_pending_suggestions())}")
