import pytest

from charlie.calendar_scheduler import deliver_due_reminders
from charlie.calendar_store import CalendarStore


@pytest.mark.asyncio
async def test_due_reminder_is_delivered_once(tmp_path):
    store = CalendarStore(str(tmp_path / "calendar.sqlite3"))
    event = store.create_event("Standup", "2026-08-20T09:00:00Z", reminder_at="2026-08-20T08:55:00Z")
    delivered = []

    await deliver_due_reminders(store, "2026-08-20T09:00:00Z", delivered.append)
    await deliver_due_reminders(store, "2026-08-20T09:01:00Z", delivered.append)

    assert [item["id"] for item in delivered] == [event["id"]]
