"""P2-14 -- swallowed exceptions must not leave the runtime reporting success.

Each test induces a real failure at a boundary where ``charlie/core.py`` used to
catch, log, and continue, then asserts the *observable* state reflects the
failure instead of an unmarked success.

Evidence class: TEST/MOCK. Every dependency here is an in-process fake; nothing
in this file proves real host, real persistence, or real LLM behaviour.
"""

import json

import pytest

import charlie.core as core
from charlie.autonomy import Requirement
from charlie.config import Config
from charlie.core import Brain
from charlie.fastpaths import FastPathMatch, FastPathResult
from charlie.subsystem_health import HealthStatus
from charlie.turn_contracts import ResultStatus, VerificationStatus

_FOREIGN_SESSION_MARKER = "confidential note that belongs to a different session"


def _config(**overrides) -> Config:
    base = {
        "llm_url": "http://localhost:11434",
        "llm_key": "no-key",
        "llm_model": "dummy",
    }
    base.update(overrides)
    return Config(**base)


class _SingleChunkStream:
    """Minimal ``httpx`` streaming response that emits one content delta."""

    def __init__(self, text: str, captured: list | None = None):
        self._text = text
        self._captured = captured

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        yield f'data: {json.dumps({"choices": [{"delta": {"content": self._text}}]})}'
        yield "data: [DONE]"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


# ---------------------------------------------------------------------------
# 1. A health transition that could not be published must not be reported as
#    applied. ``on_llm_health`` is the only channel that writes the runtime's
#    user-visible ``llm``/``brain`` subsystem status, so a swallowed failure
#    there leaves a known-dead dependency looking healthy.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unpublished_llm_health_transition_is_not_reported_as_applied():
    delivered: list[tuple[HealthStatus, str | None]] = []

    def rejecting_sink(status, detail=None):
        delivered.append((status, detail))
        raise RuntimeError("health registry has no 'llm' subsystem")

    brain = Brain(_config(), on_llm_health=rejecting_sink)
    generation = brain._allocate_primary_llm_generation()

    applied = brain._notify_primary_llm_health(generation, HealthStatus.DEGRADED, "Unreachable")

    assert delivered == [(HealthStatus.DEGRADED, "Unreachable")]
    assert applied is False, (
        "a health transition whose sink raised must not be reported as applied; "
        "returning True is the false-health signal this finding is about"
    )
    assert brain.llm_health_delivery_error == "RuntimeError"


@pytest.mark.asyncio
async def test_published_llm_health_transition_is_reported_applied_and_clears_the_error():
    delivered: list[tuple[HealthStatus, str | None]] = []

    def working_sink(status, detail=None):
        delivered.append((status, detail))

    brain = Brain(_config(), on_llm_health=working_sink)
    brain.llm_health_delivery_error = "RuntimeError"
    generation = brain._allocate_primary_llm_generation()

    applied = brain._notify_primary_llm_health(generation, HealthStatus.RUNNING, "Ready")

    assert delivered == [(HealthStatus.RUNNING, "Ready")]
    assert applied is True
    assert brain.llm_health_delivery_error is None


# ---------------------------------------------------------------------------
# 2. A fast-path operation that declares a semantic verifier must not be
#    reported as a plain completion when the verifier itself blows up.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fastpath_verifier_exception_is_not_reported_as_completed(monkeypatch):
    brain = Brain(_config())
    envelopes = []
    brain.on_operation_result = lambda _name, envelope: envelopes.append(envelope)

    match = FastPathMatch(
        intent="cpu_temperature",
        semantic_op_id="system.metrics.read",
        tool_name="system_diagnostics",
        arguments={"check": "cpu_temperature"},
        target_domain="system",
        verifier_name="verify_cpu_temperature",
    )
    monkeypatch.setattr("charlie.fastpaths.match_fast_path", lambda _query: match)
    monkeypatch.setattr(
        "charlie.fastpaths.execute_fast_path",
        lambda _match: FastPathResult("CPU temperature is 42C on average.", {}),
    )
    monkeypatch.setattr(core, "autonomy_evaluate", lambda *_args: (Requirement.ALLOW, "safe", ""))

    def exploding_verifier(*_args, **_kwargs):
        raise RuntimeError("semantic verifier backend unavailable")

    monkeypatch.setattr("charlie.verifiers.run_verifier_for_match", exploding_verifier)

    chunks = [
        chunk
        async for chunk in brain.chat_stream(
            "show current CPU temperature",
            platform="text",
            skip_pre_search=True,
            session_id="session-p2-14-verifier",
            task_id="task-p2-14-verifier",
            turn_id="turn-p2-14-verifier",
        )
    ]

    assert chunks
    assert len(envelopes) == 1
    envelope = envelopes[0]
    assert envelope.status != ResultStatus.COMPLETED.value, (
        "the verifier raised, so the operation must not be published as completed"
    )
    assert envelope.status == ResultStatus.UNVERIFIED.value
    assert envelope.verification_status == VerificationStatus.VERIFICATION_UNAVAILABLE.value
    assert envelope.data["verification_error"] == "RuntimeError"
    assert envelope.reason


