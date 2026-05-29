"""Learning Tracker — delegates to OutcomeTracker for persistence.

Keeps its existing interface (record, get_success_rate, suggest_rule, predict_need)
but uses OutcomeTracker SQLite instead of standalone JSON.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Optional

from charlie.automation.models import AutomationRule, Outcome, Prediction, RiskTier

logger = logging.getLogger("charlie.automation.learning_tracker")


class LearningTracker:
    """Tracks automation outcomes and adapts behavior over time.
    Delegates persistence to OutcomeTracker (SQLite) when available,
    falls back to in-memory list otherwise.
    """

    def __init__(self, outcome_tracker=None, persist_path: str = ""):
        self._tracker = outcome_tracker
        self._outcomes: list[Outcome] = []  # Fallback in-memory store

    @property
    def outcomes(self) -> list[Outcome]:
        """Legacy property for backward compatibility with tests."""
        return self._outcomes

    def _use_tracker(self) -> bool:
        return self._tracker is not None

    def record(self, outcome: Outcome):
        """Record the outcome of an automation action."""
        self._outcomes.append(outcome)
        if self._use_tracker():
            outcome_type = "success" if outcome.success else "failure"
            details = {
                "action": outcome.action,
                "user_approved": outcome.user_approved,
                "user_feedback": outcome.user_feedback,
            }
            self._tracker.record_outcome(
                event_type="task_complete",
                outcome_type=outcome_type,
                tool_name=outcome.action,
                details=details,
                confidence=1 if outcome.success else 0,
            )
        logger.info(
            f"outcome_recorded | event={outcome.event_type} | "
            f"action={outcome.action} | success={outcome.success}"
        )

    def get_success_rate(self, action: str) -> float:
        """How often does this action succeed?"""
        if self._use_tracker():
            rate = self._tracker.get_tool_success_rate(action)
            if rate is not None:
                return rate
        relevant = [o for o in self._outcomes if o.action == action]
        if not relevant:
            return 0.0
        return sum(1 for o in relevant if o.success) / len(relevant)

    def get_user_approval_rate(self, action: str) -> float:
        """How often does the user approve this action?"""
        if self._use_tracker():
            recent = self._tracker.get_recent_outcomes(
                event_type="task_complete", limit=500
            )
            relevant = [
                o for o in recent
                if o.tool_name == action and o.details.get("user_approved") is not None
            ]
            if relevant:
                return sum(
                    1 for o in relevant if o.details.get("user_approved", True)
                ) / len(relevant)
        relevant = [o for o in self._outcomes if o.action == action]
        if not relevant:
            return 0.0
        return sum(1 for o in relevant if o.user_approved) / len(relevant)

    def suggest_rule(self, event_type: str) -> Optional[AutomationRule]:
        """Suggest a new automation rule based on repeated successful outcomes."""
        if self._use_tracker():
            recent = self._tracker.get_recent_outcomes(
                event_type="task_complete", limit=500
            )
            relevant = [
                o for o in recent
                if o.outcome_type == "success"
            ]
        else:
            relevant_outcomes = [
                o for o in self._outcomes
                if o.event_type == event_type and o.success
            ]
            if len(relevant_outcomes) < 3:
                return None
            action_counts: dict[str, int] = defaultdict(int)
            for o in relevant_outcomes:
                action_counts[o.action] += 1
            if not action_counts:
                return None
            best_action = max(action_counts, key=action_counts.get)
            success_rate = self.get_success_rate(best_action)
            approval_rate = self.get_user_approval_rate(best_action)
            if success_rate < 0.7 or approval_rate < 0.7:
                return None
            return AutomationRule(
                name=f"auto_{event_type}_{best_action}",
                trigger=event_type,
                condition="True",
                action=best_action,
                risk_tier=RiskTier.TIER_0 if approval_rate > 0.9 else RiskTier.TIER_1,
                description=f"Auto-suggested: {event_type} -> {best_action} "
                            f"(success={success_rate:.0%}, approval={approval_rate:.0%})",
            )

        # Tracker path
        action_counts_t: dict[str, int] = defaultdict(int)
        for o in relevant:
            action_counts_t[o.tool_name] += 1
        if not action_counts_t:
            return None
        best = max(action_counts_t, key=action_counts_t.get)
        sr = self.get_success_rate(best)
        ar = self.get_user_approval_rate(best)
        if sr < 0.7 or ar < 0.7:
            return None
        return AutomationRule(
            name=f"auto_{event_type}_{best}",
            trigger=event_type,
            condition="True",
            action=best,
            risk_tier=RiskTier.TIER_0 if ar > 0.9 else RiskTier.TIER_1,
            description=f"Auto-suggested: {event_type} -> {best} "
                        f"(success={sr:.0%}, approval={ar:.0%})",
        )

    def predict_need(self) -> list[Prediction]:
        """Predict what the user will need based on time-of-day patterns."""
        predictions = []
        now = time.localtime()
        hour = now.tm_hour
        if 7 <= hour <= 9:
            predictions.append(Prediction(
                description="Morning briefing",
                confidence=0.8,
                suggested_action="daily_briefing",
            ))
        if 17 <= hour <= 19:
            predictions.append(Prediction(
                description="End of day summary",
                confidence=0.6,
                suggested_action="daily_summary",
            ))
        return predictions
