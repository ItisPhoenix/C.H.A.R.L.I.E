"""Integration: world model writers fire from chat_stream, and the reader's
slice reaches the prompt sent to the LLM.
"""

import json

import pytest

from charlie.config import Config
from charlie.core import Brain


@pytest.fixture
def brain_config():
    return Config(
        llm_url="https://example.com/v1", llm_key="test-key", llm_model="dummy",
        iteration_budget_max=5, world_model_db_path=":memory:",
    )


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


async def _collect(brain, utterance):
    chunks = []
    async for chunk in brain.chat_stream(utterance, platform="web"):
        chunks.append(chunk)
    return "".join(chunks)


class TestMachineEventWriters:
    @pytest.mark.asyncio
    async def test_gated_tool_error_recorded(self, monkeypatch, brain_config):
        brain = Brain(brain_config)
        args = {"command": "rm -rf C:\\temp"}
        monkeypatch.setattr(brain.client, "stream", lambda *a, **kw: _sse_tool_call_response("shell_execute", args))
        monkeypatch.setattr("charlie.tools.registry.execute_tool", lambda name, args: "ok")
        await _collect(brain, "run rm -rf C:\\temp")
        errors = brain.world_model.recent_events(event_type="tool_error")
        assert len(errors) == 1
        assert "shell_execute" in errors[0][1]

    @pytest.mark.asyncio
    async def test_open_app_fast_path_recorded(self, monkeypatch, brain_config):
        import subprocess

        brain = Brain(brain_config)
        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setattr("charlie.router.is_process_running", lambda name: False)
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: type("P", (), {"pid": 1})())
        await _collect(brain, "open notepad")
        events = brain.world_model.recent_events(event_type="app_open")
        assert len(events) == 1


class TestWorldModelSliceInPrompt:
    @pytest.mark.asyncio
    async def test_open_thread_reaches_system_prompt(self, monkeypatch, brain_config):
        brain = Brain(brain_config)
        brain.world_model.open_thread("migrate the database", "sess1")

        captured = {}

        def mock_stream(*a, **kw):
            captured["messages"] = kw.get("json", {}).get("messages", [])

            class MockResponse:
                def raise_for_status(self):
                    pass

                async def aiter_lines(self):
                    yield "data: " + json.dumps({"choices": [{"delta": {"content": "ok"}}]})
                    yield "data: [DONE]"

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    pass

            return MockResponse()

        monkeypatch.setattr(brain.client, "stream", mock_stream)
        await _collect(brain, "how's it going")
        system_msg = next(m["content"] for m in captured["messages"] if m["role"] == "system")
        assert "migrate the database" in system_msg
