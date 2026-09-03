"""End-to-end utterance tests: drive Brain.chat_stream() with a mocked LLM
client and assert on both the final user-visible output and real side
effects (mocked at the subprocess/tool-registry boundary, not the Brain
boundary). This is the class of test the codebase was missing entirely --
no prior test crossed the utterance-in/response-out boundary, which is
exactly how a dead path gate or fast-path ordering bug slipped through.
"""

import asyncio
import json
import subprocess
from unittest.mock import Mock

import pytest

from charlie.config import Config
from charlie.core import Brain
from charlie.research.citations import assign_citations
from charlie.research.models import EvidenceItem, ResearchMode, ResearchReport, SearchResult, SourceDocument
from charlie.voice_diagnostics import VoiceDiagnostics


@pytest.fixture
def brain_config():
    return Config(llm_url="https://example.com/v1", llm_key="test-key", llm_model="dummy", iteration_budget_max=5)


def _sse_tool_call_response(tool_name: str, arguments: dict, call_id: str = "1"):
    delta = {
        "tool_calls": [
            {"index": 0, "id": call_id, "function": {"name": tool_name, "arguments": json.dumps(arguments)}}
        ]
    }
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


def _sse_text_response(text: str):
    line = "data: " + json.dumps({"choices": [{"delta": {"content": text}}]})

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


async def _collect(brain, utterance, **kwargs):
    chunks = []
    async for chunk in brain.chat_stream(utterance, platform="web", **kwargs):
        chunks.append(chunk)
    return "".join(chunks)


def _structured_research_report(query: str = "explicit research") -> ResearchReport:
    source = SourceDocument(
        source_id="S1",
        url="https://example.com/explicit",
        canonical_url="https://example.com/explicit",
        title="Explicit source",
        domain="example.com",
        content="Grounded explicit evidence.",
    )
    report = ResearchReport(
        query=query,
        mode=ResearchMode.QUICK,
        search_results=[SearchResult("Explicit source", source.url, "Grounded explicit evidence.")],
        sources=[source],
        evidence=[EvidenceItem("S1", "Grounded explicit evidence.", confidence=0.9)],
        confidence=0.9,
        stop_reason="evidence-sufficient",
    )
    report.citations = assign_citations(report.sources)
    return report


@pytest.mark.asyncio
async def test_active_voice_control_cancels_foreground_without_chat_stream():
    import main

    voice = Mock()
    voice.is_speaking.is_set.return_value = True
    brain = Mock()
    housekeeping_cancelled = Mock()
    release = asyncio.Event()

    async def active_turn():
        await release.wait()

    active_task = asyncio.create_task(active_turn())
    handled = await main._apply_voice_control(
        "stop",
        voice=voice,
        brain=brain,
        active_turn=True,
        active_operation_cancellable=True,
        active_process_task=active_task,
        cancel_housekeeping=housekeeping_cancelled,
    )

    assert handled is True
    voice.stop_tts.assert_called_once_with()
    brain.cancel_chat.assert_called_once_with()
    housekeeping_cancelled.assert_called_once_with()
    brain.chat_stream.assert_not_called()
    assert active_task.cancelled()


@pytest.mark.asyncio
async def test_inactive_voice_control_does_not_touch_background_work():
    import main

    voice = Mock()
    voice.is_speaking.is_set.return_value = False
    brain = Mock()
    housekeeping_cancelled = Mock()

    handled = await main._apply_voice_control(
        "stop",
        voice=voice,
        brain=brain,
        active_turn=False,
        active_operation_cancellable=True,
        active_process_task=None,
        cancel_housekeeping=housekeeping_cancelled,
    )

    assert handled is False
    voice.stop_tts.assert_not_called()
    brain.cancel_chat.assert_not_called()
    housekeeping_cancelled.assert_not_called()


