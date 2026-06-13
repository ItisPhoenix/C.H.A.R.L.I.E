"""
ToolExecutor — single-responsibility handler for tool call lifecycle.

Extracted from Brain.execute_tools. Owns the tool execution pipeline:
guardian verification -> confirmation gate -> execute -> record outcome.

The Brain's `execute_tools` method is now a thin delegation:
    def execute_tools(self, tool_data, ...):
        return self.tool_executor.execute(tool_data, ...)
"""

from __future__ import annotations

import time

from charlie.utils.logger import get_logger

logger = get_logger(__name__)


class ToolExecutor:
    """Owns the lifecycle of a single tool call.

    Steps:
    1. Resolve tool entry from the registry
    2. Guardian verify (unless skip_guardian=True)
    3. Handle tier-based confirmation (return CONFIRMATION_PENDING sentinel)
    4. Execute via registry
    5. Record outcome (success/failure, elapsed_ms, tier, decision)

    The executor takes a Brain-like object in its constructor and reads
    the registry, guardian, outcome_tracker, status_q, tts_q, telegram_q,
    and tool_execution_lock from it. This keeps the executor stateless
    and tightly bound to the owning brain.
    """

    def __init__(self, brain) -> None:
        self._brain = brain

    def execute(
        self,
        tool_data: dict,
        sir_input: str = "",
        source: str = "local",
        skip_guardian: bool = False,
    ) -> str:
        """Execute a tool call through the unified catalog.

        Returns the tool's result string, or a CONFIRMATION_PENDING sentinel
        if a tier-2+ tool needs user confirmation, or an error message if
        the tool is unknown or cancelled.
        """
        brain = self._brain
        tool_name = tool_data.get("tool", "") if tool_data else ""
        args = tool_data.get("args", {}) if tool_data else {}

        def _record_decision(decision: str, outcome: str | None = None, tier=None) -> None:
            tracker = getattr(brain, "outcome_tracker", None)
            if tracker is None or not hasattr(tracker, "record_tool_decision"):
                return
            try:
                tracker.record_tool_decision(tool_name, tier, decision, outcome=outcome)
            except Exception as e:  # pragma: no cover - logging only
                logger.debug("record_tool_decision_failed | tool=%s | %s", tool_name, e)

        def _record_outcome(success: bool, elapsed_ms: float) -> None:
            tracker = getattr(brain, "outcome_tracker", None)
            if tracker is None or not hasattr(tracker, "record_tool"):
                return
            try:
                tracker.record_tool(
                    tool_name,
                    success=success,
                    details={"elapsed_ms": round(elapsed_ms, 1), "source": source},
                )
            except Exception as e:  # pragma: no cover - logging only
                logger.debug("record_tool_failed | tool=%s | %s", tool_name, e)

        with brain.tool_execution_lock:
            # Authoritative path: resolve every tool via the unified catalog.
            if not tool_name or not hasattr(brain, "tool_registry"):
                return f"Error: Tool '{tool_name}' not found."

            entry = brain.tool_registry.get(tool_name)
            if not entry:
                return f"Error: Tool '{tool_name}' not found."

            tool_func = entry.handler
            catalog_tier = brain.tool_registry.get_tier(tool_name)

            # Guardian verification using the catalog's authoritative tier
            if not skip_guardian:
                from charlie.security.tiers import CONFIRMATION_PENDING, RiskTier

                allowed, reason = brain.guardian.verify_tool(
                    tool_name, args, sir_input, tool_func=tool_func, tier=catalog_tier
                )

                if catalog_tier == RiskTier.TIER_0:
                    allowed = True

                if allowed == CONFIRMATION_PENDING:
                    brain.awaiting_confirmation = {
                        "tool": tool_name,
                        "args": args,
                        "sir_input": sir_input,
                        "tier": catalog_tier,
                        "source": source,
                    }
                    brain.last_confirmation_time = time.time()
                    if brain.confirmation_event:
                        brain.loop.call_soon_threadsafe(brain.confirmation_event.clear)

                    payload = {
                        "type": "CONFIRM_REQUIRED",
                        "content": {
                            "desc": reason,
                            "tier": catalog_tier.value,
                        },
                    }

                    if source == "local" or source == "all":
                        brain._safe_put(brain.status_q, payload)
                        brain._safe_put(brain.tts_q, {"type": "SPEAK", "content": reason})

                    if (source.startswith("telegram") or source == "all") and brain.telegram_q:
                        brain._safe_put(brain.telegram_q, payload)

                    _record_decision("gated", tier=catalog_tier)
                    return CONFIRMATION_PENDING

                if not allowed:
                    _record_decision("cancelled", tier=catalog_tier)
                    return reason

            # Execute through the catalog, timing and recording outcome.
            start = time.perf_counter()
            result = brain.tool_registry.execute(tool_name, args)
            elapsed_ms = (time.perf_counter() - start) * 1000
            success = not str(result).startswith("Error")
            _record_outcome(success, elapsed_ms)
            _record_decision(
                "executed",
                outcome="success" if success else "failure",
                tier=catalog_tier,
            )
            return result
