"""Persistent local calendar and reminder records."""

from __future__ import annotations

import sqlite3
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

REMINDER_NONE = "none"
REMINDER_PENDING = "pending"
REMINDER_DELIVERING = "delivering"
REMINDER_DELIVERED = "delivered"
_AUTOMATION_KINDS = frozenset({"reminder", "task"})
_AUTOMATION_RECURRENCES = frozenset({"once", "daily", "weekly"})


def normalize_calendar_timestamp(value: str, field_name: str) -> str:
    """Normalize one required timezone-aware ISO-8601 timestamp to UTC Z form."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a timezone-aware ISO-8601 timestamp")
    candidate = value.strip()
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a timezone-aware ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone offset")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def normalize_calendar_day(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("day must be an ISO date")
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError as exc:
        raise ValueError("day must be an ISO date") from exc


def _automation_zone(value: str) -> ZoneInfo:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timezone must be a valid IANA timezone")
    try:
        return ZoneInfo(value.strip())
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("timezone must be a valid IANA timezone") from exc


def _normalize_automation_schedule(
    kind: str,
    text: str,
    first_run_at: str,
    recurrence: str,
    timezone_name: str,
) -> dict[str, str]:
    if not isinstance(kind, str) or kind not in _AUTOMATION_KINDS:
        raise ValueError("kind must be reminder or task")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be non-empty")
    if not isinstance(recurrence, str) or recurrence not in _AUTOMATION_RECURRENCES:
        raise ValueError("recurrence must be once, daily, or weekly")
    zone = _automation_zone(timezone_name)
    normalized_first_run = normalize_calendar_timestamp(first_run_at, "first_run_at")
    candidate = first_run_at.strip()
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1] + "+00:00"
    supplied_first_run = datetime.fromisoformat(candidate)
    supplied_offset = supplied_first_run.utcoffset()
    wall_time = supplied_first_run.replace(tzinfo=None)
    zone_offsets = {
        wall_time.replace(tzinfo=zone, fold=fold).utcoffset()
        for fold in (0, 1)
    }
    if supplied_offset != timedelta(0) and supplied_offset not in zone_offsets:
        expected = ", ".join(
            sorted(
                datetime(2000, 1, 1, tzinfo=timezone(offset)).strftime("%z")
                for offset in zone_offsets
                if offset is not None
            )
        )
        raise ValueError(
            f"first_run_at offset does not match timezone {zone.key}; "
            f"use {expected} or Z for UTC"
        )
    parsed_first_run = datetime.fromisoformat(normalized_first_run.replace("Z", "+00:00"))
    # Resolve the configured zone before persisting so bad timezone names never
    # leave a schedule that a future dispatcher cannot interpret.
    parsed_first_run.astimezone(zone)
    return {
        "kind": kind,
        "text": text.strip(),
        "first_run_at": normalized_first_run,
        "recurrence": recurrence,
        "timezone": zone.key,
    }


def _local_wall_time(value: datetime, zone: ZoneInfo) -> datetime:
    """Resolve local wall time; gaps advance to their first valid instant, folds use fold=0."""
    candidate = value.replace(tzinfo=zone, fold=0)
    round_trip = candidate.astimezone(timezone.utc).astimezone(zone)
    round_trip_wall = round_trip.replace(tzinfo=None)
    if round_trip_wall == value:
        return candidate
    if round_trip_wall < value:
        raise ValueError("could not resolve local schedule time")

    # ZoneInfo maps a nonexistent time forward by the gap. Binary search that
    # interval to land on the transition itself, rather than shifting by gap size.
    lower = value
    upper = round_trip_wall
    while upper - lower > timedelta(microseconds=1):
        middle = lower + (upper - lower) / 2
        if middle <= lower or middle >= upper:
            break
        probe = middle.replace(tzinfo=zone, fold=0)
        if probe.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == middle:
            upper = middle
        else:
            lower = middle
    return upper.replace(tzinfo=zone, fold=0)


def next_automation_occurrence(
    first_run_at: str,
    recurrence: str,
    timezone_name: str,
    *,
    after: Optional[str] = None,
) -> Optional[str]:
    """Return next due instant from the first-run local clock and weekday anchor."""
    if not isinstance(recurrence, str) or recurrence not in _AUTOMATION_RECURRENCES:
        raise ValueError("recurrence must be once, daily, or weekly")
    if recurrence == "once":
        _automation_zone(timezone_name)
        normalize_calendar_timestamp(first_run_at, "first_run_at")
        if after is not None:
            normalize_calendar_timestamp(after, "after")
        return None
    zone = _automation_zone(timezone_name)
    anchor = datetime.fromisoformat(
        normalize_calendar_timestamp(first_run_at, "first_run_at").replace("Z", "+00:00")
    )
    after_utc = datetime.fromisoformat(
        normalize_calendar_timestamp(after or first_run_at, "after").replace("Z", "+00:00")
    )
    anchor_local = anchor.astimezone(zone)
    after_local = after_utc.astimezone(zone)
    interval_days = 1 if recurrence == "daily" else 7
    elapsed_days = (after_local.date() - anchor_local.date()).days
    steps = max(1, elapsed_days // interval_days)
    wall_time = anchor_local.timetz().replace(tzinfo=None)

    while True:
        local_date = anchor_local.date() + timedelta(days=steps * interval_days)
        next_local = _local_wall_time(datetime.combine(local_date, wall_time), zone)
        next_utc = next_local.astimezone(timezone.utc)
        if next_utc > after_utc:
            return normalize_calendar_timestamp(next_utc.isoformat(), "next_run_at")
        steps += 1


class CalendarStore:
    """Thread-confined SQLite store for calendar and durable automation records."""

    _RETRY_LIMIT = 3
    _RETRY_DELAY_SECONDS = 0.05

    def __init__(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._connection = sqlite3.connect(path, timeout=5.0)
        self._connection.row_factory = sqlite3.Row
        self._run("configure calendar connection", self._configure_connection)
        self._migrate()

    def _configure_connection(self) -> None:
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = NORMAL")

    def _run(self, operation: str, callback):
        for attempt in range(self._RETRY_LIMIT):
            try:
                return callback()
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "locked" not in message and "busy" not in message:
                    raise
                try:
                    self._connection.rollback()
                except sqlite3.Error:
                    pass
                if attempt >= self._RETRY_LIMIT - 1:
                    raise
                time.sleep(self._RETRY_DELAY_SECONDS * (attempt + 1))

    def _migrate(self) -> None:
        def migrate() -> None:
            with self._connection:
                self._connection.execute(
                    """CREATE TABLE IF NOT EXISTS calendar_events (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        start_at TEXT NOT NULL,
                        end_at TEXT,
                        reminder_at TEXT,
                        completed INTEGER NOT NULL DEFAULT 0,
                        reminder_state TEXT NOT NULL DEFAULT 'none',
                        reminder_revision INTEGER NOT NULL DEFAULT 0,
                        reminder_claim_token TEXT,
                        reminder_claimed_at TEXT,
                        reminder_delivered_at TEXT,
                        reminder_attempt_count INTEGER NOT NULL DEFAULT 0,
                        reminder_last_error TEXT,
                        reminder_alert_accepted_at TEXT,
                        reminder_voice_accepted_at TEXT
                    )"""
                )
                columns = {
                    row[1] for row in self._connection.execute("PRAGMA table_info(calendar_events)").fetchall()
                }
                additions = {
                    "reminder_state": "TEXT NOT NULL DEFAULT 'none'",
                    "reminder_revision": "INTEGER NOT NULL DEFAULT 0",
                    "reminder_claim_token": "TEXT",
                    "reminder_claimed_at": "TEXT",
                    "reminder_delivered_at": "TEXT",
                    "reminder_attempt_count": "INTEGER NOT NULL DEFAULT 0",
                    "reminder_last_error": "TEXT",
                    "reminder_alert_accepted_at": "TEXT",
                    "reminder_voice_accepted_at": "TEXT",
                }
                for name, declaration in additions.items():
                    if name not in columns:
                        self._connection.execute(f"ALTER TABLE calendar_events ADD COLUMN {name} {declaration}")
                self._connection.execute(
                    """UPDATE calendar_events
                       SET reminder_state = CASE
                           WHEN reminder_at IS NULL THEN 'none'
                           WHEN completed = 1 THEN 'delivered'
                           ELSE 'pending'
                       END
                       WHERE reminder_state = 'none' AND reminder_at IS NOT NULL"""
                )
                self._connection.execute(
                    "UPDATE calendar_events SET reminder_revision = 1 "
                    "WHERE reminder_at IS NOT NULL AND reminder_revision = 0"
                )
                for row in self._connection.execute(
                    "SELECT id, start_at, end_at, reminder_at FROM calendar_events"
                ).fetchall():
                    try:
                        start_at = normalize_calendar_timestamp(row["start_at"], "start_at")
                        end_at = (
                            normalize_calendar_timestamp(row["end_at"], "end_at")
                            if row["end_at"] is not None
                            else None
                        )
                        reminder_at = (
                            normalize_calendar_timestamp(row["reminder_at"], "reminder_at")
                            if row["reminder_at"] is not None
                            else None
                        )
                        if end_at is not None and end_at < start_at:
                            raise ValueError("end_at must be greater than or equal to start_at")
                        self._connection.execute(
                            "UPDATE calendar_events SET start_at = ?, end_at = ?, reminder_at = ? WHERE id = ?",
                            (start_at, end_at, reminder_at, row["id"]),
                        )
                    except ValueError:
                        self._connection.execute(
                            "UPDATE calendar_events SET reminder_state = 'none', "
                            "reminder_last_error = 'invalid legacy calendar timestamp' WHERE id = ?",
                            (row["id"],),
                        )
                self._connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_calendar_reminder_due "
                    "ON calendar_events(reminder_state, reminder_at)"
                )
                self._connection.execute(
                    """CREATE TABLE IF NOT EXISTS automation_schedules (
                        id TEXT PRIMARY KEY,
                        kind TEXT NOT NULL CHECK(kind IN ('reminder', 'task')),
                        text TEXT NOT NULL,
                        first_run_at TEXT NOT NULL,
                        recurrence TEXT NOT NULL CHECK(recurrence IN ('once', 'daily', 'weekly')),
                        timezone TEXT NOT NULL,
                        next_run_at TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'cancelled', 'completed')),
                        revision INTEGER NOT NULL DEFAULT 1,
                        claim_token TEXT,
                        claimed_at TEXT,
                        last_run_at TEXT,
                        last_run_status TEXT,
                        active_run_id TEXT,
                        active_task_id TEXT,
                        active_run_scheduled_at TEXT,
                        active_run_revision INTEGER,
                        active_run_status TEXT,
                        active_run_result TEXT,
                        active_run_error TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )"""
                )
                automation_columns = {
                    row[1] for row in self._connection.execute(
                        "PRAGMA table_info(automation_schedules)"
                    ).fetchall()
                }
                automation_additions = {
                    "active_run_id": "TEXT",
                    "active_task_id": "TEXT",
                    "active_run_scheduled_at": "TEXT",
                    "active_run_revision": "INTEGER",
                    "active_run_status": "TEXT",
                    "active_run_result": "TEXT",
                    "active_run_error": "TEXT",
                    "last_run_status": "TEXT",
                }
                for name, declaration in automation_additions.items():
                    if name not in automation_columns:
                        self._connection.execute(
                            f"ALTER TABLE automation_schedules ADD COLUMN {name} {declaration}"
                        )
                self._connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_automation_schedule_due "
                    "ON automation_schedules(status, next_run_at)"
                )

        self._run("migrate calendar schema", migrate)

    def _row(self, event_id: str) -> dict:
        row = self._connection.execute("SELECT * FROM calendar_events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            raise KeyError(event_id)
        return dict(row)

    @staticmethod
    def _normalize_event_values(values: dict[str, Any], *, require_start: bool = False) -> dict[str, Any]:
        normalized = dict(values)
        for field in ("start_at", "end_at", "reminder_at"):
            if field in normalized and normalized[field] is not None:
                normalized[field] = normalize_calendar_timestamp(normalized[field], field)
        if require_start:
            normalized["start_at"] = normalize_calendar_timestamp(normalized.get("start_at"), "start_at")
        if normalized.get("start_at") and normalized.get("end_at"):
            if normalized["end_at"] < normalized["start_at"]:
                raise ValueError("end_at must be greater than or equal to start_at")
        return normalized

    def create_event(
        self,
        title: str,
        start_at: str,
        *,
        end_at: Optional[str] = None,
        reminder_at: Optional[str] = None,
    ) -> dict:
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title must be non-empty")
        values = self._normalize_event_values(
            {"start_at": start_at, "end_at": end_at, "reminder_at": reminder_at},
            require_start=True,
        )
        event_id = uuid.uuid4().hex
        reminder_state = REMINDER_PENDING if values["reminder_at"] else REMINDER_NONE

        def create() -> dict:
            with self._connection:
                self._connection.execute(
                    """INSERT INTO calendar_events
                       (id, title, start_at, end_at, reminder_at, reminder_state, reminder_revision)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event_id,
                        title.strip(),
                        values["start_at"],
                        values.get("end_at"),
                        values.get("reminder_at"),
                        reminder_state,
                        1 if values.get("reminder_at") else 0,
                    ),
                )
            return self._row(event_id)

        return self._run("create calendar event", create)

    def get_event(self, event_id: str) -> dict:
        return self._run("get calendar event", lambda: self._row(event_id))

    def list_events(self, day: Optional[str] = None) -> list[dict]:
        def list_rows() -> list[dict]:
            if day is None:
                rows = self._connection.execute("SELECT * FROM calendar_events ORDER BY start_at").fetchall()
            else:
                normalized_day = normalize_calendar_day(day)
                lower = f"{normalized_day}T00:00:00.000000Z"
                upper = (date.fromisoformat(normalized_day) + timedelta(days=1)).isoformat()
                rows = self._connection.execute(
                    "SELECT * FROM calendar_events WHERE start_at >= ? AND start_at < ? ORDER BY start_at",
                    (lower, f"{upper}T00:00:00.000000Z"),
                ).fetchall()
            return [dict(row) for row in rows]

        return self._run("list calendar events", list_rows)

    def update_event(self, event_id: str, values: dict) -> dict:
        allowed_names = {"title", "start_at", "end_at", "reminder_at", "completed"}
        allowed = {key: values[key] for key in allowed_names if key in values}
        if not allowed:
            return self.get_event(event_id)
        if "title" in allowed and (not isinstance(allowed["title"], str) or not allowed["title"].strip()):
            raise ValueError("title must be non-empty")
        normalized = self._normalize_event_values(allowed)

        def update() -> dict:
            current = self._row(event_id)
            merged = {**current, **normalized}
            if merged.get("start_at") and merged.get("end_at") and merged["end_at"] < merged["start_at"]:
                raise ValueError("end_at must be greater than or equal to start_at")
            update_values = dict(normalized)
            schedule_changed = any(
                key in normalized and normalized[key] != current.get(key)
                for key in ("title", "start_at", "end_at", "reminder_at")
            )
            if schedule_changed:
                revision = int(current.get("reminder_revision") or 0) + 1
                update_values.update(
                    {
                        "reminder_revision": revision,
                        "reminder_claim_token": None,
                        "reminder_claimed_at": None,
                        "reminder_delivered_at": None,
                        "reminder_last_error": None,
                        "reminder_alert_accepted_at": None,
                        "reminder_voice_accepted_at": None,
                        "reminder_state": REMINDER_PENDING if merged.get("reminder_at") else REMINDER_NONE,
                    }
                )
            assignments = ", ".join(f"{key} = ?" for key in update_values)
            result = self._connection.execute(
                f"UPDATE calendar_events SET {assignments} WHERE id = ?",
                (*update_values.values(), event_id),
            )
            if result.rowcount == 0:
                raise KeyError(event_id)
            self._connection.commit()
            return self._row(event_id)

        return self._run("update calendar event", update)

    def delete_event(self, event_id: str) -> None:
        def delete() -> None:
            result = self._connection.execute("DELETE FROM calendar_events WHERE id = ?", (event_id,))
            if result.rowcount == 0:
                raise KeyError(event_id)
            self._connection.commit()

        self._run("delete calendar event", delete)

    def recover_stale_claims(self) -> int:
        def recover() -> int:
            with self._connection:
                result = self._connection.execute(
                    """UPDATE calendar_events
                       SET reminder_state = 'pending', reminder_claim_token = NULL,
                           reminder_claimed_at = NULL,
                           reminder_last_error = COALESCE(reminder_last_error, 'recovered stale delivery claim')
                       WHERE reminder_state = 'delivering'"""
                )
            return result.rowcount

        return self._run("recover stale calendar claims", recover)

    def claim_due_reminder(self, now_iso: str) -> Optional[dict]:
        now = normalize_calendar_timestamp(now_iso, "now")

        def claim() -> Optional[dict]:
            self._connection.execute("BEGIN IMMEDIATE")
            rows = self._connection.execute(
                """SELECT * FROM calendar_events
                   WHERE reminder_at IS NOT NULL AND reminder_state = 'pending'
                   ORDER BY reminder_at"""
            ).fetchall()
            row = None
            for candidate in rows:
                try:
                    due_at = normalize_calendar_timestamp(candidate["reminder_at"], "reminder_at")
                except ValueError:
                    self._connection.execute(
                        """UPDATE calendar_events SET reminder_state = 'none',
                           reminder_last_error = 'invalid reminder timestamp' WHERE id = ?""",
                        (candidate["id"],),
                    )
                    continue
                if due_at <= now:
                    row = candidate
                    break
            if row is None:
                self._connection.commit()
                return None
            token = uuid.uuid4().hex
            revision = int(row["reminder_revision"] or 0)
            self._connection.execute(
                """UPDATE calendar_events
                   SET reminder_state = 'delivering', reminder_claim_token = ?,
                       reminder_claimed_at = ?, reminder_attempt_count = reminder_attempt_count + 1,
                       reminder_last_error = NULL
                   WHERE id = ? AND reminder_revision = ? AND reminder_state = 'pending'""",
                (token, now, row["id"], revision),
            )
            self._connection.commit()
            claimed = dict(row)
            claimed.update({"reminder_claim_token": token, "reminder_revision": revision})
            return claimed

        return self._run("claim due reminder", claim)

    def claim_is_current(self, event_id: str, claim_token: str, revision: int) -> bool:
        def check() -> bool:
            row = self._connection.execute(
                """SELECT 1 FROM calendar_events
                   WHERE id = ? AND reminder_claim_token = ? AND reminder_revision = ?
                   AND reminder_state = 'delivering'""",
                (event_id, claim_token, revision),
            ).fetchone()
            return row is not None

        return bool(self._run("check reminder claim", check))

    def record_reminder_channel(
        self,
        event_id: str,
        claim_token: str,
        revision: int,
        channel: str,
        accepted_at: str,
    ) -> bool:
        if channel not in {"alert", "voice"}:
            raise ValueError("unknown reminder delivery channel")
        column = {
            "alert": "reminder_alert_accepted_at",
            "voice": "reminder_voice_accepted_at",
        }[channel]

        def record() -> bool:
            result = self._connection.execute(
                f"UPDATE calendar_events SET {column} = ? "
                "WHERE id = ? AND reminder_claim_token = ? AND reminder_revision = ? "
                "AND reminder_state = 'delivering'",
                (accepted_at, event_id, claim_token, revision),
            )
            self._connection.commit()
            return result.rowcount == 1

        return bool(self._run("record reminder delivery channel", record))

    def finalize_reminder_claim(
        self,
        event_id: str,
        claim_token: str,
        revision: int,
        *,
        delivered: bool,
        error: Optional[str] = None,
    ) -> bool:
        def finalize() -> bool:
            delivered_at = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
            if delivered:
                result = self._connection.execute(
                    """UPDATE calendar_events
                       SET reminder_state = 'delivered', reminder_claim_token = NULL,
                           reminder_claimed_at = NULL, reminder_delivered_at = ?, reminder_last_error = NULL
                       WHERE id = ? AND reminder_claim_token = ? AND reminder_revision = ?
                       AND reminder_state = 'delivering'""",
                    (delivered_at, event_id, claim_token, revision),
                )
            else:
                result = self._connection.execute(
                    """UPDATE calendar_events
                       SET reminder_state = 'pending', reminder_claim_token = NULL,
                           reminder_claimed_at = NULL, reminder_last_error = ?
                       WHERE id = ? AND reminder_claim_token = ? AND reminder_revision = ?
                       AND reminder_state = 'delivering'""",
                    (error or "reminder delivery failed", event_id, claim_token, revision),
                )
            self._connection.commit()
            return result.rowcount == 1

        return bool(self._run("finalize reminder claim", finalize))

    def due_reminders(self, now_iso: str) -> list[dict]:
        """Compatibility read of pending due reminders; claiming is scheduler-owned."""
        now = normalize_calendar_timestamp(now_iso, "now")
        return self._run(
            "list due reminders",
            lambda: [
                dict(row)
                for row in self._connection.execute(
                    """SELECT * FROM calendar_events
                       WHERE reminder_at IS NOT NULL AND reminder_at <= ?
                       AND reminder_state = 'pending' ORDER BY reminder_at""",
                    (now,),
                ).fetchall()
            ],
        )

    def _automation_row(self, schedule_id: str) -> dict:
        row = self._connection.execute(
            "SELECT * FROM automation_schedules WHERE id = ?", (schedule_id,)
        ).fetchone()
        if row is None:
            raise KeyError(schedule_id)
        return dict(row)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")

    def create_automation(
        self,
        kind: str,
        text: str,
        first_run_at: str,
        recurrence: str,
        timezone_name: str = "Asia/Kolkata",
    ) -> dict:
        values = _normalize_automation_schedule(kind, text, first_run_at, recurrence, timezone_name)
        schedule_id = uuid.uuid4().hex
        now = self._now_iso()

        def create() -> dict:
            with self._connection:
                self._connection.execute(
                    """INSERT INTO automation_schedules
                       (id, kind, text, first_run_at, recurrence, timezone, next_run_at,
                        status, revision, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'active', 1, ?, ?)""",
                    (
                        schedule_id,
                        values["kind"],
                        values["text"],
                        values["first_run_at"],
                        values["recurrence"],
                        values["timezone"],
                        values["first_run_at"],
                        now,
                        now,
                    ),
                )
            return self._automation_row(schedule_id)

        return self._run("create automation schedule", create)

    def get_automation(self, schedule_id: str) -> dict:
        return self._run("get automation schedule", lambda: self._automation_row(schedule_id))

    def list_automations(self) -> list[dict]:
        return self._run(
            "list automation schedules",
            lambda: [
                dict(row)
                for row in self._connection.execute(
                    "SELECT * FROM automation_schedules ORDER BY next_run_at, created_at, id"
                ).fetchall()
            ],
        )

    def update_automation(self, schedule_id: str, values: dict[str, Any]) -> dict:
        allowed_names = {"kind", "text", "first_run_at", "recurrence", "timezone"}
        if not isinstance(values, dict):
            raise ValueError("automation update must be an object")
        unknown = set(values) - allowed_names
        if unknown:
            raise ValueError(f"unknown automation fields: {', '.join(sorted(unknown))}")
        if not values:
            return self.get_automation(schedule_id)

        def update() -> dict:
            with self._connection:
                current = self._automation_row(schedule_id)
                if current["status"] != "active":
                    raise ValueError(f"cannot update {current['status']} automation schedule")
                merged = {key: current[key] for key in allowed_names}
                merged.update(values)
                normalized = _normalize_automation_schedule(
                    merged["kind"],
                    merged["text"],
                    merged["first_run_at"],
                    merged["recurrence"],
                    merged["timezone"],
                )
                changed = {key: value for key, value in normalized.items() if value != current[key]}
                if not changed:
                    return current
                changed["updated_at"] = self._now_iso()
                changed["revision"] = int(current["revision"]) + 1
                if current["active_run_status"] != "running":
                    changed["claim_token"] = None
                    changed["claimed_at"] = None
                    changed["active_run_status"] = "invalidated"
                if "first_run_at" in changed:
                    changed["next_run_at"] = changed["first_run_at"]
                    changed["last_run_at"] = None
                elif {"recurrence", "timezone"} & changed.keys() and current["last_run_at"]:
                    if changed.get("recurrence", current["recurrence"]) != "once":
                        changed["next_run_at"] = next_automation_occurrence(
                            current["first_run_at"],
                            changed.get("recurrence", current["recurrence"]),
                            changed.get("timezone", current["timezone"]),
                            after=current["last_run_at"],
                        )
                assignments = ", ".join(f"{key} = ?" for key in changed)
                self._connection.execute(
                    f"UPDATE automation_schedules SET {assignments} WHERE id = ?",
                    (*changed.values(), schedule_id),
                )
            return self._automation_row(schedule_id)

        return self._run("update automation schedule", update)

    def cancel_automation(self, schedule_id: str) -> dict:
        def cancel() -> dict:
            with self._connection:
                current = self._automation_row(schedule_id)
                if current["status"] == "active":
                    running = current["active_run_status"] == "running"
                    self._connection.execute(
                        """UPDATE automation_schedules SET status = 'cancelled', revision = ?,
                           claim_token = ?, claimed_at = ?, active_run_status = ?,
                           updated_at = ? WHERE id = ?""",
                        (
                            int(current["revision"]) + 1,
                            current["claim_token"] if running else None,
                            current["claimed_at"] if running else None,
                            "running" if running else "invalidated",
                            self._now_iso(),
                            schedule_id,
                        ),
                    )
            return self._automation_row(schedule_id)

        return self._run("cancel automation schedule", cancel)

    @staticmethod
    def _automation_occurrence_ids(schedule_id: str, scheduled_at: str) -> tuple[str, str]:
        identity = f"charlie-automation:{schedule_id}:{scheduled_at}"
        return (
            uuid.uuid5(uuid.NAMESPACE_URL, f"{identity}:run").hex,
            uuid.uuid5(uuid.NAMESPACE_URL, f"{identity}:task").hex,
        )

    def claim_due_automation(
        self,
        now: str,
        kind: Optional[str] = None,
        *,
        retry_interrupted: bool = False,
        schedule_id: Optional[str] = None,
    ) -> Optional[dict]:
        due_at = normalize_calendar_timestamp(now, "now")
        if kind is not None and (not isinstance(kind, str) or kind not in _AUTOMATION_KINDS):
            raise ValueError("kind must be reminder or task")
        if not isinstance(retry_interrupted, bool):
            raise ValueError("retry_interrupted must be a boolean")
        if schedule_id is not None and (not isinstance(schedule_id, str) or not schedule_id):
            raise ValueError("schedule_id must be a non-empty string or null")

        def claim() -> Optional[dict]:
            with self._connection:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._connection.execute(
                    """SELECT * FROM automation_schedules
                       WHERE status = 'active' AND next_run_at <= ? AND claim_token IS NULL
                       AND (active_run_status IS NULL OR active_run_status IN ('succeeded', 'failed', 'invalidated')
                            OR (? = 1 AND active_run_status = 'interrupted'))
                       AND (? IS NULL OR id = ?)
                       AND (? IS NULL OR kind = ?)
                       ORDER BY next_run_at, created_at, id LIMIT 1""",
                    (due_at, int(retry_interrupted), schedule_id, schedule_id, kind, kind),
                ).fetchone()
                if row is None:
                    return None
                schedule = dict(row)
                scheduled_at = schedule["next_run_at"]
                run_id, task_id = self._automation_occurrence_ids(schedule["id"], scheduled_at)
                token = uuid.uuid4().hex
                self._connection.execute(
                    """UPDATE automation_schedules SET claim_token = ?, claimed_at = ?,
                       active_run_id = ?, active_task_id = ?, active_run_scheduled_at = ?,
                       active_run_status = 'claimed', active_run_result = NULL,
                       active_run_error = NULL WHERE id = ? AND status = 'active'
                       AND revision = ? AND claim_token IS NULL""",
                    (token, due_at, run_id, task_id, scheduled_at, schedule["id"], schedule["revision"]),
                )
                self._connection.execute(
                    "UPDATE automation_schedules SET active_run_revision = ? WHERE id = ?",
                    (schedule["revision"], schedule["id"]),
                )
                return self._automation_row(schedule["id"])

        return self._run("claim due automation schedule", claim)

    def start_automation_run(self, schedule_id: str, claim_token: str, revision: int) -> bool:
        def start() -> bool:
            with self._connection:
                self._connection.execute("BEGIN IMMEDIATE")
                result = self._connection.execute(
                    """UPDATE automation_schedules SET active_run_status = 'running'
                       WHERE id = ? AND status = 'active' AND revision = ?
                       AND active_run_revision = ? AND claim_token = ?
                       AND active_run_status = 'claimed'""",
                    (schedule_id, revision, revision, claim_token),
                )
                return result.rowcount == 1

        return self._run("start automation schedule run", start)

    def resume_interrupted_automation_run(
        self,
        schedule_id: str,
        task_id: str,
        revision: int,
        claim_token: str,
    ) -> bool:
        """Reacquire an interrupted claim only to settle its known terminal task."""
        def resume() -> bool:
            with self._connection:
                self._connection.execute("BEGIN IMMEDIATE")
                result = self._connection.execute(
                    """UPDATE automation_schedules SET claim_token = ?, claimed_at = ?,
                       active_run_status = 'running' WHERE id = ? AND status IN ('active', 'cancelled')
                       AND active_run_status = 'interrupted' AND active_task_id = ?
                       AND active_run_revision = ? AND claim_token IS NULL""",
                    (claim_token, self._now_iso(), schedule_id, task_id, revision),
                )
                return result.rowcount == 1

        return self._run("resume interrupted automation task", resume)

    def finalize_automation_claim(
        self,
        schedule_id: str,
        claim_token: str,
        revision: int,
        *,
        succeeded: bool,
        result: Optional[str] = None,
        error: Optional[str] = None,
        completed_at: Optional[str] = None,
    ) -> bool:
        if not isinstance(succeeded, bool):
            raise ValueError("succeeded must be a boolean")
        if result is not None and not isinstance(result, str):
            raise ValueError("result must be a string or null")
        if error is not None and not isinstance(error, str):
            raise ValueError("error must be a string or null")
        finished = normalize_calendar_timestamp(completed_at or self._now_iso(), "completed_at")

        def finalize() -> bool:
            with self._connection:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._connection.execute(
                    """SELECT * FROM automation_schedules WHERE id = ?
                       AND status IN ('active', 'cancelled') AND claim_token = ?
                       AND active_run_revision = ? AND active_run_status = 'running'""",
                    (schedule_id, claim_token, revision),
                ).fetchone()
                if row is None:
                    return False
                schedule = dict(row)
                scheduled_at = schedule["active_run_scheduled_at"]
                changes: dict[str, Any] = {
                    "claim_token": None,
                    "claimed_at": None,
                    "active_run_status": "succeeded" if succeeded else "failed",
                    "active_run_result": result,
                    "active_run_error": error,
                    "updated_at": finished,
                    "last_run_at": finished,
                    "last_run_status": "succeeded" if succeeded else "failed",
                }
                if schedule["status"] == "active" and schedule["recurrence"] == "once":
                    changes["status"] = "completed"
                elif schedule["status"] == "active" and schedule["next_run_at"] <= finished:
                    threshold = max(scheduled_at, finished)
                    changes["next_run_at"] = next_automation_occurrence(
                        schedule["first_run_at"], schedule["recurrence"], schedule["timezone"],
                        after=threshold,
                    )
                assignments = ", ".join(f"{name} = ?" for name in changes)
                updated = self._connection.execute(
                    f"UPDATE automation_schedules SET {assignments} "
                    "WHERE id = ? AND active_run_revision = ? AND claim_token = ?",
                    (*changes.values(), schedule_id, revision, claim_token),
                )
                return updated.rowcount == 1

        return self._run("finalize automation schedule claim", finalize)

    def reconcile_automation_claims(self) -> int:
        """Release interrupted claims after restart; deterministic IDs make retries deduplicable."""
        def reconcile() -> int:
            with self._connection:
                result = self._connection.execute(
                    """UPDATE automation_schedules SET claim_token = NULL, claimed_at = NULL,
                       active_run_status = 'interrupted', active_run_error = COALESCE(
                           active_run_error, 'recovered interrupted automation claim'),
                       updated_at = ? WHERE status IN ('active', 'cancelled') AND claim_token IS NOT NULL""",
                    (self._now_iso(),),
                )
                return result.rowcount

        return self._run("reconcile automation schedule claims", reconcile)

    def close(self) -> None:
        self._connection.close()
