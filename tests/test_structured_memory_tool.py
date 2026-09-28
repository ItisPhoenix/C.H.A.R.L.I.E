import pytest

import charlie.memory_graph as memory_graph_module
import charlie.tools as tools_module
from charlie.memory_graph import MemoryGraph
from charlie.memory_service import MemoryService
from charlie.tools import graph_add_fact, graph_query, memory, vector_memory


@pytest.fixture
def structured_memory(monkeypatch):
    graph = MemoryGraph(":memory:")
    service = MemoryService(graph=graph, semantic_expected=False)
    monkeypatch.setattr(tools_module, "_memory_service", service)
    yield service
    graph.close()


def test_structured_memory_tool_add_search_correct_and_undo(structured_memory):
    added = memory(
        "add", "structured", category="preference", content="Prefers concise replies"
    )
    assert added.startswith("Remembered structured memory")
    item_id = added.split("id=", 1)[1].split(";", 1)[0]

    found = memory("search", "structured", query="concise replies")
    assert item_id in found
    assert "Prefers concise replies" in found

    corrected = memory(
        "update", "structured", item_id=item_id, content="Prefers detailed replies"
    )
    assert "Updated" in corrected
    assert "Prefers detailed replies" in memory("search", "structured", query="detailed replies")

    undone = memory("undo", "structured", item_id=item_id)
    assert "Restored" in undone
    assert "Prefers concise replies" in memory("search", "structured", query="concise replies")


def test_all_memory_search_finds_structured_fact_from_natural_recall_query(structured_memory):
    added = memory(
        "add",
        "structured",
        category="fact",
        subject="charlie_g6_test",
        predicate="marker",
        object="CHARLIE-G6-TEMP-TEST",
    )
    assert added.startswith("Remembered structured memory")

    result = memory(
        "search",
        "all",
        query="what temporary marker did I ask you to remember for my next check?",
    )

    assert "CHARLIE-G6-TEMP-TEST" in result
    assert "[structured]" in result


def test_structured_memory_duplicate_and_noop_update_are_stable(structured_memory):
    first = memory("add", "structured", category="fact", content="Likes tea")
    second = memory("add", "structured", category="fact", content="Likes tea")
    assert first.split("id=", 1)[1] == second.split("id=", 1)[1]
    item_id = first.split("id=", 1)[1].split(";", 1)[0]

    memory("update", "structured", item_id=item_id, content="Likes coffee")
    updated = structured_memory.search_items("Likes coffee", category="fact")[0]
    history_size = len(updated["metadata"]["history"])
    memory("update", "structured", item_id=item_id, content="Likes coffee")
    updated_again = structured_memory.search_items("Likes coffee", category="fact")[0]
    assert len(updated_again["metadata"]["history"]) == history_size


def test_structured_memory_missing_item_fails_truthfully(structured_memory):
    assert memory("update", "structured", item_id="missing", content="new") == "Error: Memory item was not found."
    assert memory("undo", "structured", item_id="missing") == "Error: Memory item was not found."


@pytest.mark.parametrize(
    "content",
    [
        "password is hunter2",
        "My Aadhaar number is 1234 5678 9012",
        "Card number 4111 1111 1111 1111",
        "-----BEGIN PRIVATE KEY----- hidden -----END PRIVATE KEY-----",
    ],
)
def test_structured_memory_refuses_sensitive_content_before_write(structured_memory, content):
    result = memory("add", "structured", category="fact", content=content)
    assert result == "Error: Sensitive credentials, payment data, or government IDs cannot be stored."
    assert structured_memory.list_items() == []


def test_file_memory_add_and_replace_refuse_sensitive_content(monkeypatch, tmp_path):
    memory_path = tmp_path / "memory.md"
    monkeypatch.setattr(tools_module.config, "memory_file", str(memory_path))
    refused = "Error: Sensitive credentials, payment data, or government IDs cannot be stored."

    assert memory("add", "memory", content="Password is hunter2") == refused
    assert not memory_path.exists()

    memory_path.write_text("Keep answers concise", encoding="utf-8")
    assert memory(
        "replace", "memory", old_text="Keep answers", content="Aadhaar 1234 5678 9012"
    ) == refused
    assert memory_path.read_text(encoding="utf-8") == "Keep answers concise"


