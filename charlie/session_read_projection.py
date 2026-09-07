"""Read-only SQLite projection for the web process."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import Lock
from typing import Optional


class SessionProjectionUnavailable(RuntimeError):
    """Session database cannot be opened in read-only mode."""


class SessionReadProjection:
    """Small read-only session view; never migrates or mutates the database."""

    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path)
        self._closed = False
        self._lock = Lock()

    @property
    def closed(self) -> bool:
        return self._closed

    def _connect(self) -> sqlite3.Connection:
        with self._lock:
            if self._closed:
                raise SessionProjectionUnavailable("Session read projection is closed")
        path = Path(self.db_path).resolve()
        if not path.is_file():
            raise SessionProjectionUnavailable("Session database is unavailable")
        try:
            connection = sqlite3.connect(
                f"{path.as_uri()}?mode=ro",
                uri=True,
                timeout=5.0,
            )
            connection.row_factory = sqlite3.Row
            return connection
        except sqlite3.Error as exc:
            raise SessionProjectionUnavailable("Session database is unavailable") from exc

    def get_sessions(
        self,
        *,
        source: Optional[str] = None,
        launch_id: Optional[str] = None,
    ) -> list[tuple[str, str, str, Optional[str], Optional[str]]]:
        connection = self._connect()
        try:
            clauses: list[str] = []
            params: list[str] = []
            if source is not None:
                clauses.append("source = ?")
                params.append(source)
            if launch_id is not None:
                clauses.append("launch_id = ?")
                params.append(launch_id)
            where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
            rows = connection.execute(
                "SELECT session_id, title, created_at, updated_at, launch_id "
                f"FROM sessions{where} ORDER BY created_at DESC",
                params,
            ).fetchall()
            return [tuple(row) for row in rows]
        finally:
            connection.close()

    def get_session_messages(self, session_id: str, limit: int = 50) -> list[tuple[str, str]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT messages.role, messages.content "
                "FROM messages JOIN sessions ON sessions.session_id = messages.session_id "
                "WHERE messages.session_id = ? ORDER BY messages.id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
            return [(row[0], row[1]) for row in reversed(rows)]
        finally:
            connection.close()

    def session_exists(self, session_id: str) -> bool:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT 1 FROM sessions WHERE session_id = ? LIMIT 1",
                (session_id,),
            ).fetchone()
            return row is not None
        finally:
            connection.close()

    def close(self) -> None:
        with self._lock:
            self._closed = True
