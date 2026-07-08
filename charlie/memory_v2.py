"""Four-layer evolving memory system for Charlie.

Layers:
  - Episodic:   Conversation events with timestamps, participants, context.
  - Semantic:   Facts, knowledge, entities extracted from conversations.
  - Procedural: Learned workflows, how-to knowledge, action patterns.
  - Meta:       Self-reflection, performance metrics, system state.

Uses a single SQLite database with per-layer tables. Relevance scoring
combines importance, access frequency, and temporal decay.
"""

import logging
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("charlie.memory_v2")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_DB_PATH = "charlie_memory_v2.db"

# Relevance scoring weights
_IMPORTANCE_WEIGHT = 0.5
_ACCESS_WEIGHT = 0.25
_RECENCY_WEIGHT = 0.25

# Temporal decay half-life in days
_DECAY_HALF_LIFE_DAYS = 30.0

# Maximum entries per layer before consolidation triggers
_MAX_ENTRIES_PER_LAYER = 500

# Consolidation threshold: when entry count exceeds this, run consolidation
_CONSOLIDATION_THRESHOLD = 400


# ---------------------------------------------------------------------------
# Enums and data models
# ---------------------------------------------------------------------------

class MemoryLayer(str, Enum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    META = "meta"


@dataclass
class MemoryEntry:
    """A single memory entry across any layer."""
    id: str
    layer: str
    content: str
    importance: float = 0.5  # 0.0 - 1.0
    access_count: int = 0
    created_at: str = ""
    last_accessed: str = ""
    tags: str = ""  # comma-separated
    source_session: str = ""
    metadata_json: str = "{}"  # layer-specific extra data

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Database schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    layer TEXT NOT NULL,
    content TEXT NOT NULL,
    importance REAL NOT NULL DEFAULT 0.5,
    access_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    last_accessed TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '',
    source_session TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_memories_layer ON memories(layer);
CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance DESC);
CREATE INDEX IF NOT EXISTS idx_memories_last_accessed ON memories(last_accessed DESC);
CREATE INDEX IF NOT EXISTS idx_memories_tags ON memories(tags);
"""


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """UTC timestamp with microsecond precision."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _compute_relevance(
    importance: float,
    access_count: int,
    last_accessed: str,
    now_ts: float,
) -> float:
    """Score a memory entry's current relevance.

    Combines importance, access frequency, and temporal decay.
    """
    # Recency: exponential decay based on time since last access
    try:
        last_dt = datetime.fromisoformat(last_accessed.replace("Z", "+00:00"))
        age_days = max(0.0, (now_ts - last_dt.timestamp()) / 86400.0)
    except (ValueError, TypeError):
        age_days = 999.0  # treat unparseable as very old

    recency = 2.0 ** (-age_days / _DECAY_HALF_LIFE_DAYS)

    # Access frequency: logarithmic scaling
    access_score = min(1.0, 0.1 + 0.9 * (1.0 - 1.0 / (1.0 + access_count)))

    return (
        _IMPORTANCE_WEIGHT * importance
        + _ACCESS_WEIGHT * access_score
        + _RECENCY_WEIGHT * recency
    )


def _generate_id() -> str:
    """Generate a unique ID using timestamp + random suffix."""
    import random
    import string
    ts = int(time.time() * 1000)
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"mem_{ts}_{suffix}"


# ---------------------------------------------------------------------------
# MemoryV2
# ---------------------------------------------------------------------------

