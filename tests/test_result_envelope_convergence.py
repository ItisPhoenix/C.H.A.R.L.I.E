"""Focused tests for the interactive capability-result normalization boundary."""

import asyncio
import json

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


def _multi_tool_stream(tool_calls: list[dict], final_text: str):
    """One assistant message carrying several tool calls, then a reply."""
    calls = [0]

    def mock_stream(*_args, **_kwargs):
        class MockResponse:
            def raise_for_status(self):
                pass

            async def aiter_lines(self):
                calls[0] += 1
                if calls[0] == 1:
                    deltas = [
                        {
                            "index": index,
                            "id": call["id"],
                            "function": {
                                "name": call["name"],
                                "arguments": json.dumps(call["arguments"]),
                            },
                        }
                        for index, call in enumerate(tool_calls)
                    ]
                    yield "data: " + json.dumps(
                        {"choices": [{"delta": {"tool_calls": deltas}}]}
                    )
                else:
                    yield "data: " + json.dumps(
                        {"choices": [{"delta": {"content": final_text}}]}
                    )
                yield "data: [DONE]"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return None

        return MockResponse()

    return mock_stream


@pytest.mark.asyncio
async def test_every_tool_in_a_batch_yields_one_correlated_envelope(monkeypatch):
    """No call in a multi-tool batch may degrade to a bare string result.

    The old version read Brain._chat_stream_impl's source and checked the
    ResultEnvelope annotations. That passes unchanged if a code path returns a
    str, which then silently loses status/data downstream. Here each call must
    arrive at on_operation_result as a ResultEnvelope carrying its own
    turn/task/session identity, correlated by call index.
    """
    brain = Brain(Config(llm_url="http://localhost:11434", llm_key="no-key", llm_model="dummy"))
    envelopes = []
    brain.on_operation_result = lambda name, envelope: envelopes.append((name, envelope))
    executed = []

    def execute(name, arguments):
        executed.append((name, arguments))
        return f"{name} finished for {arguments['path']}"

    monkeypatch.setattr(brain.client, "stream", _multi_tool_stream(
        [
            {"id": "1", "name": "file_read", "arguments": {"path": "notes.txt"}},
            {"id": "2", "name": "file_read", "arguments": {"path": "todo.md"}},
        ],
        "Both files read.",
    ))
    monkeypatch.setattr("charlie.tools.registry.execute_tool", execute)

    chunks = [
        chunk
        async for chunk in brain.chat_stream(
            "read notes.txt and todo.md",
            platform="text",
            skip_pre_search=True,
            session_id="session-batch",
            task_id="task-batch",
            turn_id="turn-batch",
        )
    ]

    assert chunks == ["Both files read."]
    assert [name for name, _ in executed] == ["file_read", "file_read"]
    assert [name for name, _ in envelopes] == ["file_read", "file_read"]
    for _name, envelope in envelopes:
        assert isinstance(envelope, ResultEnvelope), envelope
        assert (envelope.turn_id, envelope.task_id, envelope.session_id) == (
            "turn-batch",
            "task-batch",
            "session-batch",
        )
        assert envelope.status == ResultStatus.COMPLETED
    results = {envelope.result for _name, envelope in envelopes}
    assert results == {
        "file_read finished for notes.txt",
        "file_read finished for todo.md",
    }


