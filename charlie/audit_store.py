"""Persistent, secret-safe action audit records."""

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from charlie.log_redaction import redact_sensitive_text


class AuditStore:
    def __init__(self, path: str) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._closed = False
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS audit_entries ("
            "id TEXT PRIMARY KEY, created_at TEXT NOT NULL, tool_name TEXT NOT NULL, "
            "arguments TEXT NOT NULL, outcome TEXT NOT NULL)"
        )
        self._connection.commit()

    def record(self, tool_name: str, arguments: dict, outcome: str) -> dict:
        with self._lock:
            if self._closed:
                raise RuntimeError("AuditStore is closed")
            entry = {
                "id": uuid.uuid4().hex,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "tool_name": tool_name,
                "arguments": redact_sensitive_text(json.dumps(arguments, sort_keys=True)),
                "outcome": redact_sensitive_text(outcome),
            }
            self._connection.execute(
                "INSERT INTO audit_entries (id, created_at, tool_name, arguments, outcome) VALUES (?, ?, ?, ?, ?)",
                tuple(entry.values()),
            )
            self._connection.commit()
            return entry

    def list(self, limit: int = 100) -> list[dict]:
        with self._lock:
            if self._closed:
                raise RuntimeError("AuditStore is closed")
            rows = self._connection.execute(
                "SELECT id, created_at, tool_name, arguments, outcome "
                "FROM audit_entries ORDER BY created_at DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
            return [dict(row) for row in rows]

    def purge(self, *, older_than_days: int | None = None) -> dict[str, int]:
        """Delete audit rows through the canonical synchronized connection."""
        if older_than_days is not None and (
            isinstance(older_than_days, bool) or not isinstance(older_than_days, int) or older_than_days < 0
        ):
            raise ValueError("older_than_days must be a non-negative integer")
        with self._lock:
            if self._closed:
                raise RuntimeError("AuditStore is closed")
            if older_than_days is None:
                cursor = self._connection.execute("DELETE FROM audit_entries")
            else:
                cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
                cursor = self._connection.execute(
                    "DELETE FROM audit_entries WHERE created_at < ?",
                    (cutoff,),
                )
            count = max(0, int(cursor.rowcount))
            self._connection.commit()
            return {"items_purged": count}

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._connection.close()
