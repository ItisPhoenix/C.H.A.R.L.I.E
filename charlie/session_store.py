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
                    ("project_id", "TEXT DEFAULT NULL"),
                ):
                    try:
                        self.conn.execute(
                            f"ALTER TABLE sessions ADD COLUMN {col} {coltype}"
                        )
                    except sqlite3.OperationalError:
                        pass  # Column already exists

                # Migration: pre-fix DBs have non-ISO timestamps (no T/Z), misparsed by browsers as local time.
                self.conn.execute(
                    "UPDATE sessions SET created_at = REPLACE(created_at, ' ', 'T') || 'Z' "
                    "WHERE created_at IS NOT NULL AND created_at NOT LIKE '%Z'"
                )
                self.conn.execute(
                    "UPDATE sessions SET updated_at = REPLACE(updated_at, ' ', 'T') || 'Z' "
                    "WHERE updated_at IS NOT NULL AND updated_at NOT LIKE '%Z'"
                )

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
                # Structured tool activity log (distinct from the free-text messages table)
                self.conn.execute(
                    """CREATE TABLE IF NOT EXISTS tool_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        turn_id TEXT,
                        kind TEXT NOT NULL,
                        name TEXT NOT NULL,
                        text TEXT,
                        created_at TEXT NOT NULL
                    )"""
                )
                # Migration: add turn_id to pre-existing tool_events tables
                try:
                    self.conn.execute("ALTER TABLE tool_events ADD COLUMN turn_id TEXT")
                except sqlite3.OperationalError:
                    pass  # Column already exists
                self.conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_tool_events_turn_id "
                    "ON tool_events(turn_id)"
                )
                # Sub-agent run status, one row per agent_id, survives web_server restart
                self.conn.execute(
                    """CREATE TABLE IF NOT EXISTS agent_runs (
                        agent_id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        task TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'running',
                        last_tool TEXT,
                        result TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )"""
                )
                self.conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_agent_runs_session_id "
                    "ON agent_runs(session_id, created_at)"
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
        """Appends a single message to history and bumps the session timestamp."""
        def _do():
            with self.conn:
                self.conn.execute(
                    "INSERT INTO messages (role, content, session_id, turn_id) "
                    "VALUES (?, ?, ?, ?);",
                    (role, content, session_id, turn_id),
                )
                # Keep updated_at current so the sidebar sorts by latest activity
                self.conn.execute(
                    "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
                    (utc_now_iso(), session_id),
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

    def search(
        self,
        query: str,
        limit: int = 5,
        launch_id: Optional[str] = None,
    ) -> List[Tuple[str, str]]:
        """Searches past conversation content.

        When ``launch_id`` is provided, FTS hits are scoped to sessions that
        belong to that launch (JOINed against the ``sessions`` table). When it
        is omitted, the search falls back to the global behavior over all
        launches (backward compatible).
        """

        def _do():
            cursor = self.conn.cursor()
            if self.fts5_supported:
                if launch_id is not None:
                    cursor.execute(
                        """
                        SELECT messages.role, messages.content FROM messages
                        JOIN sessions ON sessions.session_id = messages.session_id
                        WHERE messages.id IN (
                            SELECT rowid FROM messages_fts
                            WHERE messages_fts MATCH ?
                        )
                        AND sessions.launch_id = ?
                        ORDER BY messages.id DESC LIMIT ?;
                        """,
                        (query, launch_id, limit),
                    )
                else:
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
                if launch_id is not None:
                    cursor.execute(
                        """
                        SELECT messages.role, messages.content FROM messages
                        JOIN sessions ON sessions.session_id = messages.session_id
                        WHERE messages.content LIKE ?
                        AND sessions.launch_id = ?
                        ORDER BY messages.id DESC LIMIT ?;
                        """,
                        (f"%{query}%", launch_id, limit),
                    )
                else:
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
        project_id: Optional[str] = None,
    ) -> None:
        """Creates a session metadata row with origin tracking."""
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT OR IGNORE INTO sessions "
                    "(session_id, title, source, launch_id, parent_session_id, project_id, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (session_id, title, source, launch_id, parent_session_id, project_id, utc_now_iso()),
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

    def append_tool_event(
        self,
        session_id: str,
        turn_id: Optional[str],
        kind: str,
        name: str,
        text: Optional[str] = None,
    ) -> None:
        """Records a structured tool activity (call/result) for a session/turn."""
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT INTO tool_events (session_id, turn_id, kind, name, text, created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (session_id, turn_id, kind, name, text, utc_now_iso()),
                )
        except sqlite3.Error as e:
            logger.error(f"append_tool_event failed: {e}")

    def get_tool_events(self, session_id: str) -> List[Tuple[Optional[str], str, str, Optional[str]]]:
        """Returns (turn_id, kind, name, text) tool events for a session, oldest first."""
        try:
            rows = self.conn.execute(
                "SELECT turn_id, kind, name, text FROM tool_events WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
            return [(r[0], r[1], r[2], r[3]) for r in rows]
        except sqlite3.Error as e:
            logger.error(f"get_tool_events failed: {e}")
            return []

    def get_session_messages_with_turn_id(
        self, session_id: str, limit: int = 50
    ) -> List[Tuple[str, str, Optional[int]]]:
        """Returns (role, content, turn_id) for a session, oldest first.

        Separate from get_session_messages/get_recent so Brain's history-loading
        hot path (which unpacks 2-tuples) stays untouched -- this is REST-only.
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT role, content, turn_id FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            )
            return list(reversed(cursor.fetchall()))
        except sqlite3.Error as e:
            logger.error(f"get_session_messages_with_turn_id failed: {e}")
            return []

    def create_agent_run(self, agent_id: str, task: str, session_id: str) -> None:
        """Records a newly spawned sub-agent as 'running'."""
        try:
            with self.conn:
                now = utc_now_iso()
                self.conn.execute(
                    "INSERT OR REPLACE INTO agent_runs "
                    "(agent_id, session_id, task, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, 'running', ?, ?)",
                    (agent_id, session_id, task, now, now),
                )
        except sqlite3.Error as e:
            logger.error(f"create_agent_run failed: {e}")

    def update_agent_run(
        self,
        agent_id: str,
        last_tool: Optional[str] = None,
        status: Optional[str] = None,
        result: Optional[str] = None,
    ) -> None:
        """Updates an existing agent run's last-seen tool, status, and/or result."""
        fields = ["updated_at = ?"]
        params: List[object] = [utc_now_iso()]
        if last_tool is not None:
            fields.append("last_tool = ?")
            params.append(last_tool)
        if status is not None:
            fields.append("status = ?")
            params.append(status)
        if result is not None:
            fields.append("result = ?")
            params.append(result)
        params.append(agent_id)
        try:
            with self.conn:
                self.conn.execute(
                    f"UPDATE agent_runs SET {', '.join(fields)} WHERE agent_id = ?",
                    params,
                )
        except sqlite3.Error as e:
            logger.error(f"update_agent_run failed: {e}")

    def get_agent_runs(
        self, session_id: Optional[str] = None, limit: int = 100
    ) -> List[Tuple[str, str, str, str, Optional[str], Optional[str], str, str]]:
        """Returns (agent_id, session_id, task, status, last_tool, result, created_at,
        updated_at) rows, most recently created first."""
        try:
            if session_id:
                rows = self.conn.execute(
                    "SELECT agent_id, session_id, task, status, last_tool, result, "
                    "created_at, updated_at FROM agent_runs WHERE session_id = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (session_id, limit),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT agent_id, session_id, task, status, last_tool, result, "
                    "created_at, updated_at FROM agent_runs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [tuple(r) for r in rows]
        except sqlite3.Error as e:
            logger.error(f"get_agent_runs failed: {e}")
            return []

    def close(self) -> None:
        """Closes connection cleanly."""
        if self.conn:
            try:
                self.conn.close()
            except sqlite3.Error:
                pass
            self.conn = None
