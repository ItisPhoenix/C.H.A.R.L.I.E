from datetime import datetime, timezone

import pytest

from charlie.calendar_runtime import CalendarRuntime
from charlie.timer_service import cancel_timer, create_timer


def test_timer_uses_durable_calendar_automation_and_can_be_cancelled(tmp_path):
    runtime = CalendarRuntime(str(tmp_path / "calendar.db"))
    try:
        timer = create_timer(90, "Tea is ready", runtime=runtime)
        assert timer["kind"] == "reminder"
        assert timer["text"] == "Tea is ready"
        assert timer["recurrence"] == "once"
        assert timer["status"] == "active"
        assert timer["delivery_channel"] == "local"
        due = datetime.fromisoformat(timer["first_run_at"].replace("Z", "+00:00"))
        assert 85 <= (due - datetime.now(timezone.utc)).total_seconds() <= 95

        cancelled = cancel_timer(timer["id"], runtime=runtime)
        assert cancelled["status"] == "cancelled"
    finally:
        runtime.close()


@pytest.mark.parametrize("delay", [0, -1, float("inf"), True, "5"])
def test_timer_rejects_invalid_delay(delay):
    with pytest.raises(ValueError):
        create_timer(delay, runtime=object())


def test_timer_persists_originating_delivery_channel(tmp_path):
    runtime = CalendarRuntime(str(tmp_path / "calendar.db"))
    try:
        timer = create_timer(60, runtime=runtime, delivery_channel="voice")
        assert timer["delivery_channel"] == "voice"
    finally:
        runtime.close()
