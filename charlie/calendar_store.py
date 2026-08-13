"""Persistent local calendar and reminder records."""

import sqlite3
import uuid
from pathlib import Path
from typing import Optional


class CalendarStore:
    """Small SQLite-backed event store shared by the web and tool layers."""

    def __init__(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS calendar_events (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                start_at TEXT NOT NULL,
                end_at TEXT,
                reminder_at TEXT,
                completed INTEGER NOT NULL DEFAULT 0
            )"""
        )
        self._connection.commit()

    def create_event(
        self, title: str, start_at: str, *, end_at: Optional[str] = None, reminder_at: Optional[str] = None
    ) -> dict:
        event_id = uuid.uuid4().hex
        self._connection.execute(
            "INSERT INTO calendar_events (id, title, start_at, end_at, reminder_at) VALUES (?, ?, ?, ?, ?)",
            (event_id, title.strip(), start_at, end_at, reminder_at),
        )
        self._connection.commit()
        return self.get_event(event_id)

    def get_event(self, event_id: str) -> dict:
        row = self._connection.execute("SELECT * FROM calendar_events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            raise KeyError(event_id)
        return dict(row)

    def list_events(self, day: Optional[str] = None) -> list[dict]:
        if day:
            rows = self._connection.execute(
                "SELECT * FROM calendar_events WHERE substr(start_at, 1, 10) = ? ORDER BY start_at", (day,)
            ).fetchall()
        else:
            rows = self._connection.execute("SELECT * FROM calendar_events ORDER BY start_at").fetchall()
        return [dict(row) for row in rows]

    def update_event(self, event_id: str, values: dict) -> dict:
        allowed = {
            key: values[key] for key in ("title", "start_at", "end_at", "reminder_at", "completed") if key in values
        }
        if not allowed:
            return self.get_event(event_id)
        assignments = ", ".join(f"{key} = ?" for key in allowed)
        result = self._connection.execute(
            f"UPDATE calendar_events SET {assignments} WHERE id = ?", (*allowed.values(), event_id)
        )
        if result.rowcount == 0:
            raise KeyError(event_id)
        self._connection.commit()
        return self.get_event(event_id)

    def delete_event(self, event_id: str) -> None:
        result = self._connection.execute("DELETE FROM calendar_events WHERE id = ?", (event_id,))
        if result.rowcount == 0:
            raise KeyError(event_id)
        self._connection.commit()

    def due_reminders(self, now_iso: str) -> list[dict]:
        rows = self._connection.execute(
            "SELECT * FROM calendar_events WHERE reminder_at IS NOT NULL AND reminder_at <= ? "
            "AND completed = 0 ORDER BY reminder_at",
            (now_iso,),
        ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        self._connection.close()
