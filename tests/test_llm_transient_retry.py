"""Behavioural tests for the single same-model retry in ``Brain._stream_completion``.

The retry is a correctness property, not a tuning knob: it must fire exactly once
for transient upstream conditions, never for a permanent one, never change the
model, never replay a tool, and never outlive a superseding turn. These tests
assert the decisions the runtime actually makes.

Evidence class: TEST/MOCK. No network, no real LLM.
"""

from __future__ import annotations

import httpx
import pytest

from charlie import core as core_module
from charlie.config import Config
from charlie.core import Brain, _llm_retry_delay


def _brain() -> Brain:
    return Brain(Config(llm_url="http://localhost:9", llm_key="no-key", llm_model="configured-model"))


def _generation(brain: Brain) -> int:
    """The generation production actually passes.

    ``_chat_stream_impl`` captures ``generation = self._chat_generation`` before
    streaming, so a live turn is never already superseded at the moment its
    request is issued. Tests must reproduce that, or they exercise the cancel
    path instead of the retry path.
    """
    return brain._chat_generation


class _FakeResponse:
    def __init__(self, status_code: int = 200, headers: dict | None = None):
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("POST", "http://localhost:9/chat/completions"),
                response=httpx.Response(self.status_code, headers=self.headers),
            )


class _FakeStream:
    """Async context manager standing in for ``client.stream(...)``.

    ``outcomes`` is consumed one entry per attempt; each entry is either an
    exception to raise or a response to hand back.
    """

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.attempts = 0
        self.payloads: list[dict] = []

    def stream(self, method, url, *, json=None, extensions=None):
        self.payloads.append(json)
        outcome = self._outcomes[self.attempts] if self.attempts < len(self._outcomes) else None
        self.attempts += 1
        if isinstance(outcome, Exception):
            raise outcome
        return _Ctx(outcome)

    @property
    def attempt_count(self) -> int:
        return self.attempts


class _Ctx:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *_exc):
        return False


@pytest.fixture
def stub_stream(monkeypatch):
    """Replace SSE parsing so these tests exercise only the retry decision."""

    async def _fake_parse(_response, _generation, *_args, **_kwargs):
        return ("visible answer", {}, False)

    monkeypatch.setattr(core_module, "parse_sse_stream", _fake_parse)


@pytest.fixture
def recorded_sleeps(monkeypatch):
    delays: list[float] = []

    async def _sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr(core_module.asyncio, "sleep", _sleep)
    return delays


@pytest.mark.asyncio
async def test_transient_transport_error_is_retried_once_and_succeeds(stub_stream, recorded_sleeps):
    brain = _brain()
    stream = _FakeStream([httpx.ReadTimeout("timed out"), _FakeResponse()])
    brain.client = stream

    accumulated, tool_calls = await brain._stream_completion({"model": "configured-model"}, _generation(brain))

    assert stream.attempt_count == 2
    assert accumulated == "visible answer"
    assert tool_calls == []
    assert recorded_sleeps == [core_module._LLM_RETRY_BACKOFF_SEC]


@pytest.mark.asyncio
async def test_retryable_status_is_retried_once(stub_stream, recorded_sleeps):
    brain = _brain()
    stream = _FakeStream([_FakeResponse(503), _FakeResponse()])
    brain.client = stream

    accumulated, _ = await brain._stream_completion({"model": "configured-model"}, _generation(brain))

    assert stream.attempt_count == 2
    assert accumulated == "visible answer"


@pytest.mark.parametrize("status", [401, 403, 404, 422])
@pytest.mark.asyncio
async def test_permanent_status_is_never_retried(status, stub_stream, recorded_sleeps):
    brain = _brain()
    stream = _FakeStream([_FakeResponse(status)])
    brain.client = stream

    with pytest.raises(httpx.HTTPStatusError):
        await brain._stream_completion({"model": "configured-model"}, _generation(brain))

    assert stream.attempt_count == 1, "a permanent failure must be reported, not retried"
    assert recorded_sleeps == []


