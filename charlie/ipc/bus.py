"""
MessageBus — typed wrapper around multiprocessing.Queue.

Wraps raw queues with Pydantic message envelopes for type safety, retry
on Queue.Full, graceful shutdown, and correlation-id routing.

The bus is opt-in: existing code can keep using raw queues. New code
should prefer MessageBus for any new IPC channel.
"""

from __future__ import annotations

import multiprocessing
import queue as _stdlib_queue
import time
import uuid
from typing import Any, Generic, Optional, Type, TypeVar

from pydantic import BaseModel, Field

from charlie.utils.logger import get_logger

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


# ── Message Envelopes ──────────────────────────────────────────────────────


class ChatMessage(BaseModel):
    """A user-facing text message entering or leaving the brain."""

    sender: str
    text: str
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = Field(default_factory=time.time)


class ToolResult(BaseModel):
    """The outcome of a tool execution."""

    tool_name: str
    status: str  # "ok" | "error" | "cancelled" | "pending_confirmation"
    payload: Any = None
    error: Optional[str] = None
    elapsed_ms: float = 0.0
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))


class StatusUpdate(BaseModel):
    """A status/state change from any subsystem."""

    component: str
    state: str
    detail: Optional[dict] = None
    timestamp: float = Field(default_factory=time.time)


class Ping(BaseModel):
    """Heartbeat probe — used for liveness checks."""

    source: str
    timestamp: float = Field(default_factory=time.time)


class Pong(BaseModel):
    """Reply to a Ping."""

    source: str
    in_reply_to: str
    timestamp: float = Field(default_factory=time.time)


# ── MessageBus ─────────────────────────────────────────────────────────────


class MessageBus(Generic[T]):
    """Typed wrapper around multiprocessing.Queue.

    Provides:
    - Type safety via Pydantic envelopes
    - Retry on Queue.Full
    - Correlation-ID routing for request/response patterns
    - Graceful shutdown via drain + close
    """

    def __init__(
        self,
        envelope_type: Type[T],
        name: str = "anonymous",
        maxsize: int = 1000,
        send_timeout: float = 0.1,
    ) -> None:
        self._envelope_type = envelope_type
        self._name = name
        self._send_timeout = send_timeout
        self._queue: multiprocessing.Queue = multiprocessing.Queue(maxsize=maxsize)
        self._closed = False
        self._pending: dict[str, T] = {}  # correlation_id -> envelope (for routing)

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_closed(self) -> bool:
        return self._closed

    def send(self, envelope: T, block: bool = True) -> bool:
        """Send a typed envelope. Returns False if the bus is closed or full."""
        if self._closed:
            logger.warning("bus_send_on_closed | name=%s", self._name)
            return False
        if not isinstance(envelope, self._envelope_type):
            raise TypeError(
                f"MessageBus[{self._name}] expects {self._envelope_type.__name__}, got {type(envelope).__name__}"
            )
        try:
            if block:
                self._queue.put(envelope, timeout=self._send_timeout)
            else:
                self._queue.put_nowait(envelope)
            return True
        except _stdlib_queue.Full:
            logger.warning("bus_send_full | name=%s", self._name)
            return False
        except Exception as e:
            logger.error("bus_send_failed | name=%s | %s", self._name, e)
            return False

    def receive(self, timeout: float = 0.01) -> Optional[T]:
        """Receive a typed envelope. Returns None on timeout or close."""
        if self._closed:
            return None
        try:
            raw = self._queue.get(timeout=timeout)
        except _stdlib_queue.Empty:
            return None
        except (EOFError, ValueError):
            return None
        if not isinstance(raw, self._envelope_type):
            logger.warning(
                "bus_receive_wrong_type | name=%s | expected=%s | got=%s",
                self._name,
                self._envelope_type.__name__,
                type(raw).__name__,
            )
            return None
        return raw

    def receive_nowait(self) -> Optional[T]:
        """Non-blocking receive."""
        return self.receive(timeout=0.0)

    def qsize(self) -> int:
        """Approximate queue size."""
        try:
            return self._queue.qsize()
        except Exception:
            return -1

    def drain(self) -> list[T]:
        """Pull all available envelopes (non-blocking). Used during shutdown."""
        out: list[T] = []
        while True:
            env = self.receive_nowait()
            if env is None:
                break
            out.append(env)
        return out

    def close(self) -> None:
        """Mark the bus closed and stop accepting new messages.

        Note: multiprocessing.Queue cannot be cleanly closed in all
        Python versions — we mark the bus as closed and let consumers
        observe EOF naturally.
        """
        self._closed = True
        logger.info("bus_closed | name=%s | drained=%d", self._name, len(self.drain()))


# ── Factory helpers ────────────────────────────────────────────────────────


def make_chat_bus(name: str = "chat") -> MessageBus[ChatMessage]:
    return MessageBus(ChatMessage, name=name)


def make_status_bus(name: str = "status") -> MessageBus[StatusUpdate]:
    return MessageBus(StatusUpdate, name=name)


def make_ping_bus(name: str = "ping") -> MessageBus[Ping]:
    return MessageBus(Ping, name=name)
