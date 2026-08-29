import os
import tempfile
from pathlib import Path

import pytest

import charlie.web_server as web_server
from charlie.memory_graph import MemoryGraph
from charlie.memory_service import MemoryService, fact_compatibility_id


class StrictMemoryStore:
    """Small strict semantic adapter fake that rejects stale call shapes."""

    def __init__(self, available: bool = True) -> None:
        self.is_available = available
        self.add_calls = []
        self.search_calls = []
        self.format_calls = []
        self.search_result = []
        self.stats = {"available": available, "document_count": 0}

    def add_memory(
        self,
        text: str,
        source: str,
        session_id: str,
        *,
        auto_extract: bool = True,
    ) -> int:
        self.add_calls.append((text, source, session_id, auto_extract))
        return 1

    def search(self, query: str, n_results: int = 3, threshold=None):
        self.search_calls.append((query, n_results, threshold))
        return self.search_result

    def format_for_prompt(self, results):
        self.format_calls.append(results)
        return "formatted semantic memory"

    def get_stats(self):
        return self.stats


def _remove_db(db_path: str) -> None:
    for p in (db_path, f"{db_path}-wal", f"{db_path}-shm"):
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


def test_memory_service_crud_and_clear():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test_memory.db")
        graph = MemoryGraph(db_path)
        service = MemoryService(graph=graph)

        try:
            # 1. Add items
            item1 = service.add_item(category="fact", subject="User", predicate="likes", obj="Python")
            item2 = service.add_item(category="preference", content="Prefers concise responses")
            assert item1["id"] is not None
            assert item2["id"] is not None

            # 2. List items
            items = service.list_items()
            assert len(items) == 2

            # Filter by category
            facts = service.list_items(category="fact")
            assert len(facts) == 1
            assert facts[0]["subject"] == "User"

            # 3. Search items
            search_res = service.search_items(query="concise")
            assert len(search_res) == 1
            assert "concise" in search_res[0]["content"]

            # 4. Edit item
            updated = service.update_item(item2["id"], content="Prefers ultra concise responses", category="preference")
            assert updated is not None
            assert "ultra concise" in updated["content"]

            # 5. Delete item
            assert service.delete_item(item1["id"]) is True
            assert len(service.list_items()) == 1

            # 6. Export
            export_data = service.export_all()
            assert "items" in export_data
            assert "stats" in export_data
            assert len(export_data["items"]) == 1

            # 7. Clear category
            assert service.clear_category("preference") == 1
            assert len(service.list_items()) == 0
        finally:
            graph.close()
            _remove_db(db_path)


def test_add_item_does_not_implicitly_ingest_semantic_memory():
    graph, db_path = _make_graph_for_service()
    store = StrictMemoryStore()
    service = MemoryService(graph=graph, memory_store=store)

    try:
        item = service.add_item(category="preference", content="User prefers concise responses")
        assert item["id"].startswith("mem_")
        assert store.add_calls == []
    finally:
        graph.close()
        _remove_db(db_path)


def test_semantic_facade_delegates_exactly_and_preserves_search_states():
    graph, db_path = _make_graph_for_service()
    store = StrictMemoryStore()
    store.search_result = [{"text": "User likes Python."}]
    service = MemoryService(graph=graph, memory_store=store)

    try:
        assert service.remember_semantic(
            "User likes Python.",
            source="chat",
            session_id="session-7",
            auto_extract=False,
        ) == 1
        assert store.add_calls == [("User likes Python.", "chat", "session-7", False)]

        assert service.search_semantic("Python", n_results=7, threshold=0.25) == store.search_result
        assert store.search_calls == [("Python", 7, 0.25)]
        assert service.format_semantic_for_prompt(store.search_result) == "formatted semantic memory"
        assert store.format_calls == [store.search_result]

        store.search_result = None
        assert service.search_semantic("backend failure") is None
        store.search_result = []
        assert service.search_semantic("no match") == []
    finally:
        graph.close()
        _remove_db(db_path)


def test_semantic_facade_is_neutral_when_store_is_unavailable():
    graph, db_path = _make_graph_for_service()
    store = StrictMemoryStore(available=False)
    service = MemoryService(graph=graph, memory_store=store)

    try:
        assert service.remember_semantic("text", source="chat", session_id="s1") == 0
        assert service.search_semantic("text") == []
        assert service.format_semantic_for_prompt([]) == ""
        assert store.add_calls == []
        assert store.search_calls == []
    finally:
        graph.close()
        _remove_db(db_path)


def test_memory_service_stats_include_semantic_adapter_stats():
    graph, db_path = _make_graph_for_service()
    store = StrictMemoryStore()
    store.stats = {"available": True, "document_count": 4}
    service = MemoryService(graph=graph, memory_store=store)

    try:
        stats = service.get_stats()
        assert stats["nodes"] == 0
        assert stats["edges"] == 0
        assert stats["semantic"] == {"available": True, "document_count": 4}
    finally:
        graph.close()
        _remove_db(db_path)


