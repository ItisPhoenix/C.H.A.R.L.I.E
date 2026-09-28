"""Long-term memory facade for Charlie.

Coordinates the structured ``MemoryGraph`` adapter and the optional semantic
``MemoryStore`` adapter.  Managed graph records and semantic memories have
separate operations; graph mutations are not automatically mirrored into
vector storage.  Prompt-profile files, prompt construction, and privacy
policy remain outside this facade.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from charlie.memory_graph import MemoryGraph
from charlie.utils import make_id, utc_now_iso

logger = logging.getLogger("charlie.memory_service")

_SEARCH_STOP_WORDS = frozenset({
    "a", "about", "am", "an", "and", "are", "as", "at", "be", "but", "by", "can",
    "did", "do", "does", "for", "from", "had", "has", "have", "i", "in", "is", "it",
    "me", "my", "of", "on", "or", "please", "remember", "the", "this", "to", "was",
    "what", "when", "where", "which", "who", "why", "you", "your",
})


def _search_terms(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if len(token) > 2 and token not in _SEARCH_STOP_WORDS
    }


def fact_compatibility_id(subject: str, predicate: str, obj: str) -> str:
    """Return a deterministic compatibility ID for a relational fact."""
    serialized = json.dumps(
        {"object": obj, "predicate": predicate, "subject": subject},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"fact_{digest}"


class MemoryService:
    """Facade for managed structured memory and explicit semantic memory."""

    def __init__(
        self,
        graph: Optional[MemoryGraph] = None,
        memory_store: Optional[Any] = None,
        semantic_expected: Optional[bool] = None,
    ) -> None:
        self._graph = graph
        self._memory_store = memory_store
        # A supplied adapter normally means semantic memory is expected.  The
        # composition root can override this when vector memory is optional and
        # no embedding service is configured.
        self._semantic_expected = memory_store is not None if semantic_expected is None else bool(semantic_expected)

    def _get_graph(self) -> Optional[MemoryGraph]:
        if self._graph is not None:
            return self._graph
        try:
            from charlie.config import config
            self._graph = MemoryGraph(config.memory_graph_db)
        except Exception as e:
            logger.warning("Could not instantiate MemoryGraph: %s", e)
        return self._graph

    def _semantic_available(self) -> bool:
        """Return whether the configured semantic adapter can serve requests."""
        if self._memory_store is None:
            return False
        try:
            return bool(getattr(self._memory_store, "is_available", False))
        except Exception:
            return False

    def semantic_available(self) -> bool:
        """Return whether semantic memory is available to facade consumers."""
        return self._semantic_available()

    def remember_semantic(
        self,
        text: str,
        source: str,
        session_id: str,
        auto_extract: bool = False,
    ) -> int:
        """Store explicit user-provided semantic memory without extraction.

        Automatic transcript and assistant-output ingestion is intentionally
        rejected at this boundary. Zero is the neutral result when the write
        is not explicit or semantic storage is unavailable.
        """
        if source != "user" or auto_extract:
            logger.info("Semantic memory write skipped without explicit user content")
            return 0
        if not self._semantic_available():
            return 0
        return self._memory_store.add_memory(
            text=text,
            source=source,
            session_id=session_id,
            auto_extract=auto_extract,
        )

    def search_semantic(
        self,
        query: str,
        n_results: int = 3,
        threshold: Optional[float] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """Search semantic memory, preserving ``[]`` versus ``None`` failures."""
        if not self._semantic_available():
            return []
        if threshold is None:
            return self._memory_store.search(query, n_results=n_results)
        return self._memory_store.search(query, n_results=n_results, threshold=threshold)

    def format_semantic_for_prompt(self, results: List[Dict[str, Any]]) -> str:
        """Format semantic results without applying prompt policy."""
        if not self._semantic_available():
            return ""
        return self._memory_store.format_for_prompt(results)

    def add_fact(self, subject: str, predicate: str, obj: str) -> Optional[str]:
        """Add one graph-only relational fact and return its edge ID."""
        graph = self._get_graph()
        if graph is None:
            return None
        return graph.add_fact(subject, predicate, obj)

    def search_facts(
        self,
        query: str,
        subject_filter: Optional[str] = None,
        limit: int = 20,
    ) -> Optional[List[Tuple[str, str, str, float]]]:
        """Search graph-only relational facts, preserving unavailable vs empty."""
        graph = self._get_graph()
        if graph is None:
            return None
        return graph.search_facts(query, subject_filter=subject_filter, limit=limit)

    def list_facts(self, limit: int = 500) -> Optional[List[Tuple[str, str, str]]]:
        """List graph facts through the canonical memory facade."""
        graph = self._get_graph()
        if graph is None:
            return None
        return graph.get_all_facts(limit=limit)

    def remove_fact(self, subject: str, predicate: str, obj: str) -> Optional[bool]:
        """Remove one graph fact through the canonical memory facade."""
        graph = self._get_graph()
        if graph is None:
            return None
        return graph.remove_fact(subject, predicate, obj)

    def consolidate_graph(self) -> Optional[int]:
        """Consolidate graph-only relational facts and return removed count."""
        graph = self._get_graph()
        if graph is None:
            return None
        return graph.consolidate()

    def _semantic_stats(self) -> Dict[str, Any]:
        """Return safe semantic adapter statistics or its neutral state."""
        if self._memory_store is None:
            return {"available": False, "document_count": 0}

        get_stats = getattr(self._memory_store, "get_stats", None)
        if callable(get_stats):
            try:
                stats = get_stats()
                if isinstance(stats, dict):
                    return stats
            except Exception as e:
                logger.warning("Could not read semantic memory statistics: %s", e)

        return {
            "available": self._semantic_available(),
            "document_count": 0,
        }

    @staticmethod
    def _health_error(component: str, error: Exception) -> Dict[str, Any]:
        """Return a public-safe component error without exposing exception text."""
        return {
            "status": "error",
            "available": False,
            "error": f"{component} memory adapter error ({type(error).__name__}).",
        }

    def _structured_health(self) -> Dict[str, Any]:
        """Verify structured memory through its canonical graph adapter."""
        graph = self._get_graph()
        if graph is None:
            return {"status": "unavailable", "available": False}

        get_stats = getattr(graph, "get_stats", None)
        if not callable(get_stats):
            return self._health_error("Structured", TypeError("get_stats unavailable"))
        try:
            stats = get_stats()
        except Exception as exc:
            logger.warning("Could not verify structured memory: %s", type(exc).__name__)
            return self._health_error("Structured", exc)
        if not isinstance(stats, dict):
            return self._health_error("Structured", TypeError("invalid stats"))
        return {"status": "available", "available": True}

    def _semantic_health(self) -> Dict[str, Any]:
        """Verify semantic memory while preserving configured-vs-optional state."""
        expected = self._semantic_expected
        base = {"available": False, "configured": expected, "document_count": 0}
        if self._memory_store is None:
            return {
                **base,
                "status": "unavailable" if expected else "disabled",
            }

        get_stats = getattr(self._memory_store, "get_stats", None)
        try:
            raw_stats = get_stats() if callable(get_stats) else {}
            if not isinstance(raw_stats, dict):
                raise TypeError("invalid semantic stats")
            available = raw_stats.get("available")
            if type(available) is not bool:
                available = self._semantic_available()
            document_count = raw_stats.get("document_count", 0)
            if type(document_count) is not int or document_count < 0:
                document_count = 0
        except Exception as exc:
            logger.warning("Could not verify semantic memory: %s", type(exc).__name__)
            return {**base, **self._health_error("Semantic", exc)}

        return {
            **base,
            "available": available,
            "document_count": document_count,
            "status": "available" if available else ("unavailable" if expected else "disabled"),
        }

    def get_health(self) -> Dict[str, Any]:
        """Return component health and deterministic aggregate memory state."""
        structured = self._structured_health()
        semantic = self._semantic_health()
        structured_status = structured.get("status")
        semantic_status = semantic.get("status")

        if structured_status == "error" or semantic_status == "error":
            status = "error"
        elif structured_status != "available":
            status = "unavailable"
        elif semantic_status == "unavailable":
            status = "degraded"
        else:
            # Optional semantic memory being disabled does not make the
            # structured memory subsystem unhealthy.
            status = "available"

        return {
            "status": status,
            "structured": structured,
            "semantic": semantic,
        }

    @staticmethod
    def _item_from_node(
        node: Dict[str, Any],
        fallback_category: str,
        fallback_content: str,
        fallback_subject: str = "",
        fallback_predicate: str = "",
        fallback_object: str = "",
        fallback_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Project an actual persisted graph node into the managed-item shape."""
        metadata = node.get("metadata") if "metadata" in node else fallback_metadata
        metadata = metadata or {}
        if not isinstance(metadata, dict):
            metadata = {}
        return {
            "id": node.get("id"),
            "category": node.get("node_type") or fallback_category,
            "content": node.get("content", fallback_content),
            "subject": metadata.get("subject", fallback_subject),
            "predicate": metadata.get("predicate", fallback_predicate),
            "object": metadata.get("object", fallback_object),
            "created_at": node.get("created_at") or utc_now_iso(),
            "metadata": metadata,
        }

    def add_item(
        self,
        category: str = "fact",
        content: str = "",
        subject: str = "",
        predicate: str = "",
        obj: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        *,
        provenance: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add a managed graph item without implicit semantic ingestion."""
        if not isinstance(provenance, str) or not provenance.strip():
            raise ValueError("provenance must be a non-empty string")
        item_metadata = dict(metadata or {})
        item_metadata.pop("provenance", None)
        item_metadata.pop("history", None)
        item_metadata["provenance"] = provenance.strip()
        graph = self._get_graph()
        requested_id = f"mem_{make_id(8)}"
        node_type = (
            category
            if category in ("person", "place", "concept", "task", "preference", "fact", "event")
            else "fact"
        )

        persisted_id: Optional[str] = None
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
                    **item_metadata,
                }
                persisted_id = graph.add_node(
                    node_type=node_type,
                    content=content,
                    node_id=requested_id,
                    metadata=node_meta,
                )
        else:
            if graph:
                persisted_id = graph.add_node(
                    node_type=node_type,
                    content=content,
                    node_id=requested_id,
                    metadata=item_metadata,
                )

        if graph is not None and persisted_id is not None:
            persisted_node = graph.get_node(persisted_id)
            if persisted_node is not None:
                return self._item_from_node(
                    persisted_node,
                    fallback_category=node_type,
                    fallback_content=content,
                    fallback_subject=subject,
                    fallback_predicate=predicate,
                    fallback_object=obj,
                    fallback_metadata=item_metadata,
                )

        logger.warning("Could not persist managed memory item")
        return {
            "id": persisted_id,
            "category": node_type,
            "content": content,
            "subject": subject,
            "predicate": predicate,
            "object": obj,
            "created_at": utc_now_iso(),
            "metadata": item_metadata,
        }

    def list_items(self, category: Optional[str] = None, limit: int = 300) -> List[Dict[str, Any]]:
        """List managed graph items and graph-only relational projections."""
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
                    fact_id = fact_compatibility_id(s, p, o)
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
        """Search managed graph items by matching query terms and category."""
        q_lower = query.lower().strip()
        terms = _search_terms(q_lower)
        if not terms:
            return []

        all_items = self.list_items(category=category, limit=500)
        ranked = []
        for position, item in enumerate(all_items):
            searchable = " ".join(
                str(item.get(key) or "")
                for key in ("content", "subject", "predicate", "object")
            ).casefold()
            item_terms = _search_terms(searchable)
            overlap = len(terms & item_terms)
            if not overlap:
                continue
            score = overlap + (len(terms) + 1 if q_lower in searchable else 0)
            ranked.append((score, position, item))

        ranked.sort(key=lambda row: (-row[0], row[1]))
        return [item for _score, _position, item in ranked[:limit]]

    def recall(self, query: str, n_results: int = 5) -> Optional[List[Dict[str, Any]]]:
        """Recall matching managed facts first, then semantic memories."""
        if not _search_terms(query):
            return []

        structured = self.search_items(query, limit=n_results)
        if structured:
            return [{**item, "source": "structured"} for item in structured]

        semantic = self.search_semantic(query, n_results=n_results)
        if semantic is None:
            return None

        results = []
        seen = set()
        for item in semantic or []:
            text = str(item.get("text") or "").strip()
            if not text or text.casefold() in seen:
                continue
            results.append({**item, "content": text, "source": "semantic"})
            seen.add(text.casefold())
        return results[:n_results]

    @staticmethod
    def _fact_from_metadata(metadata: Dict[str, Any]) -> Optional[Tuple[str, str, str]]:
        values = tuple(metadata.get(key) for key in ("subject", "predicate", "object"))
        if not any(values):
            return None
        if not all(isinstance(value, str) and value.strip() for value in values):
            raise ValueError("Managed graph facts require a complete subject, predicate, and object.")
        return values  # type: ignore[return-value]

    def update_item(
        self,
        item_id: str,
        content: Optional[str] = None,
        category: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        provenance: Optional[str] = None,
        subject: Optional[str] = None,
        predicate: Optional[str] = None,
        obj: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update a managed item, retaining a provenance-tagged reversible history."""
        if not isinstance(provenance, str) or not provenance.strip():
            raise ValueError("provenance must be a non-empty string")
        graph = self._get_graph()
        if not graph:
            return None

        node = graph.get_node(item_id)
        if node is None:
            return None

        current_type = category or node.get("node_type", "fact")
        node_type = (
            current_type
            if current_type in ("person", "place", "concept", "task", "preference", "fact", "event")
            else "fact"
        )

        old_meta = node.get("metadata") or {}
        if not isinstance(old_meta, dict):
            old_meta = {}
        old_meta = dict(old_meta)
        raw_history = old_meta.get("history", [])
        history = [entry for entry in raw_history if isinstance(entry, dict)] if isinstance(raw_history, list) else []
        old_meta.pop("history", None)
        old_fact = self._fact_from_metadata(old_meta)
        replacement_values = (subject, predicate, obj)
        has_replacement = any(value is not None for value in replacement_values)
        if old_fact and not has_replacement:
            raise ValueError("Triple-backed facts require subject, predicate, and object for correction.")
        if has_replacement and not all(isinstance(value, str) and value.strip() for value in replacement_values):
            raise ValueError("Triple-backed facts require subject, predicate, and object for correction.")
        new_fact = tuple(replacement_values) if has_replacement else None

        existing_meta = dict(old_meta)
        if metadata:
            incoming_meta = dict(metadata)
            for key in ("history", "provenance", "subject", "predicate", "object"):
                incoming_meta.pop(key, None)
            existing_meta.update(incoming_meta)
        if new_fact:
            existing_meta.update(dict(zip(("subject", "predicate", "object"), new_fact)))

        updated_content = node.get("content", "") if content is None else content
        existing_meta["provenance"] = provenance.strip()
        changed = (
            updated_content != node.get("content", "")
            or node_type != node.get("node_type", "fact")
            or existing_meta != old_meta
        )
        if changed:
            history.append({
                "id": make_id(8),
                "operation": "update",
                "status": "applied",
                "at": utc_now_iso(),
                "provenance": provenance.strip(),
                "before": {
                    "content": node.get("content", ""),
                    "category": node.get("node_type", "fact"),
                    "metadata": old_meta,
                },
            })
        if history:
            existing_meta["history"] = history

        if old_fact is not None or new_fact is not None:
            persisted_node = graph.update_managed_fact(
                item_id,
                old_fact=old_fact,
                new_fact=new_fact or old_fact,
                node_type=node_type,
                content=updated_content,
                metadata=existing_meta,
            )
        else:
            persisted_node = graph.update_node(
                node_id=item_id,
                node_type=node_type,
                content=updated_content,
                metadata=existing_meta,
            )
        if persisted_node is not None:
            return self._item_from_node(
                persisted_node,
                fallback_category=node_type,
                fallback_content=updated_content,
                fallback_metadata=existing_meta,
            )

        logger.warning("Could not update managed memory item %s", item_id)
        return None

    def undo_item_update(self, item_id: str, provenance: str = "owner_undo") -> Optional[Dict[str, Any]]:
        """Restore the prior state of the latest applied update, retaining an audit trail."""
        if not isinstance(provenance, str) or not provenance.strip():
            raise ValueError("provenance must be a non-empty string")
        graph = self._get_graph()
        node = graph.get_node(item_id) if graph else None
        if node is None:
            return None
        current_meta = node.get("metadata") or {}
        if not isinstance(current_meta, dict):
            current_meta = {}
        raw_history = current_meta.get("history", [])
        history = [entry for entry in raw_history if isinstance(entry, dict)] if isinstance(raw_history, list) else []
        target = next(
            (entry for entry in reversed(history)
             if entry.get("operation") == "update" and entry.get("status") == "applied"),
            None,
        )
        if target is None:
            return None

        before = target.get("before") or {}
        if not isinstance(before.get("metadata"), dict):
            return None
        target["status"] = "undone"
        restored_meta = dict(before["metadata"])
        current_base_meta = {key: value for key, value in current_meta.items() if key != "history"}
        current_fact = self._fact_from_metadata(current_base_meta)
        before_fact = self._fact_from_metadata(restored_meta)
        restored_meta["history"] = history
        history.append({
            "id": make_id(8),
            "operation": "undo",
            "status": "applied",
            "at": utc_now_iso(),
            "provenance": provenance.strip(),
            "before": {
                "content": node.get("content", ""),
                "category": node.get("node_type", "fact"),
                "metadata": {key: value for key, value in current_meta.items() if key != "history"},
            },
        })
        restored_meta["history"] = history
        if current_fact is not None or before_fact is not None:
            restored = graph.update_managed_fact(
                item_id,
                old_fact=current_fact,
                new_fact=before_fact,
                node_type=before.get("category", "fact"),
                content=before.get("content", ""),
                metadata=restored_meta,
            )
        else:
            restored = graph.update_node(
                node_id=item_id,
                node_type=before.get("category", "fact"),
                content=before.get("content", ""),
                metadata=restored_meta,
            )
        if restored is None:
            return None
        return self._item_from_node(
            restored,
            fallback_category=before.get("category", "fact"),
            fallback_content=before.get("content", ""),
            fallback_metadata=restored_meta,
        )

    def delete_item(self, item_id: str) -> bool:
        """Delete a managed graph item or its associated graph fact."""
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

        # Check if item_id corresponds to a stable relational compatibility ID
        for s, p, o in graph.get_all_facts():
            fact_id = fact_compatibility_id(s, p, o)
            if fact_id == item_id or f"{s} {p} {o}" == item_id:
                graph.remove_fact(s, p, o)
                return True

        return False

    def clear_category(self, category: Optional[str] = None) -> int:
        """Clear managed graph items in a category, or all graph memory."""
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
        """Export managed graph memory with metadata and graph statistics."""
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
        """Return graph statistics plus safe semantic adapter statistics."""
        graph = self._get_graph()
        stats = graph.get_stats() if graph else {}
        items = self.list_items(limit=1000)
        category_counts: Dict[str, int] = {}
        for it in items:
            cat = it.get("category", "fact")
            category_counts[cat] = category_counts.get(cat, 0) + 1
        stats["categories"] = category_counts
        stats["total_items"] = len(items)
        stats["semantic"] = self._semantic_stats()
        health = self.get_health()
        stats["status"] = health["status"]
        stats["health"] = health
        return stats
