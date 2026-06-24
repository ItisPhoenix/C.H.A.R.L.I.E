"""ZeroMQ-based IPC layer for Charlie voice <-> web dashboard communication.

EventBus provides two roles:
  - Producer (voice process): PUB events, PULL commands
  - Consumer (web process): SUB events, PUSH commands

Default ports: 5555 (events), 5556 (commands).
"""
import json
import asyncio
import logging
import sys
from typing import Callable, Optional

# Windows: pyzmq needs Selector event loop, not Proactor
import warnings as _warnings
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    _warnings.filterwarnings("ignore", message=".*add_reader.*", category=RuntimeWarning)

import zmq
import zmq.asyncio

logger = logging.getLogger("charlie.ipc")

DEFAULT_EVENT_PORT = 5555
DEFAULT_COMMAND_PORT = 5556


class EventBus:
    """ZeroMQ PUB/SUB + PUSH/PULL bridge between voice and web processes."""

    def __init__(self, pub_port: int = DEFAULT_EVENT_PORT,
                 pull_port: int = DEFAULT_COMMAND_PORT, is_producer: bool = True):
        self.ctx = zmq.asyncio.Context()
        self.is_producer = is_producer
        self.pub_port = pub_port
        self.pull_port = pull_port
        self._pub_socket: Optional[zmq.asyncio.Socket] = None
        self._sub_socket: Optional[zmq.asyncio.Socket] = None
        self._push_socket: Optional[zmq.asyncio.Socket] = None
        self._pull_socket: Optional[zmq.asyncio.Socket] = None

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
        for sock in (self._pub_socket, self._sub_socket,
                     self._push_socket, self._pull_socket):
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
        self.ctx.term()

    async def emit(self, event_type: str, payload: dict):
        """Producer only. Publishes an event to all subscribers."""
        if not self.is_producer or not self._pub_socket:
            raise RuntimeError("emit() called on consumer EventBus")
        data = json.dumps({"type": event_type, "payload": payload})
        await self._pub_socket.send_string(data)

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

    async def send_command(self, command: dict):
        """Consumer only. Sends a command to the voice process."""
        if self.is_producer or not self._push_socket:
            raise RuntimeError("send_command() called on producer EventBus")
        data = json.dumps(command)
        await self._push_socket.send_string(data)
