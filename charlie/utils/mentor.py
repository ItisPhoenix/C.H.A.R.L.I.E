"""
C.H.A.R.L.I.E. — Mentor & Adaptive Learning System
Tracks interaction patterns and feeds personalisation back into the system prompt.
No fine-tuning required — pure runtime adaptation.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Any, Optional

from charlie.utils.logger import get_logger

logger = get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

STATS_PATH = "charlie/memory/mentor_stats.json"
FEEDBACK_PATH = "charlie/memory/feedback_log.jsonl"
EPISODIC_LOG_PATH = "charlie/memory/episodic_memory.jsonl"

XP_THRESHOLDS = {
    "Novice": 0,
    "Apprentice": 1_000,
    "Adept": 5_000,
    "Specialist": 20_000,
    "Expert": 60_000,
}

# ── Data Model ────────────────────────────────────────────────────────────────

DEFAULT_STATS = {
    "level": "Novice",
    "xp": 0,
    "total_tasks": 0,
    "corrections": 0,  # Times Sir said "no", "wrong", "undo"
    "confirmations": 0,  # Times Sir said "yes", "correct", "good"
    "topics": {},  # { "python_debugging": {"score": 12, "last_seen": timestamp} }
    "tool_usage": {},  # { "run_command": 47, "look": 12 }
    "verbosity_preference": "technical",  # "brief" | "technical" — auto-calibrated
    "last_report": 0,
    "last_reflection_xp": 0,  # XP at which last persona reflection happened
}

# ── Core Class ────────────────────────────────────────────────────────────────


class MentorSystem:
    def __init__(self, stats_path: str = STATS_PATH) -> None:
        self.stats_path = stats_path
        os.makedirs(os.path.dirname(self.stats_path), exist_ok=True)
        self.stats = self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> dict[str, Any]:
        if os.path.exists(self.stats_path):
            try:
                with open(self.stats_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return {**DEFAULT_STATS, **data}
            except Exception as e:
                logger.error("mentor_load_failed", error=str(e))
        return DEFAULT_STATS.copy()

    def save(self) -> None:
        try:
            with open(self.stats_path, "w", encoding="utf-8") as f:
                json.dump(self.stats, f, indent=2)
        except Exception as e:
            logger.error("mentor_save_failed", error=str(e))

    # ── Interaction Tracking ──────────────────────────────────────────────────

    def log_task(self, topic: str, tool_used: Optional[str] = None, xp_delta: int = 10) -> None:
        """Call after every completed task."""
        self.stats["total_tasks"] += 1
        self._update_topic(topic, xp_delta)
        if tool_used:
            self._update_tool_usage(tool_used)
        self._recalculate_level()
        self.save()

    def log_correction(self, topic: str) -> None:
        """Call when Sir corrects Charlie (wrong answer, undo, retry)."""
        self.stats["corrections"] += 1
        self._update_topic(topic, delta=-5)
        self._recalibrate_verbosity(towards="technical")
        self.save()
        logger.info("correction_logged", topic=topic)

    def log_confirmation(self, topic: str) -> None:
        """Call when Sir confirms Charlie was correct (yes, good, correct)."""
        self.stats["confirmations"] += 1
        self._update_topic(topic, delta=15)
        self._recalibrate_verbosity(towards="brief")
        self.save()

    def log_feedback(self, user_input: str, charlie_response: str, outcome: str) -> None:
        """
        Appends raw interaction to feedback log for future fine-tuning dataset.
        outcome: 'positive' | 'negative' | 'neutral'
        """
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "input": user_input,
            "response": charlie_response,
            "outcome": outcome,
        }
        try:
            with open(FEEDBACK_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.error("feedback_log_failed", error=str(e))

    def log_episodic_memory(self, event_type: str, content: str, significance: float = 0.5) -> None:
        """
        Records a significant interaction or instruction for the Reflect-Evolve engine.
        event_type: 'user_instruction' | 'self_reflection' | 'correction' | 'tool_failure'
        """
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": event_type,
            "content": content,
            "significance": significance,
        }
        try:
            with open(EPISODIC_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.error("episodic_log_failed", error=str(e))

    # ── Internal Helpers ──────────────────────────────────────────────────────

    def _update_topic(self, topic: str, delta: int) -> None:
        if topic not in self.stats["topics"]:
            self.stats["topics"][topic] = {"score": 0, "last_seen": 0}
        self.stats["topics"][topic]["score"] += delta
        self.stats["topics"][topic]["last_seen"] = time.time()
        if delta > 0:
            self.stats["xp"] += delta * 10

    def _update_tool_usage(self, tool: str) -> None:
        self.stats["tool_usage"][tool] = self.stats["tool_usage"].get(tool, 0) + 1

    def _recalculate_level(self) -> None:
        xp = self.stats["xp"]
        for level, threshold in sorted(XP_THRESHOLDS.items(), key=lambda x: -x[1]):
            if xp >= threshold:
                self.stats["level"] = level
                break

    def _recalibrate_verbosity(self, towards: str) -> None:
        ratio = self.stats["confirmations"] / max(1, self.stats["confirmations"] + self.stats["corrections"])
        self.stats["verbosity_preference"] = "brief" if ratio > 0.75 else "technical"

    # ── Prompt Injection ──────────────────────────────────────────────────────

    def get_adaptive_prompt_injection(self) -> str:
        """Returns context block for system prompt."""
        top_topics = sorted(self.stats["topics"].items(), key=lambda x: x[1]["score"], reverse=True)[:4]

        topics_str = ", ".join(t for t, _ in top_topics) if top_topics else "not yet established"
        verbosity = self.stats["verbosity_preference"]

        return (
            f"Strongest domains      : {topics_str}\n"
            f"Preferred verbosity    : {verbosity}\n"
            f"Calibration directive  : Adjust technical depth to match the user's profile."
        )

    # ── Reporting ─────────────────────────────────────────────────────────────

    def generate_report(self) -> str:
        """Formatted report."""
        accuracy = round(
            self.stats["confirmations"] / max(1, self.stats["confirmations"] + self.stats["corrections"]) * 100
        )

        top_topics = sorted(self.stats["topics"].items(), key=lambda x: x[1]["score"], reverse=True)[:5]

        lines = [
            "📊 *Interaction Report*",
            f"Rank       : {self.stats['level']}",
            f"Total XP   : {self.stats['xp']:,}",
            f"Accuracy   : {accuracy}%",
            "",
            "*Top Domains:*",
        ]
        for topic, data in top_topics:
            lines.append(f"  - {topic.replace('_', ' ').title()}: {data['score']} pts")

        self.stats["last_report"] = time.time()
        self.save()
        return "\n".join(lines)

    def get_stats_summary(self) -> dict[str, Any]:
        """Returns summary for status display."""
        return {
            "level": self.stats["level"],
            "xp": self.stats["xp"],
            "tasks": self.stats["total_tasks"],
            "verbosity": self.stats["verbosity_preference"],
        }
