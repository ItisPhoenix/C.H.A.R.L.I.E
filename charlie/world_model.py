"""Charlie's world model: SQLite-backed store for open threads and machine
state, same boundary shape as charlie/session_store.py.
"""

import logging
import sqlite3
import threading
import time
from typing import Callable, List, Optional, Tuple, TypeVar

from charlie.utils import make_id, utc_now_iso

T = TypeVar("T")

logger = logging.getLogger("charlie.world_model")

# Reader hard budget: keeps the world-model slice inside the ~1s TTFA budget.
_DEFAULT_CHAR_BUDGET = 800


class WorldModel:
    """Threads (open work) + machine events (observed activity/errors).
    Patterns/entities tables land with their writers in the learning phase.
    """

    def __init__(self, db_path: str = "world_model.db"):
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
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    def _with_retry(self, op: "Callable[[], T]", op_name: str, reraise: bool = True) -> "Optional[T]":
        for attempt in range(2):
            try:
                return op()
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e) and attempt == 0:
                    logger.warning("world_model DB locked during %s, retrying...", op_name)
                    time.sleep(0.05)
                    continue
                logger.error("world_model %s failed: %s", op_name, e)
                if reraise:
                    raise
                return None
            except sqlite3.Error as e:
                logger.error("world_model %s failed: %s", op_name, e)
                if reraise:
                    raise
                return None

    def init_db(self) -> None:
        self.conn = self._get_connection()
        with self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS threads (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    session_id TEXT NOT NULL DEFAULT 'default',
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                );
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS machine_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                );
            """)
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_threads_status ON threads(status, updated_at);")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_events_created ON machine_events(created_at);")

    # --- Writers: threads ---

    def open_thread(self, title: str, session_id: str = "default") -> str:
        thread_id = make_id(8)
        now = utc_now_iso()

        def _op():
            with self.conn:
                self.conn.execute(
                    "INSERT INTO threads (id, title, session_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (thread_id, title, session_id, now, now),
                )
        self._with_retry(_op, "open_thread", reraise=False)
        return thread_id

    def update_thread(self, thread_id: str, summary: str, resolved: bool = False) -> None:
        def _op():
            with self.conn:
                self.conn.execute(
                    "UPDATE threads SET summary = ?, status = ?, updated_at = ? WHERE id = ?",
                    (summary, "closed" if resolved else "open", utc_now_iso(), thread_id),
                )
        self._with_retry(_op, "update_thread", reraise=False)

    def close_thread(self, thread_id: str) -> None:
        def _op():
            with self.conn:
                self.conn.execute(
                    "UPDATE threads SET status = 'closed', updated_at = ? WHERE id = ?",
                    (utc_now_iso(), thread_id),
                )
        self._with_retry(_op, "close_thread", reraise=False)

    def list_open_threads(self, limit: int = 5) -> List[Tuple[str, str, str]]:
        """Most recently touched open threads first: (id, title, summary)."""
        def _op():
            cur = self.conn.execute(
                "SELECT id, title, summary FROM threads WHERE status = 'open' ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            )
            return cur.fetchall()
        return self._with_retry(_op, "list_open_threads", reraise=False) or []

    # --- Writers: machine events ---

    def record_event(self, event_type: str, detail: str) -> None:
        def _op():
            with self.conn:
                self.conn.execute(
                    "INSERT INTO machine_events (event_type, detail) VALUES (?, ?)",
                    (event_type, detail),
                )
        self._with_retry(_op, "record_event", reraise=False)

    def recent_events(self, limit: int = 5, event_type: Optional[str] = None) -> List[Tuple[str, str, str]]:
        """Most recent events first: (event_type, detail, created_at)."""
        def _op():
            if event_type:
                cur = self.conn.execute(
                    "SELECT event_type, detail, created_at FROM machine_events "
                    "WHERE event_type = ? ORDER BY created_at DESC LIMIT ?",
                    (event_type, limit),
                )
            else:
                cur = self.conn.execute(
                    "SELECT event_type, detail, created_at FROM machine_events ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            return cur.fetchall()
        return self._with_retry(_op, "recent_events", reraise=False) or []

    # --- Reader ---

    def context_slice(self, char_budget: int = _DEFAULT_CHAR_BUDGET) -> str:
        """Open threads + recent tool errors, hard-capped at char_budget.
        Not semantic search -- active state is relevant on every turn.
        """
        parts: List[str] = []
        threads = self.list_open_threads(limit=5)
        if threads:
            parts.append("Open threads:")
            for _id, title, summary in threads:
                line = f"- {title}" + (f": {summary}" if summary else "")
                parts.append(line)

        errors = self.recent_events(limit=3, event_type="tool_error")
        if errors:
            parts.append("Recent errors:")
            for _etype, detail, _created in errors:
                parts.append(f"- {detail}")

        text = "\n".join(parts)
        return text[:char_budget]
