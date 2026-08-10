"""Real-chromadb test for MemoryStore's dimension-mismatch recovery.

Deliberately does NOT stub chromadb (unlike test_memory_store.py) -- this
needs a real PersistentClient + real collection to exercise get/delete/
re-add against actual chromadb behavior, not a mock. Uses pytest's tmp_path
for isolation (no file copying involved, unlike the ad-hoc reproduction
during development which hit an unrelated cp-consistency issue on this
filesystem).
"""

import pytest

chromadb = pytest.importorskip("chromadb")

from charlie.memory_store import _COLLECTION_NAME, MemoryStore  # noqa: E402


class _FixedDimEmbeddingFunction:
    """Deterministic fake embedding function of a fixed dimension."""

    def __init__(self, dim: int):
        self.dim = dim

    def name(self) -> str:
        return f"fixed-dim-{self.dim}"

    def __call__(self, input):
        return [[0.1] * self.dim for _ in input]

    def embed_query(self, input):
        return [[0.1] * self.dim for _ in input]

    def embed_documents(self, input):
        return [[0.1] * self.dim for _ in input]


class _FakeConfig:
    memory_db_path = ""
    memory_relevance_threshold = 0.5
    memory_auto_extract = True
    llm_url = ""
    llm_model = ""
    llm_key = "no-key"


def _bare_store(db_path: str) -> MemoryStore:
    """A MemoryStore with __init__'s side effects skipped, for calling
    _reembed_mismatched_collection directly against a controlled client."""
    store = MemoryStore.__new__(MemoryStore)
    store.config = _FakeConfig()
    store.db_path = db_path
    return store


def test_reembed_preserves_documents_under_new_dimension(tmp_path):
    db_path = str(tmp_path / "memdb")
    client = chromadb.PersistentClient(path=db_path)

    old_ef = _FixedDimEmbeddingFunction(dim=8)
    old_collection = client.get_or_create_collection(
        name=_COLLECTION_NAME, embedding_function=old_ef, metadata={"hnsw:space": "cosine"}
    )
    old_collection.add(
        ids=["a", "b"],
        documents=["The user likes dark mode", "The user's timezone is IST"],
        metadatas=[{"source": "test"}, {"source": "test"}],
    )
    assert old_collection.count() == 2

    store = _bare_store(db_path)
    store._ef = _FixedDimEmbeddingFunction(dim=4)

    fresh = store._reembed_mismatched_collection(client, active_dim=4)

    assert fresh is not None
    assert fresh.count() == 2
    got = fresh.get(include=["documents", "metadatas"])
    assert set(got["documents"]) == {"The user likes dark mode", "The user's timezone is IST"}
    assert set(got["ids"]) == {"a", "b"}


def test_reembed_is_noop_when_dimensions_already_match(tmp_path):
    db_path = str(tmp_path / "memdb")
    client = chromadb.PersistentClient(path=db_path)

    ef = _FixedDimEmbeddingFunction(dim=4)
    collection = client.get_or_create_collection(
        name=_COLLECTION_NAME, embedding_function=ef, metadata={"hnsw:space": "cosine"}
    )
    collection.add(ids=["a"], documents=["hello"], metadatas=[{"source": "test"}])

    store = _bare_store(db_path)
    store._ef = ef

    result = store._reembed_mismatched_collection(client, active_dim=4)

    assert result is None
    assert client.get_collection(name=_COLLECTION_NAME).count() == 1


def test_reembed_is_noop_when_no_existing_collection(tmp_path):
    db_path = str(tmp_path / "memdb")
    client = chromadb.PersistentClient(path=db_path)

    store = _bare_store(db_path)
    store._ef = _FixedDimEmbeddingFunction(dim=4)

    result = store._reembed_mismatched_collection(client, active_dim=4)

    assert result is None
