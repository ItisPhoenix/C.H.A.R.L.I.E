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


class TestOutcomeFeedback:
    def test_unreliable_tool_gets_a_rule(self, brain_config):
        from collections import deque

        from charlie import telemetry
        telemetry._tool_calls = deque(maxlen=telemetry._MAX_SAMPLES)

        brain = Brain(brain_config)
        for _ in range(6):
            telemetry.record_tool_call("flaky_tool", success=False)
        brain._check_outcome_feedback()
        rules = brain.world_model.active_rules()
        assert any("flaky_tool" in text for _id, text in rules)

    def test_no_duplicate_rule_on_repeat_check(self, brain_config):
        from collections import deque

        from charlie import telemetry
        telemetry._tool_calls = deque(maxlen=telemetry._MAX_SAMPLES)

        brain = Brain(brain_config)
        for _ in range(6):
            telemetry.record_tool_call("flaky_tool", success=False)
        brain._check_outcome_feedback()
        brain._check_outcome_feedback()
        rules = [text for _id, text in brain.world_model.active_rules() if "flaky_tool" in text]
        assert len(rules) == 1

    def test_reliable_tool_gets_no_rule(self, brain_config):
        from collections import deque

        from charlie import telemetry
        telemetry._tool_calls = deque(maxlen=telemetry._MAX_SAMPLES)

        brain = Brain(brain_config)
        for _ in range(6):
            telemetry.record_tool_call("solid_tool", success=True)
        brain._check_outcome_feedback()
        rules = brain.world_model.active_rules()
        assert not any("solid_tool" in text for _id, text in rules)


class TestObservedPatterns:
    def test_repeated_sequence_gets_proposed_not_active(self, brain_config):
        brain = Brain(brain_config)
        for _ in range(3):
            brain.world_model.record_event("app_open", "I've opened chrome for you.")
            brain.world_model.record_event("app_open", "I've opened spotify for you.")
        brain._check_observed_patterns()
        rules = brain.world_model.list_rules(include_decayed=True)
        proposed = [r for r in rules if "chrome" in r[1] and "spotify" in r[1]]
        assert len(proposed) == 1
        assert proposed[0][4] == "proposed"
        assert proposed[0][0] not in [r[0] for r in brain.world_model.active_rules()]

    def test_no_pattern_proposes_nothing(self, brain_config):
        brain = Brain(brain_config)
        brain._check_observed_patterns()
        assert brain.world_model.list_rules(include_decayed=True) == []

    def test_no_duplicate_proposal_on_repeat_check(self, brain_config):
        brain = Brain(brain_config)
        for _ in range(3):
            brain.world_model.record_event("app_open", "I've opened chrome for you.")
            brain.world_model.record_event("app_open", "I've opened spotify for you.")
        brain._check_observed_patterns()
        brain._check_observed_patterns()
        rules = [r for r in brain.world_model.list_rules(include_decayed=True) if "chrome" in r[1]]
        assert len(rules) == 1


class TestReviewAndForgetCommands:
    @pytest.mark.asyncio
    async def test_review_lists_learned_rules(self, monkeypatch, brain_config):
        brain = Brain(brain_config)
        brain.world_model.add_rule("always reply short on Telegram", "teaching")

        def fail_if_called(*a, **kw):
            raise AssertionError("Review-rules fast-path must not call the LLM")

        monkeypatch.setattr(brain.client, "stream", fail_if_called)
        result = await _collect(brain, "what have you learned about me")
        assert "reply short on Telegram" in result

    @pytest.mark.asyncio
    async def test_review_empty_state(self, monkeypatch, brain_config):
        brain = Brain(brain_config)
        result = await _collect(brain, "what have you learned about me")
        assert "haven't learned" in result.lower()

    @pytest.mark.asyncio
    async def test_forget_deletes_matching_rule(self, monkeypatch, brain_config):
        brain = Brain(brain_config)
        rid = brain.world_model.add_rule("always reply short on Telegram", "teaching")
        result = await _collect(brain, "forget that Telegram")
        assert "forgot" in result.lower()
        assert rid not in [r[0] for r in brain.world_model.list_rules(include_decayed=True)]

    @pytest.mark.asyncio
    async def test_forget_no_match(self, monkeypatch, brain_config):
        brain = Brain(brain_config)
        result = await _collect(brain, "forget that Discord thing")
        assert "couldn't find" in result.lower()


class TestStandingInstructionWritesRule:
    @pytest.mark.asyncio
    async def test_always_phrasing_writes_rule_never_calls_llm(self, monkeypatch, brain_config):
        brain = Brain(brain_config)

        def fail_if_called(*a, **kw):
            raise AssertionError("Standing-instruction fast-path must not call the LLM")

        monkeypatch.setattr(brain.client, "stream", fail_if_called)
        result = await _collect(brain, "always reply short on Telegram")
        assert "remember" in result.lower()
        rules = brain.world_model.active_rules()
        assert any("reply short on Telegram" in text for _id, text in rules)


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