# ---------------------------------------------------------------------------
# 3. A durable session-history read failure must be visible, and must not leave
#    another session's messages in the prompt for this turn.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_history_read_failure_is_visible_and_does_not_leak_foreign_history(monkeypatch):
    brain = Brain(_config())
    requested: list[str] = []

    class UnreadableStore:
        def get_session_messages(self, session_id, limit):
            requested.append(session_id)
            raise RuntimeError("sessions.db is locked")

        def append_tool(self, **_kwargs):
            return None

    brain.session_store = UnreadableStore()
    brain.history = [{"role": "user", "content": _FOREIGN_SESSION_MARKER}]

    payloads: list[dict] = []

    def capture_stream(_method, _url, *, json=None, **_kwargs):
        payloads.append(json)
        return _SingleChunkStream("Understood.")

    monkeypatch.setattr(brain.client, "stream", capture_stream)

    chunks = [
        chunk
        async for chunk in brain.chat_stream(
            "tell me a short joke about otters",
            platform="text",
            skip_pre_search=True,
            session_id="session-p2-14-history",
            task_id="task-p2-14-history",
            turn_id="turn-p2-14-history",
        )
    ]

    assert chunks == ["Understood."]
    assert requested == ["session-p2-14-history"]
    assert brain.session_history_error == "RuntimeError", (
        "the failed durable read was swallowed; nothing in state recorded it"
    )
    assert _FOREIGN_SESSION_MARKER not in json.dumps(payloads), (
        "an unreadable session store must not silently substitute the previous "
        "session's messages into this turn's prompt"
    )


@pytest.mark.asyncio
async def test_session_history_read_recovers_after_a_transient_failure(monkeypatch):
    brain = Brain(_config())
    payloads: list[dict] = []

    class FlakyStore:
        def __init__(self):
            self.attempts = 0

        def get_session_messages(self, _session_id, limit):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("sessions.db is locked")
            return [("user", _FOREIGN_SESSION_MARKER)]

        def append_tool(self, **_kwargs):
            return None

    brain.session_store = FlakyStore()

    def capture_stream(_method, _url, *, json=None, **_kwargs):
        payloads.append(json)
        return _SingleChunkStream("Understood.")

    monkeypatch.setattr(brain.client, "stream", capture_stream)

    for _ in range(2):
        chunks = [
            chunk
            async for chunk in brain.chat_stream(
                "tell me a short joke about otters",
                platform="text",
                skip_pre_search=True,
                session_id="session-p2-14-recovery",
                task_id="task-p2-14-recovery",
                turn_id="turn-p2-14-recovery",
            )
        ]
        assert chunks == ["Understood."]

    assert brain.session_history_error is None, (
        "a later successful read must clear the stale degraded marker"
    )
    assert _FOREIGN_SESSION_MARKER in json.dumps(payloads[-1])


# ---------------------------------------------------------------------------
# 4. A durable context-file read failure must be recorded, so a context tier
#    that silently lost MEMORY/USER/OPINIONS is distinguishable from one that
#    genuinely holds no memory.
# ---------------------------------------------------------------------------


def test_durable_context_file_read_failure_is_recorded_in_brain_state(tmp_path):
    unreadable = tmp_path / "MEMORY.md"
    unreadable.mkdir()  # a directory cannot be read as a file

    brain = Brain(_config(memory_file=str(unreadable)))

    assert brain.context_tier_read_errors == {str(unreadable): "PermissionError"}
    assert isinstance(brain._context_tier, str)


def test_durable_context_file_read_error_clears_once_the_file_is_readable(tmp_path):
    unreadable = tmp_path / "MEMORY.md"
    unreadable.mkdir()
    readable = tmp_path / "MEMORY-restored.md"
    readable.write_text("Prefers dark mode.", encoding="utf-8")

    brain = Brain(_config(memory_file=str(unreadable)))
    assert brain.context_tier_read_errors == {str(unreadable): "PermissionError"}

    brain.config.memory_file = str(readable)
    brain.reload_context()

    assert brain.context_tier_read_errors == {}
    assert "Prefers dark mode." in brain._context_tier
