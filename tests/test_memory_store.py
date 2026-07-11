"""Unit tests for charlie.memory_store.

Mock chromadb at sys.modules level so its transitive dependencies
(torchvision/grpc) never load on Windows, avoiding access-violation crashes.
"""

import sys
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Pre-emptive module stubs — prevent torchvision/grpc from ever loading.
# ---------------------------------------------------------------------------
def _install_stub(modname: str):
    fake = MagicMock()
    fake.__spec__ = MagicMock()
    fake.__spec__.submodule_search_locations = []
    sys.modules.setdefault(modname, fake)


# ChromaDB and its tree
for mod in [
    "chromadb",
    "chromadb.errors",
    "chromadb.api",
    "chromadb.api.client",
    "chromadb.config",
]:
    _install_stub(mod)

# Sentence-transformers (pulls torch)
for mod in [
    "sentence_transformers",
    "sentence_transformers.SentenceTransformer",
]:
    _install_stub(mod)


# ---------------------------------------------------------------------------
# Config fixture
# ---------------------------------------------------------------------------

class FakeConfig:
    memory_db_path = ":memory:"
    memory_embedding_url = "http://localhost:1234/v1/embeddings"
    memory_embedding_model = "test-model"
    memory_relevance_threshold = 0.5
    memory_auto_extract = True
    small_llm_url = ""
    small_llm_model = ""
    small_llm_key = "no-key"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMemoryStore:
    """Tests over MemoryStore with chromadb and sentence_transformers stubbed."""

    def _make_store(self, monkeypatch):
        """Build a MemoryStore whose _build_embedding_function returns a mock."""
        mock_ef = MagicMock()
        mock_ef.return_value.__call__ = MagicMock()
        mock_ef.embed_documents = MagicMock(return_value=[[0.1] * 384])
        mock_ef.embed_query = MagicMock(return_value=[[0.1] * 384])
        monkeypatch.setattr(
            "charlie.memory_store._build_embedding_function",
            lambda _: mock_ef,
        )
        from charlie.memory_store import MemoryStore
        return MemoryStore(FakeConfig())

    def test_imports_cleanly(self):
        from charlie.memory_store import MemoryStore
        assert MemoryStore is not None

    def test_init_collection_created(self, monkeypatch):
        store = self._make_store(monkeypatch)
        assert store._collection is not None

    def test_is_available_when_collection_ready(self, monkeypatch):
        store = self._make_store(monkeypatch)
        assert store.is_available is True

    def test_is_available_false_when_collection_none(self, monkeypatch):
        # Force collection to stay None by failing _build_embedding_function
        monkeypatch.setattr(
            "charlie.memory_store._build_embedding_function",
            lambda _: None,
        )
        from charlie.memory_store import MemoryStore
        store = MemoryStore(FakeConfig())
        assert store.is_available is False

    def test_add_memory_stores_facts(self, monkeypatch):
        store = self._make_store(monkeypatch)
        store._collection = MagicMock()
        n = store.add_memory("Charlie likes Python.", source="chat", session_id="s1")
        assert n > 0
        store._collection.add.assert_called_once()

    def test_add_memory_empty_text_returns_zero(self, monkeypatch):
        store = self._make_store(monkeypatch)
        n = store.add_memory("   ", source="chat", session_id="s1")
        assert n == 0

    def test_add_memory_when_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            "charlie.memory_store._build_embedding_function",
            lambda _: None,
        )
        from charlie.memory_store import MemoryStore
        store = MemoryStore(FakeConfig())
        assert store.add_memory("test", source="x", session_id="s1") == 0

    def test_search_returns_filtered_results(self, monkeypatch):
        store = self._make_store(monkeypatch)
        store._collection = MagicMock()
        store._collection.query.return_value = {
            "documents": [["fact one", "fact two", "fact three"]],
            "distances": [[0.1, 0.3, 0.6]],
            "metadatas": [
                [
                    {"source": "chat", "timestamp": "now"},
                    {"source": "chat", "timestamp": "now"},
                    {"source": "chat", "timestamp": "now"},
                ]
            ],
        }
        results = store.search("hello", n_results=3)
        assert len(results) == 2  # only below threshold 0.5
        assert results[0]["text"] == "fact one"
        assert results[0]["distance"] == 0.1
        assert results[0]["metadata"] == {"source": "chat", "timestamp": "now"}

    def test_search_empty_query(self, monkeypatch):
        store = self._make_store(monkeypatch)
        assert store.search("") == []

    def test_search_failure_returns_empty(self, monkeypatch):
        store = self._make_store(monkeypatch)
        store._collection = MagicMock()
        store._collection.query.side_effect = RuntimeError("chromadb down")
        assert store.search("test") == []

    def test_search_when_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            "charlie.memory_store._build_embedding_function",
            lambda _: None,
        )
        from charlie.memory_store import MemoryStore
        store = MemoryStore(FakeConfig())
        assert store.search("test") == []

    def test_format_for_prompt_empty(self, monkeypatch):
        from charlie.memory_store import MemoryStore
        assert MemoryStore.format_for_prompt(None, []) == ""

    def test_format_for_prompt_with_results(self, monkeypatch):
        from charlie.memory_store import MemoryStore
        results = [
            {"text": "User likes Python.", "distance": 0.1, "metadata": {}},
            {"text": "User works at home.", "distance": 0.3, "metadata": {}},
        ]
        prompt = MemoryStore.format_for_prompt(None, results)
        assert "Python" in prompt
        assert "home" in prompt
        assert "[Relevant memories" in prompt


