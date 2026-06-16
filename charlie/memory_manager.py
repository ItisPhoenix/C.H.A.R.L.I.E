import numpy as np
from charlie.embedder import LocalEmbedder
from charlie.config import config

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
        self.db_path = db_path or config.memory_db_path
        self._conn: sqlite3.Connection | None = None
        self.embedder = None
        self._embedding_dim = config.embedding_dimension
        if config.enable_semantic_memory:
            try:
                self.embedder = LocalEmbedder()
                self.embedder._load()
                logger.info("Embedder loaded synchronously.")
            except Exception as e:
                logger.error(f"Failed to load embedder: {e}. Semantic memory disabled.")
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
        cursor = conn.execute("PRAGMA table_info(memories)")
        columns = [row[1] for row in cursor.fetchall()]
        if "embedding" not in columns:
            conn.execute("ALTER TABLE memories ADD COLUMN embedding BLOB")
            logger.info("Added embedding column to memories table")
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
        if self.embedder:
            try:
                embedding = self.embedder.embed_single(content)
                conn.execute(
                    "UPDATE memories SET embedding = ? WHERE id = ?",
                    (embedding.tobytes(), mem_id),
                )
            except Exception as e:
                logger.warning(f"Failed to store embedding for memory {mem_id}: {e}")
        conn.commit()
        logger.debug(f"stored_memory | id={mem_id} type={type} category={category}")
        return mem_id

    def search(self, query: str, type: str = None, limit: int = 5) -> list[dict]:
        conn = self._get_conn()
        keywords = self._extract_keywords(query)

        keyword_rows = []
        if keywords:
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
            sql += " GROUP BY m.id ORDER BY m.last_accessed DESC, COUNT(mk.keyword) DESC LIMIT ?"
            params.append(limit * 3)
            keyword_rows = conn.execute(sql, params).fetchall()

        vector_rows = []
        if self.embedder:
            try:
                query_emb = self.embedder.embed_single(query)
                sql = "SELECT id, embedding FROM memories WHERE embedding IS NOT NULL"
                params = []
                if type:
                    sql += " AND type = ?"
                    params.append(type)
                sql += " LIMIT 1000"
                rows = conn.execute(sql, params).fetchall()

                if rows:
                    ids = []
                    embeddings = []
                    for row in rows:
                        ids.append(row["id"])
                        embeddings.append(np.frombuffer(row["embedding"], dtype=np.float32))
                    embeddings = np.array(embeddings)

                    similarities = np.dot(embeddings, query_emb)
                    top_indices = np.argsort(similarities)[::-1][:limit * 3]

                    top_ids = [ids[i] for i in top_indices]
                    if top_ids:
                        placeholders = ",".join("?" for _ in top_ids)
                        vector_rows = conn.execute(
                            f"SELECT id, type, category, content, confidence FROM memories WHERE id IN ({placeholders})",
                            top_ids,
                        ).fetchall()
            except Exception as e:
                logger.warning(f"Vector search failed: {e}")

        from collections import defaultdict
        scored = defaultdict(float)

        for rank, row in enumerate(keyword_rows):
            scored[row["id"]] += 1.0 / (60 + rank + 1)
        for rank, row in enumerate(vector_rows):
            scored[row["id"]] += 1.0 / (60 + rank + 1)

        all_ids = sorted(scored.keys(), key=lambda x: scored[x], reverse=True)[:limit]
        if not all_ids:
            return []

        placeholders = ",".join("?" for _ in all_ids)
        final_rows = conn.execute(
            f"SELECT id, type, category, content, confidence FROM memories WHERE id IN ({placeholders})",
            all_ids,
        ).fetchall()

        id_to_score = {mid: scored[mid] for mid in all_ids}
        final_rows = sorted(final_rows, key=lambda r: id_to_score.get(r["id"], 0), reverse=True)
        return [dict(r) for r in final_rows]

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
