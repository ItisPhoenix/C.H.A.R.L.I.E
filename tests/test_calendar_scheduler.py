import pytest

from charlie.calendar_scheduler import _delivery_targets, deliver_due_reminders
from charlie.calendar_store import CalendarStore


def test_delivery_target_resolution_never_falls_back_to_external_channels():
    callbacks = {name: (lambda _item: None) for name in ("alert", "voice", "telegram", "console")}
    assert [name for name, _ in _delivery_targets("voice", callbacks)] == ["voice"]
    assert [name for name, _ in _delivery_targets("web", callbacks)] == ["alert"]
    assert [name for name, _ in _delivery_targets("telegram", callbacks)] == ["telegram"]
    assert [name for name, _ in _delivery_targets("console", callbacks)] == ["console"]
    assert [name for name, _ in _delivery_targets("local", callbacks)] == ["alert"]
    assert _delivery_targets("telegram", {"telegram": None}) == []


@pytest.mark.asyncio
async def test_due_reminder_is_delivered_once(tmp_path):
    store = CalendarStore(str(tmp_path / "calendar.sqlite3"))
    event = store.create_event("Standup", "2026-08-20T09:00:00Z", reminder_at="2026-08-20T08:55:00Z")
    delivered = []

    await deliver_due_reminders(store, "2026-08-20T09:00:00Z", delivered.append)
    await deliver_due_reminders(store, "2026-08-20T09:01:00Z", delivered.append)

    assert [item["id"] for item in delivered] == [event["id"]]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("channel", "expected"),
    [("voice", ["voice"]), ("web", ["alert"]), ("telegram", ["telegram"]), ("console", ["console"])],
)
async def test_calendar_reminder_only_uses_its_delivery_channel(tmp_path, channel, expected):
    store = CalendarStore(str(tmp_path / "calendar.sqlite3"))
    event = store.create_event(
        "Origin test", "2026-08-20T09:00:00Z", reminder_at="2026-08-20T08:55:00Z",
        delivery_channel=channel,
    )
    callbacks = []

    await deliver_due_reminders(
        store,
        "2026-08-20T09:00:00Z",
        alert_callback=lambda _event: callbacks.append("alert"),
        voice_callback=lambda _event: callbacks.append("voice"),
        telegram_callback=lambda _event: callbacks.append("telegram"),
        console_callback=lambda _event: callbacks.append("console"),
    )

    assert callbacks == expected
    assert store.get_event(event["id"])["reminder_state"] == "delivered"
    store.close()


@pytest.mark.asyncio
async def test_voice_reminder_is_not_marked_delivered_when_speech_was_not_queued(tmp_path):
    store = CalendarStore(str(tmp_path / "calendar.sqlite3"))
    event = store.create_event(
        "Voice acceptance", "2026-08-20T09:00:00Z", reminder_at="2026-08-20T08:55:00Z",
        delivery_channel="voice",
    )

    delivered = await deliver_due_reminders(
        store,
        "2026-08-20T09:00:00Z",
        voice_callback=lambda _event: False,
    )

    saved = store.get_event(event["id"])
    assert delivered == 0
    assert saved["reminder_state"] == "pending"
    assert "did not accept" in saved["reminder_last_error"]
    store.close()


@pytest.mark.asyncio
async def test_legacy_calendar_reminder_fallback_uses_local_alert_only(tmp_path):
    store = CalendarStore(str(tmp_path / "calendar.sqlite3"))
    store.create_event("Legacy", "2026-08-20T09:00:00Z", reminder_at="2026-08-20T08:55:00Z")
    callbacks = []

    await deliver_due_reminders(
        store,
        "2026-08-20T09:00:00Z",
        alert_callback=lambda _event: callbacks.append("alert"),
        voice_callback=lambda _event: callbacks.append("voice"),
        telegram_callback=lambda _event: callbacks.append("telegram"),
    )

    assert callbacks == ["alert"]
    store.close()
