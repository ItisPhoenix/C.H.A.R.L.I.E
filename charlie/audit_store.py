"""Persistent, secret-safe action audit records."""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from charlie.log_redaction import redact_sensitive_text


class AuditStore:
    def __init__(self, path: str) -> None:
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
        rows = self._connection.execute(
            "SELECT id, created_at, tool_name, arguments, outcome FROM audit_entries ORDER BY created_at DESC LIMIT ?",
            (max(1, min(limit, 500)),),
        ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        self._connection.close()
