"""Regression tests for charlie/streaming.py's generation-based cancellation.

Barge-in works by bumping Brain._chat_generation (see cancel_chat()); the SSE
parsers must observe that per-line, not just once at stream start, or a
cancelled turn keeps yielding audio after the user interrupted. No test
covered this before -- written first so a core.py decomposition has
something real to fail against.
"""

import pytest

from charlie.streaming import (
    FollowupStreamState,
    parse_sse_stream,
    stream_followup_content,
)


class _FakeResponse:
    """Minimal stand-in for an httpx streaming response: aiter_lines() over
    a fixed list of SSE 'data: {...}' lines."""

    def __init__(self, lines):
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line


def _sse_content_line(text: str) -> str:
    return (
        '{"choices": [{"delta": {"content": ' + f'"{text}"' + "}}]}"
    )


def _data(text: str) -> str:
    return "data: " + _sse_content_line(text)


class TestParseSseStreamCancellation:
    @pytest.mark.asyncio
    async def test_runs_to_completion_when_generation_stable(self):
        response = _FakeResponse([_data("Hello "), _data("world"), "data: [DONE]"])
        text, _, cancelled = await parse_sse_stream(response, generation=1, current_generation_getter=lambda: 1)
        assert text == "Hello world"
        assert cancelled is False

    @pytest.mark.asyncio
    async def test_stops_mid_stream_when_generation_bumps(self):
        gen = {"value": 1}

        def getter():
            return gen["value"]

        async def bump_after_first(lines):
            for i, line in enumerate(lines):
                if i == 1:
                    gen["value"] = 2  # simulate cancel_chat() firing mid-stream
                yield line

        response = _FakeResponse([])
        response.aiter_lines = lambda: bump_after_first(
            [_data("Hello "), _data("world"), _data(" -- should not be seen")]
        )

        text, _, cancelled = await parse_sse_stream(response, generation=1, current_generation_getter=getter)
        assert cancelled is True
        assert "should not be seen" not in text

    @pytest.mark.asyncio
    async def test_on_content_callback_fires_per_chunk(self):
        seen = []
        response = _FakeResponse([_data("a"), _data("b"), "data: [DONE]"])
        await parse_sse_stream(
            response, generation=1, current_generation_getter=lambda: 1, on_content=seen.append
        )
        assert seen == ["a", "b"]


class TestStreamFollowupContentCancellation:
    @pytest.mark.asyncio
    async def test_yields_chunks_and_accumulates_state(self):
        response = _FakeResponse([_data("Hi "), _data("there"), "data: [DONE]"])
        state = FollowupStreamState()
        gen = stream_followup_content(response, generation=1, current_generation_getter=lambda: 1, state=state)
        chunks = [c async for c in gen]
        assert chunks == ["Hi ", "there"]
        assert state.accumulated == "Hi there"
        assert state.cancelled is False

    @pytest.mark.asyncio
    async def test_stops_yielding_when_generation_bumps_mid_stream(self):
        gen = {"value": 1}

        async def bump_after_first(lines):
            for i, line in enumerate(lines):
                if i == 1:
                    gen["value"] = 2
                yield line

        response = _FakeResponse([])
        response.aiter_lines = lambda: bump_after_first(
            [_data("Hi "), _data("there"), _data(" -- barge-in should cut this")]
        )
        state = FollowupStreamState()
        chunks = [
            c async for c in stream_followup_content(
                response, generation=1, current_generation_getter=lambda: gen["value"], state=state
            )
        ]
        assert chunks == ["Hi "]
        assert state.cancelled is True
        assert "barge-in" not in state.accumulated


class TestCollectToolCallsMalformedInput:
    def test_malformed_json_logs_warning_and_defaults_to_empty(self, caplog):
        import logging

        from charlie.streaming import collect_tool_calls
        caplog.set_level(logging.WARNING, logger="charlie.streaming")
        calls = collect_tool_calls({0: {"id": "1", "name": "web_search", "arguments": "{not json"}})
        assert calls == [{"id": "1", "name": "web_search", "arguments": {}}]
        assert "Malformed tool-call arguments" in caplog.text

    def test_empty_tool_name_logs_warning(self, caplog):
        import logging

        from charlie.streaming import collect_tool_calls
        caplog.set_level(logging.WARNING, logger="charlie.streaming")
        collect_tool_calls({0: {"id": "1", "name": "", "arguments": "{}"}})
        assert "empty name" in caplog.text
