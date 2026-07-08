"""Reflection engine for the agent swarm.

Analyzes task outcomes, detects failure patterns, and suggests
improvements to MEMORY.md rules. Operates on the shared Blackboard.
"""

import logging
import re
import time
from collections import Counter
from typing import Any, Dict, List, Optional

from charlie.blackboard import Blackboard, Task

logger = logging.getLogger("charlie.reflection")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_HISTORY = 100
_PROMPT_MEMORY_PATH = "MEMORY.md"


class Reflector:
    """Analyzes completed tasks and proposes rule improvements.

    Lifecycle:
        1. SwarmOrchestrator calls ``after_task(task_id)`` on completion.
        2. Reflector logs the outcome to its internal history.
        3. Periodically (or on demand), ``analyze()`` scans the history
           for recurring failure patterns and emits findings to the Blackboard.
        4. ``suggest_memory_updates()`` returns concrete rule text the
           orchestrator can append to MEMORY.md during low-activity windows.
    """

    def __init__(self, blackboard: Blackboard) -> None:
        self._bb = blackboard
        self._history: List[Dict[str, Any]] = []
        self._suggestions: List[str] = []

    # -- Public API --

    def after_task(self, task_id: str) -> None:
        """Record the outcome of a completed (or failed) task."""
        task = self._find_task(task_id)
        if task is None:
            logger.warning("Reflector: task %s not found on blackboard", task_id)
            return

        entry: Dict[str, Any] = {
            "task_id": task.id,
            "task_name": task.name,
            "agent": task.assigned_to,
            "status": task.status,
            "result": task.result,
            "retries": task.retry_count,
            "timestamp": time.time(),
        }
        self._history.append(entry)

        # Trim history
        if len(self._history) > _MAX_HISTORY:
            self._history = self._history[-_MAX_HISTORY:]

        logger.info(
            "Reflector: recorded %s for task '%s' (agent=%s, retries=%d)",
            task.status, task.name, task.assigned_to, task.retry_count,
        )

        # Post a finding if the task failed
        if task.status == "failed":
            self._bb.add_finding(
                f"failure:{task.id}",
                {
                    "task": task.name,
                    "agent": task.assigned_to,
                    "result": task.result,
                },
            )

    def analyze(self) -> Dict[str, Any]:
        """Scan history for patterns and return a summary.

        Returns a dict with:
            - total_tasks: int
            - success_rate: float (0.0-1.0)
            - failure_patterns: list of {agent, reason, count}
            - retry_rate: float (0.0-1.0)
            - suggestions: list of rule strings
        """
        if not self._history:
            return {"total_tasks": 0, "success_rate": 1.0, "failure_patterns": [], "retry_rate": 0.0, "suggestions": []}

        successes = sum(1 for h in self._history if h["status"] == "done")
        retried = sum(1 for h in self._history if h["retries"] > 0)
        total = len(self._history)

        success_rate = successes / total if total > 0 else 1.0
        retry_rate = retried / total if total > 0 else 0.0

        # Group failures by agent + normalized reason
        failure_counter: Counter = Counter()
        for h in self._history:
            if h["status"] == "failed":
                reason = self._normalize_reason(h.get("result", ""))
                failure_counter[(h["agent"], reason)] += 1

        failure_patterns = [
            {"agent": agent, "reason": reason, "count": count}
            for (agent, reason), count in failure_counter.most_common(10)
        ]

        # Generate suggestions
        self._suggestions = self._generate_suggestions(
            success_rate, retry_rate, failure_patterns
        )

        # Post summary to blackboard
        summary = {
            "total_tasks": total,
            "success_rate": round(success_rate, 3),
            "retry_rate": round(retry_rate, 3),
            "top_failures": failure_patterns[:3],
        }
        self._bb.add_finding("reflection:summary", summary)

        return {
            "total_tasks": total,
            "success_rate": round(success_rate, 3),
            "failure_patterns": failure_patterns,
            "retry_rate": round(retry_rate, 3),
            "suggestions": list(self._suggestions),
        }

    def suggest_memory_updates(self) -> List[str]:
        """Return rule strings that should be appended to MEMORY.md.

        Each suggestion is a concrete, actionable rule derived from
        observed failure patterns.  The orchestrator is responsible for
        deduplication and actual file writes.
        """
        return list(self._suggestions)

    def get_history(self) -> List[Dict[str, Any]]:
        """Return the full reflection history (read-only copy)."""
        return list(self._history)

    def clear_history(self) -> None:
        """Reset history. Use after a successful MEMORY.md update cycle."""
        self._history.clear()
        self._suggestions.clear()

    # -- Internal helpers --

    def _find_task(self, task_id: str) -> Optional[Task]:
        """Look up a task by ID from the blackboard."""
        for task in self._bb.get_all_tasks():
            if task.id == task_id:
                return task
        return None

    @staticmethod
    def _normalize_reason(text: str) -> str:
        """Collapse error messages to a short, searchable key."""
        if not text:
            return "unknown"
        # Strip exception class prefix if present
        text = re.sub(r"^\w+(Error|Exception):\s*", "", text)
        # Truncate
        text = text[:120].strip()
        return text or "unknown"

    @staticmethod
    def _generate_suggestions(
        success_rate: float,
        retry_rate: float,
        failure_patterns: List[Dict[str, Any]],
    ) -> List[str]:
        """Produce actionable rule suggestions from aggregate stats."""
        suggestions: List[str] = []

        if success_rate < 0.5:
            suggestions.append(
                "RULE: Overall success rate is below 50%. Review agent prompts "
                "and task decomposition granularity."
            )

        if retry_rate > 0.3:
            suggestions.append(
                "RULE: More than 30% of tasks required retries. Consider adding "
                "pre-flight validation or splitting complex tasks."
            )

        # Per-agent failure concentration
        agent_failures: Counter = Counter()
        for fp in failure_patterns:
            agent_failures[fp["agent"]] += fp["count"]

        for agent, count in agent_failures.most_common(3):
            if count >= 3:
                suggestions.append(
                    f"RULE: Agent '{agent}' has {count} failures in recent history. "
                    f"Review its system prompt or reduce its task scope."
                )

        # Recurring error reasons
        reason_counts: Counter = Counter()
        for fp in failure_patterns:
            reason_counts[fp["reason"]] += fp["count"]

        for reason, count in reason_counts.most_common(2):
            if count >= 2:
                suggestions.append(
                    f"RULE: Recurring failure reason '{reason}' ({count} times). "
                    f"Add a guard or pre-check to prevent this class of error."
                )

        return suggestions
