"""
charlie/intelligence/briefing.py

BriefingAssembler — Gathers 5 sections for morning/on-demand briefing.
Sections: Agenda, Health, Tasks, Intel, Context.
"""

import time
from dataclasses import dataclass, field

from charlie.utils.logger import get_logger

logger = get_logger("Briefing")


@dataclass
class BriefingData:
    """Complete briefing data."""
    agenda: dict = field(default_factory=dict)
    health: dict = field(default_factory=dict)
    tasks: dict = field(default_factory=dict)
    intel: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)
    learned_insights: dict = field(default_factory=dict)
    yesterday: dict = field(default_factory=dict)
    assembled_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "agenda": self.agenda,
            "health": self.health,
            "tasks": self.tasks,
            "intel": self.intel,
            "context": self.context,
            "learned_insights": self.learned_insights,
            "yesterday": self.yesterday,
            "assembled_at": self.assembled_at,
        }

    def to_text(self) -> str:
        """Human-readable briefing text for TTS."""
        lines = []

        # Agenda
        events = self.agenda.get("events", [])
        if events:
            lines.append(f"You have {len(events)} events today.")
            for e in events[:3]:
                lines.append(f"  {e.get('time', '')} — {e.get('title', '')}")
        else:
            lines.append("No events scheduled for today.")

        # Health
        vitals = self.health.get("vitals", {})
        restarts = self.health.get("restarts", 0)
        if vitals:
            cpu = vitals.get("system_cpu", 0)
            ram = vitals.get("system_ram_percent", 0)
            lines.append(f"System health: CPU at {cpu:.0f}%, RAM at {ram:.0f}%.")
        if restarts > 0:
            lines.append(f"There have been {restarts} subsystem restarts.")

        # Tasks
        pending = self.tasks.get("pending", [])
        count = self.tasks.get("count", 0)
        if count > 0:
            lines.append(f"{count} tasks pending.")
            for t in pending[:3]:
                lines.append(f"  {t.get('name', 'Unknown task')}")
        else:
            lines.append("No pending tasks.")

        # Intel
        news = self.intel.get("news", [])
        if news:
            lines.append(f"{len(news)} news updates available.")

        # Context
        recent = self.context.get("recent_conversation", [])
        if recent:
            lines.append("Last session context available.")

        # Learned insights
        insights = self.learned_insights.get("patterns", [])
        if insights:
            top = insights[0]
            lines.append(f"Learned: {top.get('description', 'patterns detected')}.")
            if len(insights) > 1:
                lines.append(f"  Plus {len(insights) - 1} more patterns.")

        # Yesterday
        yesterday = self.yesterday
        total = yesterday.get("total_outcomes", 0)
        if total > 0:
            success = yesterday.get("successful", 0)
            tools = yesterday.get("tools_used", [])
            lines.append(f"Yesterday: {success}/{total} successful.")
            if tools:
                lines.append(f"  Tools used: {', '.join(tools[:3])}.")

        return " ".join(lines)


