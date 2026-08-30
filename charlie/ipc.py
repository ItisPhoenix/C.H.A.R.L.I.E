"""ZeroMQ-based IPC layer for Charlie voice <-> web dashboard communication.

EventBus provides two roles:
  - Producer (voice process): PUB events, PULL commands
  - Consumer (web process): SUB events, PUSH commands

Default ports: 5555 (events), 5556 (commands).
"""

import asyncio
import json
import logging
import os
import sys

# Windows: pyzmq needs Selector event loop, not Proactor
import warnings as _warnings
from typing import Callable, Optional

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    _warnings.filterwarnings(
        "ignore", message=".*add_reader.*", category=RuntimeWarning
    )

import zmq
import zmq.asyncio

from charlie.events import EventMeta, build_event, normalize_event

logger = logging.getLogger("charlie.ipc")

DEFAULT_EVENT_PORT = 5555
DEFAULT_COMMAND_PORT = 5556


class EventBus:
    """ZeroMQ PUB/SUB + PUSH/PULL bridge between voice and web processes."""

    def __init__(
        self,
        pub_port: int | None = None,
        pull_port: int | None = None,
        is_producer: bool = True,
    ):
        test_mode = os.getenv("CHARLIE_TEST_MODE", "").lower() == "true"
        if test_mode:
            pub_port = int(os.getenv("CHARLIE_TEST_EVENT_PORT", "0")) if pub_port is None else pub_port
            pull_port = int(os.getenv("CHARLIE_TEST_COMMAND_PORT", "0")) if pull_port is None else pull_port
            if (
                pub_port in {DEFAULT_EVENT_PORT, DEFAULT_COMMAND_PORT}
                or pull_port in {DEFAULT_EVENT_PORT, DEFAULT_COMMAND_PORT}
            ):
                raise RuntimeError("Test EventBus cannot use production ports 5555/5556")
        else:
            pub_port = DEFAULT_EVENT_PORT if pub_port is None else pub_port
            pull_port = DEFAULT_COMMAND_PORT if pull_port is None else pull_port
        self.ctx = zmq.asyncio.Context()
        self.is_producer = is_producer
        self.pub_port = pub_port
        self.pull_port = pull_port
        self._pub_socket: Optional[zmq.asyncio.Socket] = None
        self._sub_socket: Optional[zmq.asyncio.Socket] = None
        self._push_socket: Optional[zmq.asyncio.Socket] = None
        self._pull_socket: Optional[zmq.asyncio.Socket] = None
        self._state_listener: Optional[Callable[[dict], Optional[dict]]] = None

    def set_state_listener(self, fn: Callable[[dict], Optional[dict]]) -> None:
        """Producer only. fn receives every published envelope; a non-None return is republished as-is.

        Lets a single in-process consumer (charlie/state.py's StateMachine) derive
        and emit a new event from the stream without EventBus knowing what that logic is.
        """
        self._state_listener = fn

    async def __aenter__(self):
        if self.is_producer:
            self._pub_socket = self.ctx.socket(zmq.PUB)
            self._pub_socket.bind(f"tcp://127.0.0.1:{self.pub_port}")
            self._pull_socket = self.ctx.socket(zmq.PULL)
            self._pull_socket.bind(f"tcp://127.0.0.1:{self.pull_port}")
        else:
            self._sub_socket = self.ctx.socket(zmq.SUB)
            self._sub_socket.connect(f"tcp://127.0.0.1:{self.pub_port}")
            self._sub_socket.setsockopt(zmq.SUBSCRIBE, b"")
            self._push_socket = self.ctx.socket(zmq.PUSH)
            self._push_socket.connect(f"tcp://127.0.0.1:{self.pull_port}")
        # Allow sockets time to bind/connect
        await asyncio.sleep(0.1)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        for sock in (
            self._pub_socket,
            self._sub_socket,
            self._push_socket,
            self._pull_socket,
        ):
            if sock is not None:
                try:
                    sock.close(linger=0)
                except Exception:
                    pass
        try:
            self.ctx.term()
        except Exception:
            pass

    async def emit(self, event_type: str, payload: dict, meta: Optional[EventMeta] = None):
        """Producer only. Publishes an event to all subscribers.

        meta is additive envelope metadata (source/task_id/turn_id/rationale/ts, see
        charlie/events.py) -- kept a keyword arg with a default so callers
        that only pass (event_type, payload) are unaffected.
        """
        if not self.is_producer or not self._pub_socket:
            return
        envelope = build_event(event_type, payload, meta=meta)
        data = json.dumps(envelope)
        try:
            await self._pub_socket.send_string(data)
        except zmq.ZMQError:
            logger.debug("emit_dropped_socket_closed | type=%s", event_type)
            return
        if self._state_listener is not None:
            # Existing state listeners consume the original two-field shape;
            # only the wire event uses the new contract during migration.
            derived = self._state_listener({"type": event_type, "payload": payload})
            if derived is not None:
                try:
                    await self._pub_socket.send_string(json.dumps(normalize_event(derived, allow_unknown=True)))
                except zmq.ZMQError:
                    logger.debug("emit_dropped_socket_closed | type=%s", derived.get("type"))

    async def next_command(self) -> dict:
        """Producer only. Blocks until a command arrives from the web process."""
        if not self.is_producer or not self._pull_socket:
            raise RuntimeError("next_command() called on consumer EventBus")
        data = await self._pull_socket.recv_string()
        return json.loads(data)

    async def consume_events(self, callback: Callable):
        """Consumer only. Subscribe to events and invoke callback for each."""
        if self.is_producer or not self._sub_socket:
            raise RuntimeError("consume_events() called on producer EventBus")
        while True:
            data = await self._sub_socket.recv_string()
            event = json.loads(data)
            await callback(event)

    async def send_command(self, command: dict) -> bool:
        """Consumer only. Sends a command to the voice process.

        Return a transport-level boolean so correlated command APIs can
        distinguish an unavailable producer from a command that was sent.
        Existing callers may ignore the return value.
        """
        if self.is_producer or not self._push_socket:
            return False
        data = json.dumps(command)
        try:
            await self._push_socket.send_string(data)
            return True
        except zmq.ZMQError:
            logger.debug("send_command_dropped_socket_closed")
            return False
