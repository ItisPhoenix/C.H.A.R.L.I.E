import os
import tempfile
from pathlib import Path
import pytest

from charlie.memory_graph import MemoryGraph
from charlie.memory_service import MemoryService
import charlie.web_server as web_server


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
            put_res = await web_server.update_memory_item(item_id, {"content": "Charlie runs locally on fast hardware", "category": "fact"})
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
