"""Tests for the background-task chat/voice trigger: Brain._handle_start_background_task
and its _exec_one interception (charlie/core.py) -- mirrors test_propose_new_tool.py.
"""

import json

import pytest

from charlie import background_task
from charlie.config import Config
from charlie.core import Brain


@pytest.fixture
def brain_config():
    return Config(
        llm_url="https://example.com/v1", llm_key="test-key", llm_model="dummy",
        iteration_budget_max=5, world_model_db_path=":memory:",
    )


class _FakeTask:
    id = "abc123"
    status = "running"


class TestHandleStartBackgroundTask:
    @pytest.mark.asyncio
    async def test_valid_text_starts_task_and_confirms(self, monkeypatch, brain_config):
        brain = Brain(brain_config)

        class _FakeBus:
            async def emit(self, *a, **kw):
                pass

        import charlie.recovery as recovery
        monkeypatch.setattr(recovery, "_event_bus", _FakeBus())

        captured = {}

        async def fake_start(config, event_bus, text, session_store=None, memory_store=None,
                              voice=None, priority=0, depends_on=None, on_result_stored=None):
            captured["text"] = text
            captured["priority"] = priority
            captured["depends_on"] = depends_on
            return _FakeTask()

        monkeypatch.setattr(background_task, "start", fake_start)

        result = await brain._handle_start_background_task({"text": "organize downloads folder"})
        assert "abc123" in result
        assert captured["text"] == "organize downloads folder"

    @pytest.mark.asyncio
    async def test_missing_text_returns_error_without_starting(self, monkeypatch, brain_config):
        brain = Brain(brain_config)
        called = {"n": 0}

        async def fake_start(*a, **kw):
            called["n"] += 1
            return _FakeTask()

        monkeypatch.setattr(background_task, "start", fake_start)
        result = await brain._handle_start_background_task({"text": "  "})
        assert result.startswith("Error")
        assert called["n"] == 0

    @pytest.mark.asyncio
    async def test_no_event_bus_returns_error_without_starting(self, monkeypatch, brain_config):
        brain = Brain(brain_config)
        import charlie.recovery as recovery
        monkeypatch.setattr(recovery, "_event_bus", None)
        called = {"n": 0}

        async def fake_start(*a, **kw):
            called["n"] += 1
            return _FakeTask()

        monkeypatch.setattr(background_task, "start", fake_start)
        result = await brain._handle_start_background_task({"text": "do something"})
        assert result.startswith("Error")
        assert called["n"] == 0

    @pytest.mark.asyncio
    async def test_priority_and_depends_on_forwarded(self, monkeypatch, brain_config):
        brain = Brain(brain_config)

        class _FakeBus:
            async def emit(self, *a, **kw):
                pass

        import charlie.recovery as recovery
        monkeypatch.setattr(recovery, "_event_bus", _FakeBus())

        captured = {}

        async def fake_start(config, event_bus, text, session_store=None, memory_store=None,
                              voice=None, priority=0, depends_on=None, on_result_stored=None):
            captured["priority"] = priority
            captured["depends_on"] = depends_on
            return _FakeTask()

        monkeypatch.setattr(background_task, "start", fake_start)
        await brain._handle_start_background_task(
            {"text": "step two", "priority": 5, "depends_on": ["earlier-id"]}
        )
        assert captured["priority"] == 5
        assert captured["depends_on"] == ["earlier-id"]


def _sse_tool_call_response(tool_name, arguments, call_id="1"):
    fn = {"name": tool_name, "arguments": json.dumps(arguments)}
    delta = {"tool_calls": [{"index": 0, "id": call_id, "function": fn}]}
    line = "data: " + json.dumps({"choices": [{"delta": delta}]})

    class MockResponse:
        def raise_for_status(self):
            pass

        async def aiter_lines(self):
            yield line
            yield "data: [DONE]"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

    return MockResponse()


class TestExecOneInterception:
    @pytest.mark.asyncio
    async def test_start_background_task_bypasses_registry_stub(self, monkeypatch, brain_config):
        """The registered start_background_task func is a stub that always errors --
        _exec_one must intercept before reaching it, proving the real handler ran."""
        brain = Brain(brain_config)
        args = {"text": "organize downloads folder"}

        calls = {"n": 0}

        def mock_stream(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return _sse_tool_call_response("start_background_task", args)
            delta = {"content": "Done."}
            line = "data: " + json.dumps({"choices": [{"delta": delta}]})

            class MockResponse:
                def raise_for_status(self):
                    pass

                async def aiter_lines(self):
                    yield line
                    yield "data: [DONE]"

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    pass

            return MockResponse()

        monkeypatch.setattr(brain.client, "stream", mock_stream)

        import charlie.recovery as recovery
        monkeypatch.setattr(recovery, "_event_bus", None)

        chunks = []
        async for chunk in brain.chat_stream("organize my downloads folder in the background", platform="web"):
            chunks.append(chunk)
        result = "".join(chunks)
        assert "must be intercepted" not in result
