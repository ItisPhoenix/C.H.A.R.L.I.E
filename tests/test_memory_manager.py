import os
import pytest
from charlie.memory_manager import MemoryManager

DB = "test_charlie_memory.db"


def setup_module():
    if os.path.exists(DB):
        os.remove(DB)


@pytest.fixture
def mgr():
    if os.path.exists(DB):
        os.remove(DB)
    m = MemoryManager(DB)
    yield m
    m._conn and m._conn.close()
    if os.path.exists(DB):
        os.remove(DB)


class TestMemoryManager:

    def test_store_and_search(self, mgr: MemoryManager):
        mgr.store("User prefers dark mode", "fact", "preferences")
        mgr.store("User birthday is June 14th", "fact", "family")
        results = mgr.search("dark mode preference")
        assert len(results) >= 1
        assert "dark mode" in results[0]["content"].lower()

    def test_core_facts(self, mgr: MemoryManager):
        mgr.store("User prefers dark mode", "fact", "preferences")
        facts = mgr.get_core_facts(limit=10)
        assert any("dark mode" in f.lower() for f in facts)

    def test_store_returns_id(self, mgr: MemoryManager):
        rid = mgr.store("test fact", "fact", "general")
        assert isinstance(rid, int) and rid > 0

    def test_search_empty_query(self, mgr: MemoryManager):
        results = mgr.search("")
        assert results == []

    def test_search_short_words(self, mgr: MemoryManager):
        """Words shorter than 3 chars are filtered out by _extract_keywords."""
        mgr.store("User likes tea", "fact", "preferences")
        results = mgr.search("a an of")
        assert results == []

    def test_search_by_type(self, mgr: MemoryManager):
        mgr.store("fact about work", "fact", "work")
        mgr.store("summary about work", "conversation_summary", "consolidated")
        results = mgr.search("work", type="fact")
        assert all(r["type"] == "fact" for r in results)

    def test_search_result_structure(self, mgr: MemoryManager):
        mgr.store("User lives in Berlin", "fact", "location")
        results = mgr.search("Berlin")
        assert len(results) >= 1
        r = results[0]
        assert "id" in r and "type" in r and "category" in r and "content" in r

    def test_delete_by_content_exact(self, mgr: MemoryManager):
        mgr.store("Exact match fact", "fact")
        deleted = mgr.delete_by_content("Exact match fact")
        assert deleted is True
        assert mgr.get_core_facts() == []

    def test_delete_by_content_no_match(self, mgr: MemoryManager):
        deleted = mgr.delete_by_content("Nonexistent")
        assert deleted is False

    def test_get_core_facts_limit(self, mgr: MemoryManager):
        for i in range(5):
            mgr.store(f"fact {i}", "fact")
        facts = mgr.get_core_facts(limit=3)
        assert len(facts) == 3

    def test_keywords_extracted_automatically(self, mgr: MemoryManager):
        rid = mgr.store("User loves programming in Python", "fact", "preferences")
        conn = mgr._get_conn()
        kws = conn.execute(
            "SELECT keyword FROM memory_keywords WHERE memory_id = ?", (rid,)
        ).fetchall()
        kw_set = {r["keyword"] for r in kws}
        assert "python" in kw_set
        assert "loves" in kw_set

    def test_consolidate_stub(self, mgr: MemoryManager):
        result = mgr.consolidate_old_summaries()
        assert result == "consolidate_old_summaries not yet implemented"