def test_vector_memory_refuses_sensitive_remember_but_keeps_recall(monkeypatch):
    class MemorySpy:
        def __init__(self):
            self.writes = []

        def semantic_available(self):
            return True

        def remember_semantic(self, **kwargs):
            self.writes.append(kwargs)
            return 1

        def search_semantic(self, query, n_results):
            return [{"text": "Likes tea"}]

    service = MemorySpy()
    monkeypatch.setattr(tools_module, "_memory_service", service)

    refused = vector_memory("remember", "My Aadhaar number is 1234 5678 9012")

    assert refused == "Error: Sensitive credentials, payment data, or government IDs cannot be stored."
    assert service.writes == []
    assert vector_memory("recall", "tea") == "- Likes tea"


def test_graph_add_fact_refuses_sensitive_fact_before_write(monkeypatch):
    class MemorySpy:
        def __init__(self):
            self.writes = []

        def add_fact(self, subject, predicate, obj):
            self.writes.append((subject, predicate, obj))
            return "edge-id"

    service = MemorySpy()
    monkeypatch.setattr(tools_module, "_memory_service", service)

    refused = graph_add_fact("user", "has Aadhaar", "1234 5678 9012")

    assert refused == "Error: Sensitive credentials, payment data, or government IDs cannot be stored."
    assert service.writes == []


def test_graph_add_fact_uses_provenanced_item_and_remains_queryable(structured_memory):
    result = graph_add_fact("user", "prefers", "concise replies")

    assert result == "Added: user -> prefers -> concise replies"
    item = structured_memory.search_items("concise replies", category="fact")[0]
    assert item["metadata"]["provenance"] == "tool_explicit_add"
    assert "user -> prefers -> concise replies" in graph_query("concise replies")


def test_structured_fact_correction_and_undo_keep_graph_relation_consistent(structured_memory):
    graph_add_fact("user", "prefers", "concise replies")
    item = structured_memory.search_items("concise replies", category="fact")[0]

    updated = memory(
        "update",
        "structured",
        item_id=item["id"],
        content="user prefers detailed replies",
        subject="user",
        predicate="prefers",
        object="detailed replies",
    )
    assert "Updated" in updated
    assert "user -> prefers -> concise replies" not in graph_query("concise replies")
    assert "user -> prefers -> detailed replies" in graph_query("detailed replies")

    undone = memory("undo", "structured", item_id=item["id"])
    assert "Restored" in undone
    assert "user -> prefers -> detailed replies" not in graph_query("detailed replies")
    assert "user -> prefers -> concise replies" in graph_query("concise replies")


def test_structured_fact_content_only_update_fails_closed(structured_memory):
    graph_add_fact("user", "prefers", "concise replies")
    item = structured_memory.search_items("concise replies", category="fact")[0]

    result = memory("update", "structured", item_id=item["id"], content="detailed replies")

    assert result == "Error: Triple-backed facts require subject, predicate, and object for correction."
    assert "user -> prefers -> concise replies" in graph_query("concise replies")


def test_structured_fact_edge_and_item_snapshot_roll_back_together(structured_memory, monkeypatch):
    graph_add_fact("user", "prefers", "concise replies")
    item = structured_memory.search_items("concise replies", category="fact")[0]

    def fail_metadata_serialization(_metadata):
        raise TypeError("controlled serialization failure")

    monkeypatch.setattr(memory_graph_module, "json_dumps", fail_metadata_serialization)
    with pytest.raises(TypeError, match="controlled serialization failure"):
        structured_memory.update_item(
            item["id"],
            content="user prefers detailed replies",
            subject="user",
            predicate="prefers",
            obj="detailed replies",
            provenance="test_correction",
        )

    assert "user -> prefers -> concise replies" in graph_query("concise replies")
    assert "user -> prefers -> detailed replies" not in graph_query("detailed replies")
    assert structured_memory.search_items("concise replies", category="fact")[0]["content"] == (
        "user prefers concise replies"
    )


def test_memory_tool_description_limits_structured_adds_to_durable_facts():
    definition = next(
        entry["function"]
        for entry in tools_module.registry.get_tool_definitions()
        if entry["function"]["name"] == "memory"
    )

    assert "durable user preferences or environment facts" in definition["description"]
    assert "Never store task outputs or full conversations" in definition["description"]
