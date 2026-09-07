from __future__ import annotations

import asyncio
import sqlite3

import pytest

from charlie import capabilities, tools, web_server
from charlie.calendar_runtime import CalendarRuntime, canonical_calendar_request_fingerprint
from charlie.calendar_scheduler import deliver_due_reminders
from charlie.calendar_store import CalendarStore


def test_calendar_timestamps_normalize_and_reject_naive(tmp_path):
    store = CalendarStore(str(tmp_path / "calendar.sqlite3"))
    event = store.create_event(
        "Offset",
        "2026-08-20T09:00:00+05:30",
        reminder_at="2026-08-20T08:45:00+05:30",
    )
    assert event["start_at"] == "2026-08-20T03:30:00.000000Z"
    assert event["reminder_at"] == "2026-08-20T03:15:00.000000Z"
    with pytest.raises(ValueError):
        store.create_event("Naive", "2026-08-20T09:00:00")
    store.close()


def test_calendar_legacy_rows_migrate_reminder_state(tmp_path):
    path = str(tmp_path / "legacy.sqlite3")
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE calendar_events (id TEXT PRIMARY KEY, title TEXT NOT NULL, start_at TEXT NOT NULL, "
        "end_at TEXT, reminder_at TEXT, completed INTEGER NOT NULL DEFAULT 0)"
    )
    connection.execute(
        "INSERT INTO calendar_events VALUES (?, ?, ?, ?, ?, ?)",
        ("pending", "Pending", "2026-08-20T09:00:00Z", None, "2026-08-20T08:55:00Z", 0),
    )
    connection.execute(
        "INSERT INTO calendar_events VALUES (?, ?, ?, ?, ?, ?)",
        ("delivered", "Delivered", "2026-08-20T09:00:00Z", None, "2026-08-20T08:55:00Z", 1),
    )
    connection.commit()
    connection.close()

    store = CalendarStore(path)
    rows = {row["id"]: row for row in store.list_events()}
    assert rows["pending"]["reminder_state"] == "pending"
    assert rows["delivered"]["reminder_state"] == "delivered"
    store.close()


@pytest.mark.asyncio
async def test_calendar_runtime_serializes_store_access(tmp_path):
    runtime = CalendarRuntime(str(tmp_path / "calendar.sqlite3"))
    try:
        created = await runtime.execute(
            "create_event", "Standup", "2026-08-20T09:00:00Z", reminder_at="2026-08-20T08:55:00Z"
        )
        rows = await asyncio.gather(
            runtime.execute("get_event", created["id"]),
            runtime.execute("list_events", None),
        )
        assert rows[0]["id"] == created["id"]
        assert rows[1][0]["id"] == created["id"]
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_atomic_claim_allows_only_one_owner(tmp_path):
    runtime = CalendarRuntime(str(tmp_path / "calendar.sqlite3"))
    try:
        event = await runtime.execute(
            "create_event", "Standup", "2026-08-20T09:00:00Z", reminder_at="2026-08-20T08:55:00Z"
        )
        claims = await asyncio.gather(
            runtime.execute("claim_due_reminder", "2026-08-20T09:00:00Z"),
            runtime.execute("claim_due_reminder", "2026-08-20T09:00:00Z"),
        )
        owned = [claim for claim in claims if claim is not None]
        assert len(owned) == 1
        assert owned[0]["id"] == event["id"]
    finally:
        runtime.close()


def test_stale_claim_completion_is_safe_after_delete_or_update(tmp_path):
    store = CalendarStore(str(tmp_path / "calendar.sqlite3"))
    event = store.create_event("Standup", "2026-08-20T09:00:00Z", reminder_at="2026-08-20T08:55:00Z")
    claim = store.claim_due_reminder("2026-08-20T09:00:00Z")
    assert claim is not None
    store.update_event(event["id"], {"title": "Changed"})
    assert store.finalize_reminder_claim(
        event["id"], claim["reminder_claim_token"], claim["reminder_revision"], delivered=True
    ) is False
    second = store.create_event("Delete", "2026-08-20T09:00:00Z", reminder_at="2026-08-20T08:55:00Z")
    second_claim = store.claim_due_reminder("2026-08-20T09:00:00Z")
    assert second_claim is not None
    store.delete_event(second["id"])
    assert store.finalize_reminder_claim(
        second["id"], second_claim["reminder_claim_token"], second_claim["reminder_revision"], delivered=True
    ) is False
    store.close()


@pytest.mark.asyncio
async def test_scheduler_requeues_failed_callback_and_continues(tmp_path):
    runtime = CalendarRuntime(str(tmp_path / "calendar.sqlite3"))
    try:
        event = await runtime.execute(
            "create_event", "Failure", "2026-08-20T09:00:00Z", reminder_at="2026-08-20T08:55:00Z"
        )
        calls = []

        async def fail_once(_event):
            calls.append(_event["id"])
            raise RuntimeError("voice unavailable")

        assert await deliver_due_reminders(runtime, "2026-08-20T09:00:00Z", callback=fail_once) == 0
        row = await runtime.execute("get_event", event["id"])
        assert row["reminder_state"] == "pending"
        assert row["reminder_attempt_count"] == 1
        assert "voice unavailable" in row["reminder_last_error"]
        assert calls == [event["id"]]
    finally:
        runtime.close()


def test_calendar_tools_and_media_are_separate_authorities():
    media = capabilities.get_capability_index().get_operation("media_control")
    calendar = capabilities.get_capability_index().get_operation("calendar_create")
    assert media is not None and calendar is not None
    assert capabilities.get_capability_index().get_operation_domain("calendar_create") == "calendar"
    assert calendar.required_leases == ("calendar",)
    assert "calendar_create" in tools.registry.get_tool_names()
    assert "system_control" not in tools.registry.get_tool_names()


@pytest.mark.asyncio
async def test_web_calendar_crud_forwards_to_main(monkeypatch):
    sent = []

    class Bus:
        async def send_command(self, command):
            sent.append(command)
            web_server._resolve_calendar_operation_result(
                {
                    "request_id": command["payload"]["request_id"],
                    "request_fingerprint": command["payload"]["request_fingerprint"],
                    "operation": "create",
                    "status": "completed",
                    "result": {"data": {"structured_data": {"id": "event-1", "title": "Standup"}}},
                }
            )
            return True

    monkeypatch.setattr(web_server, "event_bus", Bus())
    result = await web_server.create_calendar_event(
        {"title": "Standup", "start_at": "2026-08-20T09:00:00Z", "request_id": "cal-1"}
    )
    assert result == {"id": "event-1", "title": "Standup"}
    assert sent[0]["type"] == "calendar_operation"
    assert sent[0]["payload"]["operation"] == "create"


def test_main_calendar_result_contract_uses_fingerprint():
    assert canonical_calendar_request_fingerprint("delete", {"event_id": "e1"}) == (
        '{"event_id":"e1","operation":"delete"}'
    )