@pytest.mark.asyncio
async def test_voice_control_does_not_cancel_non_cancellable_operation():
    import main

    voice = Mock()
    voice.is_speaking.is_set.return_value = False
    brain = Mock()
    release = asyncio.Event()

    async def active_operation():
        await release.wait()

    active_task = asyncio.create_task(active_operation())
    try:
        handled = await main._apply_voice_control(
            "cancel",
            voice=voice,
            brain=brain,
            active_turn=True,
            active_operation_cancellable=False,
            active_process_task=active_task,
            cancel_housekeeping=Mock(),
        )
        assert handled is True
        voice.stop_tts.assert_called_once_with()
        brain.cancel_chat.assert_not_called()
        assert active_task.done() is False
    finally:
        active_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await active_task


@pytest.mark.asyncio
async def test_owned_vision_stream_closes_on_cancellation_and_records_lifecycle(brain_config):
    from charlie.streaming import FollowupStreamState

    brain = Brain(brain_config, register_panic_hotkey=False)
    entered = asyncio.Event()
    release = asyncio.Event()
    response_closed = False
    context_exited = False

    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

        async def aiter_lines(self):
            entered.set()
            yield 'data: ' + json.dumps({"choices": [{"delta": {"content": "vision"}}]})
            await release.wait()

        async def aclose(self):
            nonlocal response_closed
            response_closed = True

    response = Response()

    class StreamContext:
        async def __aenter__(self):
            return response

        async def __aexit__(self, *_args):
            nonlocal context_exited
            context_exited = True
            await response.aclose()
            return False

    class Client:
        def stream(self, *_args, **_kwargs):
            return StreamContext()

    trace = VoiceDiagnostics(enabled=False, wav_enabled=False).new_trace("vision-cancel")
    state = FollowupStreamState()
    payload = {
        "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data"}}]}]
    }

    async def consume():
        async for _ in brain._stream_followup_once(
            Client(), "vision-model", payload, brain._chat_generation, state, trace
        ):
            pass

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(entered.wait(), timeout=1.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        await brain.close()

    assert response_closed is True
    assert context_exited is True
    from charlie.resource_locks import current_owner

    assert current_owner("vision_gpu") is None
    stages = [event["stage"] for event in trace.events()]
    assert stages[:4] == [
        "vision_request_start",
        "vision_resource_before_lease",
        "headers_received",
        "vision_first_response_or_headers",
    ]
    assert "first_token" in stages
    assert "client_cancel_requested" in stages
    assert "vision_http_cancel_requested" in stages
    assert "vision_task_cancelled" in stages
    assert "vision_stream_aclose_begin" in stages
    assert "vision_stream_aclose_complete" in stages
    assert "stream_closed" in stages
    evidence = next(event for event in trace.events() if event["stage"] == "server_generation_stop_evidence")
    assert evidence["fields"]["observable"] is False
    finish_reason = next(
        event for event in trace.events() if event["stage"] == "vision_server_finish_reason_if_observable"
    )
    assert finish_reason["fields"]["observable"] is False
    assert "vision_cleanup_complete" in stages
    assert "vision_task_done" in stages
    assert stages[-1] == "vision_request_end"


class TestFastPathsBypassLlm:
    @pytest.mark.asyncio
    async def test_time_date_query_never_calls_llm(self, monkeypatch, brain_config):
        brain = Brain(brain_config)

        def fail_if_called(*a, **kw):
            raise AssertionError("LLM should not be called for a fast-path time/date query")

        monkeypatch.setattr(brain.client, "stream", fail_if_called)
        result = await _collect(brain, "what's the date today")
        assert "today is" in result.lower()

    @pytest.mark.asyncio
    async def test_open_known_app_never_calls_llm(self, monkeypatch, brain_config):
        brain = Brain(brain_config)
        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setattr("charlie.router.is_process_running", lambda name: False)
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: type("P", (), {"pid": 1})())

        def fail_if_called(*a, **kw):
            raise AssertionError("LLM should not be called for a fast-path open-app query")

        monkeypatch.setattr(brain.client, "stream", fail_if_called)
        result = await _collect(brain, "open notepad")
        assert "opened" in result.lower()

    @pytest.mark.asyncio
    async def test_close_known_app_never_calls_llm(self, monkeypatch, brain_config):
        brain = Brain(brain_config)
        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setattr("charlie.router.is_process_running", lambda _: False)

        def mock_run(cmd, *a, **kw):
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr(subprocess, "run", mock_run)

        def fail_if_called(*a, **kw):
            raise AssertionError("LLM should not be called for a fast-path close-app query")

        monkeypatch.setattr(brain.client, "stream", fail_if_called)
        result = await _collect(brain, "close notepad")
        assert "closed" in result.lower()


