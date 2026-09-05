"""Focused tests for the interactive capability-result normalization boundary."""

import asyncio
import inspect

import pytest

import charlie.core as core
from charlie.config import Config
from charlie.core import (
    Brain,
    _normalize_tool_result,
    _operation_succeeded,
    _RepeatToolCallGuard,
    _result_envelope_to_model_text,
)
from charlie.presentation import PresentationContext, PresentationKind, PresentationResolver
from charlie.research.models import ResearchMode, ResearchReport
from charlie.tools import ToolExecutionResult
from charlie.turn_contracts import ResultEnvelope, ResultStatus


def _identity() -> dict[str, str]:
    return {
        "turn_id": "turn-envelope",
        "task_id": "task-envelope",
        "session_id": "session-envelope",
    }


def test_result_status_vocabulary_is_explicit():
    assert {status.value for status in ResultStatus} == {
        "completed",
        "failed",
        "partially_completed",
        "unverified",
        "cancelled",
        "blocked",
    }


def test_successful_generic_result_normalizes_to_completed():
    envelope = _normalize_tool_result(
        "file_read",
        "The requested file was read successfully.",
        request="read the file",
        **_identity(),
    )

    assert isinstance(envelope, ResultEnvelope)
    assert envelope.status == ResultStatus.COMPLETED
    assert envelope.capability == "file"
    assert envelope.operation == "file.system.read"


def test_failed_generic_result_normalizes_to_failed_with_error():
    envelope = _normalize_tool_result(
        "file_read",
        "Error: file was not found.",
        request="read the missing file",
        **_identity(),
    )

    assert envelope.status == ResultStatus.FAILED
    assert envelope.errors == ["Error: file was not found."]


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (ResultStatus.FAILED, "Error: Tool 'file_read' timed out after 10s"),
        (ResultStatus.CANCELLED, "Error: Command declined by user."),
        (ResultStatus.BLOCKED, "Error: Command blocked by security policy."),
    ],
)
def test_non_success_operation_outcomes_are_explicit(status, message):
    envelope = _normalize_tool_result(
        "file_read",
        message,
        request="perform the operation",
        status=status,
        errors=[message],
        **_identity(),
    )

    assert envelope.status == status
    assert envelope.status != ResultStatus.COMPLETED
    assert envelope.errors == [message]


def test_normalization_preserves_turn_task_and_session_identity():
    envelope = _normalize_tool_result(
        "web_search",
        "A useful search result with enough detail for the model.",
        request="find current information",
        **_identity(),
    )

    assert envelope.turn_id == "turn-envelope"
    assert envelope.task_id == "task-envelope"
    assert envelope.session_id == "session-envelope"


def test_structured_tool_result_data_survives_normalization():
    structured_data = {"value": 42, "unit": "items"}
    envelope = _normalize_tool_result(
        "system_diagnostics",
        ToolExecutionResult("The diagnostic value is 42 items.", structured_data, "metric"),
        request="check the metric",
        **_identity(),
    )

    assert envelope.result == "The diagnostic value is 42 items."
    assert envelope.data["structured_data"] is structured_data
    assert envelope.data["result_kind"] == "metric"
    assert envelope.artifacts == [structured_data]


def test_research_report_remains_a_domain_artifact():
    report = ResearchReport(query="current topic", mode=ResearchMode.QUICK)
    envelope = _normalize_tool_result(
        "web_research",
        ToolExecutionResult(report.legacy_text(), report, "research_report"),
        request="research current topic",
        **_identity(),
    )

    assert envelope.result == report.legacy_text()
    assert envelope.artifacts == [report]
    assert envelope.evidence == report.evidence
    assert envelope.data["research_report"] is report
    assert envelope.data["result_kind"] == "research_report"


def test_model_facing_text_is_the_legacy_tool_result_not_the_envelope():
    model_text = "The adapter returned this exact text for the follow-up model."
    envelope = _normalize_tool_result(
        "file_read",
        model_text,
        request="read it",
        **_identity(),
    )

    assert envelope.result == model_text
    assert str(envelope) != model_text
    assert _result_envelope_to_model_text(envelope) == model_text


