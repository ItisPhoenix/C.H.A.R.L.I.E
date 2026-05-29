"""Risk Gate — evaluates whether an action should auto-execute or ask for approval."""
from __future__ import annotations

import logging

from charlie.automation.models import RiskTier

logger = logging.getLogger("charlie.automation.risk_gate")


class RiskGate:
    """Evaluates action risk and decides whether to auto-execute or ask approval."""

    def __init__(self, brain=None):
        self.brain = brain

    def evaluate_sync(self, action: str, args: dict, risk_tier: RiskTier) -> bool:
        """Synchronous evaluation. Returns True if action should proceed."""
        if risk_tier == RiskTier.TIER_0:
            return True
        return False

    async def evaluate(self, action: str, args: dict, risk_tier: RiskTier) -> bool:
        """Async evaluation. Can ask for approval via brain."""
        if risk_tier == RiskTier.TIER_0:
            return True
        if not self.brain:
            logger.warning(f"risk_gate_no_brain | action={action} | tier={risk_tier}")
            return False
        return await self._ask_approval(action, args, risk_tier)

    async def _ask_approval(self, action: str, args: dict, risk_tier: RiskTier) -> bool:
        """Ask the user for approval via dashboard or Telegram."""
        label = self.get_risk_label(risk_tier)
        msg = f"[{label}] Approve: {action}({args})?"

        try:
            self.brain._safe_put(self.brain.status_q, {
                "type": "CONFIRM_REQUIRED",
                "content": {"action": action, "args": args, "risk": label, "message": msg},
            })
        except Exception:
            pass

        try:
            self.brain._safe_put(self.brain.telegram_q, {
                "type": "CHAT_MSG", "speaker": "CHARLIE", "content": msg,
            })
        except Exception:
            pass

        logger.info(f"approval_requested | action={action} | tier={risk_tier}")
        return False

    @staticmethod
    def get_risk_label(tier: RiskTier) -> str:
        """Get human-readable risk label."""
        return {
            RiskTier.TIER_0: "AUTO",
            RiskTier.TIER_1: "APPROVAL",
            RiskTier.TIER_2: "CONFIRM",
            RiskTier.TIER_3: "DESTRUCTIVE",
        }.get(tier, "UNKNOWN")
