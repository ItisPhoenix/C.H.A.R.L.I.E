import sqlite3
import logging
import re

logger = logging.getLogger("charlie.memory_manager")

STOP_WORDS = {
    "the", "and", "for", "that", "with", "have", "this", "will", "your",
    "from", "they", "know", "want", "been", "good", "much", "some", "time",
    "very", "when", "come", "here", "just", "like", "over", "think", "also",
    "back", "after", "use", "two", "how", "what", "where", "who", "why",
    "charlie", "remember", "forget",
}


class MemoryManager:
    """Persistent long-term memory for Charlie.

    Stores facts, events, and conversation summaries in SQLite.
    Retrieval is keyword-based (extract → match → rank by recency).
    """

    def __init__(self, db_path: str = None):
        self.db_path = db_path or "charlie_memory.db"
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    # ── internal helpers ──────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL CHECK(type IN ('fact','event','conversation_summary')),
                category TEXT,
                content TEXT NOT NULL,
                confidence REAL DEFAULT 1.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS memory_keywords (
                memory_id INTEGER,
                keyword TEXT,
                FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_keyword ON memory_keywords(keyword);
            CREATE INDEX IF NOT EXISTS idx_type ON memories(type);
        """)
        conn.commit()

    def _extract_keywords(self, text: str) -> list[str]:
        """Normalise to lowercase, find words >=3 chars, filter stop-words, return first 10."""
        words = re.findall(r"\b\w{3,}\b", text.lower())
        filtered = [w for w in words if w not in STOP_WORDS]
        return filtered[:10]

    # ── public API ────────────────────────────────────────────────────

    def store(self, content: str, type: str, category: str = None,
              confidence: float = 1.0) -> int:
        """Insert a memory row, extract keywords, and return the new row id."""
        conn = self._get_conn()
        cur = conn.execute(
            "INSERT INTO memories (type, category, content, confidence) VALUES (?, ?, ?, ?)",
            (type, category, content, confidence),
        )
        mem_id = cur.lastrowid
        keywords = self._extract_keywords(content)
        conn.executemany(
            "INSERT INTO memory_keywords (memory_id, keyword) VALUES (?, ?)",
            [(mem_id, kw) for kw in keywords],
        )
        conn.commit()
        logger.debug(f"stored_memory | id={mem_id} type={type} category={category}")
        return mem_id

    def search(self, query: str, type: str = None, limit: int = 5) -> list[dict]:
        """Keyword search across memory_keywords.

        Extracts keywords from *query*, finds matching memory_id rows,
        groups by id, orders by last_accessed DESC, and returns up to *limit*.
        """
        keywords = self._extract_keywords(query)
        if not keywords:
            return []

        conn = self._get_conn()
        placeholders = ",".join("?" for _ in keywords)
        sql = (
            f"SELECT m.id, m.type, m.category, m.content, m.confidence "
            f"FROM memories m "
            f"INNER JOIN memory_keywords mk ON mk.memory_id = m.id "
            f"WHERE mk.keyword IN ({placeholders})"
        )
        params = list(keywords)
        if type:
            sql += " AND m.type = ?"
            params.append(type)
        sql += " GROUP BY m.id ORDER BY MAX(m.last_accessed) DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        # Bump last_accessed for returned rows
        ids = [r["id"] for r in rows]
        if ids:
            conn.executemany(
                "UPDATE memories SET last_accessed = CURRENT_TIMESTAMP WHERE id = ?",
                [(i,) for i in ids],
            )
            conn.commit()

        return [dict(r) for r in rows]

    def get_core_facts(self, limit: int = 20) -> list[str]:
        """Return *content* for rows where type='fact', ordered by last_accessed DESC."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT content FROM memories WHERE type = 'fact' "
            "ORDER BY last_accessed DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [r["content"] for r in rows]

    def delete_by_content(self, content_substring: str) -> bool:
        """Delete memories whose content matches *exactly* (case-sensitive, full string).

        Returns True if any row was deleted.  Strict exact match only.
        """
        conn = self._get_conn()
        cur = conn.execute("DELETE FROM memories WHERE content = ?", (content_substring,))
        conn.commit()
        return cur.rowcount > 0

    def consolidate_old_summaries(self, older_than_days: int = 7) -> str:
        """Stub — real body implemented in Step 5 of the plan."""
        return "consolidate_old_summaries not yet implemented"