def test_canonical_tool_loop_has_one_structured_operation_result_type():
    source = inspect.getsource(core.Brain._chat_stream_impl)

    assert "async def _exec_one(call: Dict[str, Any]) -> ResultEnvelope:" in source
    assert "results_map: Dict[int, ResultEnvelope]" in source
    assert "ResultEnvelope | str" not in source
    assert "isinstance(result, ResultEnvelope)" not in source


def test_telemetry_success_predicate_uses_status_not_display_text():
    envelope = ResultEnvelope(
        status=ResultStatus.COMPLETED,
        result="Error: this is display text from a legacy adapter.",
    )

    assert _operation_succeeded(envelope) is True


def test_repeat_guard_consumes_structured_failure_state():
    guard = _RepeatToolCallGuard()
    failed = _normalize_tool_result(
        "shell_execute",
        "The adapter used an error-shaped display message.",
        request="run it",
        status=ResultStatus.FAILED,
        errors=["The adapter used an error-shaped display message."],
        **_identity(),
    )

    guard.record_result("shell_execute({})", failed)

    assert guard.before("shell_execute({})") is True


def test_presentation_resolver_keeps_envelope_correlation():
    envelope = ResultEnvelope(
        request="read the file",
        turn_id="turn-envelope",
        task_id="task-envelope",
        session_id="session-envelope",
        capability="file",
        operation="file.system.read",
        result="The file was read.",
    )

    intent = PresentationResolver().resolve(envelope, PresentationContext())

    assert intent.kind == PresentationKind.CAPTION
    assert intent.turn_id == envelope.turn_id
    assert intent.task_id == envelope.task_id
    assert intent.session_id == envelope.session_id


def _mock_tool_stream(call_count: list[int]):
    def mock_stream(*args, **kwargs):
        call_count[0] += 1

        class MockResponse:
            def raise_for_status(self):
                pass

            async def aiter_lines(self):
                if call_count[0] == 1:
                    yield (
                        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"1",'
                        '"function":{"name":"file_read","arguments":"{\\"path\\":\\"notes.txt\\"}"}'
                        '}]}}]}'
                    )
                else:
                    yield 'data: {"choices":[{"delta":{"content":"Finished reading it."}}]}'
                yield "data: [DONE]"

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        return MockResponse()

    return mock_stream


@pytest.mark.asyncio
async def test_generic_tool_loop_emits_one_correlated_envelope_and_preserves_text(monkeypatch):
    brain = Brain(Config(llm_url="http://localhost:11434", llm_key="no-key", llm_model="dummy"))
    call_count = [0]
    envelopes = []
    brain.on_operation_result = lambda name, envelope: envelopes.append((name, envelope))
    monkeypatch.setattr(brain.client, "stream", _mock_tool_stream(call_count))
    monkeypatch.setattr(
        "charlie.tools.registry.execute_tool",
        lambda name, args: "The requested file contents were returned successfully for the model.",
    )

    chunks = [chunk async for chunk in brain.chat_stream("read notes.txt")]

    assert chunks == ["Finished reading it."]
    assert len(envelopes) == 1
    name, envelope = envelopes[0]
    assert name == "file_read"
    assert envelope.status == ResultStatus.COMPLETED
    assert envelope.result == "The requested file contents were returned successfully for the model."


@pytest.mark.asyncio
async def test_persistence_receives_canonical_envelope(monkeypatch):
    brain = Brain(Config(llm_url="http://localhost:11434", llm_key="no-key", llm_model="dummy"))
    persisted = []

    class CaptureStore:
        def get_session_messages(self, _session_id, limit):
            return []

        def append_tool(self, **kwargs):
            persisted.append(kwargs)

    brain.session_store = CaptureStore()
    monkeypatch.setattr(brain.client, "stream", _mock_tool_stream([0]))
    monkeypatch.setattr("charlie.tools.registry.execute_tool", lambda _name, _args: "read success")

    chunks = [chunk async for chunk in brain.chat_stream("read notes.txt", platform="text")]

    assert chunks == ["Finished reading it."]
    assert len(persisted) == 1
    assert isinstance(persisted[0]["result"], ResultEnvelope)
    assert persisted[0]["result"].result == "read success"


