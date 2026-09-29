from __future__ import annotations

import asyncio
import sqlite3

import pytest

from charlie import capabilities, tools
from charlie.calendar_runtime import CalendarRuntime, canonical_calendar_request_fingerprint
from charlie.calendar_scheduler import deliver_due_automation_reminders, deliver_due_reminders
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


def test_main_calendar_result_contract_uses_fingerprint():
    assert canonical_calendar_request_fingerprint("delete", {"event_id": "e1"}) == (
        '{"event_id":"e1","operation":"delete"}'
    )


@pytest.mark.asyncio
async def test_automation_schedule_crud_persists_through_calendar_runtime(tmp_path):
    path = str(tmp_path / "calendar.sqlite3")
    runtime = CalendarRuntime(path)
    created = await runtime.execute(
        "create_automation",
        "reminder",
        "Take medication",
        "2026-09-24T09:00:00+05:30",
        "daily",
    )
    assert created["first_run_at"] == "2026-09-24T03:30:00.000000Z"
    assert created["next_run_at"] == created["first_run_at"]
    assert created["timezone"] == "Asia/Kolkata"

    updated = await runtime.execute(
        "update_automation", created["id"], {"text": "Take vitamins", "recurrence": "weekly"}
    )
    assert updated["text"] == "Take vitamins"
    assert updated["recurrence"] == "weekly"
    assert updated["revision"] == created["revision"] + 1
    assert (await runtime.execute("get_automation", created["id"]))["id"] == created["id"]
    assert [row["id"] for row in await runtime.execute("list_automations")] == [created["id"]]
    runtime.close()

    reopened = CalendarRuntime(path)
    try:
        persisted = await reopened.execute("get_automation", created["id"])
        assert persisted["text"] == "Take vitamins"
        cancelled = await reopened.execute("cancel_automation", created["id"])
        assert cancelled["status"] == "cancelled"
        assert (await reopened.execute("get_automation", created["id"]))["status"] == "cancelled"
    finally:
        reopened.close()


def test_automation_schedule_timezone_validation_and_local_recurrence(tmp_path):
    from charlie.calendar_store import next_automation_occurrence

    store = CalendarStore(str(tmp_path / "calendar.sqlite3"))
    daily = store.create_automation(
        "task", "DST daily", "2026-03-07T09:00:00-05:00", "daily", "America/New_York"
    )
    assert daily["first_run_at"] == "2026-03-07T14:00:00.000000Z"
    assert next_automation_occurrence(daily["first_run_at"], "daily", daily["timezone"]) == (
        "2026-03-08T13:00:00.000000Z"
    )
    assert next_automation_occurrence(daily["first_run_at"], "weekly", daily["timezone"]) == (
        "2026-03-14T13:00:00.000000Z"
    )
    assert next_automation_occurrence(daily["first_run_at"], "once", daily["timezone"]) is None
    gap = store.create_automation(
        "task", "DST gap", "2026-03-07T02:30:00-05:00", "daily", "America/New_York"
    )
    assert next_automation_occurrence(gap["first_run_at"], "daily", gap["timezone"]) == (
        "2026-03-08T07:00:00.000000Z"
    )
    fold = store.create_automation(
        "task", "DST fold", "2026-10-31T01:30:00-04:00", "daily", "America/New_York"
    )
    assert next_automation_occurrence(fold["first_run_at"], "daily", fold["timezone"]) == (
        "2026-11-01T05:30:00.000000Z"
    )

    store._connection.execute(
        "UPDATE automation_schedules SET last_run_at = ?, next_run_at = ? WHERE id = ?",
        ("2026-03-08T13:00:00.000000Z", "2026-03-09T13:00:00.000000Z", daily["id"]),
    )
    store._connection.commit()
    weekly = store.update_automation(daily["id"], {"recurrence": "weekly"})
    assert weekly["next_run_at"] == "2026-03-14T13:00:00.000000Z"
    local_zone = store.update_automation(daily["id"], {"timezone": "Asia/Kolkata"})
    assert local_zone["next_run_at"] == "2026-03-14T14:00:00.000000Z"
    with pytest.raises(ValueError, match="first_run_at"):
        store.create_automation("reminder", "Naive", "2026-03-07T09:00:00", "once")
    with pytest.raises(ValueError, match="timezone"):
        store.create_automation("reminder", "Invalid zone", "2026-03-07T09:00:00Z", "once", "Not/AZone")
    with pytest.raises(ValueError, match="offset.*Asia/Kolkata"):
        store.create_automation(
            "reminder", "Wrong offset", "2026-09-28T18:19:00-05:00", "once", "Asia/Kolkata"
        )
    with pytest.raises(ValueError, match="recurrence"):
        store.create_automation("reminder", "Invalid recurrence", "2026-03-07T09:00:00Z", "monthly")
    store.close()


