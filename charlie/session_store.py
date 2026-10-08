import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple, TypeVar

T = TypeVar("T")

from charlie.turn_contracts import ResultEnvelope
from charlie.utils import utc_now_iso

logger = logging.getLogger("charlie.session_store")

# --- Constants ---
_TOOL_PERSIST_MAX_CHARS = 500  # cap stored tool result length to prevent DB bloat


class SessionStoreError(RuntimeError):
    """Base class for truthful session persistence failures."""


class SessionNotFoundError(SessionStoreError):
    """A message/tool/session mutation referenced no existing session."""


class SessionConflictError(SessionStoreError):
    """A session ID is already bound to incompatible immutable metadata."""


class SessionStorageError(SessionStoreError):
    """SQLite failed a session persistence operation."""


class SessionOutcomeUnknownError(SessionStorageError):
    """Commit outcome could not be established; mutation was not retried."""


class _WriteContext:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.dml_started = False

    def execute(self, sql: str, parameters: tuple[Any, ...] = ()):
        keyword = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
        if keyword in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
            self.dml_started = True
        return self.connection.execute(sql, parameters)


def canonical_session_request_fingerprint(operation: str, payload: dict[str, Any]) -> str:
    """Stable identity for main-owned session mutations."""
    if operation == "create":
        identity = {
            "operation": operation,
            "session_id": payload.get("session_id"),
            "title": str(payload.get("title", "New Chat")).strip(),
            "source": payload.get("source"),
            "parent_session_id": payload.get("parent_session_id"),
        }
    elif operation == "rename":
        identity = {
            "operation": operation,
            "session_id": payload.get("session_id"),
            "title": str(payload.get("title", "")).strip(),
        }
    elif operation in {"delete", "active", "chat"}:
        identity = {
            "operation": operation,
            "session_id": payload.get("session_id"),
        }
        if operation == "chat":
            identity["text"] = str(payload.get("text") or "")
    else:
        identity = {"operation": operation, **payload}
    return json.dumps(identity, sort_keys=True, separators=(",", ":"))


