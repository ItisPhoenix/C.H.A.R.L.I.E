"""Risk Gate — evaluates whether an action should auto-execute or ask for approval."""

from __future__ import annotations

import asyncio
import logging

from charlie.automation.models import RiskTier

logger = logging.getLogger("charlie.automation.risk_gate")


class RiskGate:
    """Evaluates action risk and decides whether to auto-execute or ask approval."""

    # Tier-2 countdown (seconds) — how long to wait for user approval before
    # defaulting to deny. Falls back to a conservative 30s if the brain's
    # settings don't expose the value.
    _DEFAULT_APPROVAL_TIMEOUT_S = 30

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

        # TIER_1: consult the confidence gate first. If the tool has a strong
        # success/approval history the user can be skipped; otherwise fall
        # through to the explicit approval path. Higher tiers always ask.
        if risk_tier == RiskTier.TIER_1:
            confidence_gate = getattr(self.brain, "confidence_gate", None)
            outcome_tracker = getattr(self.brain, "outcome_tracker", None)
            if confidence_gate is not None:
                try:
                    if confidence_gate.should_auto_approve(
                        action=action,
                        args=args,
                        risk_tier=risk_tier,
                        outcome_tracker=outcome_tracker,
                    ):
                        logger.info(
                            f"confidence_auto_approved | action={action} | tier={risk_tier}"
                        )
                        return True
                except Exception as e:
                    logger.debug(f"confidence_gate_eval_failed | {e}")

        return await self._ask_approval(action, args, risk_tier)

    def _approval_timeout_s(self) -> float:
        """Resolve the approval timeout from the brain's security settings."""
        try:
            return float(getattr(self.brain, "settings", {}).security.tier_2_countdown)
        except Exception:
            return self._DEFAULT_APPROVAL_TIMEOUT_S

    async def _ask_approval(self, action: str, args: dict, risk_tier: RiskTier) -> bool:
        """Ask the user for approval via dashboard or Telegram.

        Blocks on ``brain.confirmation_event`` for the configured tier-2
        countdown window, then returns the user's verdict (default: deny
        on timeout). Earlier versions pushed the request and returned
        immediately, so callers could never get a synchronous answer.
        """
        label = self.get_risk_label(risk_tier)
        msg = f"[{label}] Approve: {action}({args})?"

        # Surface the request to the dashboard and the Telegram bridge.
        self.brain._safe_put(
            self.brain.status_q,
            {
                "type": "CONFIRM_REQUIRED",
                "content": {"action": action, "args": args, "risk": label, "message": msg},
            },
        )
        self.brain._safe_put(
            self.brain.telegram_q,
            {
                "type": "CHAT_MSG",
                "speaker": "CHARLIE",
                "content": msg,
            },
        )

        # Wait for the user to confirm. If the brain hasn't set up the
        # confirmation event (init order issue, headless test, etc.) or the
        # event is missing, we fail closed.
        event = getattr(self.brain, "confirmation_event", None)
        if event is None:
            logger.warning(f"approval_no_event | action={action} | defaulting to deny")
            return False

        timeout = self._approval_timeout_s()
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.info(f"approval_timeout | action={action} | tier={risk_tier} | denying")
            return False

        # Event fired. The reactor sets confirmation_result before set();
        # read it, then clear so the next call doesn't see a stale value.
        result = bool(getattr(self.brain, "confirmation_result", False))
        try:
            event.clear()
        except Exception:
            pass
        self.brain.confirmation_result = None
        logger.info(f"approval_resolved | action={action} | approved={result}")
        return result

    @staticmethod
    def get_risk_label(tier: RiskTier) -> str:
        """Get human-readable risk label."""
        return {
            RiskTier.TIER_0: "AUTO",
            RiskTier.TIER_1: "APPROVAL",
            RiskTier.TIER_2: "CONFIRM",
            RiskTier.TIER_3: "DESTRUCTIVE",
        }.get(tier, "UNKNOWN")
