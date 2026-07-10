import logging
import os
import sqlite3
import threading
import time
from typing import Callable, List, Optional, Tuple, TypeVar

T = TypeVar("T")

from charlie.utils import utc_now_iso

logger = logging.getLogger("charlie.session_store")

# --- Constants ---
_TOOL_PERSIST_MAX_CHARS = 500  # cap stored tool result length to prevent DB bloat


class SessionStore:
    """Persistent SQLite-backed session history store with FTS5 search."""

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
        """Helper to get or reconnect to SQLite database with retries."""
        retries = 2
        for attempt in range(retries):
            try:
                # Ensure the parent directory exists
                db_dir = os.path.dirname(os.path.abspath(self.db_path))
                if db_dir and not os.path.exists(db_dir):
                    os.makedirs(db_dir, exist_ok=True)

                conn = sqlite3.connect(
                    self.db_path, timeout=5.0
                )
                # Enable foreign keys and set WAL mode for better concurrency
                conn.execute("PRAGMA foreign_keys = ON;")
                conn.execute("PRAGMA journal_mode = WAL;")
                return conn
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e) and attempt < retries - 1:
                    logger.warning("Database locked, retrying connection...")
                    time.sleep(0.05)
                else:
                    logger.error(
                        f"Failed to connect to session DB at {self.db_path}: {e}"
                    )
                    raise
            except sqlite3.Error as e:
                logger.error(f"Failed to connect to session DB at {self.db_path}: {e}")
                raise

    def _with_retry(
        self,
        op: "Callable[[], T]",
        op_name: str,
        reraise: bool = True,
    ) -> "Optional[T]":
        """Run a DB operation, retrying once on 'database is locked'.

        On a non-locked failure, log and either re-raise (mutations) or return
        None (read-only queries that should degrade gracefully).
        """
        for attempt in range(2):
            try:
                return op()
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e) and attempt == 0:
                    logger.warning("Database locked during %s, retrying...", op_name)
                    time.sleep(0.05)
                    continue
                logger.error("%s failed: %s", op_name, e)
                if reraise:
                    raise
                return None
            except sqlite3.Error as e:
                logger.error("%s failed: %s", op_name, e)
                if reraise:
                    raise
                return None

    def init_db(self) -> None:
        """Initializes tables and FTS5 search virtualization on first use."""
        self.conn = self._get_connection()
        try:
            with self.conn:
                self.conn.execute("""
                    CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY,
                        timestamp TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        session_id TEXT DEFAULT 'default',
                        turn_id INTEGER
                    );
                """)
                # Sessions metadata table
                self.conn.execute("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        session_id TEXT PRIMARY KEY,
                        title TEXT NOT NULL DEFAULT 'New Chat',
                        created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    );
                """)
                # Migration: add updated_at column if missing (existing DBs)
                try:
                    self.conn.execute(
                        "ALTER TABLE sessions ADD COLUMN updated_at TEXT DEFAULT NULL"
                    )
                except sqlite3.OperationalError:
                    pass  # Column already exists
                # Migration: add session-isolation columns for launch identity and lineage
                for col, coltype in (
                    ("source", "TEXT DEFAULT 'voice'"),
                    ("launch_id", "TEXT DEFAULT NULL"),
                    ("parent_session_id", "TEXT DEFAULT NULL"),
                ):
                    try:
                        self.conn.execute(
                            f"ALTER TABLE sessions ADD COLUMN {col} {coltype}"
                        )
                    except sqlite3.OperationalError:
                        pass  # Column already exists

                # Check for FTS5 support before creating virtual table
                fts5_supported = True
                try:
                    self.conn.execute(
                        "CREATE VIRTUAL TABLE IF NOT EXISTS temp_fts USING fts5(content);"
                    )
                    self.conn.execute("DROP TABLE temp_fts;")
                except sqlite3.OperationalError:
                    fts5_supported = False
                    logger.warning(
                        "FTS5 is not supported by sqlite3. Falling back to normal LIKE searches."
                    )

                if fts5_supported:
                    # In SQLite FTS5, external content tables can keep mapping to messages
                    self.conn.execute("""
                        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                            content,
                            content='messages',
                            content_rowid='id'
                        );
                    """)
                    # Triggers to keep FTS table in sync
                    self.conn.execute("""
                        CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                            INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
                        END;
                    """)
                    self.conn.execute("""
                        CREATE TRIGGER IF NOT EXISTS messages_ad
                        AFTER DELETE ON messages BEGIN
                            INSERT INTO messages_fts(messages_fts, rowid, content)
                            VALUES('delete', old.id, old.content);
                        END;
                    """)
                    self.conn.execute("""
                        CREATE TRIGGER IF NOT EXISTS messages_au
                        AFTER UPDATE ON messages BEGIN
                            INSERT INTO messages_fts(messages_fts, rowid, content)
                            VALUES('delete', old.id, old.content);
                            INSERT INTO messages_fts(rowid, content)
                            VALUES (new.id, new.content);
                        END;
                    """)
                else:
                    self.fts5_supported = False

                self.fts5_supported = fts5_supported

                # Index for fast session-ordered message retrieval
                self.conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_messages_session_id "
                    "ON messages(session_id, id);"
                )
        except sqlite3.Error as e:
            logger.error(f"Database initialization failed: {e}")
            raise

    def append(
        self,
        role: str,
        content: str,
        session_id: str = "default",
        turn_id: Optional[str] = None,
    ) -> None:
        """Appends a single message to history."""
        def _do():
            with self.conn:
                self.conn.execute(
                    "INSERT INTO messages (role, content, session_id, turn_id) "
                    "VALUES (?, ?, ?, ?);",
                    (role, content, session_id, turn_id),
                )
        self._with_retry(_do, "append message to history")

    def append_tool(
        self,
        turn_id: str,
        tool_name: str,
        args: dict,
        result: str,
        session_id: str = "default",
    ) -> None:
        """Append a tool execution result as a role='tool' row.

        Truncated save to prevent DB bloat: tool name + args + first
        _TOOL_PERSIST_MAX_CHARS chars of result.
        """
        import json as _json

        max_chars = _TOOL_PERSIST_MAX_CHARS
        try:
            args_str = _json.dumps(args, ensure_ascii=False)
        except (TypeError, ValueError):
            args_str = str(args)
        truncated_result = (result or "")[:max_chars]
        content = f"[{tool_name} args={args_str}] result: {truncated_result}"
        self.append("tool", content, session_id=session_id, turn_id=turn_id)

    def search(self, query: str, limit: int = 5) -> List[Tuple[str, str]]:
        """Searches past conversation content."""

        def _do():
            cursor = self.conn.cursor()
            if self.fts5_supported:
                cursor.execute(
                    """
                    SELECT role, content FROM messages
                    WHERE id IN (
                        SELECT rowid FROM messages_fts
                        WHERE messages_fts MATCH ?
                    )
                    ORDER BY id DESC LIMIT ?;
                    """,
                    (query, limit),
                )
            else:
                # Fallback to standard SQL LIKE query
                cursor.execute(
                    """
                    SELECT role, content FROM messages
                    WHERE content LIKE ?
                    ORDER BY id DESC LIMIT ?;
                    """,
                    (f"%{query}%", limit),
                )
            return cursor.fetchall()

        return self._with_retry(_do, "search", reraise=False) or []

    def get_recent(
        self, limit: int = 20, session_id: str = "default"
    ) -> List[Tuple[str, str]]:
        """Returns the most recent messages for a session, oldest first."""

        def _do():
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            )
            return list(reversed(cursor.fetchall()))

        return self._with_retry(_do, "get_recent", reraise=False) or []

    def create_session(
        self,
        session_id: str,
        title: str = "New Chat",
        source: str = "voice",
        launch_id: Optional[str] = None,
        parent_session_id: Optional[str] = None,
    ) -> None:
        """Creates a session metadata row with origin tracking."""
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT OR IGNORE INTO sessions "
                    "(session_id, title, source, launch_id, parent_session_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (session_id, title, source, launch_id, parent_session_id),
                )
                # If the row already exists but source/launch_id were NULL,
                # backfill them so filtering works for sessions created before this migration.
                self.conn.execute(
                    "UPDATE sessions SET source = COALESCE(source, ?), "
                    " launch_id = COALESCE(launch_id, ?) WHERE session_id = ?",
                    (source, launch_id, session_id),
                )
        except sqlite3.Error as e:
            logger.error(f"create_session failed: {e}")

    def get_sessions(
        self,
        source: Optional[str] = None,
        launch_id: Optional[str] = None,
    ) -> List[Tuple[str, str, str, str, str]]:
        """Returns matching sessions as (session_id, title, created_at, updated_at, launch_id), newest first.

        Pass source and/or launch_id to filter. Pass neither to list all.
        """
        try:
            cursor = self.conn.cursor()
            clauses: List[str] = []
            params: List[str] = []
            if source is not None:
                clauses.append("source = ?")
                params.append(source)
            if launch_id is not None:
                clauses.append("launch_id = ?")
                params.append(launch_id)
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            sql = (
                f"SELECT session_id, title, created_at, updated_at, launch_id"
                f" FROM sessions{where} ORDER BY created_at DESC"
            )
            cursor.execute(sql, params)
            return cursor.fetchall()
        except sqlite3.Error as e:
            logger.error(f"get_sessions failed: {e}")
            return []

    def update_session_title(self, session_id: str, title: str) -> None:
        """Updates the title and updated_at of a session."""
        try:
            with self.conn:
                self.conn.execute(
                    "UPDATE sessions SET title = ?, updated_at = ? WHERE session_id = ?",
                    (title, utc_now_iso(), session_id),
                )
        except sqlite3.Error as e:
            logger.error(f"update_session_title failed: {e}")

    def touch_session(self, session_id: str) -> None:
        """Updates updated_at timestamp for a session (marks last activity)."""
        try:
            with self.conn:
                self.conn.execute(
                    "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
                    (utc_now_iso(), session_id),
                )
        except sqlite3.Error as e:
            logger.error(f"touch_session failed: {e}")

    def delete_session(self, session_id: str) -> None:
        """Deletes a session and all its messages."""
        try:
            with self.conn:
                cursor = self.conn.cursor()
                cursor.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
                cursor.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            logger.info(f"delete_session | session_id={session_id}")
        except Exception as e:
            logger.error(f"delete_session failed: {e}")
            raise

    def get_session_messages(
        self, session_id: str, limit: int = 50
    ) -> List[Tuple[str, str]]:
        """Returns messages for a specific session, oldest first."""
        return self.get_recent(limit=limit, session_id=session_id)

    def close(self) -> None:
        """Closes connection cleanly."""
        if self.conn:
            try:
                self.conn.close()
            except sqlite3.Error:
                pass
            self.conn = None
