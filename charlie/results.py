"""Task result persistence + idle-return catch-up (Phase 6).

New table in sessions.db, reusing session_store.py's connection/retry
pattern and charlie.utils.utc_now_iso(). Producer is background_task.py
(stores one row per terminal task); consumers are the recall_results tool
(tools.py) and consume_catchup(), which the companion will call once
surfaces exist (Phase 7+) -- a live idle-return trigger needs UserContext
wired into main.py first (still charlie/context.py's noted gap).
"""

import logging
import os
import sqlite3
import threading
from dataclasses import dataclass
from typing import List, Optional

from charlie.utils import utc_now_iso

logger = logging.getLogger("charlie.results")


@dataclass
class ResultRecord:
    task_id: str
    summary: str
    full_result: str
    attention_level: int
    seen: bool
    created_at: str


class ResultsStore:
    """Persistent SQLite-backed task result store, one row per terminal task."""

    def __init__(self, db_path: str = "sessions.db"):
        self.db_path = db_path
        self._local = threading.local()
        self.init_db()

    @property
    def conn(self):
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = self._get_connection()
        return self._local.conn

    @conn.setter
    def conn(self, value):
        self._local.conn = value

    def _get_connection(self):
        db_dir = os.path.dirname(os.path.abspath(self.db_path))
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    def init_db(self) -> None:
        self.conn = self._get_connection()
        try:
            with self.conn:
                self.conn.execute("""
                    CREATE TABLE IF NOT EXISTS task_results (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        task_id TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        full_result TEXT NOT NULL,
                        attention_level INTEGER NOT NULL,
                        seen INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    );
                """)
        except sqlite3.Error as e:
            logger.error(f"Results DB initialization failed: {e}")
            raise

    def store(self, task_id: str, summary: str, full_result: str, attention_level: int) -> None:
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT INTO task_results (task_id, summary, full_result, attention_level, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (task_id, summary, full_result, attention_level, utc_now_iso()),
                )
        except sqlite3.Error as e:
            logger.error(f"store result failed: {e}")

    def get_recent(self, limit: int = 5) -> List[ResultRecord]:
        try:
            rows = self.conn.execute(
                "SELECT task_id, summary, full_result, attention_level, seen, created_at "
                "FROM task_results ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [ResultRecord(r[0], r[1], r[2], r[3], bool(r[4]), r[5]) for r in rows]
        except sqlite3.Error as e:
            logger.error(f"get_recent failed: {e}")
            return []

    def consume_catchup(self, min_level: int = 2) -> Optional[str]:
        """One nudge for everything unseen since the last call -- never a storm.

        Reads unseen rows at/above min_level and marks them seen in the same
        pass, so a second call returns None until something new lands --
        this IS the "fires exactly once" guarantee, not caller discipline.
        """
        try:
            with self.conn:
                rows = self.conn.execute(
                    "SELECT task_id, summary FROM task_results "
                    "WHERE seen = 0 AND attention_level >= ? ORDER BY id ASC",
                    (min_level,),
                ).fetchall()
                if not rows:
                    return None
                self.conn.execute(
                    "UPDATE task_results SET seen = 1 WHERE seen = 0 AND attention_level >= ?",
                    (min_level,),
                )
        except sqlite3.Error as e:
            logger.error(f"consume_catchup failed: {e}")
            return None

        summaries = [r[1] for r in rows]
        if len(summaries) == 1:
            return f"While you were away: {summaries[0]}"
        return f"While you were away, {len(summaries)} things finished: " + "; ".join(summaries)

    def close(self) -> None:
        if self.conn:
            try:
                self.conn.close()
            except sqlite3.Error:
                pass
            self.conn = None