class SessionStore:
    """Persistent SQLite-backed session history store with FTS5 search."""

    def __init__(self, db_path: str = "sessions.db"):
        self.db_path = db_path
        self._local = threading.local()
        self._connections: set[sqlite3.Connection] = set()
        self._connections_lock = threading.Lock()
        self._closed = False
        # Reason the most recent degrading read fell back to an empty result,
        # or None while reads are succeeding. A caller must be able to tell
        # "no history stored" from "history could not be read".
        self.last_read_error: Optional[str] = None
        self.init_db()

    @property
    def conn(self):
        if self._closed:
            raise SessionStorageError("SessionStore is closed")
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

                conn = sqlite3.connect(self.db_path, timeout=5.0, check_same_thread=False)
                # Enable foreign keys and set WAL mode for better concurrency
                conn.execute("PRAGMA foreign_keys = ON;")
                conn.execute("PRAGMA journal_mode = WAL;")
                with self._connections_lock:
                    self._connections.add(conn)
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

    def _note_read_degradation(self, op_name: str, exc: BaseException) -> None:
        """Record a read that degraded to an empty result instead of raising.

        Read-only queries return ``None`` on a non-locked ``sqlite3.Error`` so
        callers keep working, which means a lost durable read is otherwise
        indistinguishable from "this session genuinely has no history". The
        reason is kept here so a caller that cares can tell the two apart.
        """

        self.last_read_error = f"{op_name}: {type(exc).__name__}: {exc}"

    def _with_retry(
        self,
        op: "Callable[[], T]",
        op_name: str,
        reraise: bool = True,
    ) -> "Optional[T]":
        """Run a DB operation, retrying once on 'database is locked'.

        On a non-locked failure, log and either re-raise (mutations) or return
        None (read-only queries that should degrade gracefully). A degrading
        read records the reason in ``last_read_error``; a succeeding one clears
        it, so the marker never outlives the failure.
        """
        for attempt in range(2):
            try:
                result = op()
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e) and attempt == 0:
                    logger.warning("Database locked during %s, retrying...", op_name)
                    time.sleep(0.05)
                    continue
                logger.error("%s failed: %s", op_name, e)
                if reraise:
                    raise
                self._note_read_degradation(op_name, e)
                return None

            except sqlite3.Error as e:
                logger.error("%s failed: %s", op_name, e)
                if reraise:
                    raise
                self._note_read_degradation(op_name, e)
                return None

            if not reraise:
                self.last_read_error = None
            return result
        return None

    def _mutate(self, op: Callable[[_WriteContext], T], op_name: str) -> T:
        """Run stage-aware transaction; never replay DML after uncertain commit."""
        for begin_attempt in range(2):
            connection = self.conn
            try:
                connection.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower() and begin_attempt == 0:
                    time.sleep(0.05)
                    continue
                raise SessionStorageError(f"{op_name} could not begin") from exc
            context = _WriteContext(connection)
            try:
                result = op(context)
            except SessionStoreError:
                connection.rollback()
                raise
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                if "session_not_found" in str(exc):
                    raise SessionNotFoundError("Referenced session does not exist") from exc
                raise SessionStorageError(f"{op_name} integrity failure") from exc
            except sqlite3.OperationalError as exc:
                connection.rollback()
                if "locked" in str(exc).lower() and not context.dml_started and begin_attempt == 0:
                    time.sleep(0.05)
                    continue
                if "locked" in str(exc).lower() and context.dml_started:
                    raise SessionOutcomeUnknownError(f"{op_name} outcome is unknown") from exc
                raise SessionStorageError(f"{op_name} failed") from exc
            except sqlite3.Error as exc:
                connection.rollback()
                raise SessionStorageError(f"{op_name} failed") from exc
            except Exception:
                connection.rollback()
                raise

            commit_error: Optional[BaseException] = None
            for _ in range(2):
                try:
                    connection.commit()
                    commit_error = None
                    break
                except sqlite3.OperationalError as exc:
                    commit_error = exc
                    if "locked" not in str(exc).lower():
                        break
                    time.sleep(0.05)
                except sqlite3.Error as exc:
                    commit_error = exc
                    break
            if commit_error is not None:
                self._retire_connection(connection)
                raise SessionOutcomeUnknownError(f"{op_name} commit outcome is unknown") from commit_error
            return result
        raise SessionStorageError(f"{op_name} could not acquire transaction")

    def _retire_connection(self, connection: sqlite3.Connection) -> None:
        with self._connections_lock:
            self._connections.discard(connection)
        if getattr(self._local, "conn", None) is connection:
            self._local.conn = None
        try:
            connection.close()
        except sqlite3.Error:
            logger.debug("Failed to retire unusable session connection", exc_info=True)

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
                # Structured tool activity log (distinct from the free-text messages table)
                self.conn.execute(
                    """CREATE TABLE IF NOT EXISTS tool_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        name TEXT NOT NULL,
                        text TEXT,
                        created_at TEXT NOT NULL
                    )"""
                )
                self.conn.execute(
                    """CREATE TABLE IF NOT EXISTS dashboard_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        event_id TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        UNIQUE(session_id, event_id)
                    )"""
                )
                self.conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_dashboard_events_session "
                    "ON dashboard_events(session_id, id);"
                )
                # Existing databases predate declared foreign keys. These
                # idempotent triggers enforce the same parent-session invariant
                # without rebuilding historical tables in place.
                self.conn.execute(
                    """CREATE TRIGGER IF NOT EXISTS messages_require_session
                    BEFORE INSERT ON messages
                    WHEN NEW.session_id IS NULL OR NOT EXISTS (
                        SELECT 1 FROM sessions WHERE session_id = NEW.session_id
                    )
                    BEGIN
                        SELECT RAISE(ABORT, 'session_not_found');
                    END;"""
                )
                self.conn.execute(
                    """CREATE TRIGGER IF NOT EXISTS tool_events_require_session
                    BEFORE INSERT ON tool_events
                    WHEN NOT EXISTS (
                        SELECT 1 FROM sessions WHERE session_id = NEW.session_id
                    )
                    BEGIN
                        SELECT RAISE(ABORT, 'session_not_found');
                    END;"""
                )
                self.conn.execute(
                    """CREATE TRIGGER IF NOT EXISTS dashboard_events_require_session
                    BEFORE INSERT ON dashboard_events
                    WHEN NOT EXISTS (
                        SELECT 1 FROM sessions WHERE session_id = NEW.session_id
                    )
                    BEGIN
                        SELECT RAISE(ABORT, 'session_not_found');
                    END;"""
                )
                self.conn.execute(
                    """CREATE TRIGGER IF NOT EXISTS sessions_delete_messages
                    AFTER DELETE ON sessions
                    BEGIN
                        DELETE FROM messages WHERE session_id = OLD.session_id;
                    END;"""
                )
                self.conn.execute(
                    """CREATE TRIGGER IF NOT EXISTS sessions_delete_tool_events
                    AFTER DELETE ON sessions
                    BEGIN
                        DELETE FROM tool_events WHERE session_id = OLD.session_id;
                    END;"""
                )
                self.conn.execute(
                    """CREATE TRIGGER IF NOT EXISTS sessions_delete_dashboard_events
                    AFTER DELETE ON sessions
                    BEGIN
                        DELETE FROM dashboard_events WHERE session_id = OLD.session_id;
                    END;"""
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
        def _do(tx: _WriteContext):
            tx.execute(
                "INSERT INTO messages (role, content, session_id, turn_id) "
                "VALUES (?, ?, ?, ?);",
                (role, content, session_id, turn_id),
            )
            # Keep updated_at current so the sidebar sorts by latest activity
            tx.execute(
                "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
                (utc_now_iso(), session_id),
            )
        self._mutate(_do, "append message to history")


    def append_tool(
        self,
        turn_id: Optional[str],
        tool_name: str,
        args: dict,
        result: Any,
        session_id: str = "default",
        *,
        approval_status: Optional[str] = None,
    ) -> None:
        """Append a tool execution result as a role='tool' row.

        Production callers pass the canonical ``ResultEnvelope``. Legacy text
        callers remain supported at this history-rendering boundary.
        Truncated save to prevent DB bloat: tool name + args + first
        _TOOL_PERSIST_MAX_CHARS chars of result.

        The approval outcome is persisted alongside the row. An approval that
        was granted, refused, or bypassed is the part of a tool record that has
        to stay explainable after the fact, so it must not be reconstructible
        only from memory of the request.
        """
        import json as _json

        max_chars = _TOOL_PERSIST_MAX_CHARS
        try:
            args_str = _json.dumps(args, ensure_ascii=False)
        except (TypeError, ValueError):
            args_str = str(args)
        if approval_status is None and isinstance(result, ResultEnvelope):
            data = getattr(result, "data", None)
            if isinstance(data, dict):
                approval_status = data.get("approval_status")
        status_suffix = f" approval={approval_status}" if approval_status else ""
        result_text = result.result if isinstance(result, ResultEnvelope) else result
        truncated_result = str(result_text or "")[:max_chars]
        content = f"[{tool_name} args={args_str}]{status_suffix} result: {truncated_result}"
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
                        SELECT messages.role, messages.content FROM messages
                        JOIN sessions ON sessions.session_id = messages.session_id
                        WHERE messages.id IN (
                            SELECT rowid FROM messages_fts
                            WHERE messages_fts MATCH ?
                        )
                        ORDER BY messages.id DESC LIMIT ?;
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
                        SELECT messages.role, messages.content FROM messages
                        JOIN sessions ON sessions.session_id = messages.session_id
                        WHERE messages.content LIKE ?
                        ORDER BY messages.id DESC LIMIT ?;
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
                "SELECT messages.role, messages.content FROM messages "
                "JOIN sessions ON sessions.session_id = messages.session_id "
                "WHERE messages.session_id = ? ORDER BY messages.id DESC LIMIT ?",
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
    ) -> dict[str, Any]:
        """Create or return one canonical session row."""
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id is required")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title is required")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("source is required")
        def _do(tx: _WriteContext) -> dict[str, Any]:
            existing = tx.execute(
                "SELECT session_id, title, created_at, updated_at, source, launch_id, parent_session_id "
                "FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing[4] != source
                    or existing[5] != launch_id
                    or existing[6] != parent_session_id
                ):
                    raise SessionConflictError(
                        f"Session '{session_id}' is bound to incompatible lineage"
                    )
                return self._session_record(existing)
            tx.execute(
                "INSERT INTO sessions "
                "(session_id, title, source, launch_id, parent_session_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, title, source, launch_id, parent_session_id),
            )
            row = tx.execute(
                "SELECT session_id, title, created_at, updated_at, source, launch_id, parent_session_id "
                "FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise SessionStorageError(f"Session '{session_id}' was not persisted")
            return self._session_record(row)
        return self._mutate(_do, "create session")

    @staticmethod
    def _session_record(row: tuple[Any, ...]) -> dict[str, Any]:
        return {
            "session_id": row[0],
            "title": row[1],
            "created_at": row[2],
            "updated_at": row[3],
            "source": row[4],
            "launch_id": row[5],
            "parent_session_id": row[6],
        }

    def get_session_record(self, session_id: str) -> Optional[dict[str, Any]]:
        try:
            row = self.conn.execute(
                "SELECT session_id, title, created_at, updated_at, source, launch_id, parent_session_id "
                "FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            return self._session_record(row) if row is not None else None
        except sqlite3.Error as exc:
            raise SessionStorageError(f"Failed to read session '{session_id}'") from exc

    def session_exists(self, session_id: str) -> bool:
        return self.get_session_record(session_id) is not None

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
        """Update title; raise when session is missing or storage fails."""
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title is required")

        def _do(tx: _WriteContext) -> None:
            cursor = tx.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE session_id = ?",
                (title, utc_now_iso(), session_id),
            )
            if cursor.rowcount != 1:
                raise SessionNotFoundError(f"Session '{session_id}' does not exist")
        self._mutate(_do, "rename session")

    def auto_title_session(self, session_id: str, title: str) -> bool:
        """Set first-turn title only while it is still the placeholder."""
        if not isinstance(title, str) or not title.strip():
            return False
        def _do(tx: _WriteContext) -> bool:
            cursor = tx.execute(
                "UPDATE sessions SET title = ?, updated_at = ? "
                "WHERE session_id = ? AND title = 'New Chat'",
                (title, utc_now_iso(), session_id),
            )
            return cursor.rowcount == 1
        return self._mutate(_do, "auto-title session")

    def touch_session(self, session_id: str) -> None:
        """Update activity timestamp; raise when session is missing."""
        def _do(tx: _WriteContext) -> None:
            cursor = tx.execute(
                "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
                (utc_now_iso(), session_id),
            )
            if cursor.rowcount != 1:
                raise SessionNotFoundError(f"Session '{session_id}' does not exist")
        self._mutate(_do, "touch session")

    def delete_session(self, session_id: str) -> dict[str, Any]:
        """Delete one session and all related rows atomically."""
        def _do(tx: _WriteContext) -> dict[str, Any]:
            row = tx.execute(
                "SELECT session_id, title, created_at, updated_at, source, launch_id, parent_session_id "
                "FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise SessionNotFoundError(f"Session '{session_id}' does not exist")
            tx.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            return self._session_record(row)
        deleted = self._mutate(_do, "delete session")
        logger.info("delete_session | session_id=%s", session_id)
        return deleted

    def purge_transcripts(
        self,
        *,
        older_than_days: Optional[int] = None,
        protected_session_ids: tuple[str, ...] = (),
    ) -> dict[str, int]:
        """Purge transcript rows through the canonical SessionStore transaction."""
        if older_than_days is not None and (
            isinstance(older_than_days, bool) or not isinstance(older_than_days, int) or older_than_days < 0
        ):
            raise ValueError("older_than_days must be a non-negative integer")

        protected = tuple(sorted({session_id for session_id in protected_session_ids if session_id}))

        def _do(tx: _WriteContext) -> dict[str, int]:
            if older_than_days is None:
                if protected:
                    placeholders = ",".join("?" for _ in protected)
                    busy_rows = tx.execute(
                        f"SELECT session_id FROM sessions WHERE session_id IN ({placeholders})",
                        protected,
                    ).fetchall()
                    if busy_rows:
                        busy_ids = ", ".join(str(row[0]) for row in busy_rows)
                        raise SessionConflictError(
                            f"Cannot purge active session data while session(s) are in use: {busy_ids}"
                        )

                messages_purged = int(tx.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
                sessions_purged = int(tx.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])
                tool_events_purged = int(tx.execute("SELECT COUNT(*) FROM tool_events").fetchone()[0])
                dashboard_events_purged = int(tx.execute("SELECT COUNT(*) FROM dashboard_events").fetchone()[0])
                tx.execute("DELETE FROM sessions")
                # Remove legacy orphan rows too; normal rows are already removed
                # by the canonical session-delete triggers.
                tx.execute("DELETE FROM messages")
                tx.execute("DELETE FROM tool_events")
                tx.execute("DELETE FROM dashboard_events")
                return {
                    "messages_purged": messages_purged,
                    "sessions_purged": sessions_purged,
                    "tool_events_purged": tool_events_purged,
                    "dashboard_events_purged": dashboard_events_purged,
                    "items_purged": messages_purged + sessions_purged + tool_events_purged + dashboard_events_purged,
                }

            cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )
            protected_clause = ""
            protected_parameters: tuple[str, ...] = ()
            if protected:
                placeholders = ",".join("?" for _ in protected)
                protected_clause = f" AND (session_id IS NULL OR session_id NOT IN ({placeholders}))"
                protected_parameters = protected
            cutoff_parameters = (cutoff, *protected_parameters)
            messages_purged = int(
                tx.execute(
                    f"SELECT COUNT(*) FROM messages WHERE timestamp < ?{protected_clause}",
                    cutoff_parameters,
                ).fetchone()[0]
            )
            tool_events_purged = int(
                tx.execute(
                    f"SELECT COUNT(*) FROM tool_events WHERE created_at < ?{protected_clause}",
                    cutoff_parameters,
                ).fetchone()[0]
            )
            dashboard_events_purged = int(
                tx.execute(
                    f"SELECT COUNT(*) FROM dashboard_events WHERE created_at < ?{protected_clause}",
                    cutoff_parameters,
                ).fetchone()[0]
            )
            tx.execute(
                f"DELETE FROM messages WHERE timestamp < ?{protected_clause}",
                cutoff_parameters,
            )
            tx.execute(
                f"DELETE FROM tool_events WHERE created_at < ?{protected_clause}",
                cutoff_parameters,
            )
            tx.execute(
                f"DELETE FROM dashboard_events WHERE created_at < ?{protected_clause}",
                cutoff_parameters,
            )
            return {
                "messages_purged": messages_purged,
                "sessions_purged": 0,
                "tool_events_purged": tool_events_purged,
                "dashboard_events_purged": dashboard_events_purged,
                "items_purged": messages_purged + tool_events_purged + dashboard_events_purged,
            }

        return self._mutate(_do, "purge transcripts")

    def backup_to(self, target: str | Path) -> None:
        """Write a consistent SQLite snapshot, including committed WAL rows."""
        target_path = Path(target)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if target_path.resolve() == Path(self.db_path).resolve():
            raise ValueError("Backup target must differ from the live SessionStore database")
        destination = sqlite3.connect(str(target_path))
        try:
            self.conn.backup(destination)
            destination.commit()
        except sqlite3.Error as exc:
            raise SessionStorageError("SessionStore backup failed") from exc
        finally:
            destination.close()

    def get_session_messages(
        self, session_id: str, limit: int = 50
    ) -> List[Tuple[str, str]]:
        """Returns messages for a specific session, oldest first."""
        return self.get_recent(limit=limit, session_id=session_id)

    def append_dashboard_event(
        self,
        session_id: str,
        event_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        timestamp: Optional[str] = None,
    ) -> None:
        """Persist a bounded Dashboard projection inside its owning session."""
        if not session_id or not event_id or not event_type or not isinstance(payload, dict):
            raise ValueError("session, event id, event type, and object payload are required")
        payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        created_at = timestamp or utc_now_iso()
        is_research = event_type == "research_result"
        history_type_clause = "event_type = 'research_result'" if is_research else "event_type != 'research_result'"
        history_limit = 10 if is_research else 40

        def _do(tx: _WriteContext) -> None:
            tx.execute(
                "INSERT INTO dashboard_events (session_id, event_id, event_type, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(session_id, event_id) DO UPDATE SET "
                "event_type = excluded.event_type, "
                "payload_json = excluded.payload_json, "
                "created_at = excluded.created_at",
                (session_id, event_id, event_type, payload_json, created_at),
            )
            tx.execute(
                f"DELETE FROM dashboard_events WHERE session_id = ? AND {history_type_clause} "
                "AND id NOT IN (SELECT id FROM dashboard_events WHERE session_id = ? AND "
                f"{history_type_clause} ORDER BY id DESC LIMIT ?)",
                (session_id, session_id, history_limit),
            )

        self._mutate(_do, "persist dashboard event")

    def get_dashboard_events(self, session_id: str, limit: int = 50) -> List[dict[str, Any]]:
        """Return recent session Dashboard events oldest first for stable replay."""
        if not session_id:
            return []
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("limit must be a non-negative integer")
        bounded_limit = min(limit, 50)
        try:
            rows = self.conn.execute(
                "SELECT event_id, event_type, payload_json, created_at FROM dashboard_events "
                "WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, bounded_limit),
            ).fetchall()
            events: List[dict[str, Any]] = []
            for event_id, event_type, payload_json, timestamp in reversed(rows):
                try:
                    payload = json.loads(payload_json)
                except (TypeError, ValueError):
                    logger.warning("Ignoring malformed persisted dashboard event %s", event_id)
                    continue
                if isinstance(payload, dict):
                    events.append({"id": event_id, "type": event_type, "timestamp": timestamp, "payload": payload})
            return events
        except sqlite3.Error as exc:
            raise SessionStorageError(f"Failed to read dashboard events for '{session_id}'") from exc

    def append_tool_event(
        self,
        session_id: str,
        kind: str,
        name: str,
        text: Optional[str] = None,
    ) -> None:
        """Records a structured tool activity (call/result) for a session."""
        def _do(tx: _WriteContext) -> None:
            tx.execute(
                "INSERT INTO tool_events (session_id, kind, name, text, created_at) "
                "VALUES (?,?,?,?,?)",
                (session_id, kind, name, text, utc_now_iso()),
            )
        self._mutate(_do, "append tool event")

    def get_tool_events(self, session_id: str) -> List[Tuple[str, str, Optional[str]]]:
        """Returns (kind, name, text) tool events for a session, oldest first."""
        try:
            rows = self.conn.execute(
                "SELECT kind, name, text FROM tool_events WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
            return [(r[0], r[1], r[2]) for r in rows]
        except sqlite3.Error as exc:
            raise SessionStorageError(f"Failed to read tool events for '{session_id}'") from exc

    def count_legacy_orphans(self) -> dict[str, int]:
        """Count pre-existing rows that violate the new parent-session invariant."""
        try:
            messages = self.conn.execute(
                "SELECT COUNT(*) FROM messages "
                "WHERE session_id IS NULL OR NOT EXISTS "
                "(SELECT 1 FROM sessions WHERE sessions.session_id = messages.session_id)"
            ).fetchone()[0]
            tool_events = self.conn.execute(
                "SELECT COUNT(*) FROM tool_events "
                "WHERE NOT EXISTS "
                "(SELECT 1 FROM sessions WHERE sessions.session_id = tool_events.session_id)"
            ).fetchone()[0]
            dashboard_events = self.conn.execute(
                "SELECT COUNT(*) FROM dashboard_events "
                "WHERE NOT EXISTS "
                "(SELECT 1 FROM sessions WHERE sessions.session_id = dashboard_events.session_id)"
            ).fetchone()[0]
            return {
                "messages": int(messages),
                "tool_events": int(tool_events),
                "dashboard_events": int(dashboard_events),
            }
        except sqlite3.Error as exc:
            raise SessionStorageError("Failed to count legacy session orphans") from exc

    def close(self) -> None:
        """Closes connection cleanly."""
        with self._connections_lock:
            connections = list(self._connections)
            self._connections.clear()
            self._closed = True
        for connection in connections:
            try:
                connection.close()
            except sqlite3.Error:
                pass
        if hasattr(self._local, "conn"):
            self._local.conn = None
