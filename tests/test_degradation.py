"""Cloud-LLM-unreachable behavior: verifies the turn fails gracefully, not with a raw traceback,
and that a fully dead endpoint actually shows up in telemetry (unlike an HTTP error response,
a connection failure never triggers httpx's response event hook)."""

import httpx
import pytest

from charlie import telemetry
from charlie.config import Config
from charlie.core import Brain
from charlie.errors import ErrorClass, classify_exception


@pytest.fixture
def brain_config():
    return Config(llm_url="http://localhost:1", llm_key="no-key", llm_model="dummy")


@pytest.mark.asyncio
async def test_stream_completion_records_telemetry_failure_on_dead_endpoint(monkeypatch, brain_config):
    brain = Brain(brain_config)

    def mock_stream(*args, **kwargs):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(brain.client, "stream", mock_stream)
    monkeypatch.setattr(telemetry, "_llm_calls", telemetry._llm_calls.__class__(maxlen=200))

    with pytest.raises(httpx.ConnectError):
        await brain._stream_completion({"model": "dummy", "messages": []}, generation=1)

    assert telemetry.llm_error_rate() == 1.0


def test_dead_endpoint_error_classifies_as_retryable_with_no_traceback():
    error_class, message = classify_exception(httpx.ConnectError("Connection refused"))
    assert error_class == ErrorClass.RETRYABLE
    assert message == "I can't reach my reasoning service right now. Local functions are still available."
    assert "Traceback" not in message
    assert "ConnectError" not in message
