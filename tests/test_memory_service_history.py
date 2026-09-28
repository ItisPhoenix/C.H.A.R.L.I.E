import pytest

from charlie.memory_graph import MemoryGraph
from charlie.memory_service import MemoryService


def test_structured_memory_updates_are_deduplicated_provenanced_and_reversible():
    graph = MemoryGraph(":memory:")
    service = MemoryService(graph=graph, semantic_expected=False)
    try:
        item = service.add_item(
            category="preference",
            content="Prefers concise answers",
            provenance="user_explicit",
        )
        with pytest.raises(ValueError, match="provenance"):
            service.update_item(item["id"], content="Unattributed correction")

        corrected = service.update_item(
            item["id"],
            content="Prefers detailed answers",
            metadata={"history": ["forged"], "provenance": "forged"},
            provenance="user_correction",
        )
        assert corrected["content"] == "Prefers detailed answers"
        assert corrected["metadata"]["provenance"] == "user_correction"
        assert corrected["metadata"]["history"][0]["before"]["content"] == "Prefers concise answers"

        duplicate = service.update_item(
            item["id"],
            content="Prefers detailed answers",
            provenance="user_correction",
        )
        assert duplicate["metadata"]["history"] == corrected["metadata"]["history"]

        restored = service.undo_item_update(item["id"], provenance="owner_undo")
        assert restored["content"] == "Prefers concise answers"
        assert restored["metadata"]["provenance"] == "user_explicit"
        assert restored["metadata"]["history"][-1]["operation"] == "undo"
        assert restored["metadata"]["history"][-1]["provenance"] == "owner_undo"
        assert service.undo_item_update(item["id"]) is None

        metadata_updated = service.update_item(
            item["id"], metadata={"confidence": "high"}, provenance="user_clarification"
        )
        assert metadata_updated["metadata"]["confidence"] == "high"
        assert metadata_updated["metadata"]["history"][-1]["before"]["metadata"]["provenance"] == "user_explicit"
        metadata_restored = service.undo_item_update(item["id"])
        assert metadata_restored["metadata"].get("confidence") is None
        assert metadata_restored["metadata"]["provenance"] == "user_explicit"

        with pytest.raises(ValueError, match="provenance"):
            service.add_item(category="preference", content="Unattributed")
    finally:
        graph.close()


def test_natural_query_terms_find_a_structured_fact_without_returning_all_memory():
    graph = MemoryGraph(":memory:")
    service = MemoryService(graph=graph, semantic_expected=False)
    try:
        item = service.add_item(
            category="fact",
            subject="charlie_g6_test",
            predicate="marker",
            obj="CHARLIE-G6-TEMP-TEST",
            provenance="user_explicit",
        )

        results = service.search_items(
            "what temporary marker did I ask you to remember for my next check?"
        )

        assert results[0]["id"] == item["id"]
        assert results[0]["object"] == "CHARLIE-G6-TEMP-TEST"
        assert service.search_items("what is it?") == []
        assert service.recall("what temporary marker did I ask you to remember?")[0]["id"] == item["id"]
    finally:
        graph.close()


def test_memory_recall_uses_semantic_store_when_no_structured_match_exists():
    class SemanticStore:
        is_available = True

        def search(self, query, n_results=3):
            assert query == "tea"
            return [{"text": "Prefers tea", "id": "semantic-1"}]

    graph = MemoryGraph(":memory:")
    service = MemoryService(graph=graph, memory_store=SemanticStore())
    try:
        assert service.recall("tea") == [
            {"text": "Prefers tea", "id": "semantic-1", "content": "Prefers tea", "source": "semantic"}
        ]
    finally:
        graph.close()