class BriefingAssembler:
    """
    Assembles a 5-section briefing from CHARLIE subsystems.

    Usage:
        assembler = BriefingAssembler(brain)
        briefing = await assembler.assemble()
        text = briefing.to_text()
    """

    def __init__(self, brain=None):
        self.brain = brain

    async def assemble(self) -> BriefingData:
        """Assemble a complete briefing."""
        briefing = BriefingData()

        try:
            briefing.agenda = self._gather_agenda()
        except Exception as e:
            logger.warning(f"briefing_agenda_failed | {e}")
            briefing.agenda = {"events": [], "error": str(e)}

        try:
            briefing.health = self._gather_health()
        except Exception as e:
            logger.warning(f"briefing_health_failed | {e}")
            briefing.health = {"vitals": {}, "error": str(e)}

        try:
            briefing.tasks = self._gather_tasks()
        except Exception as e:
            logger.warning(f"briefing_tasks_failed | {e}")
            briefing.tasks = {"pending": [], "count": 0, "error": str(e)}

        try:
            briefing.intel = self._gather_intel()
        except Exception as e:
            logger.warning(f"briefing_intel_failed | {e}")
            briefing.intel = {"news": [], "error": str(e)}

        try:
            briefing.context = self._gather_context()
        except Exception as e:
            logger.warning(f"briefing_context_failed | {e}")
            briefing.context = {"recent_conversation": [], "error": str(e)}

        try:
            briefing.learned_insights = self._gather_learned_insights()
        except Exception as e:
            logger.warning(f"briefing_insights_failed | {e}")
            briefing.learned_insights = {"patterns": [], "error": str(e)}

        try:
            briefing.yesterday = self._gather_yesterday()
        except Exception as e:
            logger.warning(f"briefing_yesterday_failed | {e}")
            briefing.yesterday = {"total_outcomes": 0, "error": str(e)}

        logger.info("briefing_assembled")
        return briefing

    def _gather_agenda(self) -> dict:
        """Calendar events for today."""
        if not self.brain:
            return {"events": []}

        events = []
        try:
            if hasattr(self.brain, 'calendar') and self.brain.calendar:
                schedule = self.brain.calendar.get_schedule()
                if schedule:
                    events = [
                        {"time": e.get("time", ""), "title": e.get("title", "")}
                        for e in schedule[:10]
                    ]
        except Exception as e:
            logger.debug(f"agenda_fetch_failed | {e}")

        return {"events": events}

    def _gather_health(self) -> dict:
        """System health summary."""
        vitals = {}
        restarts = 0

        if self.brain:
            try:
                if hasattr(self.brain, 'doctor') and self.brain.doctor:
                    vitals = self.brain.doctor.obs.get_vitals()
            except Exception:
                pass

            try:
                if hasattr(self.brain, 'supervisor_restart_count'):
                    restarts = self.brain.supervisor_restart_count
            except Exception:
                pass

        return {"vitals": vitals, "restarts": restarts}

    def _gather_tasks(self) -> dict:
        """Pending tasks from AutonomousTaskQueue."""
        pending = []

        if self.brain:
            try:
                if hasattr(self.brain, 'task_queue') and self.brain.task_queue:
                    queue = self.brain.task_queue
                    if hasattr(queue, 'queue'):
                        pending = [
                            {"name": getattr(t, 'name', str(t)), "priority": getattr(t, 'priority', 0)}
                            for t in list(queue.queue)[:10]
                        ]
            except Exception:
                pass

        return {"pending": pending, "count": len(pending)}

    def _gather_intel(self) -> dict:
        """News, weather, integration updates."""
        news = []

        return {"news": news}

    def _gather_context(self) -> dict:
        """Last session context."""
        recent = []
        world = {}

        if self.brain:
            try:
                if hasattr(self.brain, 'history') and self.brain.history:
                    recent = self.brain.history[-6:]
            except Exception:
                pass

            try:
                if hasattr(self.brain, 'world') and self.brain.world:
                    world = {
                        "active_app": getattr(self.brain.world, 'active_app', ''),
                        "task": getattr(self.brain.world, 'current_task_inferred', ''),
                        "frustration": getattr(self.brain.world, 'frustration_score', 0),
                    }
            except Exception:
                pass

        return {"recent_conversation": recent, "world_state": world}

    def _gather_learned_insights(self) -> dict:
        """Learned behavioral patterns from PatternDetector."""
        patterns = []
        if self.brain:
            try:
                detector = getattr(self.brain, "pattern_detector", None)
                if detector:
                    detected = detector.detect_patterns(min_confidence=3)
                    patterns = [
                        {"type": p.pattern_type, "description": p.description, "confidence": p.confidence}
                        for p in detected[:5]
                    ]
            except Exception as e:
                logger.debug(f"insights_fetch_failed | {e}")
        return {"patterns": patterns}

    def _gather_yesterday(self) -> dict:
        """Yesterday's activity summary from OutcomeTracker."""
        if not self.brain:
            return {"total_outcomes": 0, "successful": 0, "tools_used": []}

        try:
            tracker = getattr(self.brain, "outcome_tracker", None)
            if not tracker:
                return {"total_outcomes": 0, "successful": 0, "tools_used": []}

            # Get last 24h of outcomes
            recent = tracker.get_recent_outcomes(limit=200)
            if not recent:
                return {"total_outcomes": 0, "successful": 0, "tools_used": []}

            total = len(recent)
            successful = sum(1 for o in recent if o.outcome_type == "success")
            tools = set(o.tool_name for o in recent if o.tool_name)

            return {
                "total_outcomes": total,
                "successful": successful,
                "tools_used": sorted(tools)[:5],
            }
        except Exception as e:
            logger.debug(f"yesterday_fetch_failed | {e}")
            return {"total_outcomes": 0, "successful": 0, "error": str(e)}
