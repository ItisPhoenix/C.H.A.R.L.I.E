"""Unified Memory Management Service for Charlie.

Coordinates SQLite Knowledge Graph, Vector Store, and memory files
for search, inspection, CRUD, category purge, export, and auto-memory management.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from charlie.memory_graph import MemoryGraph
from charlie.utils import make_id, utc_now_iso

logger = logging.getLogger("charlie.memory_service")


class MemoryService:
    """Unified service for inspecting, searching, modifying, exporting, and purging memories."""

    def __init__(self, graph: Optional[MemoryGraph] = None, memory_store: Optional[Any] = None) -> None:
        self._graph = graph
        self._memory_store = memory_store

    def _get_graph(self) -> Optional[MemoryGraph]:
        if self._graph is not None:
            return self._graph
        try:
            from charlie.config import config
            self._graph = MemoryGraph(config.memory_graph_db)
        except Exception as e:
            logger.warning("Could not instantiate MemoryGraph: %s", e)
        return self._graph

    def add_item(
        self,
        category: str = "fact",
        content: str = "",
        subject: str = "",
        predicate: str = "",
        obj: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Add a memory item (triple or freeform content) to the graph and vector store."""
        graph = self._get_graph()
        now_iso = utc_now_iso()
        item_id = f"mem_{make_id(8)}"
        node_type = (
            category
            if category in ("person", "place", "concept", "task", "preference", "fact", "event")
            else "fact"
        )

        if subject and predicate and obj:
            if not content:
                content = f"{subject} {predicate} {obj}"
            if graph:
                # Add fact triples into graph
                graph.add_fact(subject, predicate, obj)
                # And create a dedicated node
                node_meta = {
                    "subject": subject,
                    "predicate": predicate,
                    "object": obj,
                    **(metadata or {}),
                }
                graph.add_node(
                    node_type=node_type,
                    content=content,
                    node_id=item_id,
                    metadata=node_meta,
                )
        else:
            if graph:
                graph.add_node(
                    node_type=node_type,
                    content=content,
                    node_id=item_id,
                    metadata=metadata,
                )

        if self._memory_store is not None and content:
            try:
                self._memory_store.add_memory(content, metadata={"category": category, "id": item_id})
            except Exception as e:
                logger.debug("Could not add to vector memory store: %s", e)

        return {
            "id": item_id,
            "category": category,
            "content": content,
            "subject": subject,
            "predicate": predicate,
            "object": obj,
            "created_at": now_iso,
            "metadata": metadata or {},
        }

    def list_items(self, category: Optional[str] = None, limit: int = 300) -> List[Dict[str, Any]]:
        """List all structured and unstructured memory items."""
        graph = self._get_graph()
        if not graph:
            return []

        nodes = graph.get_all_nodes(node_type=category, limit=limit)
        items: List[Dict[str, Any]] = []
        seen_ids = set()

        for node in nodes:
            nid = node["id"]
            if nid in seen_ids:
                continue

            # Fetch node details with metadata
            full_node = graph.get_node(nid) or node
            meta = full_node.get("metadata") or {}
            if not isinstance(meta, dict):
                meta = {}

            is_memory_item = (
                nid.startswith("mem_")
                or bool(meta.get("subject"))
                or node.get("node_type") in ("preference", "task", "event")
            )
            if not is_memory_item:
                continue

            seen_ids.add(nid)
            items.append({
                "id": nid,
                "category": node.get("node_type", "fact"),
                "content": node.get("content", ""),
                "subject": meta.get("subject", ""),
                "predicate": meta.get("predicate", ""),
                "object": meta.get("object", ""),
                "created_at": node.get("created_at") or utc_now_iso(),
                "metadata": meta,
            })

        # Also include relational facts if category in (None, "fact")
        if category in (None, "fact"):
            for s, p, o in graph.get_all_facts(limit=limit):
                fact_text = f"{s} {p} {o}"
                if not any(
                    it["content"] == fact_text or (it.get("subject") == s and it.get("object") == o)
                    for it in items
                ):
                    fact_id = f"fact_{abs(hash((s, p, o)))}"
                    items.append({
                        "id": fact_id,
                        "category": "fact",
                        "content": fact_text,
                        "subject": s,
                        "predicate": p,
                        "object": o,
                        "created_at": utc_now_iso(),
                        "metadata": {},
                    })

        return items[:limit]

    def search_items(self, query: str, category: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Search memory items by keyword and category."""
        all_items = self.list_items(category=category, limit=500)
        q_lower = query.lower().strip()
        if not q_lower:
            return all_items[:limit]

        matched = [
            item for item in all_items
            if q_lower in item["content"].lower()
            or q_lower in item.get("subject", "").lower()
            or q_lower in item.get("predicate", "").lower()
            or q_lower in item.get("object", "").lower()
        ]
        return matched[:limit]

    def update_item(
        self,
        item_id: str,
        content: str = "",
        category: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update content and properties of an existing memory item."""
        graph = self._get_graph()
        if not graph:
            return None

        node = graph.get_node(item_id)
        current_type = category or (node.get("node_type") if node else "fact")
        node_type = (
            current_type
            if current_type in ("person", "place", "concept", "task", "preference", "fact", "event")
            else "fact"
        )

        existing_meta = (node.get("metadata") if node else None) or {}
        if not isinstance(existing_meta, dict):
            existing_meta = {}
        if metadata:
            existing_meta.update(metadata)

        # Delete old node if exists and recreate with updated content
        if node:
            graph.delete_node(item_id)

        graph.add_node(
            node_type=node_type,
            content=content,
            node_id=item_id,
            metadata=existing_meta,
        )

        return {
            "id": item_id,
            "category": node_type,
            "content": content,
            "subject": existing_meta.get("subject", ""),
            "predicate": existing_meta.get("predicate", ""),
            "object": existing_meta.get("object", ""),
            "created_at": node.get("created_at") if node else utc_now_iso(),
            "metadata": existing_meta,
        }

    def delete_item(self, item_id: str) -> bool:
        """Delete an item by id or remove associated graph fact."""
        graph = self._get_graph()
        if not graph:
            return False

        # Try deleting node
        node = graph.get_node(item_id)
        if node:
            meta = node.get("metadata") or {}
            if isinstance(meta, dict):
                s, p, o = meta.get("subject"), meta.get("predicate"), meta.get("object")
                if s and p and o:
                    graph.remove_fact(s, p, o)
            graph.delete_node(item_id)
            return True

        # Check if item_id corresponds to a fact hash
        for s, p, o in graph.get_all_facts():
            fact_id = f"fact_{abs(hash((s, p, o)))}"
            if fact_id == item_id or f"{s} {p} {o}" == item_id:
                graph.remove_fact(s, p, o)
                return True

        return False

    def clear_category(self, category: Optional[str] = None) -> int:
        """Clear all items in a specific category, or all memories if category is None or 'all'."""
        graph = self._get_graph()
        if not graph:
            return 0

        items_to_delete = self.list_items(category=category if category != "all" else None, limit=1000)
        cleared = 0
        for item in items_to_delete:
            if self.delete_item(item["id"]):
                cleared += 1

        if category in (None, "all", "fact"):
            for s, p, o in list(graph.get_all_facts()):
                if graph.remove_fact(s, p, o):
                    cleared += 1

        return cleared

    def export_all(self) -> Dict[str, Any]:
        """Export full memory dataset with metadata and graph statistics."""
        graph = self._get_graph()
        items = self.list_items(limit=1000)
        stats = graph.get_stats() if graph else {}
        return {
            "exported_at": utc_now_iso(),
            "items_count": len(items),
            "stats": stats,
            "items": items,
        }

    def get_stats(self) -> Dict[str, Any]:
        """Return memory statistics."""
        graph = self._get_graph()
        stats = graph.get_stats() if graph else {}
        items = self.list_items(limit=1000)
        category_counts: Dict[str, int] = {}
        for it in items:
            cat = it.get("category", "fact")
            category_counts[cat] = category_counts.get(cat, 0) + 1
        stats["categories"] = category_counts
        stats["total_items"] = len(items)
        return stats


# Global default instance
memory_service = MemoryService()