@pytest.mark.asyncio
async def test_deterministic_fastpath_unavailable_result_is_unverified_envelope(monkeypatch):
    from charlie.autonomy import Requirement
    from charlie.fastpaths import FastPathMatch, FastPathResult

    brain = Brain(Config(llm_url="http://localhost:11434", llm_key="no-key", llm_model="dummy"))
    envelopes = []
    brain.on_operation_result = lambda _name, envelope: envelopes.append(envelope)
    match = FastPathMatch(
        intent="cpu_temperature",
        semantic_op_id="system.metrics.read",
        tool_name="system_diagnostics",
        arguments={"check": "cpu_temperature"},
        target_domain="system",
    )
    monkeypatch.setattr("charlie.fastpaths.match_fast_path", lambda _query: match)
    monkeypatch.setattr(
        "charlie.fastpaths.execute_fast_path",
        lambda _match: FastPathResult(
            "CPU temperature is unavailable on this system.",
            {"available": False, "reason": "unsupported_or_unavailable"},
        ),
    )
    monkeypatch.setattr(core, "autonomy_evaluate", lambda *_args: (Requirement.ALLOW, "safe", ""))

    chunks = [
        chunk
        async for chunk in brain.chat_stream(
            "show current CPU temperature",
            platform="text",
            skip_pre_search=True,
            session_id="session-fastpath",
            task_id="task-fastpath",
            turn_id="turn-fastpath",
        )
    ]

    assert "unavailable" in "".join(chunks).lower()
    assert len(envelopes) == 1
    assert envelopes[0].status == ResultStatus.UNVERIFIED
    assert envelopes[0].data["available"] is False
    assert (envelopes[0].turn_id, envelopes[0].task_id, envelopes[0].session_id) == (
        "turn-fastpath",
        "task-fastpath",
        "session-fastpath",
    )


@pytest.mark.asyncio
async def test_timeout_and_exception_reach_callback_as_failed_envelopes(monkeypatch):
    for failure in (asyncio.TimeoutError(), RuntimeError("backend exploded")):
        brain = Brain(Config(llm_url="http://localhost:11434", llm_key="no-key", llm_model="dummy"))
        envelopes = []
        brain.on_operation_result = lambda _name, envelope: envelopes.append(envelope)
        monkeypatch.setattr(brain.client, "stream", _mock_tool_stream([0]))
        monkeypatch.setattr(core, "_tool_timeout", lambda *_args: 0.01)

        def execute(_name, _args):
            raise failure

        monkeypatch.setattr("charlie.tools.registry.execute_tool", execute)
        chunks = [chunk async for chunk in brain.chat_stream("read notes.txt")]

        assert chunks == ["Finished reading it."]
        assert len(envelopes) == 1
        assert isinstance(envelopes[0], ResultEnvelope)
        assert envelopes[0].status == ResultStatus.FAILED
        assert envelopes[0].data["failure_kind"] == (
            "timeout" if isinstance(failure, asyncio.TimeoutError) else "exception"
        )