@pytest.mark.asyncio
async def test_interactive_approval_denial_short_circuits_the_batch(monkeypatch):
    """A denied interactive call must arrive as a structured denial envelope.

    _exec_one feeds (result.data or {}).get("failure_kind") -- a bare string
    here would raise instead of gating the remaining interactive calls, so this
    also proves the batch really stops rather than proceeding.
    """
    brain = Brain(
        Config(llm_url="http://localhost:11434", llm_key="no-key", llm_model="dummy"),
        on_tool_approval_request=None,
    )
    envelopes = []
    brain.on_operation_result = lambda name, envelope: envelopes.append((name, envelope))
    executed = []

    monkeypatch.setattr(brain.client, "stream", _multi_tool_stream(
        [{"id": "1", "name": "shell_execute", "arguments": {"command": "echo hello"}}],
        "done",
    ))
    monkeypatch.setattr(
        "charlie.tools.registry.execute_tool_structured",
        lambda name, arguments: executed.append(name) or "never reached",
    )

    chunks = [
        chunk
        async for chunk in brain.chat_stream(
            "run echo hello",
            platform="telegram",
            skip_pre_search=True,
            session_id="session-denied",
            task_id="task-denied",
            turn_id="turn-denied",
        )
    ]

    assert executed == [], "a denied interactive tool must never execute"
    assert len(envelopes) == 1
    name, envelope = envelopes[0]
    assert name == "shell_execute"
    assert isinstance(envelope, ResultEnvelope)
    assert envelope.data["failure_kind"] == "approval_denied"
    assert envelope.data["approval_status"] != "approved"
    assert envelope.requires_approval is True
    assert envelope.status == ResultStatus.CANCELLED
    # The turn reports the denial instead of claiming the command ran.
    reply = "".join(chunks).lower()
    assert "approval" in reply
    assert "hello" not in reply


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
@pytest.mark.parametrize("final_fails", [False, True])
async def test_repeated_successful_call_stops_at_five_and_uses_one_no_tool_final(
    monkeypatch, caplog, final_fails
):
    brain = Brain(
        Config(
            llm_url="http://localhost:11434",
            llm_key="no-key",
            llm_model="dummy",
            native_tool_calling=True,
            iteration_budget_max=12,
        )
    )
    brain._use_native_tools = True
    operation = core.capability_index.get_operation("file_read")
    assert operation is not None
    monkeypatch.setattr(operation, "cacheable", True, raising=False)
    monkeypatch.setattr(operation, "freshness_sec", None, raising=False)
    initial_call = {
        "id": "call_read_1",
        "name": "file_read",
        "arguments": {"path": "notes.txt"},
    }
    execution_calls = []
    followup_payloads = []
    envelopes = []

    async def initial_completion(_payload, _generation):
        return "", [initial_call]

    async def repeated_followup(_client, _model, payload, _generation, state, *_args):
        followup_payloads.append(payload)
        if "tools" not in payload:
            if final_fails:
                raise RuntimeError("final response unavailable")
            state.accumulated = "I read the note and stopped repeating the same result."
            yield state.accumulated
            return

        repeat_number = sum("tools" in item for item in followup_payloads)
        state.tc_by_index = {
            0: {
                "id": f"call_read_{repeat_number + 1}",
                "name": "file_read",
                "arguments": json.dumps({"path": "notes.txt"}),
            }
        }
        if False:
            yield ""

    def execute_read(tool_name, arguments):
        execution_calls.append((tool_name, arguments))
        return ToolExecutionResult(
            "Verified note contents.",
            structured_data={"verified": True},
        )

    brain.on_operation_result = lambda _name, envelope: envelopes.append(envelope)
    monkeypatch.setattr(brain, "_stream_completion", initial_completion)
    monkeypatch.setattr(brain, "_stream_followup_once", repeated_followup)
    monkeypatch.setattr("charlie.tools.registry.execute_tool", execute_read)

    try:
        with caplog.at_level("WARNING", logger="charlie.core"):
            chunks = [
                chunk
                async for chunk in brain.chat_stream(
                    "Read notes.txt and summarize it.",
                    platform="text",
                    session_id="session-repeat-success",
                    task_id="task-repeat-success",
                    turn_id="turn-repeat-success",
                )
            ]
    finally:
        await brain.close()

    assert len(execution_calls) == 1
    assert len(envelopes) == 1
    assert len(followup_payloads) == 5
    assert all("tools" in payload for payload in followup_payloads[:-1])
    assert "tools" not in followup_payloads[-1]
    assert sum("twice without progress" in record.message for record in caplog.records) == 1

    messages = followup_payloads[-1]["messages"]
    call_ids = [
        call["id"]
        for message in messages
        if message.get("role") == "assistant"
        for call in message.get("tool_calls", [])
    ]
    result_ids = [message["tool_call_id"] for message in messages if message.get("role") == "tool"]
    assert call_ids == [f"call_read_{index}" for index in range(1, 6)]
    assert result_ids == call_ids

    if final_fails:
        assert len(chunks) == 1
        assert chunks[0].startswith("Verified so far: Verified note contents.")
        assert "Status: I stopped after five identical successful tool results" in chunks[0]
    else:
        assert chunks == ["I read the note and stopped repeating the same result."]


@pytest.mark.asyncio
async def test_fresh_window_observations_bypass_cache_and_no_progress_stop(monkeypatch, caplog):
    brain = Brain(
        Config(
            llm_url="http://localhost:11434",
            llm_key="no-key",
            llm_model="dummy",
            native_tool_calling=True,
            iteration_budget_max=6,
        )
    )
    brain._use_native_tools = True
    operation = core.capability_index.get_operation("desktop_windows")
    assert operation is not None
    monkeypatch.setattr(operation, "cacheable", True, raising=False)
    monkeypatch.setattr(operation, "freshness_sec", 0.0, raising=False)
    initial_call = {"id": "call_windows_1", "name": "desktop_windows", "arguments": {}}
    execution_calls = []
    followup_payloads = []

    async def initial_completion(_payload, _generation):
        return "", [initial_call]

    async def repeated_followup(_client, _model, payload, _generation, state, *_args):
        followup_payloads.append(payload)
        if "tools" not in payload:
            pytest.fail("fresh observations must not trigger the no-progress final attempt")
        repeat_number = len(followup_payloads)
        if repeat_number == 5:
            state.accumulated = "I observed the current windows."
            yield state.accumulated
            return
        state.tc_by_index = {
            0: {
                "id": f"call_windows_{repeat_number + 1}",
                "name": "desktop_windows",
                "arguments": "{}",
            }
        }
        if False:
            yield ""

    def observe_windows(tool_name, arguments):
        execution_calls.append((tool_name, arguments))
        return ToolExecutionResult(
            "The open windows are unchanged.",
            structured_data={"verified": True},
        )

    monkeypatch.setattr(brain, "_stream_completion", initial_completion)
    monkeypatch.setattr(brain, "_stream_followup_once", repeated_followup)
    monkeypatch.setattr("charlie.tools.registry.execute_tool", observe_windows)

    try:
        with caplog.at_level("WARNING", logger="charlie.core"):
            chunks = [
                chunk
                async for chunk in brain.chat_stream(
                    "List open windows.",
                    platform="text",
                    session_id="session-window-repeat",
                    task_id="task-window-repeat",
                    turn_id="turn-window-repeat",
                )
            ]
    finally:
        await brain.close()

    assert len(execution_calls) == 5
    assert len(followup_payloads) == 5
    assert all("tools" in payload for payload in followup_payloads)
    assert not any("twice without progress" in record.message for record in caplog.records)
    assert chunks == ["I observed the current windows."]


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

    chunks = [
        chunk
        async for chunk in brain.chat_stream(
            "Read notes.txt and run `echo ok`.",
            platform="text",
        )
    ]

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
