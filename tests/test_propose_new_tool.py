"""Tests for the tier-3 self-extension chat trigger: Brain._handle_propose_new_tool
and its _exec_one interception (charlie/core.py), plus the web-server staging
side (charlie/web_server.py:_stage_proposed_extension).
"""

import json

import pytest

from charlie.config import Config
from charlie.core import Brain

_VALID_CODE = '''
def double_it(n):
    """Doubles the given number and returns it as a string."""
    return str(int(n) * 2)
'''


@pytest.fixture
def brain_config():
    return Config(
        llm_url="https://example.com/v1", llm_key="test-key", llm_model="dummy",
        iteration_budget_max=5, world_model_db_path=":memory:",
    )


class TestHandleProposeNewTool:
    @pytest.mark.asyncio
    async def test_valid_code_emits_event_and_confirms(self, monkeypatch, brain_config):
        brain = Brain(brain_config)
        emitted = {}

        class _FakeBus:
            async def emit(self, event_type, payload):
                emitted["type"] = event_type
                emitted["payload"] = payload

        import charlie.recovery as recovery
        monkeypatch.setattr(recovery, "_event_bus", _FakeBus())

        result = await brain._handle_propose_new_tool(
            {"name": "double_it", "description": "doubles a number", "code": _VALID_CODE}
        )
        assert "double_it" in result
        assert "review" in result.lower()
        assert emitted["type"] == "extension_proposed"
        assert emitted["payload"]["name"] == "double_it"
        assert emitted["payload"]["raw_text"] == _VALID_CODE

    @pytest.mark.asyncio
    async def test_invalid_code_returns_error_no_event(self, monkeypatch, brain_config):
        brain = Brain(brain_config)
        emitted = {"called": False}

        class _FakeBus:
            async def emit(self, event_type, payload):
                emitted["called"] = True

        import charlie.recovery as recovery
        monkeypatch.setattr(recovery, "_event_bus", _FakeBus())

        result = await brain._handle_propose_new_tool(
            {"name": "double_it", "description": "x", "code": "def wrong_name(n):\n    return n"}
        )
        assert result.startswith("Error")
        assert emitted["called"] is False

    @pytest.mark.asyncio
    async def test_no_event_bus_does_not_crash(self, monkeypatch, brain_config):
        brain = Brain(brain_config)
        import charlie.recovery as recovery
        monkeypatch.setattr(recovery, "_event_bus", None)
        result = await brain._handle_propose_new_tool(
            {"name": "double_it", "description": "x", "code": _VALID_CODE}
        )
        assert "double_it" in result


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
    async def test_propose_new_tool_bypasses_registry_stub(self, monkeypatch, brain_config):
        """The registered propose_new_tool func is a stub that always errors --
        _exec_one must intercept before reaching it, proving the real handler ran."""
        brain = Brain(brain_config)
        args = {"name": "double_it", "description": "doubles", "code": _VALID_CODE}

        calls = {"n": 0}

        def mock_stream(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return _sse_tool_call_response("propose_new_tool", args)
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
        async for chunk in brain.chat_stream("learn to double numbers", platform="web"):
            chunks.append(chunk)
        result = "".join(chunks)
        assert "must be intercepted" not in result
