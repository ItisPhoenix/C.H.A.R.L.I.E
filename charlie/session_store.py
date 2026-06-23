import os
import sqlite3
import logging
import time
from typing import List, Tuple

logger = logging.getLogger("charlie.session_store")

class SessionStore:
    """Persistent SQLite-backed session history store with FTS5 search."""
    
    def __init__(self, db_path: str = "sessions.db"):
        self.db_path = db_path
        self.conn = None
        self.init_db()

    def _get_connection(self):
        """Helper to get or reconnect to SQLite database with retries."""
        retries = 2
        for attempt in range(retries):
            try:
                # Ensure the parent directory exists
                db_dir = os.path.dirname(os.path.abspath(self.db_path))
                if db_dir and not os.path.exists(db_dir):
                    os.makedirs(db_dir, exist_ok=True)
                
                conn = sqlite3.connect(self.db_path, timeout=5.0)
                # Enable foreign keys and set WAL mode for better concurrency
                conn.execute("PRAGMA foreign_keys = ON;")
                conn.execute("PRAGMA journal_mode = WAL;")
                return conn
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e) and attempt < retries - 1:
                    logger.warning("Database locked, retrying connection...")
                    time.sleep(0.05)
                else:
                    logger.error(f"Failed to connect to session DB at {self.db_path}: {e}")
                    raise
            except sqlite3.Error as e:
                logger.error(f"Failed to connect to session DB at {self.db_path}: {e}")
                raise

    def init_db(self) -> None:
        """Initializes tables and FTS5 search virtualization on first use."""
        self.conn = self._get_connection()
        try:
            with self.conn:
                self.conn.execute("""
                    CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY,
                        timestamp TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        session_id TEXT DEFAULT 'default',
                        turn_id INTEGER
                    );
                """)
                
                # Check for FTS5 support before creating virtual table
                fts5_supported = True
                try:
                    self.conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp_fts USING fts5(content);")
                    self.conn.execute("DROP TABLE temp_fts;")
                except sqlite3.OperationalError:
                    fts5_supported = False
                    logger.warning("FTS5 is not supported by sqlite3. Falling back to normal LIKE searches.")
                
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
                        CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
                            INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
                        END;
                    """)
                    self.conn.execute("""
                        CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
                            INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
                            INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
                        END;
                    """)
                else:
                    self.fts5_supported = False
                
                self.fts5_supported = fts5_supported
        except sqlite3.Error as e:
            logger.error(f"Database initialization failed: {e}")
            raise

    def append(self, role: str, content: str, session_id: str = "default") -> None:
        """Appends a single message to history."""
        retries = 2
        for attempt in range(retries):
            try:
                with self.conn:
                    self.conn.execute(
                        "INSERT INTO messages (role, content, session_id) VALUES (?, ?, ?);",
                        (role, content, session_id)
                    )
                return
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e) and attempt < retries - 1:
                    logger.warning("Database locked during append, retrying...")
                    time.sleep(0.05)
                else:
                    logger.error(f"Failed to append message to history: {e}")
                    raise
            except sqlite3.Error as e:
                logger.error(f"Failed to append message to history: {e}")
                raise

    def search(self, query: str, limit: int = 5) -> List[Tuple[str, str]]:
        """Searches past conversation content."""
        retries = 2
        for attempt in range(retries):
            try:
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
                        (query, limit)
                    )
                else:
                    # Fallback to standard SQL LIKE query
                    cursor.execute(
                        """
                        SELECT role, content FROM messages 
                        WHERE content LIKE ? 
                        ORDER BY id DESC LIMIT ?;
                        """,
                        (f"%{query}%", limit)
                    )
                return cursor.fetchall()
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e) and attempt < retries - 1:
                    logger.warning("Database locked during search, retrying...")
                    time.sleep(0.05)
                else:
                    logger.error(f"FTS5/search failed: {e}")
                    return []
            except sqlite3.Error as e:
                logger.error(f"Search failed: {e}")
                return []

    def close(self) -> None:
        """Closes connection cleanly."""
        if self.conn:
            try:
                self.conn.close()
            except sqlite3.Error:
                pass
            self.conn = None
