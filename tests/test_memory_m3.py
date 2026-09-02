"""Focused regression coverage for MemoryService operational authority."""

from __future__ import annotations

import asyncio
import logging
import threading

import pytest

import charlie.memory_service as memory_service_module
import charlie.tools as tools_module
from charlie.config import Config
from charlie.core import Brain
from charlie.memory_graph import MemoryGraph
from charlie.memory_service import MemoryService
from charlie.tools import graph_add_fact, graph_consolidate, graph_query, vector_memory


class _GraphSpy:
    def __init__(self, search_result=None, consolidate_result=0):
        self.add_fact_calls = []
        self.search_calls = []
        self.consolidate_calls = 0
        self.search_result = [] if search_result is None else search_result
        self.consolidate_result = consolidate_result
        self.close_calls = 0

    def add_fact(self, subject, predicate, obj):
        self.add_fact_calls.append((subject, predicate, obj))
        return "edge-1"

    def add_node(self, *args, **kwargs):
        raise AssertionError("relational add_fact must not create managed graph nodes")

    def search_facts(self, query, subject_filter=None, limit=20):
        self.search_calls.append((query, subject_filter, limit))
        return self.search_result

    def consolidate(self):
        self.consolidate_calls += 1
        return self.consolidate_result

    def close(self):
        self.close_calls += 1


class _ExplodingRawStore:
    is_available = True

    def add_memory(self, *args, **kwargs):
        raise AssertionError("Brain must use MemoryService for semantic writes")

    def search(self, *args, **kwargs):
        raise AssertionError("Brain must use MemoryService for semantic reads")

    def format_for_prompt(self, *args, **kwargs):
        raise AssertionError("Brain must use MemoryService for semantic formatting")


class _ExplodingRawGraph:
    def add_fact(self, *args, **kwargs):
        raise AssertionError("Brain must use MemoryService for relational writes")

    def consolidate(self, *args, **kwargs):
        raise AssertionError("Brain must use MemoryService for graph consolidation")

    def close(self):
        return None


class _MemoryServiceSpy:
    def __init__(self, semantic_results=None, add_fact_result="edge-from-service"):
        self.semantic_results = semantic_results
        self.add_fact_result = add_fact_result
        self.semantic_available_calls = 0
        self.search_calls = []
        self.format_calls = []
        self.remember_calls = []
        self.add_fact_calls = []
        self.search_fact_calls = []
        self.consolidate_calls = 0
        self.remember_done = threading.Event()

    def semantic_available(self):
        self.semantic_available_calls += 1
        return True

    def search_semantic(self, query, n_results=3):
        self.search_calls.append((query, n_results))
        return self.semantic_results

    def format_semantic_for_prompt(self, results):
        self.format_calls.append(results)
        return "[memory context]" if results else ""

    def remember_semantic(self, text, source, session_id, auto_extract=True):
        self.remember_calls.append((text, source, session_id, auto_extract))
        self.remember_done.set()
        return 1

    def add_fact(self, subject, predicate, obj):
        self.add_fact_calls.append((subject, predicate, obj))
        return self.add_fact_result

    def search_facts(self, query, subject_filter=None, limit=20):
        self.search_fact_calls.append((query, subject_filter, limit))
        return []

    def consolidate_graph(self):
        self.consolidate_calls += 1
        return 0


def _config(tmp_path) -> Config:
    return Config(
        llm_url="http://localhost:11434",
        llm_key="no-key",
        llm_model="dummy",
        memory_graph_db=str(tmp_path / "memory-graph.db"),
        world_model_db_path=str(tmp_path / "world-model.db"),
        session_db_path=str(tmp_path / "sessions.db"),
        memory_file=str(tmp_path / "MEMORY.md"),
        user_file=str(tmp_path / "USER.md"),
        opinions_file=str(tmp_path / "OPINIONS.md"),
    )


def _install_tool_service(monkeypatch, service):
    monkeypatch.setattr(tools_module, "_memory_service", service)


class _SemanticWriteSpy:
    is_available = True

    def __init__(self):
        self.add_calls = []

    def add_memory(self, *args, **kwargs):
        self.add_calls.append((args, kwargs))
        raise AssertionError("relational add_fact must not ingest semantic memory")


def test_relational_facade_add_fact_persists_graph_only_without_managed_item(tmp_path):
    graph = MemoryGraph(str(tmp_path / "memory-graph.db"))
    store = _SemanticWriteSpy()
    service = MemoryService(graph=graph, memory_store=store)

    try:
        edge_id = service.add_fact("user", "prefers", "dark mode")
        nodes = graph.get_all_nodes(limit=20)
        stats = graph.get_stats()

        assert isinstance(edge_id, str) and edge_id
        assert graph.get_all_facts() == [("user", "prefers", "dark mode")]
        assert stats["nodes"] == 2
        assert stats["edges"] == 1
        assert stats["by_type"] == {"fact": 2}
        assert [node["id"] for node in nodes if node["id"].startswith("mem_")] == []
        assert store.add_calls == []
    finally:
        graph.close()