class TestSecurityGatesFireForRealUtterances:
    @pytest.mark.asyncio
    async def test_file_read_env_gates_instead_of_silently_succeeding(self, monkeypatch, brain_config, tmp_path):
        """A real .env-path file_read must trigger the approval gate, not read silently."""
        brain = Brain(brain_config)
        env_path = str(tmp_path / ".env")
        args = {"path": env_path}
        monkeypatch.setattr(brain.client, "stream", lambda *a, **kw: _sse_tool_call_response("file_read", args))

        called = {"file_read": False}

        def mock_execute_tool(name, args):
            if name == "file_read":
                called["file_read"] = True
                return "SECRET_CONTENT"
            return "ok"

        monkeypatch.setattr("charlie.tools.registry.execute_tool", mock_execute_tool)
        result = await _collect(brain, "read my env file")
        assert called["file_read"] is False, "file_read ran without approval on a gated path"
        assert "SECRET_CONTENT" not in result

    @pytest.mark.asyncio
    async def test_shell_execute_rm_rf_gates_instead_of_running(self, monkeypatch, brain_config):
        brain = Brain(brain_config)
        args = {"command": "rm -rf C:\\temp"}
        monkeypatch.setattr(brain.client, "stream", lambda *a, **kw: _sse_tool_call_response("shell_execute", args))

        called = {"shell_execute": False}

        def mock_execute_tool(name, args):
            called["shell_execute"] = True
            return "ok"

        monkeypatch.setattr("charlie.tools.registry.execute_tool", mock_execute_tool)
        await _collect(brain, "run rm -rf C:\\temp")
        assert called["shell_execute"] is False, "gated shell command ran without approval"


