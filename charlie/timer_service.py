"""Small facade for durable one-shot timers on Charlie's automation store."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

from charlie.calendar_runtime import CalendarRuntime, calendar_runtime_required
from charlie.calendar_store import normalize_delivery_channel


def create_timer(
    delay_seconds: float,
    text: str = "Timer finished",
    *,
    timezone_name: str = "Asia/Kolkata",
    delivery_channel: str = "local",
    runtime: CalendarRuntime | None = None,
) -> dict[str, Any]:
    """Create a durable one-shot reminder due after delay_seconds."""
    if isinstance(delay_seconds, bool) or not isinstance(delay_seconds, (int, float)):
        raise ValueError("delay_seconds must be a positive number")
    if not math.isfinite(delay_seconds) or delay_seconds <= 0:
        raise ValueError("delay_seconds must be a positive number")
    channel = normalize_delivery_channel(delivery_channel)
    due_at = (datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)).isoformat()
    return (runtime or calendar_runtime_required()).execute_sync(
        "create_automation", "reminder", text, due_at, "once",
        timezone_name=timezone_name, delivery_channel=channel,
    )


def cancel_timer(schedule_id: str, *, runtime: CalendarRuntime | None = None) -> dict[str, Any]:
    """Cancel a timer by its canonical automation schedule ID."""
    return (runtime or calendar_runtime_required()).execute_sync("cancel_automation", schedule_id)
