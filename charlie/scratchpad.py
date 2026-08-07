"""Global freeform scratchpad, shared across all transports. SQLite-backed, capped."""

import logging
import os
import sqlite3
import threading
from typing import List, Tuple

from charlie.utils import utc_now_iso

logger = logging.getLogger("charlie.scratchpad")

MAX_CHARS = 5000


class Scratchpad:
    """1-based positional index over insertion-ordered rows, recomputed per call."""

    def __init__(self, db_path: str = "scratchpad.db"):
        self.db_path = db_path
        self._local = threading.local()
        self._init_db()

    @property
    def conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            db_dir = os.path.dirname(os.path.abspath(self.db_path))
            if db_dir and not os.path.exists(db_dir):
                os.makedirs(db_dir, exist_ok=True)
            self._local.conn = sqlite3.connect(self.db_path, timeout=5.0)
        return self._local.conn

    def _init_db(self) -> None:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS scratchpad ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        conn.commit()
        conn.close()

    def _rows(self) -> List[Tuple[int, str, str]]:
        return self.conn.execute(
            "SELECT id, text, created_at FROM scratchpad ORDER BY id ASC"
        ).fetchall()

    def _trim_to_cap(self) -> None:
        """Drop oldest entries until total text length fits MAX_CHARS."""
        while True:
            total = self.conn.execute(
                "SELECT COALESCE(SUM(LENGTH(text)), 0) FROM scratchpad"
            ).fetchone()[0]
            if total <= MAX_CHARS:
                return
            oldest = self.conn.execute(
                "SELECT id FROM scratchpad ORDER BY id ASC LIMIT 1"
            ).fetchone()
            if oldest is None:
                return
            self.conn.execute("DELETE FROM scratchpad WHERE id = ?", (oldest[0],))
            self.conn.commit()

    def add(self, text: str) -> int:
        """Append an entry. Returns its 1-based index. Raises ValueError if
        text is empty or alone exceeds MAX_CHARS."""
        text = (text or "").strip()
        if not text:
            raise ValueError("text is required")
        if len(text) > MAX_CHARS:
            raise ValueError(f"entry exceeds {MAX_CHARS} char cap")
        self.conn.execute(
            "INSERT INTO scratchpad (text, created_at) VALUES (?, ?)", (text, utc_now_iso())
        )
        self.conn.commit()
        self._trim_to_cap()
        return len(self._rows())

    def list(self) -> List[Tuple[int, str, str]]:
        """[(1-based index, text, created_at), ...] in insertion order."""
        return [(i, text, created_at) for i, (_id, text, created_at) in enumerate(self._rows(), 1)]

    def edit(self, index: int, text: str) -> bool:
        """Returns False if index is out of range."""
        text = (text or "").strip()
        if not text:
            raise ValueError("text is required")
        if len(text) > MAX_CHARS:
            raise ValueError(f"entry exceeds {MAX_CHARS} char cap")
        rows = self._rows()
        if index < 1 or index > len(rows):
            return False
        row_id = rows[index - 1][0]
        self.conn.execute("UPDATE scratchpad SET text = ? WHERE id = ?", (text, row_id))
        self.conn.commit()
        self._trim_to_cap()
        return True

    def delete(self, index: int) -> bool:
        """Returns False if index is out of range."""
        rows = self._rows()
        if index < 1 or index > len(rows):
            return False
        row_id = rows[index - 1][0]
        self.conn.execute("DELETE FROM scratchpad WHERE id = ?", (row_id,))
        self.conn.commit()
        return True

    def clear(self) -> None:
        self.conn.execute("DELETE FROM scratchpad")
        self.conn.commit()

    def close(self) -> None:
        if hasattr(self._local, "conn") and self._local.conn is not None:
            self._local.conn.close()
            self._local.conn = None