class TestMemoryStoreEdgeCases:
    """Edge cases: empty config fields, fact extraction configured, etc."""

    def test_config_missing_fields_default(self):
        """MemoryStore shouldn't crash when config lacks optional attrs."""
        import types
        minimal = types.SimpleNamespace()
        from charlie.memory_store import MemoryStore
        store = MemoryStore.__new__(MemoryStore)
        store.config = minimal
        store.db_path = ":memory:"
        store.relevance_threshold = 0.5
        store.auto_extract = True
        store._collection = None
        assert store.is_available is False

    def test_fact_extraction_disabled(self, monkeypatch):
        """When small_llm_url is empty, _extract_facts returns []."""
        store = self._make_store(monkeypatch)
        store.config.small_llm_url = ""
        assert store._extract_facts("Some long text here.") == []

    def test_search_no_metadata(self, monkeypatch):
        """search handles missing/different metadata gracefully."""
        store = self._make_store(monkeypatch)
        store._collection = MagicMock()
        store._collection.query.return_value = {
            "documents": [["test"]],
            "distances": [[0.2]],
            "metadatas": [[None]],
        }
        results = store.search("test")
        assert len(results) == 1
        assert results[0]["text"] == "test"
        assert results[0]["metadata"] == {}  # None → {}

    def test_add_memory_short_fact_skipped(self, monkeypatch):
        """Facts shorter than 5 chars are filtered out."""
        store = self._make_store(monkeypatch)
        store._collection = MagicMock()
        store.auto_extract = False
        n = store.add_memory("Hi", source="chat", session_id="s1", auto_extract=False)
        assert n == 0  # "Hi" is only 2 chars, skips
        store._collection.add.assert_not_called()

    def _make_store(self, monkeypatch):
        """Build a MemoryStore whose _build_embedding_function returns a mock."""
        mock_ef = MagicMock()
        mock_ef.return_value.__call__ = MagicMock()
        mock_ef.embed_documents = MagicMock(return_value=[[0.1] * 384])
        mock_ef.embed_query = MagicMock(return_value=[[0.1] * 384])
        monkeypatch.setattr(
            "charlie.memory_store._build_embedding_function",
            lambda _: mock_ef,
        )
        from charlie.memory_store import MemoryStore
        return MemoryStore(FakeConfig())
