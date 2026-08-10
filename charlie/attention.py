"""Deterministic attention scoring (Phase 4, plan section "Phase 4 -- Autonomy policy + attention engine").

No LLM call anywhere in this module, ever -- must work at full speed with
the cloud LLM unreachable (see CLAUDE.md section 6). decide() is a pure
function; a caller owns the cooldowns dict across calls (same explicit-state
style as charlie/monitors.py's evaluate_sample()), so this module has no
hidden module-level mutable state to reset between tests or processes.
"""

import time
from enum import IntEnum
from typing import Any, Dict, Optional, Tuple

from charlie.events import EventType

# Dedupe window for repeated non-approval nudges -- shorter than monitors.py's 1800s, cheaper to repeat.
_COOLDOWN_S = 60.0


class AttentionLevel(IntEnum):
    SILENT = 0
    PASSIVE = 1
    INFORM = 2
    ATTENTION = 3
    INTERRUPT = 4


# Event types that always demand a decision, regardless of context.
_BASE_LEVEL: Dict[EventType, AttentionLevel] = {
    EventType.TOOL_APPROVAL_REQUEST: AttentionLevel.INTERRUPT,
    EventType.EXTENSION_PENDING: AttentionLevel.INTERRUPT,
    EventType.RECOVERY_PROPOSAL: AttentionLevel.INTERRUPT,
}

_ALERT_SEVERITY_TO_LEVEL: Dict[str, AttentionLevel] = {
    "error": AttentionLevel.ATTENTION,
    "warning": AttentionLevel.INFORM,
}

_BACKGROUND_TASK_STATUS_TO_LEVEL: Dict[str, AttentionLevel] = {
    "planning": AttentionLevel.SILENT,
    "running": AttentionLevel.SILENT,
    "paused": AttentionLevel.INFORM,
    "done": AttentionLevel.INFORM,
    "cancelled": AttentionLevel.INFORM,
    "failed": AttentionLevel.ATTENTION,
}


def _base_level(event: Dict[str, Any]) -> Tuple[AttentionLevel, str]:
    etype = event.get("type")
    payload = event.get("payload") or {}

    if etype == EventType.ALERT:
        severity = payload.get("severity")
        level = _ALERT_SEVERITY_TO_LEVEL.get(severity, AttentionLevel.PASSIVE)
        return level, payload.get("message") or f"alert ({severity or 'unknown'} severity)"

    if etype == EventType.BACKGROUND_TASK:
        status = payload.get("status")
        level = _BACKGROUND_TASK_STATUS_TO_LEVEL.get(status, AttentionLevel.SILENT)
        return level, f"background task {status or 'unknown'}"

    try:
        level = _BASE_LEVEL.get(EventType(etype), AttentionLevel.SILENT)
    except ValueError:
        level = AttentionLevel.SILENT
    return (level, f"{etype} requires a decision") if level == AttentionLevel.INTERRUPT else (level, "")


def decide(
    event: Dict[str, Any],
    ctx: Optional[Any] = None,
    cooldowns: Optional[Dict[str, float]] = None,
    now: Optional[float] = None,
) -> Tuple[AttentionLevel, str]:
    """Attention level + rationale for one event.

    ctx.focus_mode (Phase 3's UserContext) caps any non-INTERRUPT level at INFORM.
    cooldowns, if passed, dedupes repeats of the same event+reason within
    _COOLDOWN_S -- never applied to INTERRUPT, so an approval is never stranded.
    """
    level, reason = _base_level(event)

    if ctx is not None and getattr(ctx, "focus_mode", False) and level != AttentionLevel.INTERRUPT:
        level = min(level, AttentionLevel.INFORM)

    if cooldowns is not None and level not in (AttentionLevel.SILENT, AttentionLevel.INTERRUPT):
        now = now if now is not None else time.monotonic()
        key = f"{event.get('type')}:{reason}"
        last = cooldowns.get(key, float("-inf"))
        if now - last < _COOLDOWN_S:
            return AttentionLevel.SILENT, ""
        cooldowns[key] = now

    return level, reason
