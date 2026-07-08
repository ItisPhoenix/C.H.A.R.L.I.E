"""Lightweight SQLite knowledge graph for Charlie's memory system.

Stores entities (nodes) and relationships (edges) to enable structured
knowledge queries across sessions. Used by the LLM to look up facts,
people, places, and their relationships.
"""

import logging
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from charlie.utils import json_dumps, json_loads, make_id

logger = logging.getLogger("charlie.memory_graph")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_DB_PATH = "charlie_memory_graph.db"

# Node types recognized by the graph
NODE_TYPES = ("person", "place", "concept", "task", "preference", "fact", "event")

# Edge relation types
EDGE_TYPES = ("works_on", "knows", "prefers", "located_at", "created", "uses", "mentions")


# ---------------------------------------------------------------------------
# MemoryGraph
# ---------------------------------------------------------------------------


class MemoryGraph:
    """SQLite-backed knowledge graph. Thread-safe via per-thread connections."""

    def __init__(self, db_path: str = _DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self._local = threading.local()
        self._init_db()

    # -- connection helpers (matches SessionStore pattern) -------------------

    @property
    def conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = self._get_connection()
        return self._local.conn

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        """Create tables if they do not exist."""
        try:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS nodes (
                    id          TEXT PRIMARY KEY,
                    node_type   TEXT NOT NULL,
                    content     TEXT NOT NULL,
                    metadata    TEXT,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS edges (
                    id           TEXT PRIMARY KEY,
                    from_node_id TEXT NOT NULL,
                    to_node_id   TEXT NOT NULL,
                    relation     TEXT NOT NULL,
                    created_at   TEXT NOT NULL,
                    FOREIGN KEY (from_node_id) REFERENCES nodes(id),
                    FOREIGN KEY (to_node_id)   REFERENCES nodes(id)
                );

                CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(node_type);
                CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(from_node_id);
                CREATE INDEX IF NOT EXISTS idx_edges_to   ON edges(to_node_id);
                CREATE INDEX IF NOT EXISTS idx_edges_rel  ON edges(relation);
                """
            )
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error("MemoryGraph init_db failed: %s", e)
            raise

    # -- public API ---------------------------------------------------------
    def add_node(
        self,
        node_type: str,
        content: str,
        node_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Add a node and return its id. If node_type+content already exists,
        update updated_at and return the existing id."""
        if node_type not in NODE_TYPES:
            logger.warning("Unknown node_type '%s', storing as 'fact'", node_type)
            node_type = "fact"

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        meta_json = json_dumps(metadata) if metadata else None

        # Check for duplicate (same type + content)
        existing = self.conn.execute(
            "SELECT id FROM nodes WHERE node_type = ? AND content = ?",
            (node_type, content),
        ).fetchone()
        if existing:
            self.conn.execute(
                "UPDATE nodes SET updated_at = ? WHERE id = ?",
                (now, existing["id"]),
            )
            self.conn.commit()
            return existing["id"]

        node_id = node_id or make_id()
        self.conn.execute(
            "INSERT INTO nodes (id, node_type, content, metadata, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (node_id, node_type, content, meta_json, now, now),
        )
        self.conn.commit()
        logger.debug("Added node %s (%s): %s", node_id, node_type, content)
        return node_id

    def add_edge(
        self,
        from_node_id: str,
        to_node_id: str,
        relation: str,
        edge_id: Optional[str] = None,
    ) -> str:
        """Add a directed edge between two nodes. Returns the edge id."""
        if relation not in EDGE_TYPES:
            logger.warning("Unknown relation '%s', storing as 'mentions'", relation)
            relation = "mentions"

        # Verify both nodes exist
        for nid in (from_node_id, to_node_id):
            if not self.conn.execute("SELECT 1 FROM nodes WHERE id = ?", (nid,)).fetchone():
                raise ValueError(f"Node {nid} not found")
        # Deduplicate same-direction edges
        existing = self.conn.execute(
            "SELECT id FROM edges WHERE from_node_id = ? AND to_node_id = ? AND relation = ?",
            (from_node_id, to_node_id, relation),
        ).fetchone()
        if existing:
            return existing["id"]



        edge_id = edge_id or make_id()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        self.conn.execute(
            "INSERT INTO edges (id, from_node_id, to_node_id, relation, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (edge_id, from_node_id, to_node_id, relation, now),
        )
        self.conn.commit()
        logger.debug("Added edge %s: %s -[%s]-> %s", edge_id, from_node_id, relation, to_node_id)
        return edge_id

    def query_neighbors(
        self,
        node_id: str,
        direction: str = "both",
        relation: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Return neighbor nodes connected to the given node.

        direction: "out" (from this node), "in" (to this node), or "both".
        relation: filter by relation type (optional).
        """
        results: List[Dict[str, Any]] = []
        params: List[Any]
        rel_filter = "AND e.relation = ?" if relation else ""
        limit_clause = "LIMIT ?" if limit else ""

        if direction in ("out", "both"):
            params = [node_id]
            if relation:
                params.append(relation)
            params.append(limit)
            rows = self.conn.execute(
                f"""
                SELECT n.id, n.node_type, n.content, e.relation, 'out' AS direction
                FROM edges e JOIN nodes n ON n.id = e.to_node_id
                WHERE e.from_node_id = ? {rel_filter}
                {limit_clause}
                """,
                params,
            ).fetchall()
            results.extend(dict(r) for r in rows)

        if direction in ("in", "both"):
            params = [node_id]
            if relation:
                params.append(relation)
            params.append(limit)
            rows = self.conn.execute(
                f"""
                SELECT n.id, n.node_type, n.content, e.relation, 'in' AS direction
                FROM edges e JOIN nodes n ON n.id = e.from_node_id
                WHERE e.to_node_id = ? {rel_filter}
                {limit_clause}
                """,
                params,
            ).fetchall()
            results.extend(dict(r) for r in rows)

        return results

    def query_path(
        self,
        from_id: str,
        to_id: str,
        max_depth: int = 4,
    ) -> Optional[List[Dict[str, Any]]]:
        """BFS search for shortest path between two nodes.
        Returns list of {id, node_type, content, relation} dicts, or None."""
        if from_id == to_id:
            node = self.conn.execute(
                "SELECT id, node_type, content FROM nodes WHERE id = ?", (from_id,)
            ).fetchone()
            return [dict(node)] if node else None

        visited = {from_id}
        queue: List[Tuple[str, List[Dict[str, Any]]]] = [(from_id, [])]

        for _ in range(max_depth):
            next_queue: List[Tuple[str, List[Dict[str, Any]]]] = []
            for current, path_so_far in queue:
                neighbors = self.conn.execute(
                    """
                    SELECT n.id, n.node_type, n.content, e.relation
                    FROM edges e JOIN nodes n ON n.id = e.to_node_id
                    WHERE e.from_node_id = ?
                    """,
                    (current,),
                ).fetchall()
                for row in neighbors:
                    node_dict = dict(row)
                    if node_dict["id"] == to_id:
                        return path_so_far + [node_dict]
                    if node_dict["id"] not in visited:
                        visited.add(node_dict["id"])
                        next_queue.append((node_dict["id"], path_so_far + [node_dict]))
            queue = next_queue
            if not queue:
                break
        return None

    def search_nodes(
        self,
        query: str,
        node_type: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Search nodes by content (SQL LIKE). Returns matching nodes."""
        sql = "SELECT id, node_type, content, created_at, updated_at FROM nodes WHERE content LIKE ?"
        params: List[Any] = [f"%{query}%"]
        if node_type:
            sql += " AND node_type = ?"
            params.append(node_type)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Get a single node by id."""
        row = self.conn.execute(
            "SELECT id, node_type, content, metadata, created_at, updated_at FROM nodes WHERE id = ?",
            (node_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("metadata"):
            d["metadata"] = json_loads(d["metadata"])
        return d

    def delete_node(self, node_id: str) -> bool:
        """Delete a node and all its edges."""
        try:
            self.conn.execute("DELETE FROM edges WHERE from_node_id = ? OR to_node_id = ?", (node_id, node_id))
            self.conn.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
            self.conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error("delete_node failed: %s", e)
            return False

    def get_all_nodes(self, node_type: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """List all nodes, optionally filtered by type."""
        sql = "SELECT id, node_type, content, created_at, updated_at FROM nodes"
        params: List[Any] = []
        if node_type:
            sql += " WHERE node_type = ?"
            params.append(node_type)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def get_stats(self) -> Dict[str, Any]:
        """Return graph statistics."""
        node_count = self.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edge_count = self.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        type_counts = {}
        for row in self.conn.execute(
            "SELECT node_type, COUNT(*) as cnt FROM nodes GROUP BY node_type"
        ).fetchall():
            type_counts[row["node_type"]] = row["cnt"]
        return {
            "nodes": node_count,
            "edges": edge_count,
            "by_type": type_counts,
        }

    # -- high-level fact API (used by tools.py) ---------------------------

    def add_fact(self, subject: str, predicate: str, obj: str) -> str:
        """Add a fact triple: subject -> predicate -> object.

        Creates subject and object nodes (type 'fact') if they don't exist,
        then adds an edge with the predicate as the relation.
        Returns the edge id.
        """
        # Map predicate to a known edge type if possible, else 'mentions'
        relation = predicate.lower().replace(" ", "_")
        if relation not in EDGE_TYPES:
            relation = "mentions"

        subject_id = self.add_node("fact", subject)
        object_id = self.add_node("fact", obj)
        return self.add_edge(subject_id, object_id, relation)

    def search_facts(
        self,
        query: str,
        subject_filter: Optional[str] = None,
        limit: int = 20,
    ) -> List[Tuple[str, str, str, float]]:
        """Search for facts matching `query` in node content.

        Returns list of (subject, predicate, object, score) tuples.
        Score is a simple relevance heuristic: 1.0 for exact match,
        0.5 for partial match, 0.3 for edge-only match.
        """
        results: List[Tuple[str, str, str, float]] = []
        query_lower = query.lower()

        # Find nodes matching the query
        if subject_filter:
            matching_nodes = self.conn.execute(
                "SELECT id, content FROM nodes WHERE content LIKE ? AND content LIKE ?",
                (f"%{subject_filter}%", f"%{query}%"),
            ).fetchall()
        else:
            matching_nodes = self.conn.execute(
                "SELECT id, content FROM nodes WHERE content LIKE ?",
                (f"%{query}%",),
            ).fetchall()

        for node in matching_nodes:
            # Find edges from this node
            edges = self.conn.execute(
                """
                SELECT e.relation, n2.content
                FROM edges e JOIN nodes n2 ON n2.id = e.to_node_id
                WHERE e.from_node_id = ?
                """,
                (node["id"],),
            ).fetchall()
            for edge in edges:
                score = 1.0 if query_lower in node["content"].lower() else 0.5
                results.append((node["content"], edge["relation"], edge["content"], score))

        # Also search for edges where the object matches
        if subject_filter:
            matching_obj = self.conn.execute(
                """
                SELECT e.relation, n1.content AS subject_content, n2.content AS object_content
                FROM edges e
                JOIN nodes n1 ON n1.id = e.from_node_id
                JOIN nodes n2 ON n2.id = e.to_node_id
                WHERE n2.content LIKE ? AND n1.content LIKE ?
                """,
                (f"%{query}%", f"%{subject_filter}%"),
            ).fetchall()
        else:
            matching_obj = self.conn.execute(
                """
                SELECT e.relation, n1.content AS subject_content, n2.content AS object_content
                FROM edges e
                JOIN nodes n1 ON n1.id = e.from_node_id
                JOIN nodes n2 ON n2.id = e.to_node_id
                WHERE n2.content LIKE ?
                """,
                (f"%{query}%",),
            ).fetchall()
        for row in matching_obj:
            score = 0.5 if query_lower in row["object_content"].lower() else 0.3
            results.append((row["subject_content"], row["relation"], row["object_content"], score))

        # Deduplicate
        seen = set()
        unique: List[Tuple[str, str, str, float]] = []
        for s, p, o, sc in results:
            key = (s, p, o)
            if key not in seen:
                seen.add(key)
                unique.append((s, p, o, sc))

        unique.sort(key=lambda x: x[3], reverse=True)
        return unique[:limit]

    def consolidate(self) -> int:
        """Merge duplicate edges and remove orphan nodes.

        Returns the number of stale/duplicate entries removed.
        """
        removed = 0

        # Remove duplicate edges (same from, to, relation) keeping the newest
        dupes = self.conn.execute(
            """
            SELECT from_node_id, to_node_id, relation, MIN(id) AS keep_id,
                   COUNT(*) AS cnt
            FROM edges
            GROUP BY from_node_id, to_node_id, relation
            HAVING cnt > 1
            """
        ).fetchall()
        for row in dupes:
            self.conn.execute(
                "DELETE FROM edges WHERE from_node_id = ? AND to_node_id = ? AND relation = ? AND id != ?",
                (row["from_node_id"], row["to_node_id"], row["relation"], row["keep_id"]),
            )
            removed += row["cnt"] - 1

        # Remove orphan nodes (no edges in or out)
        orphans = self.conn.execute(
            """
            SELECT n.id FROM nodes n
            WHERE NOT EXISTS (SELECT 1 FROM edges WHERE from_node_id = n.id)
              AND NOT EXISTS (SELECT 1 FROM edges WHERE to_node_id = n.id)
            """
        ).fetchall()
        for row in orphans:
            self.conn.execute("DELETE FROM nodes WHERE id = ?", (row["id"],))
            removed += 1

        if removed:
            self.conn.commit()
        return removed

    def close(self) -> None:
        """Close the thread-local connection."""
        if hasattr(self._local, "conn") and self._local.conn is not None:
            self._local.conn.close()
            self._local.conn = None


