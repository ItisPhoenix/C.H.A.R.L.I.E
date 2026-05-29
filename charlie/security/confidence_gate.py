"""
charlie/security/confidence_gate.py

Confidence-based auto-approval for low-risk actions.
Skips confirmation for high-confidence, low-risk operations.
"""

import logging
import time
from typing import Optional

logger = logging.getLogger("charlie.security.confidence_gate")

# Confidence thresholds per risk tier
THRESHOLDS = {
    0: 0.0,   # TIER_0: always auto-approve (already the case)
    1: 0.85,  # TIER_1: auto-approve if confidence > 0.85
    2: 1.0,   # TIER_2: never auto-approve (needs explicit confirmation)
    3: 1.0,   # TIER_3: never auto-approve
}


class ConfidenceGate:
    """Decides whether to auto-approve actions based on confidence."""

    def __init__(self):
        self._approval_history: dict[str, list[dict]] = {}

    def should_auto_approve(
        self,
        tool_name: str,
        args: dict,
        risk_tier: int,
        outcome_tracker=None,
        user_approvals: Optional[list[dict]] = None,
    ) -> tuple[bool, float]:
        """Decide if an action should be auto-approved.

        Args:
            tool_name: Name of the tool to execute
            args: Tool arguments
            risk_tier: Risk tier (0-3)
            outcome_tracker: OutcomeTracker for historical data
            user_approvals: List of past user approval decisions

        Returns:
            (should_approve, confidence_score)
        """
        # TIER_0 always auto-approves
        if risk_tier <= 0:
            return True, 1.0

        # TIER_2+ never auto-approves
        if risk_tier >= 2:
            return False, 0.0

        # TIER_1: calculate confidence
        confidence = self._calculate_confidence(
            tool_name, args, outcome_tracker, user_approvals
        )

        threshold = THRESHOLDS.get(risk_tier, 1.0)
        should_approve = confidence >= threshold

        if should_approve:
            logger.info("auto_approved | tool=%s confidence=%.2f", tool_name, confidence)

        return should_approve, confidence

    def _calculate_confidence(
        self,
        tool_name: str,
        args: dict,
        outcome_tracker,
        user_approvals: Optional[list[dict]],
    ) -> float:
        """Calculate confidence score for a tool invocation."""
        scores = []

        # Factor 1: Historical success rate (0-1)
        if outcome_tracker and hasattr(outcome_tracker, "get_recent_outcomes"):
            outcomes = outcome_tracker.get_recent_outcomes(limit=20)
            tool_outcomes = [o for o in outcomes if o.get("tool") == tool_name]
            if tool_outcomes:
                success_rate = sum(1 for o in tool_outcomes if o.get("success")) / len(tool_outcomes)
                scores.append(("success_rate", success_rate, 0.4))  # 40% weight

        # Factor 2: User approval rate for this tool (0-1)
        if user_approvals:
            tool_approvals = [a for a in user_approvals if a.get("tool") == tool_name]
            if tool_approvals:
                approval_rate = sum(1 for a in tool_approvals if a.get("approved")) / len(tool_approvals)
                scores.append(("approval_rate", approval_rate, 0.3))  # 30% weight

        # Factor 3: Familiarity (how often has this tool been used)
        history = self._approval_history.get(tool_name, [])
        familiarity = min(len(history) / 10.0, 1.0)  # 10 uses = max familiarity
        scores.append(("familiarity", familiarity, 0.3))  # 30% weight

        if not scores:
            return 0.0

        # Weighted average
        total_weight = sum(w for _, _, w in scores)
        if total_weight == 0:
            return 0.0

        confidence = sum(v * w for _, v, w in scores) / total_weight
        return min(max(confidence, 0.0), 1.0)

    def record_approval(self, tool_name: str, approved: bool) -> None:
        """Record an approval decision for future confidence calculation."""
        if tool_name not in self._approval_history:
            self._approval_history[tool_name] = []
        self._approval_history[tool_name].append({
            "approved": approved,
            "timestamp": time.time(),
        })
        # Keep last 50 per tool
        self._approval_history[tool_name] = self._approval_history[tool_name][-50:]
