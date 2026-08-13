from charlie.calendar_store import CalendarStore


def test_calendar_events_survive_store_reopen(tmp_path):
    path = str(tmp_path / "calendar.sqlite3")
    first = CalendarStore(path)
    created = first.create_event("Dentist", "2026-08-20T09:00:00+05:30", reminder_at="2026-08-20T08:45:00+05:30")
    first.close()

    second = CalendarStore(path)
    events = second.list_events("2026-08-20")

    assert events[0]["id"] == created["id"]
    assert events[0]["title"] == "Dentist"
    assert events[0]["reminder_at"] == "2026-08-20T08:45:00+05:30"
    second.close()
