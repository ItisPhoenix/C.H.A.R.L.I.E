"""Tests for /api/memory/facts returning real subject/predicate/object triples.

Regression test for the bug where the endpoint mapped every knowledge-graph
node to a fake {node_type} is {content} triple instead of reading the edges
table where add_fact stores the real relationships.
"""

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
async def test_get_memory_facts_returns_real_triples(monkeypatch):
    with tempfile.TemporaryDirectory(suffix="-webfacts") as d:
        db_path = str(Path(d) / "graph.db")
        graph = MemoryGraph(db_path)
        try:
            graph.add_fact("Alice", "works_on", "graphs")
            graph.add_fact("Bob", "knows", "Alice")

            monkeypatch.setattr(web_server, "_get_memory_graph", lambda: graph)

            result = await web_server.get_memory_facts()

            facts = result["facts"]
            assert {"subject": "Alice", "predicate": "works_on", "object": "graphs"} in facts
            assert {"subject": "Bob", "predicate": "knows", "object": "Alice"} in facts
            # Not the old bug shape: predicate should never just be "is".
            assert all(f["predicate"] != "is" for f in facts)
        finally:
            graph.close()
            _remove_db(db_path)


@pytest.mark.asyncio
async def test_get_memory_facts_no_graph_returns_empty(monkeypatch):
    monkeypatch.setattr(web_server, "_get_memory_graph", lambda: None)
    result = await web_server.get_memory_facts()
    assert result == {"facts": []}


@pytest.mark.asyncio
async def test_get_memory_facts_empty_graph_returns_empty(monkeypatch):
    with tempfile.TemporaryDirectory(suffix="-webfacts-empty") as d:
        db_path = str(Path(d) / "graph.db")
        graph = MemoryGraph(db_path)
        try:
            monkeypatch.setattr(web_server, "_get_memory_graph", lambda: graph)
            result = await web_server.get_memory_facts()
            assert result == {"facts": []}
        finally:
            graph.close()
            _remove_db(db_path)
