"""ZeroMQ event publisher for canonical runtime telemetry and state."""

import asyncio
import json
import logging
import os
import sys
import warnings as _warnings
from typing import Callable, Optional

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    _warnings.filterwarnings("ignore", message=".*add_reader.*", category=RuntimeWarning)

import zmq
import zmq.asyncio

from charlie.events import EventMeta, build_event, normalize_event

logger = logging.getLogger("charlie.ipc")

DEFAULT_EVENT_PORT = 5555


class EventBus:
    """Publish typed runtime events and derive canonical state envelopes."""

    def __init__(self, pub_port: int | None = None) -> None:
        test_mode = os.getenv("CHARLIE_TEST_MODE", "").lower() == "true"
        if test_mode:
            pub_port = int(os.getenv("CHARLIE_TEST_EVENT_PORT", "0")) if pub_port is None else pub_port
            if pub_port == DEFAULT_EVENT_PORT:
                raise RuntimeError("Test EventBus cannot use production port 5555")
        else:
            pub_port = DEFAULT_EVENT_PORT if pub_port is None else pub_port
        self.ctx = zmq.asyncio.Context()
        self.pub_port = pub_port
        self._pub_socket: Optional[zmq.asyncio.Socket] = None
        self._state_listener: Optional[Callable[[dict], Optional[dict]]] = None

    def set_state_listener(self, fn: Callable[[dict], Optional[dict]]) -> None:
        """Derive and republish one canonical state event after each emit."""
        self._state_listener = fn

    async def __aenter__(self):
        self._pub_socket = self.ctx.socket(zmq.PUB)
        self._pub_socket.bind(f"tcp://127.0.0.1:{self.pub_port}")
        await asyncio.sleep(0.1)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._pub_socket is not None:
            try:
                self._pub_socket.close(linger=0)
            except Exception:
                pass
        try:
            self.ctx.term()
        except Exception:
            pass
        logger.info("EventBus/ZMQ closed")

    async def emit(self, event_type: str, payload: dict, meta: Optional[EventMeta] = None):
        """Publish one typed event and any derived canonical state event."""
        if not self._pub_socket:
            return False
        envelope = build_event(event_type, payload, meta=meta)
        try:
            await self._pub_socket.send_string(json.dumps(envelope))
        except zmq.ZMQError:
            logger.debug("emit_dropped_socket_closed | type=%s", event_type)
            return False
        if self._state_listener is not None:
            derived = self._state_listener({"type": event_type, "payload": payload})
            if derived is not None:
                try:
                    await self._pub_socket.send_string(json.dumps(normalize_event(derived, allow_unknown=True)))
                except zmq.ZMQError:
                    logger.debug("emit_dropped_socket_closed | type=%s", derived.get("type"))
        return True
