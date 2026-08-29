"""Regression coverage for main-process long-term-memory composition."""

from __future__ import annotations

import pytest

import charlie.memory_graph as memory_graph_module
import main
from charlie import background_task
from charlie.config import Config
from charlie.core import Brain
from charlie.memory_graph import MemoryGraph
from charlie.memory_service import MemoryService
from charlie.task_journal import TaskJournal
from charlie.tasks import TaskManager


class _TrackingGraph:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


def _config(tmp_path) -> Config:
    return Config(
        llm_url="https://example.com/v1",
        llm_key="test-key",
        llm_model="dummy",
        memory_graph_db=str(tmp_path / "memory-graph.db"),
        world_model_db_path=str(tmp_path / "world-model.db"),
        session_db_path=str(tmp_path / "sessions.db"),
        background_iteration_budget_max=1,
    )


def test_importing_memory_service_does_not_create_global_service_instance():
    import charlie.memory_service as memory_service_module

    assert not hasattr(memory_service_module, "memory_service")


@pytest.mark.asyncio
async def test_service_only_brain_injection_rejects_before_graph_construction(monkeypatch, tmp_path):
    constructor_calls = []

    def unexpected_graph(db_path):
        constructor_calls.append(db_path)
        raise AssertionError("MemoryGraph must not be constructed for service-only injection")

    monkeypatch.setattr("charlie.memory_graph.MemoryGraph", unexpected_graph)

    with pytest.raises(ValueError, match="memory_graph is required when memory_service is provided"):
        Brain(
            _config(tmp_path),
            memory_service=object(),
            register_panic_hotkey=False,
        )

    assert constructor_calls == []


@pytest.mark.asyncio
async def test_main_composition_constructs_one_graph_and_one_service(monkeypatch, tmp_path):
    graph_instances = []
    service_instances = []

    class TrackingMemoryGraph(MemoryGraph):
        def __init__(self, db_path: str) -> None:
            graph_instances.append(self)
            super().__init__(db_path)

    class TrackingMemoryService(MemoryService):
        def __init__(self, graph=None, memory_store=None) -> None:
            service_instances.append(self)
            super().__init__(graph=graph, memory_store=memory_store)

    sentinel_store = object()
    monkeypatch.setattr(main, "MemoryGraph", TrackingMemoryGraph)
    monkeypatch.setattr(main, "MemoryStore", lambda config: sentinel_store)
    monkeypatch.setattr(main, "MemoryService", TrackingMemoryService)
    monkeypatch.setattr(main, "_set_subsystem_health", lambda *args, **kwargs: None)

    graph, memory_store, service = main._compose_memory_dependencies(_config(tmp_path))

    try:
        assert len(graph_instances) == 1
        assert len(service_instances) == 1
        assert graph is graph_instances[0]
        assert memory_store is sentinel_store
        assert service is service_instances[0]
        assert service._graph is graph
        assert service._memory_store is memory_store

        foreground_brain = Brain(
            _config(tmp_path),
            memory_store=memory_store,
            memory_graph=graph,
            memory_service=service,
            register_panic_hotkey=False,
        )
        try:
            assert foreground_brain.memory_store is memory_store
            assert foreground_brain.memory_graph is graph
            assert foreground_brain.memory_service is service
            assert foreground_brain._owns_memory_graph is False
        finally:
            await foreground_brain.close()
    finally:
        graph.close()


def test_main_composition_keeps_graph_when_vector_store_is_unavailable(monkeypatch, tmp_path):
    graph = _TrackingGraph()
    health = []

    class UnavailableMemoryStore:
        def __init__(self, config) -> None:
            raise RuntimeError("embedding backend unavailable")

    monkeypatch.setattr(main, "MemoryGraph", lambda db_path: graph)
    monkeypatch.setattr(main, "MemoryStore", UnavailableMemoryStore)
    monkeypatch.setattr(main, "_set_subsystem_health", lambda name, status: health.append((name, status)))

    composed_graph, memory_store, service = main._compose_memory_dependencies(_config(tmp_path))

    assert composed_graph is graph
    assert memory_store is None
    assert service._graph is graph
    assert service._memory_store is None
    assert health == [("memory", main.HealthStatus.DEGRADED)]
    assert graph.close_calls == 0
    graph.close()


