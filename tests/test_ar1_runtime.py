"""AR1 lane, routing, and task-acknowledgement regressions."""

from types import SimpleNamespace

import pytest

import main
from charlie.task_journal import TaskOrigin
from charlie.turn_contracts import TurnRequest


def test_only_explicit_research_is_sustained():
    config = SimpleNamespace(research_enabled=True, research_default_mode="auto")

    assert main._is_sustained_research_request("What's the latest Python release?", config) is False
    assert main._is_sustained_research_request("Deep research Windows security changes", config) is True


@pytest.mark.asyncio
async def test_sustained_research_acknowledges_task_without_foreground_generation(monkeypatch):
    request = TurnRequest.allocate("Deep research Windows security changes", "session-ar1", "voice")
    captured = {}

    class Bus:
        def __init__(self):
            self.events = []

        async def emit(self, event_type, payload, meta=None):
            self.events.append((event_type, payload, meta))

    class Store:
        def __init__(self):
            self.messages = []

        def append(self, *args, **kwargs):
            self.messages.append((args, kwargs))

        def touch_session(self, session_id):
            captured["touched"] = session_id

    class Voice:
        def __init__(self):
            self.spoken = []

        def speak(self, text, emotion):
            self.spoken.append((text, emotion))

    async def fake_start(config, event_bus, text, **kwargs):
        captured.update(text=text, kwargs=kwargs)
        return SimpleNamespace(id="research-task", status="running")

    monkeypatch.setattr(main.background_task, "start", fake_start)
    bus = Bus()
    store = Store()
    voice = Voice()
    task = await main._start_sustained_research_task(
        request,
        runtime_config=SimpleNamespace(),
        event_bus=bus,
        voice=voice,
        store=store,
        memory_store=None,
        memory_graph=None,
        memory_service=None,
    )

    assert task.id == "research-task"
    assert captured["touched"] == "session-ar1"
    assert captured["kwargs"]["origin"] is TaskOrigin.RESEARCH
    assert captured["kwargs"]["session_id"] == "session-ar1"
    assert captured["kwargs"]["turn_id"] == request.turn_id
    assert captured["kwargs"]["research_query"] == request.input
    assert [event_type for event_type, _payload, _meta in bus.events] == ["transcript", "token", "response_done"]
    assert voice.spoken