class MemoryV2:
    """Four-layer evolving memory backed by SQLite.

    Provides store, retrieve, consolidate, and decay operations across
    episodic, semantic, procedural, and meta memory layers.
    """

    def __init__(self, db_path: str = _DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self._local = threading.local()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Thread-local SQLite connection."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return conn

    def _init_db(self) -> None:
        """Create tables if they don't exist."""
        conn = self._get_conn()
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
        logger.info("MemoryV2 initialized: %s", self.db_path)

    # -------------------------------------------------------------------
    # Store
    # -------------------------------------------------------------------

    def store(
        self,
        layer: MemoryLayer,
        content: str,
        importance: float = 0.5,
        tags: Optional[List[str]] = None,
        source_session: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        entry_id: Optional[str] = None,
    ) -> str:
        """Store a new memory entry. Returns the entry ID."""
        import json

        entry_id = entry_id or _generate_id()
        now = _now_iso()
        tags_str = ",".join(tags) if tags else ""
        meta_str = json.dumps(metadata or {})

        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO memories
               (id, layer, content, importance, access_count, created_at,
                last_accessed, tags, source_session, metadata_json)
               VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?)""",
            (entry_id, layer.value, content, importance, now, now,
             tags_str, source_session, meta_str),
        )
        conn.commit()

        # Check if consolidation is needed
        count = self._count_entries(layer)
        if count > _CONSOLIDATION_THRESHOLD:
            logger.info("Layer %s reached %d entries, running consolidation", layer.value, count)
            self.consolidate(layer)

        return entry_id

    def store_episodic(
        self,
        content: str,
        importance: float = 0.6,
        tags: Optional[List[str]] = None,
        source_session: str = "",
        participants: Optional[List[str]] = None,
    ) -> str:
        """Store an episodic memory (conversation event)."""
        metadata = {}
        if participants:
            metadata["participants"] = participants
        return self.store(
            layer=MemoryLayer.EPISODIC,
            content=content,
            importance=importance,
            tags=tags or ["conversation"],
            source_session=source_session,
            metadata=metadata,
        )

    def store_semantic(
        self,
        content: str,
        importance: float = 0.7,
        tags: Optional[List[str]] = None,
        source_session: str = "",
        entity_type: str = "",
    ) -> str:
        """Store a semantic memory (fact or knowledge)."""
        metadata = {}
        if entity_type:
            metadata["entity_type"] = entity_type
        return self.store(
            layer=MemoryLayer.SEMANTIC,
            content=content,
            importance=importance,
            tags=tags or ["fact"],
            source_session=source_session,
            metadata=metadata,
        )

    def store_procedural(
        self,
        content: str,
        importance: float = 0.6,
        tags: Optional[List[str]] = None,
        source_session: str = "",
        workflow_name: str = "",
    ) -> str:
        """Store a procedural memory (learned workflow or action pattern)."""
        metadata = {}
        if workflow_name:
            metadata["workflow_name"] = workflow_name
        return self.store(
            layer=MemoryLayer.PROCEDURAL,
            content=content,
            importance=importance,
            tags=tags or ["procedure"],
            source_session=source_session,
            metadata=metadata,
        )

    def store_meta(
        self,
        content: str,
        importance: float = 0.5,
        tags: Optional[List[str]] = None,
        source_session: str = "",
    ) -> str:
        """Store a meta memory (self-reflection or system state)."""
        return self.store(
            layer=MemoryLayer.META,
            content=content,
            importance=importance,
            tags=tags or ["reflection"],
            source_session=source_session,
        )

    # -------------------------------------------------------------------
    # Retrieve
    # -------------------------------------------------------------------

    def retrieve(
        self,
        query: Optional[str] = None,
        layer: Optional[MemoryLayer] = None,
        tags: Optional[List[str]] = None,
        limit: int = 10,
        min_relevance: float = 0.0,
    ) -> List[MemoryEntry]:
        """Retrieve memory entries ranked by relevance.

        If query is provided, uses SQL LIKE for text matching.
        Otherwise returns entries sorted by relevance score.
        """
        conn = self._get_conn()
        conditions = []
        params: List[Any] = []

        if layer:
            conditions.append("layer = ?")
            params.append(layer.value)

        if tags:
            for tag in tags:
                conditions.append("tags LIKE ?")
                params.append(f"%{tag}%")

        if query:
            conditions.append("content LIKE ?")
            params.append(f"%{query}%")

        where = " AND ".join(conditions) if conditions else "1=1"
        sql = f"SELECT * FROM memories WHERE {where} ORDER BY last_accessed DESC LIMIT 200"

        rows = conn.execute(sql, params).fetchall()
        now_ts = time.time()

        entries = []
        for row in rows:
            entry = MemoryEntry(
                id=row["id"],
                layer=row["layer"],
                content=row["content"],
                importance=row["importance"],
                access_count=row["access_count"],
                created_at=row["created_at"],
                last_accessed=row["last_accessed"],
                tags=row["tags"],
                source_session=row["source_session"],
                metadata_json=row["metadata_json"],
            )
            relevance = _compute_relevance(
                entry.importance, entry.access_count, entry.last_accessed, now_ts
            )
            if relevance >= min_relevance:
                entries.append((relevance, entry))

        # Sort by relevance descending
        entries.sort(key=lambda x: x[0], reverse=True)

        # Update access counts for retrieved entries
        results = []
        for relevance, entry in entries[:limit]:
            self._touch_entry(entry.id)
            results.append(entry)

        return results

    def get_entry(self, entry_id: str) -> Optional[MemoryEntry]:
        """Get a single memory entry by ID."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM memories WHERE id = ?", (entry_id,)
        ).fetchone()
        if not row:
            return None

        self._touch_entry(entry_id)
        return MemoryEntry(
            id=row["id"],
            layer=row["layer"],
            content=row["content"],
            importance=row["importance"],
            access_count=row["access_count"],
            created_at=row["created_at"],
            last_accessed=row["last_accessed"],
            tags=row["tags"],
            source_session=row["source_session"],
            metadata_json=row["metadata_json"],
        )

    def _touch_entry(self, entry_id: str) -> None:
        """Update access count and last_accessed timestamp."""
        conn = self._get_conn()
        conn.execute(
            """UPDATE memories
               SET access_count = access_count + 1,
                   last_accessed = ?
               WHERE id = ?""",
            (_now_iso(), entry_id),
        )
        conn.commit()

    # -------------------------------------------------------------------
    # Update / Delete
    # -------------------------------------------------------------------

    def update_importance(self, entry_id: str, importance: float) -> bool:
        """Update the importance score of an entry."""
        conn = self._get_conn()
        cursor = conn.execute(
            "UPDATE memories SET importance = ? WHERE id = ?",
            (importance, entry_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    def update_tags(self, entry_id: str, tags: List[str]) -> bool:
        """Replace tags on an entry."""
        conn = self._get_conn()
        cursor = conn.execute(
            "UPDATE memories SET tags = ? WHERE id = ?",
            (",".join(tags), entry_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    def delete(self, entry_id: str) -> bool:
        """Delete a memory entry."""
        conn = self._get_conn()
        cursor = conn.execute("DELETE FROM memories WHERE id = ?", (entry_id,))
        conn.commit()
        return cursor.rowcount > 0

    def delete_by_layer(self, layer: MemoryLayer) -> int:
        """Delete all entries in a layer. Returns count deleted."""
        conn = self._get_conn()
        cursor = conn.execute("DELETE FROM memories WHERE layer = ?", (layer.value,))
        conn.commit()
        return cursor.rowcount

    # -------------------------------------------------------------------
    # Consolidation
    # -------------------------------------------------------------------

    def consolidate(self, layer: MemoryLayer, max_keep: int = _MAX_ENTRIES_PER_LAYER) -> int:
        """Consolidate a memory layer by removing low-relevance entries.

        Keeps the top `max_keep` entries by relevance score.
        Returns the number of entries removed.
        """
        conn = self._get_conn()
        now_ts = time.time()

        rows = conn.execute(
            "SELECT id, importance, access_count, last_accessed FROM memories WHERE layer = ?",
            (layer.value,),
        ).fetchall()

        if len(rows) <= max_keep:
            return 0

        # Score all entries
        scored = []
        for row in rows:
            relevance = _compute_relevance(
                row["importance"], row["access_count"], row["last_accessed"], now_ts
            )
            scored.append((relevance, row["id"]))

        # Sort by relevance, keep top N
        scored.sort(key=lambda x: x[0], reverse=True)
        ids_to_remove = [sid for _, sid in scored[max_keep:]]

        if ids_to_remove:
            placeholders = ",".join("?" * len(ids_to_remove))
            conn.execute(
                f"DELETE FROM memories WHERE id IN ({placeholders})",
                ids_to_remove,
            )
            conn.commit()
            logger.info(
                "Consolidated %s: removed %d low-relevance entries",
                layer.value, len(ids_to_remove),
            )

        return len(ids_to_remove)

    def consolidate_all(self, max_per_layer: int = _MAX_ENTRIES_PER_LAYER) -> Dict[str, int]:
        """Consolidate all layers. Returns per-layer removal counts."""
        results = {}
        for layer in MemoryLayer:
            results[layer.value] = self.consolidate(layer, max_per_layer)
        return results

    # -------------------------------------------------------------------
    # Decay
    # -------------------------------------------------------------------

    def decay(self, threshold: float = 0.05) -> int:
        """Remove entries whose relevance has decayed below threshold.

        This prunes stale memories that haven't been accessed recently
        and have low importance. Returns count of entries removed.
        """
        conn = self._get_conn()
        now_ts = time.time()

        rows = conn.execute(
            "SELECT id, importance, access_count, last_accessed FROM memories"
        ).fetchall()

        ids_to_remove = []
        for row in rows:
            relevance = _compute_relevance(
                row["importance"], row["access_count"], row["last_accessed"], now_ts
            )
            if relevance < threshold:
                ids_to_remove.append(row["id"])

        if ids_to_remove:
            placeholders = ",".join("?" * len(ids_to_remove))
            conn.execute(
                f"DELETE FROM memories WHERE id IN ({placeholders})",
                ids_to_remove,
            )
            conn.commit()
            logger.info("Decayed %d stale memories below threshold %.3f", len(ids_to_remove), threshold)

        return len(ids_to_remove)

    # -------------------------------------------------------------------
    # Query / Stats
    # -------------------------------------------------------------------

    def count(self, layer: Optional[MemoryLayer] = None) -> int:
        """Count entries, optionally filtered by layer."""
        conn = self._get_conn()
        if layer:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM memories WHERE layer = ?", (layer.value,)
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) as cnt FROM memories").fetchone()
        return row["cnt"] if row else 0

    def _count_entries(self, layer: MemoryLayer) -> int:
        return self.count(layer)

    def stats(self) -> Dict[str, Any]:
        """Return memory statistics across all layers."""
        conn = self._get_conn()
        result: Dict[str, Any] = {"total": self.count()}

        for layer in MemoryLayer:
            row = conn.execute(
                """SELECT COUNT(*) as cnt,
                          AVG(importance) as avg_importance,
                          SUM(access_count) as total_accesses
                   FROM memories WHERE layer = ?""",
                (layer.value,),
            ).fetchone()
            result[layer.value] = {
                "count": row["cnt"] if row else 0,
                "avg_importance": round(row["avg_importance"] or 0.0, 3),
                "total_accesses": row["total_accesses"] or 0,
            }

        return result

    def get_recent(self, layer: Optional[MemoryLayer] = None, limit: int = 20) -> List[MemoryEntry]:
        """Get most recently created entries."""
        conn = self._get_conn()
        if layer:
            rows = conn.execute(
                "SELECT * FROM memories WHERE layer = ? ORDER BY created_at DESC LIMIT ?",
                (layer.value, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()

        return [
            MemoryEntry(
                id=r["id"], layer=r["layer"], content=r["content"],
                importance=r["importance"], access_count=r["access_count"],
                created_at=r["created_at"], last_accessed=r["last_accessed"],
                tags=r["tags"], source_session=r["source_session"],
                metadata_json=r["metadata_json"],
            )
            for r in rows
        ]

    def search_by_tag(self, tag: str, layer: Optional[MemoryLayer] = None, limit: int = 50) -> List[MemoryEntry]:
        """Find entries matching a specific tag."""
        conn = self._get_conn()
        if layer:
            rows = conn.execute(
                "SELECT * FROM memories WHERE tags LIKE ? AND layer = ? ORDER BY importance DESC LIMIT ?",
                (f"%{tag}%", layer.value, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM memories WHERE tags LIKE ? ORDER BY importance DESC LIMIT ?",
                (f"%{tag}%", limit),
            ).fetchall()

        return [
            MemoryEntry(
                id=r["id"], layer=r["layer"], content=r["content"],
                importance=r["importance"], access_count=r["access_count"],
                created_at=r["created_at"], last_accessed=r["last_accessed"],
                tags=r["tags"], source_session=r["source_session"],
                metadata_json=r["metadata_json"],
            )
            for r in rows
        ]

    # -------------------------------------------------------------------
    # Export / Context Building
    # -------------------------------------------------------------------

    def build_context(self, max_chars: int = 2000, layer: Optional[MemoryLayer] = None) -> str:
        """Build a text context string from memory for LLM prompt injection.

        Returns the most relevant entries formatted for inclusion in a
        system prompt, up to max_chars.
        """
        entries = self.retrieve(layer=layer, limit=50)
        lines = []
        total = 0

        for entry in entries:
            line = f"[{entry.layer}] {entry.content}"
            if total + len(line) + 1 > max_chars:
                break
            lines.append(line)
            total += len(line) + 1

        return "\n".join(lines)

    def export_layer(self, layer: MemoryLayer) -> List[Dict[str, Any]]:
        """Export all entries in a layer as a list of dicts."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM memories WHERE layer = ? ORDER BY created_at",
            (layer.value,),
        ).fetchall()
        return [dict(r) for r in rows]

    # -------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------

    def close(self) -> None:
        """Close the thread-local database connection."""
        conn = getattr(self._local, "conn", None)
        if conn:
            conn.close()
            self._local.conn = None

    def __enter__(self) -> "MemoryV2":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