def test_main_composition_does_not_fallback_when_graph_construction_fails(monkeypatch, tmp_path):
    store_calls = []

    def fail_graph(db_path):
        raise RuntimeError("graph unavailable")

    def unexpected_store(config):
        store_calls.append(config)
        raise AssertionError("vector store must not replace graph construction")

    monkeypatch.setattr(main, "MemoryGraph", fail_graph)
    monkeypatch.setattr(main, "MemoryStore", unexpected_store)

    with pytest.raises(RuntimeError, match="graph unavailable"):
        main._compose_memory_dependencies(_config(tmp_path))

    assert store_calls == []


@pytest.mark.asyncio
async def test_injected_brain_memory_dependencies_keep_exact_identity_and_graph_open(tmp_path):
    graph = _TrackingGraph()
    store = object()
    service = object()
    brain = Brain(
        _config(tmp_path),
        memory_store=store,
        memory_graph=graph,
        memory_service=service,
        register_panic_hotkey=False,
    )

    assert brain.memory_store is store
    assert brain.memory_graph is graph
    assert brain.memory_service is service
    assert brain._owns_memory_graph is False

    await brain.close()

    assert graph.close_calls == 0


@pytest.mark.asyncio
async def test_compatibility_brain_owns_and_closes_self_created_graph(monkeypatch, tmp_path):
    graph = _TrackingGraph()

    monkeypatch.setattr("charlie.memory_graph.MemoryGraph", lambda db_path: graph)
    brain = Brain(_config(tmp_path), register_panic_hotkey=False)

    assert brain.memory_graph is graph
    assert brain._owns_memory_graph is True
    assert brain.memory_service._graph is graph

    await brain.close()

    assert graph.close_calls == 1


@pytest.mark.asyncio
async def test_background_start_preserves_process_memory_identity(monkeypatch, tmp_path):
    monkeypatch.setattr(background_task, "_current_task", None)
    monkeypatch.setattr(background_task, "_active_event_bus", None)
    monkeypatch.setattr(
        background_task,
        "_manager",
        TaskManager(
            max_parallel=1,
            on_status_change=background_task._on_manager_status_change,
        ),
    )
    monkeypatch.setattr(
        background_task,
        "_journal",
        TaskJournal(state_path=tmp_path / "background-memory-journal.json"),
    )
    monkeypatch.setattr(background_task._manager, "submit", lambda task, runner: None)

    async def fake_chat_stream(self, user_input, **kwargs):
        if "Break the following task" in user_input:
            yield "1. Check memory identity\n"
        else:
            yield ""

    monkeypatch.setattr(Brain, "chat_stream", fake_chat_stream)

    class EventBus:
        async def emit(self, *args, **kwargs):
            return None

    config = _config(tmp_path)
    graph = _TrackingGraph()
    store = object()
    constructor_calls = []

    def create_graph(db_path):
        constructor_calls.append(db_path)
        return graph

    monkeypatch.setattr(main, "MemoryGraph", create_graph)
    monkeypatch.setattr(memory_graph_module, "MemoryGraph", create_graph)
    monkeypatch.setattr(main, "MemoryStore", lambda config: store)
    monkeypatch.setattr(main, "_set_subsystem_health", lambda *args, **kwargs: None)
    composed_graph, composed_store, service = main._compose_memory_dependencies(config)

    task = await background_task.start(
        config,
        EventBus(),
        "verify shared memory",
        memory_store=composed_store,
        memory_graph=composed_graph,
        memory_service=service,
    )

    try:
        assert constructor_calls == [config.memory_graph_db]
        assert task.brain.memory_store is composed_store
        assert task.brain.memory_graph is composed_graph
        assert task.brain.memory_service is service
        assert task.brain._owns_memory_graph is False
    finally:
        await task.brain.close()

    assert graph.close_calls == 0


def test_main_wires_tool_registry_from_process_composition(monkeypatch):
    import charlie.tools as tools_module

    calls = []
    monkeypatch.setattr(tools_module.registry, "set_memory_service", lambda value: calls.append(value))

    service = object()
    main._wire_memory_service(service)

    assert calls == [service]
