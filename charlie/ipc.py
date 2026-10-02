"""ZeroMQ event publisher for canonical runtime telemetry and state.

Transport security
------------------
The publisher binds loopback only (``tcp://127.0.0.1:{port}``) and runs as a
ZeroMQ CURVE *server* with a keypair generated fresh for each ``EventBus``
instance. A subscriber must know that per-boot server public key to derive the
session keys, so without it the subscriber receives ciphertext it cannot read.
That closes the "any local process can read every event, including token text
and log lines" hole: the read, not merely the connection, is what is denied.

Deliberate limitation, stated rather than papered over: pyzmq 27.1.0 exposes no
``ZMQ_ZAP_HANDLER`` (option 61 is absent from ``zmq.ContextOption`` and
``Context.set`` rejects a callable), so a client-key allow-list cannot be
installed from Python in this environment. libzmq 4.3.5 therefore accepts *any*
client keypair once the session is established. The property relied on below is
confidentiality plus server authentication -- the server proves possession of
the per-boot secret to the subscriber -- not rejection of a known-but-wrong
client key. If this venv ever gains ZAP support, add the allow-list here.

Error behaviour
---------------
A failed close is never reported as a successful close, and a publish that does
not reach the wire is never silent: both are counted and logged at WARNING or
ERROR so the canonical runtime cannot claim telemetry it did not deliver.
"""

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
# Loopback only. CURVE is what keeps a *local* process from reading the stream;
# this constant is what keeps a remote host from reaching it at all.
EVENT_BIND_HOST = "127.0.0.1"


def _require_curve_support() -> None:
    """Fail closed rather than fall back to a plaintext publisher."""
    if not zmq.has("curve"):
        raise RuntimeError(
            "libzmq was built without CURVE support; refusing to publish Charlie's "
            "event stream over an unauthenticated plaintext socket"
        )


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
        _require_curve_support()
        self.ctx = zmq.asyncio.Context()
        self.pub_port = pub_port
        # Per-instance CURVE identity. A fresh keypair per bus is strictly
        # stronger than a per-boot one: a restart never reuses a secret.
        self._curve_server_public, self._curve_server_secret = zmq.curve_keypair()
        self._curve_client_public, self._curve_client_secret = zmq.curve_keypair()
        self._pub_socket: Optional[zmq.asyncio.Socket] = None
        self._state_listener: Optional[Callable[[dict], Optional[dict]]] = None
        self._listeners: set[Callable[[dict], None]] = set()
        # Honesty counters. ``dropped_events`` is the number of emits that never
        # reached the wire; ``close_ok`` is None until __aexit__ has run.
        self.dropped_events = 0
        self.close_ok: Optional[bool] = None
        self.close_error: str = ""

    @property
    def curve_server_public(self) -> bytes:
        """The per-boot CURVE identity a subscriber must present."""
        return self._curve_server_public

    @property
    def bind_endpoint(self) -> str:
        return f"tcp://{EVENT_BIND_HOST}:{self.pub_port}"

    def new_subscriber(self) -> zmq.asyncio.Socket:
        """Build a CURVE subscriber already wired to this bus.

        The only sanctioned way for an in-process consumer to read the stream.
        """
        sub = self.ctx.socket(zmq.SUB)
        sub.curve_publickey = self._curve_client_public
        sub.curve_secretkey = self._curve_client_secret
        sub.curve_serverkey = self._curve_server_public
        sub.setsockopt(zmq.SUBSCRIBE, b"")
        sub.connect(self.bind_endpoint)
        return sub

    def security_report(self) -> dict:
        return {
            "endpoint": self.bind_endpoint,
            "transport": "curve",
            "server_authenticated": True,
            "client_allowlist": False,
            "server_public_key": self._curve_server_public.hex(),
        }

    def subscribe(self, listener: Callable[[dict], None]) -> Callable[[], None]:
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    def set_state_listener(self, fn: Callable[[dict], Optional[dict]]) -> None:
        """Derive and republish one canonical state event after each emit."""
        self._state_listener = fn

    async def __aenter__(self):
        self._pub_socket = self.ctx.socket(zmq.PUB)
        # CURVE must be armed before bind; arming after bind would leave the
        # socket briefly accepting plaintext handshakes.
        try:
            self._pub_socket.curve_publickey = self._curve_server_public
            self._pub_socket.curve_secretkey = self._curve_server_secret
            self._pub_socket.curve_server = True
        except zmq.ZMQError as exc:
            self._pub_socket.close(linger=0)
            self._pub_socket = None
            raise RuntimeError(
                f"failed to arm CURVE on the event publisher: {exc}"
            ) from exc
        self._pub_socket.bind(self.bind_endpoint)
        await asyncio.sleep(0.1)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        # Returns None so an in-flight exception propagates; only the teardown
        # outcome is recorded on self.
        self.close_ok = True
        self.close_error = ""
        if self._pub_socket is not None:
            try:
                self._pub_socket.close(linger=0)
            except Exception as close_exc:
                self.close_ok = False
                self.close_error = f"socket close: {type(close_exc).__name__}: {close_exc}"
                logger.error("EventBus socket close failed: %s", self.close_error, exc_info=True)
        try:
            self.ctx.term()
        except Exception as term_exc:
            self.close_ok = False
            detail = f"context term: {type(term_exc).__name__}: {term_exc}"
            self.close_error = "; ".join(part for part in (self.close_error, detail) if part)
            logger.error("EventBus/ZMQ close failed: %s", detail, exc_info=True)
            return None
        if self.close_ok:
            logger.info("EventBus/ZMQ closed")
        return None

    def _record_drop(self, event_type: str, reason: str, exc: Optional[BaseException] = None) -> None:
        """Make one lost publish visible instead of silent."""
        self.dropped_events += 1
        logger.warning(
            "event publish dropped | type=%s | dropped_total=%d | reason=%s",
            event_type,
            self.dropped_events,
            reason,
            exc_info=exc,
        )

    async def emit(self, event_type: str, payload: dict, meta: Optional[EventMeta] = None):
        """Publish one typed event and any derived canonical state event."""
        if not self._pub_socket:
            self._record_drop(event_type, "publisher socket is not open")
            return False
        envelope = build_event(event_type, payload, meta=meta)
        for listener in tuple(self._listeners):
            try:
                listener(envelope)
            except Exception:
                logger.debug("local event listener failed", exc_info=True)
        try:
            await self._pub_socket.send_string(json.dumps(envelope))
        except zmq.ZMQError as exc:
            self._record_drop(event_type, f"zmq.ZMQError: {exc}", exc)
            return False
        if self._state_listener is not None:
            derived = self._state_listener({"type": event_type, "payload": payload})
            if derived is not None:
                for listener in tuple(self._listeners):
                    try:
                        listener(derived)
                    except Exception:
                        logger.debug("local derived event listener failed", exc_info=True)
                try:
                    await self._pub_socket.send_string(json.dumps(normalize_event(derived, allow_unknown=True)))
                except zmq.ZMQError as exc:
                    self._record_drop(str(derived.get("type")), f"zmq.ZMQError: {exc}", exc)
        return True
