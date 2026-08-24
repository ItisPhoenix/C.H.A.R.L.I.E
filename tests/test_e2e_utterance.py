"""End-to-end utterance tests: drive Brain.chat_stream() with a mocked LLM
client and assert on both the final user-visible output and real side
effects (mocked at the subprocess/tool-registry boundary, not the Brain
boundary). This is the class of test the codebase was missing entirely --
no prior test crossed the utterance-in/response-out boundary, which is
exactly how a dead path gate or fast-path ordering bug slipped through.
"""

import json
import subprocess

import pytest

from charlie.config import Config
from charlie.core import Brain
from charlie.research.citations import assign_citations
from charlie.research.models import EvidenceItem, ResearchMode, ResearchReport, SearchResult, SourceDocument


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
    async def test_plain_text_reply_no_tools(self, monkeypatch, brain_config):
        brain = Brain(brain_config)
        monkeypatch.setattr(brain.client, "stream", lambda *a, **kw: _sse_text_response("Hello there."))
        result = await _collect(brain, "say hello")
        assert "Hello there." in result

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