def test_relational_facade_preserves_search_none_empty_and_tuple_shape():
    unavailable = MemoryService(graph=None)
    unavailable._get_graph = lambda: None
    assert unavailable.search_facts("anything") is None

    graph = _GraphSpy(search_result=[])
    assert MemoryService(graph=graph).search_facts("anything") == []

    facts = [("user", "prefers", "dark mode", 1.0)]
    graph = _GraphSpy(search_result=facts)
    service = MemoryService(graph=graph)
    assert service.search_facts("dark", subject_filter="user", limit=7) == facts
    assert graph.search_calls == [("dark", "user", 7)]


def test_relational_facade_preserves_consolidation_zero_and_unavailable():
    graph = _GraphSpy(consolidate_result=0)
    assert MemoryService(graph=graph).consolidate_graph() == 0
    assert graph.consolidate_calls == 1

    unavailable = MemoryService(graph=None)
    unavailable._get_graph = lambda: None
    assert unavailable.consolidate_graph() is None


@pytest.mark.asyncio
async def test_brain_semantic_reads_and_formatting_use_injected_service(tmp_path, monkeypatch):
    service = _MemoryServiceSpy(semantic_results=[{"text": "dark mode"}])
    brain = Brain(
        _config(tmp_path),
        memory_store=_ExplodingRawStore(),
        memory_graph=_ExplodingRawGraph(),
        memory_service=service,
        register_panic_hotkey=False,
    )

    async def fake_stream_completion(payload, generation):
        return "assistant answer", []

    async def fake_thread_update(*args, **kwargs):
        return None

    monkeypatch.setattr(brain, "_stream_completion", fake_stream_completion)
    monkeypatch.setattr(brain, "_extract_thread_update", fake_thread_update)
    monkeypatch.setattr(brain, "_save_to_memory", lambda *args: None)

    chunks = [
        chunk
        async for chunk in brain.chat_stream(
            "Tell me about my preference for dark mode",
            skip_pre_search=True,
            skip_tools=True,
        )
    ]

    assert chunks == ["assistant answer"]
    assert service.search_calls == [("Tell me about my preference for dark mode", 3)]
    assert service.format_calls == [[{"text": "dark mode"}]]
    assert service.semantic_available_calls == 1
    await brain.close()


@pytest.mark.parametrize("semantic_results", [None, []], ids=["search-failure", "no-matches"])
@pytest.mark.asyncio
async def test_brain_semantic_neutral_results_do_not_prepend_memory(
    tmp_path, monkeypatch, semantic_results
):
    query = "Tell me about my preference for dark mode"
    service = _MemoryServiceSpy(semantic_results=semantic_results)
    brain = Brain(
        _config(tmp_path),
        memory_store=_ExplodingRawStore(),
        memory_graph=_ExplodingRawGraph(),
        memory_service=service,
        register_panic_hotkey=False,
    )
    captured_payloads = []

    async def fake_stream_completion(payload, generation):
        captured_payloads.append(payload)
        return "assistant answer", []

    async def fake_thread_update(*args, **kwargs):
        return None

    monkeypatch.setattr(brain, "_stream_completion", fake_stream_completion)
    monkeypatch.setattr(brain, "_extract_thread_update", fake_thread_update)
    monkeypatch.setattr(brain, "_save_to_memory", lambda *args: None)

    chunks = [
        chunk
        async for chunk in brain.chat_stream(
            query,
            skip_pre_search=True,
            skip_tools=True,
        )
    ]

    assert chunks == ["assistant answer"]
    assert service.search_calls == [(query, 3)]
    assert service.format_calls == [semantic_results]
    assert captured_payloads[0]["messages"][-1]["content"] == query
    await brain.close()


@pytest.mark.asyncio
async def test_brain_semantic_write_uses_service_and_keeps_async_executor(tmp_path):
    service = _MemoryServiceSpy()
    brain = Brain(
        _config(tmp_path),
        memory_store=_ExplodingRawStore(),
        memory_graph=_ExplodingRawGraph(),
        memory_service=service,
        register_panic_hotkey=False,
    )

    brain._save_to_memory("This assistant response is long enough to persist.", "assistant")
    await asyncio.wait_for(asyncio.to_thread(service.remember_done.wait, 1), timeout=2)

    assert service.remember_calls == [
        ("This assistant response is long enough to persist.", "assistant", "auto", True)
    ]
    await brain.close()


@pytest.mark.asyncio
async def test_background_housekeeping_can_be_cancelled_by_new_voice_work(tmp_path):
    brain = Brain(_config(tmp_path), register_panic_hotkey=False)
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_housekeeping():
        started.set()
        await release.wait()

    task = brain.schedule_background_task(slow_housekeeping())
    await asyncio.wait_for(started.wait(), timeout=1)
    brain.cancel_background_tasks()

    with pytest.raises(asyncio.CancelledError):
        await task
    await brain.close()


