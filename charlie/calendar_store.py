"""Persistent local calendar and reminder records."""

from __future__ import annotations

import sqlite3
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

REMINDER_NONE = "none"
REMINDER_PENDING = "pending"
REMINDER_DELIVERING = "delivering"
REMINDER_DELIVERED = "delivered"


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


class CalendarStore:
    """Thread-confined SQLite calendar store with claim-safe reminder state."""

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

    def close(self) -> None:
        self._connection.close()