@pytest.mark.asyncio
async def test_only_one_retry_is_attempted_then_the_error_surfaces(stub_stream, recorded_sleeps):
    brain = _brain()
    stream = _FakeStream([httpx.ReadTimeout("a"), httpx.ReadTimeout("b"), _FakeResponse()])
    brain.client = stream

    with pytest.raises(httpx.ReadTimeout):
        await brain._stream_completion({"model": "configured-model"}, _generation(brain))

    assert stream.attempt_count == 2, "the retry must be bounded to exactly one attempt"
    assert len(recorded_sleeps) == 1


@pytest.mark.asyncio
async def test_model_is_never_switched_across_the_retry(stub_stream, recorded_sleeps):
    brain = _brain()
    stream = _FakeStream([httpx.ConnectError("refused"), _FakeResponse()])
    brain.client = stream

    await brain._stream_completion({"model": "configured-model"}, _generation(brain))

    assert [payload["model"] for payload in stream.payloads] == ["configured-model"] * 2


@pytest.mark.asyncio
async def test_superseded_turn_is_not_retried(stub_stream, recorded_sleeps):
    """A newer chat generation means barge-in or cancellation won; do not retry."""
    brain = _brain()
    stream = _FakeStream([httpx.ReadTimeout("timed out"), _FakeResponse()])
    brain.client = stream
    # The turn captures its generation when it starts, then barge-in or a
    # cancellation supersedes it while the request is in flight.
    generation = _generation(brain)
    brain.cancel_chat()

    with pytest.raises(httpx.ReadTimeout):
        await brain._stream_completion({"model": "configured-model"}, generation)

    assert stream.attempt_count == 1, "cancellation must not be delayed by a retry"
    assert recorded_sleeps == []


@pytest.mark.asyncio
async def test_diagnostic_trace_records_the_retry_without_claiming_a_fallback(stub_stream, recorded_sleeps):
    brain = _brain()
    stream = _FakeStream([httpx.ReadTimeout("timed out"), _FakeResponse()])
    brain.client = stream

    marks: list[tuple[str, dict]] = []

    class _Trace:
        def mark(self, event, *, fields=None, **_kwargs):
            marks.append((event, fields or {}))

        def mark_once(self, *_args, **_kwargs):
            return None

    await brain._stream_completion(
        {"model": "configured-model"}, _generation(brain), diagnostic_trace=_Trace()
    )

    retry_marks = [fields for event, fields in marks if event == "llm_transient_retry"]
    assert len(retry_marks) == 1
    assert retry_marks[0]["model_unchanged"] is True
    assert retry_marks[0]["attempt"] == 1


class TestRetryDelayPolicy:
    """The delay policy is a pure function, so it is tested directly."""

    def test_transport_errors_use_the_default_backoff(self):
        assert _llm_retry_delay(httpx.ReadTimeout("x")) == core_module._LLM_RETRY_BACKOFF_SEC
        assert _llm_retry_delay(httpx.RemoteProtocolError("x")) == core_module._LLM_RETRY_BACKOFF_SEC
        assert _llm_retry_delay(httpx.PoolTimeout("x")) == core_module._LLM_RETRY_BACKOFF_SEC

    def test_retry_after_header_is_honoured(self):
        exc = httpx.HTTPStatusError(
            "x",
            request=httpx.Request("POST", "http://x"),
            response=httpx.Response(429, headers={"Retry-After": "2.5"}),
        )
        assert _llm_retry_delay(exc) == 2.5

    def test_retry_after_is_clamped(self):
        exc = httpx.HTTPStatusError(
            "x",
            request=httpx.Request("POST", "http://x"),
            response=httpx.Response(503, headers={"Retry-After": "600"}),
        )
        assert _llm_retry_delay(exc) == core_module._LLM_RETRY_AFTER_MAX_SEC

    def test_http_date_retry_after_falls_back_instead_of_guessing(self):
        exc = httpx.HTTPStatusError(
            "x",
            request=httpx.Request("POST", "http://x"),
            response=httpx.Response(503, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
        )
        assert _llm_retry_delay(exc) == core_module._LLM_RETRY_BACKOFF_SEC

    def test_unrelated_exceptions_are_not_retryable(self):
        assert _llm_retry_delay(ValueError("bad payload")) is None
        assert _llm_retry_delay(KeyboardInterrupt()) is None
