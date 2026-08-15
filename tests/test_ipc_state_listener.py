"""EventBus.set_state_listener: derived-envelope republishing (Phase 2 state wiring)."""

import json

import pytest

from charlie.ipc import EventBus


class _FakePubSocket:
    def __init__(self):
        self.sent: list[str] = []

    async def send_string(self, data: str) -> None:
        self.sent.append(data)


def _producer_bus() -> EventBus:
    bus = EventBus(is_producer=True)
    bus._pub_socket = _FakePubSocket()
    return bus


@pytest.mark.asyncio
async def test_emit_without_listener_sends_only_original():
    bus = _producer_bus()
    await bus.emit("thinking", {"session_id": "s1"})
    assert len(bus._pub_socket.sent) == 1
    assert json.loads(bus._pub_socket.sent[0])["type"] == "thinking"


@pytest.mark.asyncio
async def test_listener_returning_none_sends_only_original():
    bus = _producer_bus()
    bus.set_state_listener(lambda envelope: None)
    await bus.emit("audio_level", {"level": 0.1})
    assert len(bus._pub_socket.sent) == 1


@pytest.mark.asyncio
async def test_listener_derived_envelope_also_published():
    bus = _producer_bus()
    derived = {"type": "charlie_state", "payload": {"state": "listening"}}
    bus.set_state_listener(lambda envelope: derived)
    await bus.emit("vad_start", {})
    assert len(bus._pub_socket.sent) == 2
    assert json.loads(bus._pub_socket.sent[0])["type"] == "vad_start"
    derived_wire = json.loads(bus._pub_socket.sent[1])
    assert derived_wire["type"] == derived["type"]
    assert derived_wire["payload"] == derived["payload"]
    assert derived_wire["version"] == 1
    assert derived_wire["id"]


@pytest.mark.asyncio
async def test_listener_receives_the_published_envelope():
    bus = _producer_bus()
    seen = []

    def _listener(envelope):
        seen.append(envelope)
        return None

    bus.set_state_listener(_listener)
    await bus.emit("thinking", {"session_id": "s1"})
    assert seen == [{"type": "thinking", "payload": {"session_id": "s1"}}]
