import os
import tempfile
from pathlib import Path

import pytest

import charlie.web_server as web_server
from charlie.memory_graph import MemoryGraph


def _remove_db(db_path: str) -> None:
    for path in (db_path, f"{db_path}-wal", f"{db_path}-shm"):
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


@pytest.mark.asyncio
async def test_get_dashboard_config():
    res = await web_server.get_dashboard_config()
    assert "GPU_DEVICE" in res
    assert "KOKORO_LANG" in res
    assert "WHISPER_MODEL" in res
    assert "MCP_SERVERS" in res


@pytest.mark.asyncio
async def test_update_dashboard_config(monkeypatch):
    # Mock _update_env_file to avoid changing active .env during tests
    called = []
    def mock_update(updates):
        called.append(updates)
    monkeypatch.setattr(web_server, "_update_env_file", mock_update)

    test_payload = {
        "GPU_DEVICE": "cpu",
        "KOKORO_LANG": "en-gb",
        "WAKE_WORD_ENABLED": True,
    }

    res = await web_server.update_dashboard_config(test_payload)
    assert res["status"] == "ok"
    assert res["config"]["GPU_DEVICE"] == "cpu"
    assert res["config"]["KOKORO_LANG"] == "en-gb"
    assert res["config"]["WAKE_WORD_ENABLED"] is True
    assert len(called) == 1
    assert called[0]["GPU_DEVICE"] == "cpu"


@pytest.mark.asyncio
async def test_delete_memory_fact(monkeypatch):
    with tempfile.TemporaryDirectory(suffix="-web-facts-delete") as d:
        db_path = str(Path(d) / "graph.db")
        graph = MemoryGraph(db_path)
        try:
            graph.add_fact("Alice", "works_on", "graphs")
            monkeypatch.setattr(web_server, "_get_memory_graph", lambda: graph)

            # Confirm fact is added
            facts_before = graph.get_all_facts()
            assert len(facts_before) == 1

            # Delete the fact
            del_res = await web_server.delete_memory_fact("Alice", "works_on", "graphs")
            assert del_res["status"] == "ok"

            # Verify it is deleted from sqlite
            facts_after = graph.get_all_facts()
            assert len(facts_after) == 0
        finally:
            graph.close()
            _remove_db(db_path)
