"""Session-isolation test for the web_server WebSocket forwarding layer.

Architecture under test:

  main.py (voice process) --PUB--> EventBus (ZMQ PUB/SUB) --SUB--> web_server
  web_server._event_bridge() --> web_server.broadcast(data)

The EventBus itself is a naive PUB/SUB: `consume_events` subscribes to
*bytes* (all events), so it does NOT enforce per-session isolation. The
isolation boundary is `web_server.broadcast()`: session-scoped event types
(`token`, `transcript`) are delivered only to WebSocket connections whose
subscribed session (``ws_sessions[ws]``) matches the event's session_id.
All other event types (e.g. `thinking`, `speaking_start`) are broadcast to
every connected client.

This test locks in that boundary: a session-scoped event for session "B"
must NOT be delivered to a consumer subscribed only to session "A", while a
session-scoped event for session "A" must be delivered.
"""

from typing import List

import pytest


class _FakeWebSocket:
    """Minimal async WebSocket stand-in that records sent messages."""

    def __init__(self, session_id: str | None) -> None:
        self.session_id = session_id
        self.sent: List[dict] = []

    async def send_text(self, message: str) -> None:
        # broadcast() forwards already-json-encoded strings over the socket.
        import json

        self.sent.append(json.loads(message))


@pytest.mark.asyncio
async def test_session_scoped_event_isolated_by_session():
    """A `token` for session B is not delivered to a client on session A."""
    from charlie import web_server

    # Register a client subscribed only to session "A".
    client_a = _FakeWebSocket("A")
    client_b = _FakeWebSocket("B")
    web_server.active_connections.add(client_a)
    web_server.active_connections.add(client_b)
    web_server.ws_sessions[client_a] = "A"
    web_server.ws_sessions[client_b] = "B"

    try:
        # Event forwarded by _event_bridge from the bus (including session_id).
        event_b = {
            "type": "token",
            "payload": {"session_id": "B", "text": "secret-B"},
        }
        event_a = {
            "type": "token",
            "payload": {"session_id": "A", "text": "visible-A"},
        }

        await web_server.broadcast(event_b)
        await web_server.broadcast(event_a)

        # Client A must receive A's token but never B's token.
        a_types = [m["payload"]["text"] for m in client_a.sent]
        b_types = [m["payload"]["text"] for m in client_b.sent]

        assert "secret-B" not in a_types
        assert "visible-A" in a_types

        # Client B receives its own token.
        assert "secret-B" in b_types
        assert "visible-A" not in b_types
    finally:
        web_server.active_connections.discard(client_a)
        web_server.active_connections.discard(client_b)
        web_server.ws_sessions.pop(client_a, None)
        web_server.ws_sessions.pop(client_b, None)


@pytest.mark.asyncio
async def test_session_scoped_event_unsubscribed_client_gets_nothing():
    """A client subscribed to no session receives no scoped event."""
    from charlie import web_server

    client_none = _FakeWebSocket(None)
    web_server.active_connections.add(client_none)
    web_server.ws_sessions[client_none] = None

    try:
        event_b = {
            "type": "transcript",
            "payload": {"session_id": "B", "text": "stray-B"},
        }
        await web_server.broadcast(event_b)

        assert client_none.sent == []
    finally:
        web_server.active_connections.discard(client_none)
        web_server.ws_sessions.pop(client_none, None)


@pytest.mark.asyncio
async def test_non_session_event_is_broadcast_to_all():
    """Non-session-scoped events (e.g. `thinking`) reach every client."""
    from charlie import web_server

    client_a = _FakeWebSocket("A")
    client_b = _FakeWebSocket("B")
    web_server.active_connections.add(client_a)
    web_server.active_connections.add(client_b)
    web_server.ws_sessions[client_a] = "A"
    web_server.ws_sessions[client_b] = "B"

    try:
        event = {"type": "thinking", "payload": {"session_id": "B"}}
        await web_server.broadcast(event)

        # thinking is NOT in _SESSION_SCOPED_EVENTS, so both clients get it.
        assert any(m["type"] == "thinking" for m in client_a.sent)
        assert any(m["type"] == "thinking" for m in client_b.sent)
    finally:
        web_server.active_connections.discard(client_a)
        web_server.active_connections.discard(client_b)
        web_server.ws_sessions.pop(client_a, None)
        web_server.ws_sessions.pop(client_b, None)
