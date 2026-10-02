"""EventBus transport honesty + LLM_TRUST_ENV resolvability.

Evidence class: TEST/MOCK. The CURVE cases bind a real loopback ZeroMQ PUB socket
and connect real SUB sockets, so they exercise the real libzmq handshake, but
they run inside the test process -- not the production runtime on port 5555.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest
import zmq

from charlie.config import Config
from charlie.ipc import EventBus

REPO_ROOT = Path(__file__).resolve().parents[1]
IPC_LOGGER = "charlie.ipc"
CONFIG_LOGGER = "charlie.config"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# --------------------------------------------------------------------------
# Finding 1a: a failed close must not be reported as a successful close.
# --------------------------------------------------------------------------


class _ExplodingContext:
    """Stands in for the ZMQ context and fails ``term()`` like a real teardown fault."""

    def term(self) -> None:
        raise zmq.ZMQError(zmq.EFSM, "context has outstanding sockets")


class _FakeContext:
    def term(self) -> None:
        return None


@pytest.mark.asyncio
async def test_close_failure_is_reported_as_failure_not_success(caplog):
    bus = EventBus(pub_port=_free_port())
    bus.ctx = _ExplodingContext()
    bus._pub_socket = None

    with caplog.at_level(logging.DEBUG, logger=IPC_LOGGER):
        await bus.__aexit__(None, None, None)

    records = [r for r in caplog.records if r.name == IPC_LOGGER]
    success = [r for r in records if r.levelno == logging.INFO and "closed" in r.getMessage()]
    failure = [r for r in records if r.levelno >= logging.ERROR]

    assert bus.close_ok is False, "a failed ctx.term() must not be recorded as a successful close"
    assert bus.close_error, "the close failure must retain the reason"
    assert failure, "a failed close must be logged at ERROR or above"
    assert not success, "a failed close must not emit the success 'closed' line"


@pytest.mark.asyncio
async def test_successful_close_is_reported_as_success(caplog):
    bus = EventBus(pub_port=_free_port())
    bus.ctx = _FakeContext()
    bus._pub_socket = None

    with caplog.at_level(logging.INFO, logger=IPC_LOGGER):
        await bus.__aexit__(None, None, None)

    assert bus.close_ok is True
    assert not bus.close_error
    assert any("closed" in r.getMessage() for r in caplog.records if r.name == IPC_LOGGER)


@pytest.mark.asyncio
async def test_aexit_never_suppresses_the_inflight_exception():
    """``__aexit__`` returning truthy would swallow a runtime failure."""
    bus = EventBus(pub_port=_free_port())
    bus.ctx = _FakeContext()
    bus._pub_socket = None
    assert await bus.__aexit__(None, None, None) in (None, False)


# --------------------------------------------------------------------------
# Finding 1b: a dropped event must be observable, never silent.
# --------------------------------------------------------------------------


class _ZMQErrorPubSocket:
    def __init__(self) -> None:
        self.attempts = 0

    async def send_string(self, data: str) -> None:
        self.attempts += 1
        raise zmq.ZMQError(zmq.EFSM, "operation cannot be accomplished in current state")


@pytest.mark.asyncio
async def test_emit_zmqerror_is_observable_not_silent(caplog):
    bus = EventBus(pub_port=_free_port())
    bus._pub_socket = _ZMQErrorPubSocket()

    with caplog.at_level(logging.DEBUG, logger=IPC_LOGGER):
        delivered = await bus.emit("thinking", {"session_id": "s1"})

    assert delivered is False
    assert bus.dropped_events == 1, "a dropped publish must be counted"
    warnings = [
        r for r in caplog.records if r.name == IPC_LOGGER and r.levelno == logging.WARNING
    ]
    assert warnings, "a dropped publish must be logged at WARNING, not DEBUG"
    joined = " ".join(r.getMessage() for r in warnings)
    assert "thinking" in joined, "the warning must name the event that was dropped"
    assert "operation cannot be accomplished" in joined or "EFSM" in joined, (
        "the warning must carry the underlying ZMQ error"
    )


@pytest.mark.asyncio
async def test_emit_before_socket_start_is_observable_not_silent(caplog):
    bus = EventBus(pub_port=_free_port())
    bus._pub_socket = None

    with caplog.at_level(logging.DEBUG, logger=IPC_LOGGER):
        delivered = await bus.emit("token", {"text": "hello"})

    assert delivered is False
    assert bus.dropped_events == 1
    assert [
        r for r in caplog.records if r.name == IPC_LOGGER and r.levelno == logging.WARNING
    ], "emitting before the bus is started is a dropped event and must be reported"


@pytest.mark.asyncio
async def test_successful_emit_does_not_increment_the_drop_counter():
    class _OkPubSocket:
        async def send_string(self, data: str) -> None:
            return None

    bus = EventBus(pub_port=_free_port())
    bus._pub_socket = _OkPubSocket()
    assert await bus.emit("thinking", {"session_id": "s1"}) is True
    assert bus.dropped_events == 0


# --------------------------------------------------------------------------
# Finding 1c: CURVE -- an unauthenticated subscriber must not read events.
# --------------------------------------------------------------------------


def test_curve_is_available_in_this_environment():
    if not zmq.has("curve"):
        pytest.fail("libzmq has no CURVE support; the transport cannot be authenticated here")


def test_pub_socket_is_configured_as_a_curve_server():
    """The publisher must be a CURVE server *before* bind, on loopback only."""
    port = _free_port()
    bus = EventBus(pub_port=port)
    assert bus.curve_server_public, "a per-boot CURVE server public key must exist"
    assert len(bus.curve_server_public) == 40
    assert bus.bind_endpoint == f"tcp://127.0.0.1:{port}"


def test_curve_server_key_is_generated_per_instance_not_a_constant():
    """A fresh secret per bus, so it is also fresh per boot and never hard-coded."""
    first = EventBus(pub_port=_free_port()).curve_server_public
    second = EventBus(pub_port=_free_port()).curve_server_public
    assert first != second, "the CURVE server key must be generated, not a hard-coded constant"


@pytest.mark.asyncio
async def test_unauthenticated_subscriber_receives_nothing():
    """The read itself must be denied, not merely the connection.

    Two real attacker shapes are covered. ``plain`` is what an unrelated local
    process would actually do -- connect a stock SUB socket with no CURVE
    options at all, which receives every event when the publisher is plaintext.
    ``wrong_key`` is a CURVE client that guessed a server identity. Neither may
    read a byte. If CURVE is ever removed from the publisher, ``plain`` reads
    the token and this test fails.
    """
    port = _free_port()
    async with EventBus(pub_port=port) as bus:
        plain = zmq.asyncio.Context().socket(zmq.SUB)
        plain.setsockopt(zmq.SUBSCRIBE, b"")
        plain.connect(f"tcp://127.0.0.1:{port}")

        wrong_key = zmq.asyncio.Context().socket(zmq.SUB)
        wrong_key.curve_publickey, wrong_key.curve_secretkey = zmq.curve_keypair()
        # The intruder guesses a server identity: it has no way to learn the real one.
        wrong_key.curve_serverkey = zmq.curve_keypair()[0]
        wrong_key.setsockopt(zmq.SUBSCRIBE, b"")
        wrong_key.connect(f"tcp://127.0.0.1:{port}")

        for name, intruder in (("plain", plain), ("wrong_key", wrong_key)):
            assert await _drain(intruder, bus, "sk-UNAUTHORIZED-SECRET") is None, (
                f"{name} subscriber read an event it had no per-boot key for"
            )
            assert not await intruder.poll(300), f"{name} subscriber received a message"
            intruder.close(linger=0)


@pytest.mark.asyncio
async def test_authenticated_subscriber_receives_events():
    port = _free_port()
    async with EventBus(pub_port=port) as bus:
        sub = bus.new_subscriber()
        received = await _drain(sub, bus, "sk-AUTHORIZED-SECRET")
        assert received, "an authorized CURVE subscriber must still receive events"
        assert json.loads(received)["type"] == "token"
        sub.close(linger=0)


async def _drain(sub_socket, bus: EventBus, secret: str, attempts: int = 6) -> str | None:
    """Publish a few times to defeat the PUB/SUB slow-joiner, return one message."""
    for _ in range(attempts):
        await bus.emit("token", {"text": secret})
        if await sub_socket.poll(200):
            return await sub_socket.recv_string()
        await __import__("asyncio").sleep(0.05)
    return None


# --------------------------------------------------------------------------
# Regression guard: the uncommitted subscribe() / local fan-out edits.
# --------------------------------------------------------------------------


class _FakePubSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_string(self, data: str) -> None:
        self.sent.append(data)


@pytest.mark.asyncio
async def test_subscribe_fanout_still_delivers_and_unsubscribes():
    bus = EventBus(pub_port=_free_port())
    bus._pub_socket = _FakePubSocket()
    seen: list[dict] = []

    unsubscribe = bus.subscribe(seen.append)
    await bus.emit("thinking", {"session_id": "s1"})
    assert len(seen) == 1
    assert seen[0]["type"] == "thinking"

    unsubscribe()
    await bus.emit("thinking", {"session_id": "s2"})
    assert len(seen) == 1, "unsubscribe must detach the listener"


@pytest.mark.asyncio
async def test_subscribe_fanout_reaches_derived_state_events():
    """The second local fan-out loop (derived state) must survive."""
    bus = EventBus(pub_port=_free_port())
    bus._pub_socket = _FakePubSocket()
    derived = {"type": "charlie_state", "payload": {"state": "listening"}}
    bus.set_state_listener(lambda envelope: derived)
    seen: list[dict] = []
    bus.subscribe(seen.append)

    await bus.emit("vad_start", {})
    assert [e["type"] for e in seen] == ["vad_start", "charlie_state"]


@pytest.mark.asyncio
async def test_a_failing_local_listener_does_not_break_publish():
    bus = EventBus(pub_port=_free_port())
    bus._pub_socket = _FakePubSocket()
    good: list[dict] = []

    def _boom(_envelope):
        raise RuntimeError("listener fault")

    bus.subscribe(_boom)
    bus.subscribe(good.append)
    assert await bus.emit("thinking", {"session_id": "s1"}) is True
    assert len(good) == 1
    assert len(bus._pub_socket.sent) == 1


# --------------------------------------------------------------------------
# Finding 2: LLM_TRUST_ENV must be non-silent and default to the safe value.
# --------------------------------------------------------------------------

_PROBE = (
    "import json;"
    "from charlie.config import Config;"
    "print(json.dumps({'trust_env': Config().llm_trust_env}))"
)


def _resolved_trust_env_in_subprocess(env_overrides: dict[str, str | None]) -> bool:
    env = dict(os.environ)
    env["CHARLIE_TEST_MODE"] = "true"
    for key, value in env_overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    out = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert out.returncode == 0, f"probe failed: {out.stderr}"
    return bool(json.loads(out.stdout.strip().splitlines()[-1])["trust_env"])


def test_llm_trust_env_resolves_false_when_the_env_var_is_absent():
    """The import-time default is the safe value when nothing opts in.

    The dataclass default reads ``os.environ`` when the class body executes, so
    this must be probed in a fresh interpreter rather than by mutating this one.
    """
    assert _resolved_trust_env_in_subprocess({"LLM_TRUST_ENV": None}) is False


def test_llm_trust_env_resolves_true_only_on_explicit_opt_in():
    assert _resolved_trust_env_in_subprocess({"LLM_TRUST_ENV": "true"}) is True
    assert _resolved_trust_env_in_subprocess({"LLM_TRUST_ENV": "false"}) is False


def test_repository_env_opted_in_and_is_therefore_a_live_risk():
    """Guards the finding: the shipped .env turns the unsafe state on."""
    assert _resolved_trust_env_in_subprocess({"CHARLIE_TEST_MODE": None}) is True, (
        "the repository .env sets LLM_TRUST_ENV=true, so the key-bearing client "
        "honours HTTPS_PROXY / SSL_CERT_FILE / REQUESTS_CA_BUNDLE at runtime"
    )


def test_llm_trust_env_true_is_reported_not_silent(caplog):
    with caplog.at_level(logging.DEBUG, logger=CONFIG_LOGGER):
        cfg = Config(llm_trust_env=True, llm_key="sk-live-key-1234")

    assert cfg.llm_trust_env is True
    warnings = [r for r in caplog.records if r.name == CONFIG_LOGGER and r.levelno == logging.WARNING]
    assert warnings, "enabling LLM_TRUST_ENV on a key-bearing client must warn"
    joined = " ".join(r.getMessage() for r in warnings)
    for named in ("HTTPS_PROXY", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        assert named in joined, f"the warning must name the {named} interception surface"
    assert "LLM_API_KEY" in joined, "the warning must name the credential at risk"


def test_llm_trust_env_report_names_the_risk_and_resolved_value():
    report = Config(llm_trust_env=True, llm_key="sk-live-key-1234").llm_trust_env_report()
    assert report["llm_trust_env"] is True
    assert report["unsafe"] is True
    assert report["llm_key_configured"] is True
    assert report["env_vars_honoured"]

    safe = Config(llm_trust_env=False, llm_key="sk-live-key-1234").llm_trust_env_report()
    assert safe["llm_trust_env"] is False
    assert safe["unsafe"] is False


def test_llm_trust_env_true_without_a_key_is_not_reported_as_credential_risk():
    """No key means no credential to intercept, so no misleading warning."""
    with pytest.warns(None) if False else _nullcontext():
        cfg = Config(llm_trust_env=True, llm_key="no-key")
    assert cfg.llm_trust_env_report()["llm_key_configured"] is False


def test_llm_trust_env_false_never_warns_about_interception():
    import io

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger(CONFIG_LOGGER)
    logger.addHandler(handler)
    try:
        Config(llm_trust_env=False, llm_key="sk-live-key-1234")
    finally:
        logger.removeHandler(handler)
    assert "HTTPS_PROXY" not in stream.getvalue()


def test_runtime_settings_surface_exposes_the_knob():
    spec = {s["key"]: s for s in Config.editable_field_specs()}
    assert "LLM_TRUST_ENV" in spec
    assert spec["LLM_TRUST_ENV"]["field"] == "llm_trust_env"
    assert spec["LLM_TRUST_ENV"]["type"] == "bool"


import contextlib


@contextlib.contextmanager
def _nullcontext():
    yield