@pytest.mark.asyncio
async def test_brain_reflection_uses_service_for_graph_mutations(tmp_path, monkeypatch):
    service = _MemoryServiceSpy()
    brain = Brain(
        _config(tmp_path),
        memory_graph=_ExplodingRawGraph(),
        memory_service=service,
        register_panic_hotkey=False,
    )
    brain.history = [
        {"role": "user", "content": "I prefer dark mode."},
        {"role": "assistant", "content": "Noted."},
    ]

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": "user | prefers | dark mode"}}]}

    async def fake_post(*args, **kwargs):
        return Response()

    monkeypatch.setattr(brain.client, "post", fake_post)
    await brain._reflect_and_consolidate()

    assert service.add_fact_calls == [("user", "prefers", "dark mode")]
    assert service.consolidate_calls == 1
    await brain.close()


@pytest.mark.asyncio
async def test_brain_reflection_suppresses_unavailable_fact_success(tmp_path, monkeypatch, caplog):
    service = _MemoryServiceSpy(add_fact_result=None)
    brain = Brain(
        _config(tmp_path),
        memory_graph=_ExplodingRawGraph(),
        memory_service=service,
        register_panic_hotkey=False,
    )
    brain.history = [
        {"role": "user", "content": "I prefer dark mode."},
        {"role": "assistant", "content": "Noted."},
    ]

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": "user | prefers | dark mode"}}]}

    async def fake_post(*args, **kwargs):
        return Response()

    emitted = []

    def record_event(store, summary):
        emitted.append((store, summary))

    monkeypatch.setattr(brain.client, "post", fake_post)
    monkeypatch.setattr(tools_module, "emit_memory_updated", record_event)
    caplog.set_level(logging.INFO, logger="charlie.core")

    await brain._reflect_and_consolidate()

    assert service.add_fact_calls == [("user", "prefers", "dark mode")]
    assert emitted == []
    assert not any("Reflection: added" in record.getMessage() for record in caplog.records)
    await brain.close()


@pytest.mark.asyncio
async def test_compatibility_brain_facade_wraps_injected_graph(tmp_path):
    graph = _GraphSpy()
    brain = Brain(_config(tmp_path), memory_graph=graph, register_panic_hotkey=False)

    assert isinstance(brain.memory_service, MemoryService)
    assert brain.memory_service._graph is graph
    assert brain._owns_memory_graph is False
    await brain.close()
    assert graph.close_calls == 0


@pytest.mark.asyncio
async def test_injected_service_does_not_construct_compatibility_facade(tmp_path, monkeypatch):
    graph = _GraphSpy()
    service = object()

    def unexpected_service(*args, **kwargs):
        raise AssertionError("injected service must be used directly")

    monkeypatch.setattr(memory_service_module, "MemoryService", unexpected_service)
    brain = Brain(
        _config(tmp_path),
        memory_graph=graph,
        memory_service=service,
        register_panic_hotkey=False,
    )

    assert brain.memory_service is service
    assert brain._owns_memory_graph is False
    await brain.close()


def test_tool_memory_operations_use_injected_service_and_preserve_messages(monkeypatch):
    service = _MemoryServiceSpy(semantic_results=None)
    _install_tool_service(monkeypatch, service)

    assert vector_memory("remember", "user likes tea") == "Remembered: user likes tea"
    assert service.remember_calls == [("user likes tea", "user", "explicit", False)]
    assert "failed" in vector_memory("recall", "tea").lower()

    service.semantic_results = []
    assert vector_memory("recall", "tea") == "No relevant memories found."

    service.semantic_results = [{"text": "user likes tea"}]
    assert vector_memory("recall", "tea") == "- user likes tea"

    assert graph_add_fact("user", "likes", "tea") == "Added: user -> likes -> tea"
    assert service.add_fact_calls == [("user", "likes", "tea")]
    assert graph_query("tea") == "No matching facts found."
    assert graph_consolidate() == "Consolidated graph. Removed 0 stale/duplicate facts."


def test_tool_graph_operations_preserve_unavailable_and_result_formatting(monkeypatch):
    service = _MemoryServiceSpy()
    _install_tool_service(monkeypatch, service)

    service.add_fact = lambda *args: None
    assert graph_add_fact("user", "likes", "tea") == "Knowledge graph is not available."

    service.search_facts = lambda *args, **kwargs: None
    assert graph_query("tea") == "Knowledge graph is not available."
    service.search_facts = lambda *args, **kwargs: [("user", "likes", "tea", 0.5)]
    assert graph_query("tea") == "- user -> likes -> tea (relevance: 0.50)"

    service.consolidate_graph = lambda: None
    assert graph_consolidate() == "Knowledge graph is not available."


def test_tool_registry_requires_only_service_pointer(monkeypatch):
    service = _MemoryServiceSpy()
    monkeypatch.setattr(tools_module, "_memory_service", None)
    tools_module.registry.set_memory_service(service)
    assert tools_module._memory_service is service
