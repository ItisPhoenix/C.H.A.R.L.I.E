import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from charlie.memory_graph import MemoryGraph

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_graph() -> tuple[MemoryGraph, str]:
    """Create a MemoryGraph backed by a fresh temp sqlite file."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return MemoryGraph(db_path), db_path


def _remove_db(db_path: str) -> None:
    """Best-effort cleanup for sqlite + WAL/SHM sidecars."""
    for path in [
        db_path,
        f"{db_path}-wal",
        f"{db_path}-shm",
    ]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def graph() -> MemoryGraph:
    with tempfile.TemporaryDirectory(suffix="-mgraph") as d:
        db_path = Path(d) / "graph.db"
        yield MemoryGraph(str(db_path))


# ---------------------------------------------------------------------------
# add_node / get_node / get_all_nodes / delete_node
# ---------------------------------------------------------------------------


class TestNodeCrud:
    def test_add_node_returns_id(self):
        graph, db = _make_graph()
        try:
            nid = graph.add_node("person", "Alice")
            assert isinstance(nid, str)
            assert len(nid) > 0
        finally:
            graph.close()
            _remove_db(db)

    def test_add_node_explicit_id(self):
        graph, db = _make_graph()
        try:
            nid = graph.add_node("person", "Alice", node_id="alice-1")
            assert nid == "alice-1"
        finally:
            graph.close()
            _remove_db(db)


    def test_add_node_deduplicates_same_type_and_content(self):
        graph, db = _make_graph()
        try:
            a = graph.add_node("person", "Alice")
            b = graph.add_node("person", "Alice")
            assert a == b
        finally:
            graph.close()
            _remove_db(db)

    def test_get_node_missing_returns_none(self):
        graph, db = _make_graph()
        try:
            assert graph.get_node("missing") is None
        finally:
            graph.close()
            _remove_db(db)

    def test_delete_node_removes_edges(self):
        graph, db = _make_graph()
        try:
            n1 = graph.add_node("person", "Alice")
            n2 = graph.add_node("person", "Bob")
            graph.add_edge(n1, n2, "knows")
            assert graph.delete_node(n1) is True
            assert graph.get_node(n1) is None
            assert graph.get_node(n2) is not None
        finally:
            graph.close()
            _remove_db(db)

    def test_get_all_nodes_returns_inserted(self):
        graph, db = _make_graph()
        try:
            graph.add_node("person", "Alice")
            graph.add_node("concept", "graphs")
            nodes = graph.get_all_nodes()
            contents = [n["content"] for n in nodes]
            assert "Alice" in contents
            assert "graphs" in contents
        finally:
            graph.close()
            _remove_db(db)

    def test_get_all_nodes_filter_by_type(self):
        graph, db = _make_graph()
        try:
            graph.add_node("person", "Alice")
            graph.add_node("concept", "graphs")
            nodes = graph.get_all_nodes(node_type="person")
            assert [n["content"] for n in nodes] == ["Alice"]
        finally:
            graph.close()
            _remove_db(db)

    def test_delete_missing_node_returns_true(self):
        graph, db = _make_graph()
        try:
            assert graph.delete_node("doesnotexist") is True
        finally:
            graph.close()
            _remove_db(db)


# ---------------------------------------------------------------------------
# add_edge / query_neighbors / query_path
# ---------------------------------------------------------------------------


class TestEdges:
    def test_add_edge_returns_id(self):
        graph, db = _make_graph()
        try:
            a = graph.add_node("person", "Alice")
            b = graph.add_node("person", "Bob")
            eid = graph.add_edge(a, b, "knows")
            assert isinstance(eid, str)
            assert len(eid) > 0
        finally:
            graph.close()
            _remove_db(db)

    def test_add_edge_deduplicates(self):
        graph, db = _make_graph()
        try:
            a = graph.add_node("person", "Alice")
            b = graph.add_node("person", "Bob")
            graph.add_edge(a, b, "knows")
            graph.add_edge(a, b, "knows")
            assert graph.get_stats()["edges"] == 1
        finally:
            graph.close()
            _remove_db(db)

    def test_add_edge_unknown_relation_falls_back(self):
        graph, db = _make_graph()
        try:
            a = graph.add_node("person", "Alice")
            b = graph.add_node("person", "Bob")
            eid = graph.add_edge(a, b, "teleports")
            assert eid is not None
        finally:
            graph.close()
            _remove_db(db)

    def test_add_edge_missing_node_raises(self):
        graph, db = _make_graph()
        try:
            a = graph.add_node("person", "Alice")
            with pytest.raises(ValueError):
                graph.add_edge(a, "missing", "knows")
        finally:
            graph.close()
            _remove_db(db)

    def test_query_neighbors_out_direction(self):
        graph, db = _make_graph()
        try:
            a = graph.add_node("person", "Alice")
            b = graph.add_node("person", "Bob")
            c = graph.add_node("person", "Carol")
            graph.add_edge(a, b, "knows")
            graph.add_edge(a, c, "knows")
            neighbors = graph.query_neighbors(a, direction="out", relation="knows")
            contents = {n["content"] for n in neighbors}
            assert contents == {"Bob", "Carol"}
            assert all(n["direction"] == "out" for n in neighbors)
        finally:
            graph.close()
            _remove_db(db)

    def test_query_neighbors_in_direction(self):
        graph, db = _make_graph()
        try:
            a = graph.add_node("person", "Alice")
            b = graph.add_node("person", "Bob")
            c = graph.add_node("person", "Carol")
            graph.add_edge(b, a, "knows")
            graph.add_edge(c, a, "knows")
            neighbors = graph.query_neighbors(a, direction="in", relation="knows")
            contents = {n["content"] for n in neighbors}
            assert contents == {"Bob", "Carol"}
        finally:
            graph.close()
            _remove_db(db)

    def test_query_neighbors_both_direction(self):
        graph, db = _make_graph()
        try:
            a = graph.add_node("person", "Alice")
            b = graph.add_node("person", "Bob")
            graph.add_edge(a, b, "knows")
            graph.add_edge(b, a, "knows")
            neighbors = graph.query_neighbors(a, direction="both", relation="knows")
            contents = {n["content"] for n in neighbors}
            assert contents == {"Bob"}
        finally:
            graph.close()
            _remove_db(db)

    def test_query_neighbors_no_relation_filter(self):
        graph, db = _make_graph()
        try:
            a = graph.add_node("person", "Alice")
            b = graph.add_node("person", "Bob")
            graph.add_edge(a, b, "knows")
            neighbors = graph.query_neighbors(a, direction="out")
            assert len(neighbors) == 1
            assert neighbors[0]["relation"] == "knows"
        finally:
            graph.close()
            _remove_db(db)

    def test_query_path_short_path(self):
        graph, db = _make_graph()
        try:
            a = graph.add_node("person", "Alice")
            b = graph.add_node("person", "Bob")
            c = graph.add_node("person", "Carol")
            graph.add_edge(a, b, "knows")
            graph.add_edge(b, c, "knows")
            path = graph.query_path(a, c, max_depth=4)
            assert path is not None
            assert path[0]["id"] == b
            assert path[1]["id"] == c
        finally:
            graph.close()
            _remove_db(db)

    def test_query_path_same_node(self):
        graph, db = _make_graph()
        try:
            a = graph.add_node("person", "Alice")
            path = graph.query_path(a, a)
            assert path is not None
            assert len(path) == 1
            assert path[0]["id"] == a
        finally:
            graph.close()
            _remove_db(db)

    def test_query_path_no_path_returns_none(self):
        graph, db = _make_graph()
        try:
            a = graph.add_node("person", "Alice")
            b = graph.add_node("person", "Bob")
            graph.add_edge(a, b, "knows")
            assert graph.query_path(b, a, max_depth=4) is None
        finally:
            graph.close()
            _remove_db(db)

    def test_query_path_disconnected_pairs(self):
        graph, db = _make_graph()
        try:
            assert graph.query_path("a", "b") is None
            assert graph.query_path("a", "missing") is None
        finally:
            graph.close()
            _remove_db(db)


# ---------------------------------------------------------------------------
# search_nodes / get_stats
# ---------------------------------------------------------------------------


class TestSearchAndStats:
    def test_search_nodes_matches_content(self):
        graph, db = _make_graph()
        try:
            graph.add_node("person", "Alice")
            graph.add_node("person", "Alicia")
            results = graph.search_nodes("lic")
            contents = [r["content"] for r in results]
            assert "Alice" in contents
            assert "Alicia" in contents
        finally:
            graph.close()
            _remove_db(db)

    def test_search_nodes_type_filter(self):
        graph, db = _make_graph()
        try:
            graph.add_node("person", "Alice")
            graph.add_node("concept", "Alice algorithm")
            results = graph.search_nodes("Alice", node_type="person")
            assert [r["content"] for r in results] == ["Alice"]
        finally:
            graph.close()
            _remove_db(db)

    def test_search_nodes_limit(self):
        graph, db = _make_graph()
        try:
            for i in range(10):
                graph.add_node("person", f"user{i}")
            results = graph.search_nodes("user", limit=3)
            assert len(results) == 3
        finally:
            graph.close()
            _remove_db(db)

    def test_get_stats_counts(self):
        graph, db = _make_graph()
        try:
            graph.add_node("person", "Alice")
            graph.add_node("concept", "graphs")
            stats = graph.get_stats()
            assert stats["nodes"] == 2
            assert stats["edges"] == 0
            assert "person" in stats["by_type"]
            assert stats["by_type"]["person"] == 1
        finally:
            graph.close()
            _remove_db(db)


# ---------------------------------------------------------------------------
# add_fact / search_facts / consolidate
# ---------------------------------------------------------------------------


class TestFacts:
    def test_add_fact_creates_nodes_and_edge(self):
        graph, db = _make_graph()
        try:
            eid = graph.add_fact("Alice", "works_on", "graphs")
            assert isinstance(eid, str) and eid

            stats = graph.get_stats()
            assert stats["nodes"] >= 2
            assert stats["edges"] >= 1
        finally:
            graph.close()
            _remove_db(db)

    def test_add_fact_existing_subject_object_do_not_duplicate(self):
        graph, db = _make_graph()
        try:
            graph.add_fact("Alice", "knows", "Bob")
            graph.add_fact("Alice", "knows", "Bob")
            stats = graph.get_stats()
            assert stats["nodes"] == 2
            assert stats["edges"] >= 1
        finally:
            graph.close()
            _remove_db(db)

    def test_search_facts_partial_match(self):
        graph, db = _make_graph()
        try:
            graph.add_fact("Alice", "works_on", "graphs")
            results = graph.search_facts("graph")
            assert len(results) >= 1
            assert results[0][0] == "Alice"
            assert results[0][2] == "graphs"
        finally:
            graph.close()
            _remove_db(db)

    def test_search_facts_subject_filter(self):
        graph, db = _make_graph()
        try:
            graph.add_fact("Alice", "works_on", "graphs")
            graph.add_fact("Bob", "works_on", "graphs")
            results = graph.search_facts("works_on", subject_filter="Alice")
            assert all(s == "Alice" for s, _, _, _ in results)
        finally:
            graph.close()
            _remove_db(db)

    def test_search_facts_empty_returns_empty(self):
        graph, db = _make_graph()
        try:
            assert graph.search_facts("nope") == []
        finally:
            graph.close()
            _remove_db(db)

    def test_search_facts_scoring_order(self):
        graph, db = _make_graph()
        try:
            graph.add_fact("machine learning", "is", "AI")
            graph.add_fact("learning", "is", "education")
            results = graph.search_facts("learning")
            assert len(results) >= 2
            assert results[0][0] == "machine learning"
            assert all(results[i][3] >= results[i + 1][3] for i in range(len(results) - 1))
        finally:
            graph.close()
            _remove_db(db)

    def test_get_all_facts_returns_triples(self):
        graph, db = _make_graph()
        try:
            graph.add_fact("Alice", "works_on", "graphs")
            graph.add_fact("Bob", "knows", "Alice")
            facts = graph.get_all_facts()
            assert ("Alice", "works_on", "graphs") in facts
            assert ("Bob", "knows", "Alice") in facts
            assert all(len(f) == 3 for f in facts)
        finally:
            graph.close()
            _remove_db(db)

    def test_get_all_facts_empty_graph(self):
        graph, db = _make_graph()
        try:
            assert graph.get_all_facts() == []
        finally:
            graph.close()
            _remove_db(db)

    def test_get_all_facts_respects_limit(self):
        graph, db = _make_graph()
        try:
            for i in range(5):
                graph.add_fact(f"subject{i}", "knows", f"object{i}")
            facts = graph.get_all_facts(limit=2)
            assert len(facts) == 2
        finally:
            graph.close()
            _remove_db(db)

    def test_consolidate_removes_duplicate_edges(self):
        graph, db = _make_graph()
        try:
            a = graph.add_node("person", "Alice", node_id="a")
            b = graph.add_node("person", "Bob", node_id="b")
            now = "2024-01-01 00:00:00.000000"
            for edge_id in ("e1", "e2", "e3"):
                graph.conn.execute(
                    "INSERT INTO edges (id, from_node_id, to_node_id, relation, created_at) VALUES (?, ?, ?, ?, ?)",
                    (edge_id, a, b, "knows", now),
                )
            graph.conn.commit()
            assert graph.get_stats()["edges"] == 3
            removed = graph.consolidate()
            assert removed == 2
            assert graph.get_stats()["edges"] == 1
        finally:
            graph.close()
            _remove_db(db)

    def test_consolidate_removes_orphan_nodes(self):
        graph, db = _make_graph()
        try:
            graph.add_node("person", "Alice")
            graph.add_node("person", "Bob")
            removed = graph.consolidate()
            assert removed >= 2
            assert graph.get_stats()["nodes"] == 0
        finally:
            graph.close()
            _remove_db(db)

    def test_consolidate_does_not_remove_connected_nodes(self):
        graph, db = _make_graph()
        try:
            graph.add_fact("Alice", "knows", "Bob")
            assert graph.consolidate() == 0
            assert graph.get_stats()["nodes"] >= 2
        finally:
            graph.close()
            _remove_db(db)


# ---------------------------------------------------------------------------
# close / thread safety
# ---------------------------------------------------------------------------


class TestCloseAndThreads:
    def test_close_does_not_raise(self):
        graph, db = _make_graph()
        try:
            graph.close()
        finally:
            _remove_db(db)

    def test_thread_safety_shared_graph(self):
        graph, db = _make_graph()
        try:
            def worker(idx: int) -> str:
                return graph.add_node("person", f"User{idx}")

            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [pool.submit(worker, i) for i in range(50)]
                ids = [f.result() for f in as_completed(futures)]
            assert len(set(ids)) == 50
            assert graph.get_stats()["nodes"] == 50
        finally:
            graph.close()
            _remove_db(db)


# ---------------------------------------------------------------------------
# edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_graph_stats_zero(self):
        graph, db = _make_graph()
        try:
            stats = graph.get_stats()
            assert stats == {"nodes": 0, "edges": 0, "by_type": {}}
        finally:
            graph.close()
            _remove_db(db)

    def test_query_neighbors_nonexistent_node(self):
        graph, db = _make_graph()
        try:
            assert graph.query_neighbors("missing") == []
        finally:
            graph.close()
            _remove_db(db)

    def test_query_path_nonexistent_nodes(self):
        graph, db = _make_graph()
        try:
            assert graph.query_path("a", "b") is None
        finally:
            graph.close()
            _remove_db(db)

    def test_get_all_nodes_empty(self):
        graph, db = _make_graph()
        try:
            assert graph.get_all_nodes() == []
        finally:
            graph.close()
            _remove_db(db)

    def test_search_nodes_no_match(self):
        graph, db = _make_graph()
        try:
            graph.add_node("person", "Alice")
            assert graph.search_nodes("not-in-db") == []
        finally:
            graph.close()
            _remove_db(db)