class TestNormalRoundTrips:
    @pytest.mark.asyncio
    async def test_pure_screen_query_uses_local_vision_without_research_and_falls_back_after_timeout(self, monkeypatch):
        from charlie import core as core_module
        from charlie.tools import set_pending_vision_image
        from charlie.turn_contracts import TurnRequest

        config = Config(
            llm_url="http://cloud.example/v1",
            llm_key="test-key",
            llm_model="chat-model",
            vision_enabled=True,
            vision_llm_url="http://local-vision/v1",
            vision_llm_key="vision-key",
            vision_llm_model="vision-model",
            desktop_control_enabled=True,
            world_model_db_path=":memory:",
            memory_graph_db=":memory:",
        )
        decisions = []
        brain = Brain(config, register_panic_hotkey=False, on_intent_decision=decisions.append)
        class FakeVisionClient:
            async def aclose(self):
                pass

        vision_client = FakeVisionClient()
        brain._vision_client = vision_client
        brain._vision_model = "vision-model"
        executed_tools = []
        followups = []

        async def empty_initial_completion(payload, generation):
            return "", []

        async def fake_followup(client, model, payload, generation, state):
            followups.append((client, model, payload))
            if len(followups) == 1:
                assert client is vision_client
                state.accumulated = "Local VLM sees the Settings window."
                state.tc_by_index = {
                    0: {"id": "unexpected-followup", "name": "desktop_observe", "arguments": "{}"}
                }
            else:
                assert client is brain.client
                raise TimeoutError("optional synthesis timed out")
            if False:
                yield ""

        def fake_execute_tool(name, arguments):
            executed_tools.append(name)
            if name == "desktop_screenshot":
                set_pending_vision_image("data:image/png;base64,screen")
            return "Screen observation marks."

        monkeypatch.setattr(brain, "_stream_completion", empty_initial_completion)
        monkeypatch.setattr(brain, "_stream_followup_once", fake_followup)
        monkeypatch.setattr("charlie.tools.registry.execute_tool", fake_execute_tool)
        monkeypatch.setattr(
            core_module,
            "route_research",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("pure screen query entered research")),
        )
        monkeypatch.setattr(
            brain,
            "_run_research_for_turn",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("research execution was attempted")),
        )

        request = TurnRequest.allocate("What do you see on my screen?", "session-screen", "web")
        try:
            result = await _collect(brain, request.input, turn_request=request)
        finally:
            await brain.close()

        assert result == "Local VLM sees the Settings window."
        assert executed_tools.count("desktop_screenshot") == 1
        assert "web_search" not in executed_tools
        assert "web_research" not in executed_tools
        assert len(followups) == 2
        assert decisions
        assert decisions[0].intent == "desktop"
        assert decisions[0].turn_id == request.turn_id

    @pytest.mark.asyncio
    async def test_contextual_screen_fragment_stays_on_local_desktop_path(self, monkeypatch):
        from charlie import core as core_module

        config = Config(
            llm_url="http://cloud.example/v1",
            llm_key="test-key",
            llm_model="chat-model",
            vision_enabled=False,
            desktop_control_enabled=True,
            memory_graph_db=":memory:",
            world_model_db_path=":memory:",
        )
        brain = Brain(config, register_panic_hotkey=False)
        brain.history = [{"role": "user", "content": "What do you see on my screen?"}]
        research_calls = []
        executed_tools = []

        async def local_answer(payload, generation):
            return "The screen shows a settings panel.", []

        def execute_tool(name, arguments):
            executed_tools.append(name)
            return "Foreground screen marks."

        monkeypatch.setattr(brain, "_stream_completion", local_answer)
        monkeypatch.setattr("charlie.tools.registry.execute_tool", execute_tool)
        monkeypatch.setattr(
            core_module,
            "route_research",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("screen fragment entered research")),
        )

        async def fail_research(*args, **kwargs):
            research_calls.append(args)
            raise AssertionError("screen fragment research execution was attempted")

        monkeypatch.setattr(brain, "_run_research_for_turn", fail_research)
        try:
            result = await _collect(brain, "what about my screen?", skip_pre_search=False)
        finally:
            await brain.close()

        assert result == "The screen shows a settings panel."
        assert executed_tools == ["desktop_observe"]
        assert research_calls == []

    @pytest.mark.asyncio
    async def test_unavailable_local_vlm_returns_truthful_screen_failure(self, monkeypatch):
        from charlie.tools import set_pending_vision_image

        config = Config(
            llm_url="http://cloud.example/v1",
            llm_key="test-key",
            llm_model="chat-model",
            native_tool_calling=True,
            vision_enabled=True,
            vision_llm_url="",
            vision_llm_key="",
            desktop_control_enabled=True,
            memory_graph_db=":memory:",
            world_model_db_path=":memory:",
        )
        brain = Brain(config, register_panic_hotkey=False)

        async def empty_initial_completion(payload, generation):
            return "", []

        def execute_tool(name, arguments):
            if name == "desktop_screenshot":
                set_pending_vision_image("data:image/png;base64,screen")
            return "Screen observation marks."

        set_pending_vision_image(None)
        monkeypatch.setattr(brain, "_stream_completion", empty_initial_completion)
        monkeypatch.setattr("charlie.tools.registry.execute_tool", execute_tool)
        try:
            result = await _collect(brain, "What do you see on my screen?")
        finally:
            set_pending_vision_image(None)
            await brain.close()

        assert result == "Local vision model is unavailable; I couldn't inspect the screen."

    @pytest.mark.asyncio
    async def test_empty_local_vlm_response_is_not_presented_as_an_answer(self, monkeypatch):
        from charlie.tools import set_pending_vision_image

        config = Config(
            llm_url="http://cloud.example/v1",
            llm_key="test-key",
            llm_model="chat-model",
            native_tool_calling=True,
            vision_enabled=True,
            vision_llm_url="http://local-vision/v1",
            vision_llm_key="vision-key",
            vision_llm_model="vision-model",
            desktop_control_enabled=True,
            memory_graph_db=":memory:",
            world_model_db_path=":memory:",
        )
        brain = Brain(config, register_panic_hotkey=False)

        class EmptyVisionClient:
            async def aclose(self):
                pass

        brain._vision_client = EmptyVisionClient()
        brain._vision_model = "vision-model"
        calls = []

        async def empty_initial_completion(payload, generation):
            return "", []

        async def empty_vision_followup(client, model, payload, generation, state):
            calls.append((client, model))
            if False:
                yield ""

        def execute_tool(name, arguments):
            if name == "desktop_screenshot":
                set_pending_vision_image("data:image/png;base64,screen")
            return "Screen observation marks."

        set_pending_vision_image(None)
        monkeypatch.setattr(brain, "_stream_completion", empty_initial_completion)
        monkeypatch.setattr(brain, "_stream_followup_once", empty_vision_followup)
        monkeypatch.setattr("charlie.tools.registry.execute_tool", execute_tool)
        try:
            result = await _collect(brain, "What do you see on my screen?")
        finally:
            set_pending_vision_image(None)
            await brain.close()

        assert result == "Local vision model returned no usable description."
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_plain_text_reply_no_tools(self, monkeypatch, brain_config):
        brain = Brain(brain_config)
        monkeypatch.setattr(brain.client, "stream", lambda *a, **kw: _sse_text_response("Hello there."))
        result = await _collect(brain, "say hello")
        assert "Hello there." in result

    @pytest.mark.asyncio
    async def test_model_text_alone_cannot_claim_unknown_app_action_succeeded(self, monkeypatch, brain_config):
        brain = Brain(brain_config, register_panic_hotkey=False)

        async def fabricated_completion(payload, generation):
            return "I've closed the mystery utility.", []

        monkeypatch.setattr(brain, "_stream_completion", fabricated_completion)
        result = await _collect(brain, "close the mystery utility", skip_pre_search=True)

        assert result == "I couldn't verify that requested app action was executed."

    @pytest.mark.asyncio
    async def test_failed_desktop_action_cannot_be_rewritten_as_success(self, monkeypatch, brain_config):
        brain = Brain(brain_config, register_panic_hotkey=False)

        async def initial_action(payload, generation):
            return "", [{
                "id": "close-1",
                "name": "desktop_window",
                "arguments": {"window": "mystery utility", "action": "close"},
            }]

        async def fabricated_followup(client, model, payload, generation, state):
            state.accumulated = "I've closed the mystery utility."
            if False:
                yield ""

        monkeypatch.setattr(brain, "_stream_completion", initial_action)
        monkeypatch.setattr(brain, "_stream_followup_once", fabricated_followup)
        monkeypatch.setattr(
            "charlie.tools.registry.execute_tool",
            lambda name, arguments: "Error: no window matching 'mystery utility'.",
        )

        result = await _collect(brain, "close the mystery utility", skip_pre_search=True)

        assert result == "I couldn't complete that requested app action."

    @pytest.mark.asyncio
    async def test_tool_call_round_trip_produces_final_answer(self, monkeypatch, brain_config):
        brain = Brain(brain_config)
        calls = {"n": 0}

        def mock_stream(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return _sse_tool_call_response("web_search", {"query": "weather"})
            return _sse_text_response("It is sunny.")

        monkeypatch.setattr(brain.client, "stream", mock_stream)
        search_result = "Weather: sunny, 75F, more text to pass the relevance-length gate."
        monkeypatch.setattr("charlie.tools.registry.execute_tool", lambda name, args: search_result)
        result = await _collect(brain, "what's the weather")
        assert "sunny" in result.lower()
        assert calls["n"] == 2

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool_name", ["web_research", "web_search"])
    async def test_explicit_structured_research_publishes_final_followup_once(
        self, monkeypatch, brain_config, tool_name
    ):
        brain = Brain(brain_config)
        calls = {"llm": 0, "research": 0}
        report = _structured_research_report("explicit research")
        published = []
        final_answer = "Final synthesized answer from explicit research."

        def mock_stream(*args, **kwargs):
            calls["llm"] += 1
            if calls["llm"] == 1:
                return _sse_tool_call_response(tool_name, {"query": "explicit research"})
            return _sse_text_response(final_answer)

        def run_research(name, arguments):
            calls["research"] += 1
            assert name == tool_name
            return report

        monkeypatch.setattr(brain.client, "stream", mock_stream)
        monkeypatch.setattr("charlie.tools._run_research_report", run_research)
        brain.on_research_result = lambda item, *, session_id, task_id=None: published.append(
            (item, session_id, task_id)
        )

        result = await _collect(brain, "explicitly research this", skip_pre_search=True, session_id="session-explicit")

        assert final_answer in result
        assert calls["research"] == 1
        assert len(published) == 1
        assert published[0][0] is report
        assert published[0][0].answer == final_answer
        assert published[0][1] == "session-explicit"
        assert published[0][2]

    @pytest.mark.asyncio
    async def test_failed_research_followup_does_not_publish_error_as_synthesis(self, monkeypatch, brain_config):
        brain = Brain(brain_config)
        report = _structured_research_report()
        published = []

        async def initial_tool_call(*args, **kwargs):
            return "", [{"id": "call-1", "name": "web_research", "arguments": {"query": "explicit research"}}]

        monkeypatch.setattr(brain, "_stream_completion", initial_tool_call)
        monkeypatch.setattr(
            "charlie.tools._run_research_report",
            lambda name, arguments: report,
        )

        async def failed_followup(*args, **kwargs):
            raise RuntimeError("follow-up unavailable")
            yield  # pragma: no cover

        monkeypatch.setattr(brain, "_stream_followup_once", failed_followup)
        brain.on_research_result = lambda item, *, session_id, task_id=None: published.append(item)

        result = await _collect(brain, "explicitly research this", skip_pre_search=True, session_id="session-failed")

        assert "follow-up model call failed" in result
        assert published == []
        assert report.answer == ""

    @pytest.mark.asyncio
    async def test_automatic_pre_search_publishes_one_report_after_final_answer(self, monkeypatch, brain_config):
        brain = Brain(brain_config)
        report = _structured_research_report("fresh web question")
        research_calls = []
        prompt_payloads = []
        published = []

        async def run_research(query, session_id):
            research_calls.append((query, session_id))
            return report

        async def final_completion(payload, generation):
            prompt_payloads.append(payload)
            return "Final answer grounded in fresh evidence.", []

        monkeypatch.setattr(brain, "_run_research", run_research)
        monkeypatch.setattr(brain, "_stream_completion", final_completion)
        brain.on_research_result = lambda item, *, session_id, task_id=None: published.append(
            (item, session_id, task_id)
        )

        result = await _collect(brain, "fresh web question",)

        assert "Final answer grounded" in result
        assert research_calls == [("fresh web question", "default")]
        assert len(prompt_payloads) == 1
        assert "UNTRUSTED SOURCE CONTENT" in json.dumps(prompt_payloads[0])
        assert len(published) == 1
        assert published[0][0] is report
        assert report.answer == "Final answer grounded in fresh evidence."

    @pytest.mark.asyncio
    async def test_insufficient_research_cannot_be_replaced_by_model_prior(self, monkeypatch, brain_config):
        brain = Brain(brain_config)
        report = ResearchReport(
            query="QZ-4819 protocol",
            mode=ResearchMode.STANDARD,
            stop_reason="insufficient-evidence",
            errors=["No reliable extracted evidence"],
        )

        async def run_research(query, session_id):
            return report

        async def fabricated_completion(payload, generation):
            serialized = json.dumps(payload)
            assert "insufficient evidence" in serialized.lower()
            return "QZ-4819 was verified in 2026. [S1]", []

        monkeypatch.setattr(brain, "_run_research", run_research)
        monkeypatch.setattr(brain, "_stream_completion", fabricated_completion)

        result = await _collect(brain, "QZ-4819 protocol")

        assert result == "I couldn't find sufficient reliable evidence to answer that research question."
        assert report.answer == result

    @pytest.mark.asyncio
    async def test_desktop_click_sequence_round_trip(self, monkeypatch, brain_config):
        brain = Brain(brain_config)
        calls = {"n": 0}

        def mock_stream(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return _sse_tool_call_response("desktop_click", {"mark_id": "3"})
            return _sse_text_response("Clicked it.")

        monkeypatch.setattr(brain.client, "stream", mock_stream)
        monkeypatch.setattr("charlie.tools.registry.execute_tool", lambda name, args: "Clicked mark 3.")
        result = await _collect(brain, "click the submit button")
        assert "clicked it" in result.lower()
        assert calls["n"] == 2

    @pytest.mark.asyncio
    async def test_vector_memory_recall_round_trip(self, monkeypatch, brain_config):
        brain = Brain(brain_config)
        calls = {"n": 0}

        def mock_stream(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return _sse_tool_call_response("vector_memory", {"action": "recall", "content": "birthday"})
            return _sse_text_response("Your birthday is in May.")

        monkeypatch.setattr(brain.client, "stream", mock_stream)
        recall_result = "Recalled memory: user's birthday is in May, mentioned three weeks ago."
        monkeypatch.setattr("charlie.tools.registry.execute_tool", lambda name, args: recall_result)
        result = await _collect(brain, "when's my birthday again")
        assert "may" in result.lower()
        assert calls["n"] == 2

    @pytest.mark.asyncio
    async def test_explicit_memory_survives_new_brain_session_without_dumping_irrelevant_facts(
        self, monkeypatch, brain_config
    ):
        from charlie import tools as tools_module
        from charlie.memory_graph import MemoryGraph
        from charlie.memory_service import MemoryService

        class PersistentStore:
            is_available = True

            def __init__(self):
                self.documents = []
                self.queries = []

            def add_memory(self, text, source, session_id, auto_extract=True):
                self.documents.append((text, source, session_id, auto_extract))
                return 1

            def search(self, query, n_results=3, threshold=None):
                self.queries.append(query)
                if "color" not in query.lower():
                    return []
                return [{"text": self.documents[0][0], "distance": 0.1, "metadata": {}}]

            def format_for_prompt(self, results):
                return "[Relevant memories from past conversations:]\n- " + results[0]["text"] if results else ""

            def get_stats(self):
                return {"available": True, "document_count": len(self.documents)}

        store = PersistentStore()
        service = MemoryService(graph=MemoryGraph(":memory:"), memory_store=store)
        monkeypatch.setattr(tools_module, "_memory_service", service)

        session_a = Brain(
            brain_config,
            memory_graph=service._get_graph(),
            memory_service=service,
            register_panic_hotkey=False,
        )
        try:
            assert await _collect(session_a, "Remember that my test color is blue.") == (
                "Remembered: my test color is blue"
            )
        finally:
            await session_a.close()

        session_b = Brain(
            brain_config,
            memory_graph=service._get_graph(),
            memory_service=service,
            register_panic_hotkey=False,
        )
        payloads = []

        async def answer_from_memory(payload, generation):
            payloads.append(payload)
            return "Your test color is blue.", []

        monkeypatch.setattr(session_b, "_stream_completion", answer_from_memory)
        try:
            result = await _collect(session_b, "What is my test color?", skip_pre_search=True, session_id="session-b")
            assert result == "Your test color is blue."
            assert store.queries == ["What is my test color?"]
            assert len(store.documents) == 1
            assert "my test color is blue" in str(payloads[0])

            payloads.clear()
            await _collect(session_b, "What is the capital of France?", skip_pre_search=True, session_id="session-c")
            assert "my test color is blue" not in str(payloads[0])
        finally:
            await session_b.close()
            service._get_graph().close()


class TestRouterClassifierFallback:
    @pytest.mark.asyncio
    async def test_unmatched_phrasing_resolves_via_classifier(self, monkeypatch, brain_config):
        """A phrasing the regex table misses ('fire up spotify') still resolves to the
        right tool via the classifier fallback, without a full LLM tool-calling round trip.
        """
        brain_config.router_classifier_enabled = True
        brain = Brain(brain_config)
        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setattr("charlie.router.is_process_running", lambda name: False)
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: type("P", (), {"pid": 1})())

        def fail_if_called(*a, **kw):
            raise AssertionError("Normal tool-calling LLM stream should not fire when classifier resolves it")

        monkeypatch.setattr(brain.client, "stream", fail_if_called)

        class _FakeClassifierResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": json.dumps({"intent": "open_app", "app": "spotify"})}}]}

        class _FakeClassifierClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **kw):
                return _FakeClassifierResponse()

        monkeypatch.setattr("charlie.core.httpx.AsyncClient", lambda *a, **kw: _FakeClassifierClient())
        result = await _collect(brain, "fire up spotify")
        assert "opened" in result.lower()
