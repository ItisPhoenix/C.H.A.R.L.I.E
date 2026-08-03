import logging

import pytest

from charlie.streaming import FollowupStreamState, parse_sse_stream, stream_followup_content


class _FakeResponse:
    """Minimal stand-in for an httpx streaming response's aiter_lines()."""

    def __init__(self, lines):
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line


@pytest.mark.asyncio
async def test_parse_sse_stream_logs_malformed_line(caplog):
    """Regression: a malformed SSE line used to vanish silently (bare `except:
    continue`), making a real parse failure indistinguishable from an empty
    response -- exactly what happened with the vision follow-up route."""
    response = _FakeResponse(["data: {not valid json", "data: [DONE]"])
    with caplog.at_level(logging.WARNING, logger="charlie.streaming"):
        accumulated, tc_by_index, cancelled = await parse_sse_stream(response, 0, lambda: 0)
    assert accumulated == ""
    assert "failed to parse SSE line" in caplog.text


@pytest.mark.asyncio
async def test_parse_sse_stream_keeps_valid_content_after_bad_line():
    response = _FakeResponse([
        "data: {not valid json",
        'data: {"choices": [{"delta": {"content": "hello"}}]}',
        "data: [DONE]",
    ])
    accumulated, _, _ = await parse_sse_stream(response, 0, lambda: 0)
    assert accumulated == "hello"


@pytest.mark.asyncio
async def test_stream_followup_content_logs_malformed_line(caplog):
    response = _FakeResponse(["data: {not valid json", "data: [DONE]"])
    state = FollowupStreamState()
    with caplog.at_level(logging.WARNING, logger="charlie.streaming"):
        async for _ in stream_followup_content(response, 0, lambda: 0, state):
            pass
    assert state.accumulated == ""
    assert "failed to parse SSE line" in caplog.text


@pytest.mark.asyncio
async def test_stream_followup_content_logs_upstream_error(caplog):
    """Regression: an SSE data line shaped like an error (no "choices" key,
    e.g. LM Studio's "exceeds the available context size") parsed as valid
    JSON with zero exception, so it silently looked identical to an empty
    response -- this is exactly what made a real context-overflow error on
    the vision follow-up route indistinguishable from "the model said
    nothing" for the whole investigation."""
    error_line = 'data: {"error": {"message": "exceeds the available context size (5888 tokens)"}}'
    response = _FakeResponse([error_line, "data: [DONE]"])
    state = FollowupStreamState()
    with caplog.at_level(logging.WARNING, logger="charlie.streaming"):
        async for _ in stream_followup_content(response, 0, lambda: 0, state):
            pass
    assert state.accumulated == ""
    assert "upstream error" in caplog.text
    assert "context size" in caplog.text


@pytest.mark.asyncio
async def test_parse_sse_stream_logs_upstream_error(caplog):
    error_line = 'data: {"error": {"message": "boom"}}'
    response = _FakeResponse([error_line, "data: [DONE]"])
    with caplog.at_level(logging.WARNING, logger="charlie.streaming"):
        accumulated, _, _ = await parse_sse_stream(response, 0, lambda: 0)
    assert accumulated == ""
    assert "upstream error" in caplog.text
