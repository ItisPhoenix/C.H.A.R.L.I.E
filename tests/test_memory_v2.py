"""Tests for charlie.memory_v2 - Four-layer evolving memory system."""

import time

import pytest

from charlie.memory_v2 import (
    MemoryLayer,
    MemoryV2,
    _compute_relevance,
    _now_iso,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    """Provide a temporary database path."""
    return str(tmp_path / "test_memory_v2.db")


@pytest.fixture
def mem(tmp_db):
    """Provide a MemoryV2 instance with a temp database."""
    m = MemoryV2(db_path=tmp_db)
    yield m
    m.close()


# ---------------------------------------------------------------------------
# Utility tests
# ---------------------------------------------------------------------------

class TestUtility:
    def test_now_iso_format(self):
        ts = _now_iso()
        assert ts.endswith("Z")
        assert "T" in ts
        # Should parse without error
        from datetime import datetime
        datetime.fromisoformat(ts.replace("Z", "+00:00"))

    def test_compute_relevance_high_importance_recent(self):
        now = time.time()
        score = _compute_relevance(1.0, 10, _now_iso(), now)
        assert score > 0.7

    def test_compute_relevance_low_importance_old(self):
        old_ts = "2020-01-01T00:00:00.000000Z"
        now = time.time()
        score = _compute_relevance(0.1, 0, old_ts, now)
        assert score < 0.2

    def test_compute_relevance_unparseable_date(self):
        score = _compute_relevance(0.5, 1, "not-a-date", time.time())
        # Should still produce a valid score (treats as very old)
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# Store tests
# ---------------------------------------------------------------------------

class TestStore:
    def test_store_returns_id(self, mem):
        entry_id = mem.store(MemoryLayer.EPISODIC, "Test content")
        assert entry_id.startswith("mem_")

    def test_store_with_custom_id(self, mem):
        entry_id = mem.store(MemoryLayer.SEMANTIC, "Fact", entry_id="custom_123")
        assert entry_id == "custom_123"

    def test_store_episodic(self, mem):
        eid = mem.store_episodic(
            "User asked about weather",
            importance=0.7,
            tags=["weather", "question"],
            source_session="sess_1",
            participants=["user", "charlie"],
        )
        entry = mem.get_entry(eid)
        assert entry is not None
        assert entry.layer == "episodic"
        assert entry.importance == 0.7
        assert "weather" in entry.tags

    def test_store_semantic(self, mem):
        eid = mem.store_semantic(
            "User prefers dark mode",
            importance=0.8,
            entity_type="preference",
        )
        entry = mem.get_entry(eid)
        assert entry is not None
        assert entry.layer == "semantic"
        assert entry.importance == 0.8

    def test_store_procedural(self, mem):
        eid = mem.store_procedural(
            "Run ruff check before commit",
            workflow_name="pre_commit",
        )
        entry = mem.get_entry(eid)
        assert entry is not None
        assert entry.layer == "procedural"

    def test_store_meta(self, mem):
        eid = mem.store_meta("System health check passed")
        entry = mem.get_entry(eid)
        assert entry is not None
        assert entry.layer == "meta"

    def test_store_default_importance(self, mem):
        eid = mem.store(MemoryLayer.EPISODIC, "Default importance")
        entry = mem.get_entry(eid)
        assert entry.importance == 0.5


# ---------------------------------------------------------------------------
# Retrieve tests
# ---------------------------------------------------------------------------

class TestRetrieve:
    def test_retrieve_by_layer(self, mem):
        mem.store(MemoryLayer.EPISODIC, "Ep1")
        mem.store(MemoryLayer.SEMANTIC, "Sem1")
        mem.store(MemoryLayer.PROCEDURAL, "Proc1")

        results = mem.retrieve(layer=MemoryLayer.EPISODIC)
        assert len(results) == 1
        assert results[0].content == "Ep1"

    def test_retrieve_by_tag(self, mem):
        mem.store(MemoryLayer.EPISODIC, "Weather question", tags=["weather"])
        mem.store(MemoryLayer.EPISODIC, "Code question", tags=["code"])

        results = mem.retrieve(tags=["weather"])
        assert len(results) == 1
        assert "Weather" in results[0].content

    def test_retrieve_by_query(self, mem):
        mem.store(MemoryLayer.SEMANTIC, "User name is Alice")
        mem.store(MemoryLayer.SEMANTIC, "User likes pizza")

        results = mem.retrieve(query="Alice")
        assert len(results) == 1
        assert "Alice" in results[0].content

    def test_retrieve_limit(self, mem):
        for i in range(20):
            mem.store(MemoryLayer.EPISODIC, f"Entry {i}")

        results = mem.retrieve(limit=5)
        assert len(results) == 5

    def test_retrieve_increments_access_count(self, mem):
        eid = mem.store(MemoryLayer.EPISODIC, "Accessed entry")
        entry_before = mem.get_entry(eid)
        assert entry_before.access_count == 0

        mem.retrieve(query="Accessed")
        entry_after = mem.get_entry(eid)
        assert entry_after.access_count >= 1

    def test_retrieve_empty(self, mem):
        results = mem.retrieve(query="nonexistent")
        assert results == []

    def test_get_entry_nonexistent(self, mem):
        assert mem.get_entry("nonexistent_id") is None


# ---------------------------------------------------------------------------
# Update / Delete tests
# ---------------------------------------------------------------------------

class TestUpdateDelete:
    def test_update_importance(self, mem):
        eid = mem.store(MemoryLayer.SEMANTIC, "Update me")
        assert mem.update_importance(eid, 0.9)
        entry = mem.get_entry(eid)
        assert entry.importance == 0.9

    def test_update_importance_nonexistent(self, mem):
        assert not mem.update_importance("fake_id", 0.9)

    def test_update_tags(self, mem):
        eid = mem.store(MemoryLayer.EPISODIC, "Tagged", tags=["old"])
        assert mem.update_tags(eid, ["new", "fresh"])
        entry = mem.get_entry(eid)
        assert "new" in entry.tags
        assert "fresh" in entry.tags

    def test_delete_entry(self, mem):
        eid = mem.store(MemoryLayer.EPISODIC, "Delete me")
        assert mem.delete(eid)
        assert mem.get_entry(eid) is None

    def test_delete_nonexistent(self, mem):
        assert not mem.delete("fake_id")

    def test_delete_by_layer(self, mem):
        mem.store(MemoryLayer.EPISODIC, "E1")
        mem.store(MemoryLayer.EPISODIC, "E2")
        mem.store(MemoryLayer.SEMANTIC, "S1")

        deleted = mem.delete_by_layer(MemoryLayer.EPISODIC)
        assert deleted == 2
        assert mem.count(MemoryLayer.EPISODIC) == 0
        assert mem.count(MemoryLayer.SEMANTIC) == 1


# ---------------------------------------------------------------------------
# Consolidation tests
# ---------------------------------------------------------------------------

class TestConsolidation:
    def test_consolidate_removes_low_relevance(self, mem):
        # Store many entries with varying importance
        for i in range(50):
            mem.store(
                MemoryLayer.EPISODIC,
                f"Entry {i}",
                importance=0.1 + (i * 0.01),
            )

        # Consolidate to keep only 10
        removed = mem.consolidate(MemoryLayer.EPISODIC, max_keep=10)
        assert removed == 40
        assert mem.count(MemoryLayer.EPISODIC) == 10

    def test_consolidate_noop_when_under_limit(self, mem):
        mem.store(MemoryLayer.SEMANTIC, "Only one")
        removed = mem.consolidate(MemoryLayer.SEMANTIC, max_keep=100)
        assert removed == 0

    def test_consolidate_all(self, mem):
        for _ in range(5):
            for layer in MemoryLayer:
                mem.store(layer, f"Test {layer.value}")

        mem.consolidate_all(max_per_layer=2)
        for layer in MemoryLayer:
            assert mem.count(layer) <= 2


# ---------------------------------------------------------------------------
# Decay tests
# ---------------------------------------------------------------------------

class TestDecay:
    def test_decay_removes_old_entries(self, mem):
        # Store an entry and manually backdate it
        eid = mem.store(MemoryLayer.EPISODIC, "Old entry", importance=0.1)
        conn = mem._get_conn()
        conn.execute(
            "UPDATE memories SET last_accessed = '2020-01-01T00:00:00.000000Z', access_count = 0 WHERE id = ?",
            (eid,),
        )
        conn.commit()

        # Store a fresh entry
        mem.store(MemoryLayer.EPISODIC, "Fresh entry", importance=0.9)

        removed = mem.decay(threshold=0.3)
        assert removed >= 1
        # Fresh entry should still exist
        assert mem.get_entry(eid) is None


# ---------------------------------------------------------------------------
# Stats tests
# ---------------------------------------------------------------------------

class TestStats:
    def test_stats_empty(self, mem):
        stats = mem.stats()
        assert stats["total"] == 0
        for layer in MemoryLayer:
            assert stats[layer.value]["count"] == 0

    def test_stats_with_entries(self, mem):
        mem.store(MemoryLayer.EPISODIC, "E1", importance=0.8)
        mem.store(MemoryLayer.SEMANTIC, "S1", importance=0.6)

        stats = mem.stats()
        assert stats["total"] == 2
        assert stats["episodic"]["count"] == 1
        assert stats["semantic"]["count"] == 1

    def test_count(self, mem):
        assert mem.count() == 0
        mem.store(MemoryLayer.EPISODIC, "E1")
        mem.store(MemoryLayer.SEMANTIC, "S1")
        assert mem.count() == 2
        assert mem.count(MemoryLayer.EPISODIC) == 1


# ---------------------------------------------------------------------------
# Context building tests
# ---------------------------------------------------------------------------

class TestContextBuilding:
    def test_build_context(self, mem):
        mem.store(MemoryLayer.SEMANTIC, "User prefers dark mode")
        mem.store(MemoryLayer.PROCEDURAL, "Always run ruff before commit")

        ctx = mem.build_context(max_chars=500)
        assert "dark mode" in ctx
        assert "ruff" in ctx

    def test_build_context_respects_limit(self, mem):
        long_content = "x" * 200
        for i in range(20):
            mem.store(MemoryLayer.EPISODIC, f"{long_content} {i}")

        ctx = mem.build_context(max_chars=300)
        assert len(ctx) <= 350  # some overhead for layer tags

    def test_build_context_layer_filter(self, mem):
        mem.store(MemoryLayer.EPISODIC, "Episodic only")
        mem.store(MemoryLayer.SEMANTIC, "Semantic only")

        ctx = mem.build_context(layer=MemoryLayer.EPISODIC)
        assert "Episodic" in ctx
        assert "Semantic" not in ctx


# ---------------------------------------------------------------------------
# Export tests
# ---------------------------------------------------------------------------

class TestExport:
    def test_export_layer(self, mem):
        mem.store(MemoryLayer.EPISODIC, "E1")
        mem.store(MemoryLayer.EPISODIC, "E2")
        mem.store(MemoryLayer.SEMANTIC, "S1")

        exported = mem.export_layer(MemoryLayer.EPISODIC)
        assert len(exported) == 2
        assert all(e["layer"] == "episodic" for e in exported)

    def test_get_recent(self, mem):
        for i in range(5):
            mem.store(MemoryLayer.EPISODIC, f"Entry {i}")

        recent = mem.get_recent(limit=3)
        assert len(recent) == 3

    def test_search_by_tag(self, mem):
        mem.store(MemoryLayer.EPISODIC, "Weather Q", tags=["weather", "question"])
        mem.store(MemoryLayer.EPISODIC, "Code Q", tags=["code", "question"])

        results = mem.search_by_tag("weather")
        assert len(results) == 1
        assert "Weather" in results[0].content


# ---------------------------------------------------------------------------
# Thread safety tests
# ---------------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_stores(self, mem):
        import concurrent.futures

        def store_one(i):
            mem.store(MemoryLayer.EPISODIC, f"Concurrent {i}")
            return True

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(store_one, range(20)))

        assert all(results)
        assert mem.count(MemoryLayer.EPISODIC) == 20


# ---------------------------------------------------------------------------
# Context manager tests
# ---------------------------------------------------------------------------

class TestContextManager:
    def test_context_manager(self, tmp_db):
        with MemoryV2(db_path=tmp_db) as m:
            m.store(MemoryLayer.EPISODIC, "In context manager")
            assert m.count() == 1
        # After exit, connection should be closed
        assert getattr(m._local, "conn", None) is None