@pytest.mark.asyncio
async def test_repeated_call_suppression_emits_blocked_envelopes_and_preserves_identity(monkeypatch):
    brain = Brain(Config(llm_url="http://localhost:11434", llm_key="no-key", llm_model="dummy"))
    envelopes = []
    brain.on_operation_result = lambda _name, envelope: envelopes.append(envelope)

    def repeated_stream(*_args, **_kwargs):
        class MockResponse:
            def raise_for_status(self):
                pass

            async def aiter_lines(self):
                yield (
                    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"1",'
                    '"function":{"name":"file_read","arguments":"{\\"path\\":\\"notes.txt\\"}"}'
                    '}]}}]}'
                )
                yield "data: [DONE]"

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        return MockResponse()

    monkeypatch.setattr(brain.client, "stream", repeated_stream)
    monkeypatch.setattr("charlie.tools.registry.execute_tool", lambda _name, _args: "Error: read failed")

    chunks = [
        chunk
        async for chunk in brain.chat_stream(
            "read notes.txt",
            platform="text",
            session_id="session-repeat",
            task_id="task-repeat",
            turn_id="turn-repeat",
        )
    ]

    assert any("couldn't complete" in chunk.lower() for chunk in chunks)
    assert len(envelopes) == 3
    assert all(isinstance(envelope, ResultEnvelope) for envelope in envelopes)
    assert envelopes[1].status == ResultStatus.BLOCKED
    assert envelopes[1].data == {"suppressed": True, "repeat_guard": "identical_call"}
    assert all(
        (envelope.turn_id, envelope.task_id, envelope.session_id)
        == ("turn-repeat", "task-repeat", "session-repeat")
        for envelope in envelopes
    )


@pytest.mark.asyncio
async def test_parallel_and_sequential_tool_results_are_envelopes(monkeypatch):
    brain = Brain(Config(llm_url="http://localhost:11434", llm_key="no-key", llm_model="dummy"))
    envelopes = []
    brain.on_operation_result = lambda name, envelope: envelopes.append((name, envelope))
    stream_calls = [0]

    def mixed_stream(*_args, **_kwargs):
        stream_calls[0] += 1

        class MockResponse:
            def raise_for_status(self):
                pass

            async def aiter_lines(self):
                if stream_calls[0] == 1:
                    yield (
                        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"1",'
                        '"function":{"name":"file_read","arguments":"{\\"path\\":\\"notes.txt\\"}"}},'
                        '{"index":1,"id":"2","function":{"name":"shell_execute",'
                        '"arguments":"{\\"command\\":\\"echo ok\\"}"}}]}}]}'
                    )
                else:
                    yield 'data: {"choices":[{"delta":{"content":"Finished both operations."}}]}'
                yield "data: [DONE]"

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        return MockResponse()

    monkeypatch.setattr(brain.client, "stream", mixed_stream)
    monkeypatch.setattr(core, "autonomy_evaluate", lambda *_args, **_kwargs: (core.Requirement.ALLOW, "safe", ""))
    monkeypatch.setattr("charlie.tools.registry.execute_tool", lambda name, _args: f"{name} completed")

    chunks = [chunk async for chunk in brain.chat_stream("read notes and run echo", platform="text")]

    assert chunks == ["Finished both operations."]
    assert [name for name, _ in envelopes] == ["file_read", "shell_execute"]
    assert all(isinstance(envelope, ResultEnvelope) for _, envelope in envelopes)


@pytest.mark.asyncio
async def test_conversational_prose_does_not_emit_an_operation_envelope(monkeypatch):
    brain = Brain(Config(llm_url="http://localhost:11434", llm_key="no-key", llm_model="dummy"))
    envelopes = []
    brain.on_operation_result = lambda name, envelope: envelopes.append(envelope)

    def mock_stream(*args, **kwargs):
        class MockResponse:
            def raise_for_status(self):
                pass

            async def aiter_lines(self):
                yield 'data: {"choices":[{"delta":{"content":"Just a conversational answer."}}]}'
                yield "data: [DONE]"

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        return MockResponse()

    monkeypatch.setattr(brain.client, "stream", mock_stream)

    chunks = [chunk async for chunk in brain.chat_stream("say hello")]

    assert chunks == ["Just a conversational answer."]
    assert envelopes == []


def test_legacy_execution_outcome_alias_remains_compatible():
    from charlie.presentation import ExecutionOutcome

    assert ExecutionOutcome is ResultEnvelope