def test_duplicate_managed_content_returns_actual_persisted_id():
    graph, db_path = _make_graph_for_service()
    service = MemoryService(graph=graph)

    try:
        first = service.add_item(category="preference", content="User prefers concise responses")
        second = service.add_item(category="preference", content="User prefers concise responses")

        assert first["id"] == second["id"]
        assert first["id"].startswith("mem_")
        persisted = graph.get_node(first["id"])
        assert persisted is not None
        assert second["id"] == persisted["id"]
        assert second["created_at"] == persisted["created_at"]
        assert len(service.list_items(category="preference")) == 1
    finally:
        graph.close()
        _remove_db(db_path)


def test_managed_add_does_not_adopt_generic_dedup_node():
    graph, db_path = _make_graph_for_service()
    graph.add_node("fact", "Generic graph content", node_id="generic-fact")
    service = MemoryService(graph=graph)

    try:
        item = service.add_item(category="fact", content="Generic graph content")

        assert item["id"].startswith("mem_")
        assert graph.get_node(item["id"]) is not None
        assert graph.get_node("generic-fact") is not None
        assert item["id"] in {entry["id"] for entry in service.list_items()}
        graph.consolidate()
        assert graph.get_node(item["id"]) is not None
    finally:
        graph.close()
        _remove_db(db_path)


def test_update_managed_item_preserves_id_on_duplicate_content_collision():
    graph, db_path = _make_graph_for_service()
    service = MemoryService(graph=graph)

    try:
        item = service.add_item(category="preference", content="Original preference")
        graph.add_node("preference", "Replacement preference", node_id="generic-preference")

        updated = service.update_item(item["id"], content="Replacement preference")

        assert updated is not None
        assert updated["id"] == item["id"]
        assert graph.get_node(item["id"])["content"] == "Replacement preference"
        assert graph.get_node("generic-preference") is not None
    finally:
        graph.close()
        _remove_db(db_path)


def test_relational_compatibility_id_is_stable_and_deletable():
    graph, db_path = _make_graph_for_service()
    service = MemoryService(graph=graph)

    try:
        graph.add_fact("Alice", "uses", "Python")
        item = next(item for item in service.list_items() if item["content"] == "Alice uses Python")
        expected_id = "fact_98fef6b77a71c29439bc74cdd7eed4840933119963380ab1b9a7d5661b8e3117"
        assert item["id"] == expected_id
        assert item["id"] == fact_compatibility_id("Alice", "uses", "Python")
        assert item["id"] == fact_compatibility_id("Alice", "uses", "Python")
        assert service.delete_item(item["id"]) is True
        assert graph.get_all_facts() == []
    finally:
        graph.close()
        _remove_db(db_path)


def test_missing_update_returns_none_without_creating_data():
    graph, db_path = _make_graph_for_service()
    service = MemoryService(graph=graph)

    try:
        before = graph.get_stats()
        assert service.update_item("mem_missing", content="should not exist") is None
        assert graph.get_stats() == before
        assert graph.get_node("mem_missing") is None
    finally:
        graph.close()
        _remove_db(db_path)


def test_managed_orphan_survives_graph_consolidation():
    graph, db_path = _make_graph_for_service()
    service = MemoryService(graph=graph)

    try:
        item = service.add_item(category="fact", content="Standalone durable memory")
        assert graph.get_node(item["id"]) is not None
        graph.consolidate()
        assert graph.get_node(item["id"]) is not None
    finally:
        graph.close()
        _remove_db(db_path)


def _make_graph_for_service():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return MemoryGraph(db_path), db_path


@pytest.mark.asyncio
async def test_web_server_memory_management_endpoints(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test_web_memory.db")
        graph = MemoryGraph(db_path)
        service = MemoryService(graph=graph)
        monkeypatch.setattr(web_server, "_get_memory_graph", lambda: graph)
        monkeypatch.setattr(web_server, "_memory_service", service)

        try:
            # 1. POST /api/memory/items
            res1 = await web_server.create_memory_item({
                "category": "fact",
                "subject": "Charlie",
                "predicate": "runs_on",
                "object": "LocalHost",
            })
            assert res1["status"] == "ok"
            item_id = res1["item"]["id"]

            # 2. GET /api/memory/items
            list_res = await web_server.get_memory_items()
            assert len(list_res["items"]) == 1
            assert list_res["items"][0]["id"] == item_id

            # 3. GET /api/memory/search
            search_res = await web_server.search_memory_items(q="LocalHost")
            assert len(search_res["items"]) == 1

            # 4. PUT /api/memory/items/{id}
            put_res = await web_server.update_memory_item(
                item_id,
                {"content": "Charlie runs locally on fast hardware", "category": "fact"},
            )
            assert put_res["status"] == "ok"

            # 5. GET /api/memory/export
            export_res = await web_server.export_memory()
            assert "items" in export_res
            assert len(export_res["items"]) == 1

            # 6. POST /api/memory/clear
            clear_res = await web_server.clear_memory({"category": "fact"})
            assert clear_res["status"] == "ok"
            assert clear_res["cleared_count"] == 1

            list_after = await web_server.get_memory_items()
            assert len(list_after["items"]) == 0
        finally:
            graph.close()
            _remove_db(db_path)