def test_automation_update_and_cancel_invalidate_existing_claims(tmp_path):
    store = CalendarStore(str(tmp_path / "calendar.sqlite3"))
    schedule = store.create_automation(
        "reminder", "Claimed", "2026-09-24T09:00:00+05:30", "daily"
    )
    store._connection.execute(
        "UPDATE automation_schedules SET claim_token = ?, claimed_at = ? WHERE id = ?",
        ("claim-update", "2026-09-24T03:30:00.000000Z", schedule["id"]),
    )
    store._connection.commit()
    updated = store.update_automation(schedule["id"], {"text": "Updated while claimed"})
    assert updated["revision"] == schedule["revision"] + 1
    assert updated["claim_token"] is None
    assert updated["claimed_at"] is None

    store._connection.execute(
        "UPDATE automation_schedules SET claim_token = ?, claimed_at = ? WHERE id = ?",
        ("claim-cancel", "2026-09-24T03:30:00.000000Z", schedule["id"]),
    )
    store._connection.commit()
    cancelled = store.cancel_automation(schedule["id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["revision"] == updated["revision"] + 1
    assert cancelled["claim_token"] is None
    assert cancelled["claimed_at"] is None
    with pytest.raises(ValueError, match="cannot update cancelled"):
        store.update_automation(schedule["id"], {"text": "late update"})
    assert store.cancel_automation(schedule["id"])["text"] == "Updated while claimed"

    completed = store.create_automation(
        "task", "One shot", "2026-09-24T09:00:00+05:30", "once"
    )
    store._connection.execute(
        "UPDATE automation_schedules SET status = 'completed', last_run_at = ? WHERE id = ?",
        (completed["first_run_at"], completed["id"]),
    )
    store._connection.commit()
    with pytest.raises(ValueError, match="cannot update completed"):
        store.update_automation(completed["id"], {"text": "late update"})
    assert store.cancel_automation(completed["id"])["status"] == "completed"
    store.close()


def test_automation_claim_reconcile_and_revision_guard(tmp_path):
    store = CalendarStore(str(tmp_path / "calendar.sqlite3"))
    schedule = store.create_automation(
        "task", "Recover me", "2026-09-24T09:00:00Z", "once", "UTC"
    )
    claim = store.claim_due_automation("2026-09-24T09:00:00Z")
    assert claim is not None
    assert claim["active_run_id"] and claim["active_task_id"]
    assert store.claim_due_automation("2026-09-24T09:00:00Z") is None
    assert store.reconcile_automation_claims() == 1
    recovered = store.get_automation(schedule["id"])
    assert recovered["active_run_status"] == "interrupted"
    # Restart reconciliation is fail-closed by default; the main dispatcher may
    # opt in only after it confirms TaskJournal has no record for this stable ID.
    assert store.claim_due_automation("2026-09-24T09:00:00Z") is None
    retried = store.claim_due_automation("2026-09-24T09:00:00Z", retry_interrupted=True)
    assert retried is not None
    assert retried["active_run_id"] == claim["active_run_id"]
    assert retried["active_task_id"] == claim["active_task_id"]
    assert store.finalize_automation_claim(
        schedule["id"], claim["claim_token"], claim["revision"], succeeded=True
    ) is False
    updated = store.update_automation(schedule["id"], {"text": "Changed"})
    assert store.finalize_automation_claim(
        schedule["id"], retried["claim_token"], retried["revision"], succeeded=True
    ) is False
    assert updated["status"] == "active"
    store.close()


def test_automation_finalize_once_and_coalesces_missed_recurrence(tmp_path):
    store = CalendarStore(str(tmp_path / "calendar.sqlite3"))
    once = store.create_automation(
        "task", "Once", "2026-09-24T09:00:00Z", "once", "UTC"
    )
    claim = store.claim_due_automation("2026-09-24T10:00:00Z")
    assert claim is not None and claim["id"] == once["id"]
    assert store.start_automation_run(once["id"], claim["claim_token"], claim["revision"])
    assert store.finalize_automation_claim(
        once["id"], claim["claim_token"], claim["revision"], succeeded=True,
        result="completed", completed_at="2026-09-24T10:00:00Z",
    ) is True
    assert store.get_automation(once["id"])["status"] == "completed"

    daily = store.create_automation(
        "reminder", "Daily", "2026-09-24T09:00:00Z", "daily", "UTC"
    )
    due = store.claim_due_automation("2026-09-28T12:00:00Z")
    assert due is not None and due["id"] == daily["id"]
    stable_run = due["active_run_id"]
    assert store.start_automation_run(daily["id"], due["claim_token"], due["revision"])
    assert store.finalize_automation_claim(
        daily["id"], due["claim_token"], due["revision"], succeeded=False,
        error="temporary failure", completed_at="2026-09-28T12:00:00Z",
    ) is True
    failed = store.get_automation(daily["id"])
    assert failed["next_run_at"] == "2026-09-29T09:00:00.000000Z"
    assert failed["active_run_status"] == "failed"
    assert failed["last_run_status"] == "failed"
    assert store.claim_due_automation("2026-09-28T12:00:00Z") is None
    next_occurrence = store.claim_due_automation("2026-09-29T09:00:00Z")
    assert next_occurrence is not None
    assert next_occurrence["active_run_id"] != stable_run
    assert store.start_automation_run(
        daily["id"], next_occurrence["claim_token"], next_occurrence["revision"]
    )
    assert store.finalize_automation_claim(
        daily["id"], next_occurrence["claim_token"], next_occurrence["revision"], succeeded=True,
        result="sent", completed_at="2026-09-29T09:00:00Z",
    ) is True
    finished = store.get_automation(daily["id"])
    assert finished["next_run_at"] == "2026-09-30T09:00:00.000000Z"

    failed_once = store.create_automation(
        "task", "Failed once", "2026-09-24T09:00:00Z", "once", "UTC"
    )
    one_shot = store.claim_due_automation("2026-09-24T09:00:00Z")
    assert one_shot is not None and one_shot["id"] == failed_once["id"]
    assert store.start_automation_run(
        failed_once["id"], one_shot["claim_token"], one_shot["revision"]
    )
    assert store.finalize_automation_claim(
        failed_once["id"], one_shot["claim_token"], one_shot["revision"], succeeded=False,
        error="permanent failure", completed_at="2026-09-24T09:01:00Z",
    ) is True
    final_once = store.get_automation(failed_once["id"])
    assert final_once["status"] == "completed"
    assert final_once["last_run_status"] == "failed"
    assert store.claim_due_automation("2026-09-25T00:00:00Z") is None
    store.close()


def test_automation_update_and_cancel_respect_started_runs(tmp_path):
    store = CalendarStore(str(tmp_path / "calendar.sqlite3"))
    update_claimed = store.create_automation(
        "task", "Claimed", "2026-09-24T09:00:00Z", "daily", "UTC"
    )
    claim = store.claim_due_automation("2026-09-24T09:00:00Z")
    assert claim is not None and claim["id"] == update_claimed["id"]
    changed = store.update_automation(update_claimed["id"], {"text": "Updated before start"})
    assert changed["claim_token"] is None
    assert changed["active_run_status"] == "invalidated"
    assert not store.start_automation_run(update_claimed["id"], claim["claim_token"], claim["revision"])
    store.cancel_automation(update_claimed["id"])

    update_running = store.create_automation(
        "task", "Running", "2026-09-24T09:00:00Z", "daily", "UTC"
    )
    running = store.claim_due_automation("2026-09-24T09:00:00Z")
    assert running is not None and running["id"] == update_running["id"]
    assert store.start_automation_run(update_running["id"], running["claim_token"], running["revision"])
    changed_running = store.update_automation(
        update_running["id"], {"first_run_at": "2026-09-26T09:00:00Z"}
    )
    assert changed_running["claim_token"] == running["claim_token"]
    assert changed_running["active_run_status"] == "running"
    assert store.claim_due_automation("2026-09-24T09:00:00Z") is None
    assert store.finalize_automation_claim(
        update_running["id"], running["claim_token"], running["revision"], succeeded=True,
        completed_at="2026-09-24T10:00:00Z",
    )
    assert store.get_automation(update_running["id"])["next_run_at"] == "2026-09-26T09:00:00.000000Z"

    cancel_claimed = store.create_automation(
        "task", "Cancel before start", "2026-09-24T09:00:00Z", "once", "UTC"
    )
    pending = store.claim_due_automation("2026-09-24T09:00:00Z")
    assert pending is not None and pending["id"] == cancel_claimed["id"]
    cancelled = store.cancel_automation(cancel_claimed["id"])
    assert cancelled["claim_token"] is None
    assert not store.start_automation_run(cancel_claimed["id"], pending["claim_token"], pending["revision"])

    cancel_running = store.create_automation(
        "task", "Cancel while running", "2026-09-24T09:00:00Z", "once", "UTC"
    )
    active = store.claim_due_automation("2026-09-24T09:00:00Z")
    assert active is not None and active["id"] == cancel_running["id"]
    assert store.start_automation_run(cancel_running["id"], active["claim_token"], active["revision"])
    cancelled_running = store.cancel_automation(cancel_running["id"])
    assert cancelled_running["claim_token"] == active["claim_token"]
    assert store.claim_due_automation("2026-09-24T09:00:00Z") is None
    assert store.finalize_automation_claim(
        cancel_running["id"], active["claim_token"], active["revision"], succeeded=True,
        result="finished", completed_at="2026-09-24T10:00:00Z",
    )
    terminal = store.get_automation(cancel_running["id"])
    assert terminal["status"] == "cancelled"
    assert terminal["active_run_status"] == "succeeded"
    assert terminal["claim_token"] is None
    store.close()


@pytest.mark.asyncio
async def test_automation_reminder_delivery_filters_due_reminder_kind(tmp_path):
    runtime = CalendarRuntime(str(tmp_path / "calendar.sqlite3"))
    try:
        task = await runtime.execute(
            "create_automation", "task", "Task", "2026-09-24T09:00:00Z", "once", "UTC"
        )
        reminder = await runtime.execute(
            "create_automation", "reminder", "Future reminder", "2026-09-25T09:00:00Z", "once", "UTC"
        )
        callbacks = []

        async def accepted(_schedule):
            callbacks.append(True)

        assert await deliver_due_automation_reminders(
            runtime, "2026-09-24T09:00:00Z", alert_callback=accepted
        ) == 0
        assert callbacks == []
        assert (await runtime.execute("get_automation", task["id"]))["claim_token"] is None

        assert await deliver_due_automation_reminders(
            runtime, "2026-09-25T09:00:00Z", alert_callback=accepted
        ) == 1
        assert callbacks == [True]
        assert (await runtime.execute("get_automation", reminder["id"]))["status"] == "completed"
        assert (await runtime.execute("get_automation", task["id"]))["status"] == "active"
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_automation_reminder_channels_record_acceptance_without_claiming_heard(tmp_path):
    runtime = CalendarRuntime(str(tmp_path / "calendar.sqlite3"))
    try:
        schedule = await runtime.execute(
            "create_automation", "reminder", "Water", "2026-09-24T09:00:00Z", "once", "UTC"
        )
        callbacks = []

        async def alert(_schedule):
            callbacks.append("alert")

        async def speech(_schedule):
            callbacks.append("speech")

        async def telegram(_schedule):
            callbacks.append("telegram")

        assert await deliver_due_automation_reminders(
            runtime, "2026-09-24T09:00:00Z", alert_callback=alert,
            voice_callback=speech, telegram_callback=telegram,
        ) == 1
        row = await runtime.execute("get_automation", schedule["id"])
        assert callbacks == ["alert", "speech", "telegram"]
        assert row["active_run_status"] == "succeeded"
        assert row["active_run_result"] == "alert accepted; speech queued; Telegram message accepted"
        assert "heard" not in row["active_run_result"]
        assert "delivered" not in row["active_run_result"]
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_automation_reminder_failure_consumes_occurrence_and_does_not_hot_loop(tmp_path, monkeypatch):
    monkeypatch.setattr(
        CalendarStore,
        "_now_iso",
        staticmethod(lambda: "2026-09-24T09:00:00.000000Z"),
    )
    monkeypatch.setattr("charlie.calendar_scheduler._now_iso", lambda: "2026-09-24T09:00:00.000000Z")
    runtime = CalendarRuntime(str(tmp_path / "calendar.sqlite3"))
    try:
        schedule = await runtime.execute(
            "create_automation", "reminder", "Daily", "2026-09-24T09:00:00Z", "daily", "UTC"
        )

        async def fail(_schedule):
            raise RuntimeError("alert unavailable")

        assert await deliver_due_automation_reminders(
            runtime, "2026-09-24T09:00:00Z", alert_callback=fail
        ) == 1
        row = await runtime.execute("get_automation", schedule["id"])
        assert row["active_run_status"] == "failed"
        assert row["last_run_status"] == "failed"
        assert row["next_run_at"] == "2026-09-25T09:00:00.000000Z"
        assert "alert unavailable" in row["active_run_error"]
        assert await deliver_due_automation_reminders(
            runtime, "2026-09-24T09:00:00Z", alert_callback=fail
        ) == 0
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_automation_reminder_run_cannot_overlap_before_finalize(tmp_path):
    runtime = CalendarRuntime(str(tmp_path / "calendar.sqlite3"))
    entered = asyncio.Event()
    release = asyncio.Event()
    try:
        schedule = await runtime.execute(
            "create_automation", "reminder", "Once", "2026-09-24T09:00:00Z", "once", "UTC"
        )

        async def blocking_alert(_schedule):
            entered.set()
            await release.wait()

        first = asyncio.create_task(deliver_due_automation_reminders(
            runtime, "2026-09-24T09:00:00Z", alert_callback=blocking_alert
        ))
        await entered.wait()
        assert await deliver_due_automation_reminders(
            runtime, "2026-09-24T09:00:00Z", alert_callback=blocking_alert
        ) == 0
        running = await runtime.execute("get_automation", schedule["id"])
        assert running["active_run_status"] == "running"
        assert running["claim_token"] is not None
        release.set()
        assert await first == 1
        finished = await runtime.execute("get_automation", schedule["id"])
        assert finished["status"] == "completed"
        assert finished["claim_token"] is None
    finally:
        release.set()
        runtime.close()


@pytest.mark.asyncio
async def test_reconciled_interrupted_reminder_is_not_automatically_replayed(tmp_path):
    path = str(tmp_path / "calendar.sqlite3")
    first = CalendarRuntime(path)
    schedule = await first.execute(
        "create_automation", "reminder", "Check in", "2026-09-24T09:00:00Z", "once", "UTC"
    )
    claim = await first.execute("claim_due_automation", "2026-09-24T09:00:00Z", kind="reminder")
    assert claim is not None and claim["id"] == schedule["id"]
    assert await first.execute(
        "start_automation_run", schedule["id"], claim["claim_token"], claim["revision"]
    )
    stable_task_id = claim["active_task_id"]
    first.close()

    reopened = CalendarRuntime(path)
    callbacks = []
    try:
        assert await reopened.execute("reconcile_automation_claims") == 1

        async def would_send(_schedule):
            callbacks.append(True)

        assert await deliver_due_automation_reminders(
            reopened, "2026-09-25T09:00:00Z", alert_callback=would_send
        ) == 0
        row = await reopened.execute("get_automation", schedule["id"])
        assert row["active_run_status"] == "interrupted"
        assert row["active_task_id"] == stable_task_id
        assert callbacks == []
    finally:
        reopened.close()


def test_automation_tools_are_registered_structured_and_fail_truthfully(tmp_path, monkeypatch):
    from charlie import calendar_runtime
    from charlie.core import _AUTOMATION_POLICY_TOOLS

    names = {"automation_create", "automation_update", "automation_get", "automation_list", "automation_cancel"}
    assert names <= _AUTOMATION_POLICY_TOOLS
    runtime = CalendarRuntime(str(tmp_path / "calendar.sqlite3"))
    monkeypatch.setattr(calendar_runtime, "_calendar_runtime", runtime)
    try:
        result = tools.registry.execute_tool_structured(
            "automation_create",
            {"kind": "reminder", "text": "Hydrate", "first_run_at": "2026-09-24T09:00:00+05:30", "recurrence": "once"},
        )
        assert result.result_kind == "create"
        assert result.structured_data["verified"] is True
        assert result.structured_data["verification_status"] == "verified_success"
        assert result.structured_data["timezone"] == "Asia/Kolkata"
        assert result.structured_data["id"] in result.model_text
        assert "2026-09-24T03:30:00.000000Z" in result.model_text
        assert "Asia/Kolkata" in result.model_text

        schedule_id = result.structured_data["id"]
        updated = tools.registry.execute_tool_structured(
            "automation_update", {"schedule_id": schedule_id, "text": "Hydrate after lunch"}
        )
        assert updated.structured_data["verified"] is True
        assert updated.structured_data["verification_status"] == "verified_success"
        assert "Hydrate after lunch" in updated.model_text
        assert schedule_id in updated.model_text

        observed = tools.registry.execute_tool_structured("automation_get", {"schedule_id": schedule_id})
        assert observed.structured_data["observed"] is True
        assert "verified" not in observed.structured_data
        assert "Hydrate after lunch" in observed.model_text
        listed = tools.registry.execute_tool_structured("automation_list", {})
        assert listed.structured_data["observed"] is True
        assert "Hydrate after lunch" in listed.model_text

        cancelled = tools.registry.execute_tool_structured("automation_cancel", {"schedule_id": schedule_id})
        assert cancelled.structured_data["verified"] is True
        assert cancelled.structured_data["verification_status"] == "verified_success"
        assert "cancelled" in cancelled.model_text
        already_cancelled = tools.registry.execute_tool_structured(
            "automation_cancel", {"schedule_id": schedule_id}
        )
        assert already_cancelled.structured_data["verified"] is True
        assert "already cancelled" in already_cancelled.model_text

        completed_schedule = tools.registry.execute_tool_structured(
            "automation_create",
            {"kind": "task", "text": "Finished", "first_run_at": "2026-09-24T09:00:00Z", "recurrence": "once"},
        )
        completed_id = completed_schedule.structured_data["id"]
        store = CalendarStore(str(tmp_path / "calendar.sqlite3"))
        store._connection.execute(
            "UPDATE automation_schedules SET status = 'completed' WHERE id = ?", (completed_id,)
        )
        store._connection.commit()
        store.close()
        terminal_cancel = tools.registry.execute_tool_structured("automation_cancel", {"schedule_id": completed_id})
        assert terminal_cancel.structured_data["ok"] is False
        assert terminal_cancel.structured_data["verification_status"] == "verified_failure"
        assert "completed" in terminal_cancel.model_text
        assert "not cancelled" in terminal_cancel.model_text

        invalid = tools.registry.execute_tool_structured(
            "automation_create",
            {
                "kind": "reminder",
                "text": "Bad zone",
                "first_run_at": "2026-09-24T09:00:00Z",
                "recurrence": "once",
                "timezone": "Not/AZone",
            },
        )
        assert invalid.structured_data["ok"] is False
        assert invalid.structured_data["failure_kind"] == "invalid_arguments"
        missing = tools.registry.execute_tool_structured("automation_get", {"schedule_id": "missing"})
        assert missing.structured_data["ok"] is False
        assert missing.structured_data["failure_kind"] == "not_found"
        for name in names:
            operation = capabilities.get_capability_index().get_operation(name)
            assert operation is not None
            assert operation.required_leases == ("calendar",)
            assert name in tools.registry.get_tool_names()
    finally:
        runtime.close()
