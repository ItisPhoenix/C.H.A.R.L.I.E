"""Focused behavioral coverage for one primary IntentDecision per turn."""

import ast
import asyncio
from pathlib import Path
from typing import Any

import pytest

import main
from charlie import core, router
from charlie.autonomy import Requirement, RiskClass
from charlie.config import Config
from charlie.fastpaths import FastPathResult
from charlie.research.models import ResearchMode, ResearchReport, SourceDocument
from charlie.research.router import ResearchDecision
from charlie.turn_contracts import IntentDecision, ResultEnvelope, TurnContractError, TurnRequest

ROOT = Path(__file__).resolve().parents[1]
MAIN_SOURCE = (ROOT / "main.py").read_text(encoding="utf-8")


@pytest.fixture
def brain_config(tmp_path: Path) -> Config:
    return Config(
        llm_url="http://localhost:11434",
        llm_key="no-key",
        llm_model="dummy",
        native_tool_calling=False,
        router_classifier_enabled=False,
        session_db_path=str(tmp_path / "sessions.db"),
        world_model_db_path=str(tmp_path / "world.db"),
    )


def _request(text: str, *, channel: str = "web") -> TurnRequest:
    return TurnRequest(
        turn_id=f"turn-{abs(hash(text))}",
        session_id="session-intent",
        input=text,
        channel=channel,
    )


def test_tool_scope_accepts_requested_facts_and_suppresses_shell_detours() -> None:
    request = "Tell me the Python version here and the first heading in Windows taskkill help. Don't stop anything."
    calls = [
        {"id": "version", "name": "shell_execute", "arguments": {"command": "python --version"}},
        {"id": "help", "name": "shell_execute", "arguments": {"command": "taskkill /?"}},
        {"id": "path", "name": "shell_execute", "arguments": {"command": "where python"}},
        {
            "id": "absolute-path",
            "name": "shell_execute",
            "arguments": {"command": '"C:\\Python314\\python.exe" --version'},
        },
        {"id": "echo", "name": "shell_execute", "arguments": {"command": "echo test"}},
        {"id": "list", "name": "shell_execute", "arguments": {"command": "dir"}},
        {"id": "kill", "name": "shell_execute", "arguments": {"command": "taskkill /IM notepad.exe /F"}},
    ]

    allowed, suppressed = core._filter_tool_calls_to_request_scope(calls, request)

    assert [call["id"] for call in allowed] == ["version", "help"]
    assert suppressed == 5
    assert not core._tool_call_matches_request_scope(
        request,
        {"name": "automation_cancel", "arguments": {"schedule_id": "old-reminder"}},
    )
    assert not core._tool_call_matches_request_scope(
        "How do I close Notepad?",
        {"name": "desktop_close_app", "arguments": {"app": "notepad"}},
    )


def test_tool_scope_uses_verified_result_provenance_for_a_navigation_target() -> None:
    request = "Open the official report page and summarize it."
    call = {
        "name": "browser_task",
        "arguments": {"task": "Open https://example.com/official-report"},
    }
    assert not core._tool_call_matches_request_scope(request, call)

    fetched = ResultEnvelope(
        status="completed",
        verification_status="verified_success",
        result="Official report: https://example.com/official-report",
    )
    assert core._tool_call_matches_request_scope(request, call, verified_results=[({}, fetched)])

    report = ResearchReport(
        query="official report",
        mode=ResearchMode.STANDARD,
        sources=[
            SourceDocument(
                url="https://example.com/official-report",
                title="Official report",
                content="Fetched body",
            )
        ],
    )
    research_result = ResultEnvelope(status="completed", data={"research_report": report})
    assert core._tool_call_matches_request_scope(request, call, verified_results=[({}, research_result)])


def test_requested_shell_facts_stop_only_after_all_requested_facts_are_verified() -> None:
    request = "Tell me the Python version and first heading in taskkill help."
    version_call = {"name": "shell_execute", "arguments": {"command": "python --version"}}
    version = ResultEnvelope(
        status="completed",
        verification_status="verified_success",
        result="Python 3.14.6",
    )
    assert not core._verified_requested_shell_facts_complete(request, [(version_call, version)])

    help_call = {"name": "shell_execute", "arguments": {"command": "taskkill /?"}}
    help_result = ResultEnvelope(
        status="completed",
        verification_status="verified_success",
        result="TASKKILL help\n\nDescription:\nTerminates processes.",
    )
    assert core._verified_requested_shell_facts_complete(request, [(version_call, version), (help_call, help_result)])


def test_interrupted_turn_reports_verified_partial_results_first() -> None:
    request = "Tell me the Python version and first heading in taskkill help."
    call = {"name": "shell_execute", "arguments": {"command": "python --version"}}
    result = ResultEnvelope(
        status="completed",
        verification_status="verified_success",
        result="Python 3.14.6",
    )

    reply = core._verified_partial_result_reply(request, [(call, result)], "The next step failed.")

    assert reply.startswith("Python version: 3.14.6.")
    assert "Status: The next step failed." in reply
    assert "I didn't attempt the remaining steps." in reply


@pytest.mark.asyncio
async def test_verified_shell_answers_stop_followup_detours(
    monkeypatch: pytest.MonkeyPatch,
    brain_config: Config,
) -> None:
    request = _request(
        "Tell me the Python version here and the first heading in Windows taskkill help. Don't stop anything.",
        channel="telegram",
    )
    brain = core.Brain(brain_config, register_panic_hotkey=False)
    executed: list[str] = []

    async def fake_completion(_payload: dict[str, Any], _generation: int) -> tuple[str, list[dict[str, Any]]]:
        return "", [
            {"id": "version", "name": "shell_execute", "arguments": {"command": "python --version"}},
            {"id": "help", "name": "shell_execute", "arguments": {"command": "taskkill /?"}},
        ]

    async def fake_followup(
        _client: Any,
        _model: str,
        _payload: dict[str, Any],
        _generation: int,
        state: Any,
    ) -> Any:
        state.accumulated = "I should check some other commands too."
        state.tc_by_index = {
            0: {"id": "detour", "name": "shell_execute", "arguments": '{"command":"echo test"}'}
        }
        if False:
            yield ""

    def fake_execute_tool(_name: str, arguments: dict[str, Any]) -> core.ToolExecutionResult:
        command = arguments["command"]
        executed.append(command)
        if command == "python --version":
            stdout = "Python 3.14.6\n"
        elif command == "taskkill /?":
            stdout = "TASKKILL help\n\nDescription:\nHelp text"
        else:
            raise AssertionError(f"out-of-scope command executed: {command}")
        return core.ToolExecutionResult(
            stdout,
            {"exit_code": 0, "stdout": stdout, "stderr": ""},
            "terminal_result",
        )

    monkeypatch.setattr(brain, "_stream_completion", fake_completion)
    monkeypatch.setattr(brain, "_stream_followup_once", fake_followup)
    monkeypatch.setattr(core.tool_registry, "execute_tool_structured", fake_execute_tool)
    try:
        chunks = await _run_turn(brain, request)
    finally:
        await brain.close()

    assert executed == ["python --version", "taskkill /?"]
    reply = "".join(chunks)
    assert "Python version: 3.14.6." in reply
    assert "First heading in Windows taskkill help: Description." in reply


async def _run_turn(brain: core.Brain, request: TurnRequest, *, skip_pre_search: bool = True) -> list[str]:
    return [
        chunk
        async for chunk in brain.chat_stream(
            request.input,
            platform=request.channel,
            session_id=request.session_id,
            task_id="task-separate",
            turn_id=request.turn_id,
            turn_request=request,
            skip_pre_search=skip_pre_search,
        )
    ]


def _function_source(name: str) -> str:
    module = ast.parse(MAIN_SOURCE)
    matches = [
        node
        for node in ast.walk(module)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]
    assert matches, f"{name} was not found in main.py"
    return ast.get_source_segment(MAIN_SOURCE, matches[0]) or ""


@pytest.mark.asyncio
async def test_time_date_turn_emits_one_canonical_decision(brain_config: Config) -> None:
    request = _request("What time is it?")
    decisions: list[IntentDecision] = []
    brain = core.Brain(brain_config, on_intent_decision=decisions.append, register_panic_hotkey=False)
    try:
        chunks = await _run_turn(brain, request)
        assert brain.last_intent_decision is None
        assert brain._intent_decisions == {}
    finally:
        await brain.close()

    assert chunks
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.turn_id == request.turn_id
    assert decision.session_id == request.session_id
    assert decision.original_request == request.input
    assert decision.intent == "time_date"
    assert decision.capabilities == ("system",)
    assert getattr(decision, "execution_policy", None) == "conversation"
    assert getattr(decision, "durable_work_required", False) is False
    assert decision.routing_source == "deterministic"
    assert decision.confidence == 1.0


@pytest.mark.asyncio
async def test_system_metric_turn_records_system_fastpath_decision(
    monkeypatch: pytest.MonkeyPatch, brain_config: Config
) -> None:
    monkeypatch.setattr(core, "autonomy_evaluate", lambda *_args: (Requirement.ALLOW, RiskClass.SAFE, ""))
    monkeypatch.setattr("charlie.fastpaths.execute_fast_path", lambda _match: FastPathResult("CPU is 12%"))
    request = _request("What is the CPU usage?")
    decisions: list[IntentDecision] = []
    brain = core.Brain(brain_config, on_intent_decision=decisions.append, register_panic_hotkey=False)
    try:
        chunks = await _run_turn(brain, request)
    finally:
        await brain.close()

    assert chunks == ["CPU is 12%"]
    assert len(decisions) == 1
    assert decisions[0].intent == "system"
    assert decisions[0].capabilities == ("system",)
    assert decisions[0].routing_source == "fastpath"


@pytest.mark.asyncio
async def test_research_turn_records_research_and_live_freshness(
    monkeypatch: pytest.MonkeyPatch, brain_config: Config
) -> None:
    request = _request("Research the latest NVIDIA security news")
    report = ResearchReport(query=request.input, mode=ResearchMode.STANDARD)
    decisions: list[IntentDecision] = []
    brain = core.Brain(brain_config, on_intent_decision=decisions.append, register_panic_hotkey=False)

    async def fake_research(*_args: Any, **_kwargs: Any) -> ResearchReport:
        return report

    async def fake_completion(_payload: dict[str, Any], _generation: int) -> tuple[str, list[dict[str, Any]]]:
        return "Research answer", []

    monkeypatch.setattr(brain, "_run_research_for_turn", fake_research)
    monkeypatch.setattr(brain, "_stream_completion", fake_completion)
    try:
        chunks = await _run_turn(brain, request, skip_pre_search=False)
    finally:
        await brain.close()

    assert chunks == ["Research answer"]
    assert len(decisions) == 1
    assert decisions[0].intent == "research"
    assert decisions[0].capabilities == ("research",)
    assert decisions[0].freshness_requirement == "live"
    assert getattr(decisions[0], "execution_policy", None) == "grounded_answer"
    assert getattr(decisions[0], "durable_work_required", False) is False
    assert decisions[0].routing_source == "research_router"
    assert decisions[0].confidence == 1.0


@pytest.mark.asyncio
async def test_disabled_research_keeps_the_model_route(
    monkeypatch: pytest.MonkeyPatch, brain_config: Config
) -> None:
    brain_config.research_enabled = False
    request = _request("Research the latest NVIDIA security news")
    decisions: list[IntentDecision] = []
    brain = core.Brain(brain_config, on_intent_decision=decisions.append, register_panic_hotkey=False)

    async def no_research(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def fake_completion(_payload: dict[str, Any], _generation: int) -> tuple[str, list[dict[str, Any]]]:
        return "Model answer", []

    monkeypatch.setattr(brain, "_run_research_for_turn", no_research)
    monkeypatch.setattr(brain, "_stream_completion", fake_completion)
    try:
        chunks = await _run_turn(brain, request, skip_pre_search=False)
    finally:
        await brain.close()

    assert chunks == ["Model answer"]
    assert len(decisions) == 1
    assert decisions[0].intent == "conversation"
    assert decisions[0].capabilities == ()
    assert getattr(decisions[0], "execution_policy", None) == "conversation"
    assert decisions[0].routing_source == "model"


@pytest.mark.asyncio
async def test_browser_turn_records_browser_context_decision(
    monkeypatch: pytest.MonkeyPatch, brain_config: Config
) -> None:
    brain_config.browser_enabled = True
    request = _request("Search mechanical keyboards on amazon")
    decisions: list[IntentDecision] = []
    brain = core.Brain(brain_config, on_intent_decision=decisions.append, register_panic_hotkey=False)

    async def fake_browser(*_args: Any, **_kwargs: Any) -> str:
        return "Browser answer"

    monkeypatch.setattr(brain, "_browser_task_bounded", fake_browser)
    try:
        chunks = await _run_turn(brain, request)
    finally:
        await brain.close()

    assert chunks == ["Browser answer"]
    assert len(decisions) == 1
    assert decisions[0].intent == "browser"
    assert decisions[0].capabilities == ("browser",)
    assert decisions[0].routing_source == "browser_context"


@pytest.mark.asyncio
async def test_ordinary_conversation_records_no_capability_model_route(brain_config: Config) -> None:
    request = _request("Explain recursion")
    decisions: list[IntentDecision] = []
    brain = core.Brain(brain_config, on_intent_decision=decisions.append, register_panic_hotkey=False)

    async def fake_completion(_payload: dict[str, Any], _generation: int) -> tuple[str, list[dict[str, Any]]]:
        return "Recursion is a function calling itself.", []

    brain._stream_completion = fake_completion
    try:
        chunks = await _run_turn(brain, request)
    finally:
        await brain.close()

    assert chunks == ["Recursion is a function calling itself."]
    assert len(decisions) == 1
    assert decisions[0].intent == "conversation"
    assert decisions[0].capabilities == ()
    assert getattr(decisions[0], "execution_policy", None) == "conversation"
    assert decisions[0].routing_source == "model"
    assert decisions[0].confidence is None


@pytest.mark.asyncio
async def test_latest_bounded_question_is_grounded_without_durable_work(
    monkeypatch: pytest.MonkeyPatch, brain_config: Config
) -> None:
    request = _request("What is the latest stable Python release?")
    report = ResearchReport(query=request.input, mode=ResearchMode.STANDARD)
    decisions: list[IntentDecision] = []
    brain = core.Brain(brain_config, on_intent_decision=decisions.append, register_panic_hotkey=False)

    async def fake_research(*_args: Any, **_kwargs: Any) -> ResearchReport:
        return report

    async def fake_completion(_payload: dict[str, Any], _generation: int) -> tuple[str, list[dict[str, Any]]]:
        return "The latest release is available from current evidence.", []

    monkeypatch.setattr(brain, "_run_research_for_turn", fake_research)
    monkeypatch.setattr(brain, "_stream_completion", fake_completion)
    try:
        await _run_turn(brain, request, skip_pre_search=False)
    finally:
        await brain.close()

    assert len(decisions) == 1
    assert getattr(decisions[0], "execution_policy", None) == "grounded_answer"
    assert decisions[0].freshness_requirement == "live"
    assert decisions[0].durable_work_required is False


@pytest.mark.asyncio
async def test_open_app_is_action_without_durable_work(
    monkeypatch: pytest.MonkeyPatch, brain_config: Config
) -> None:
    request = _request("Open Notepad")
    decisions: list[IntentDecision] = []
    brain = core.Brain(brain_config, on_intent_decision=decisions.append, register_panic_hotkey=False)

    async def fake_execute(*_args: Any, **kwargs: Any) -> ResultEnvelope:
        return ResultEnvelope(
            request=kwargs.get("request", request.input),
            turn_id=kwargs.get("turn_id", request.turn_id),
            task_id=kwargs.get("task_id"),
            session_id=kwargs.get("session_id", request.session_id),
            capability="desktop",
            operation="desktop.app.open",
            result="Notepad opened.",
        )

    monkeypatch.setattr(brain, "execute_tool_operation", fake_execute)
    try:
        await _run_turn(brain, request)
    finally:
        await brain.close()

    assert len(decisions) == 1
    assert getattr(decisions[0], "execution_policy", None) == "action"
    assert getattr(decisions[0], "external_action_required", False) is True
    assert getattr(decisions[0], "durable_work_required", False) is False


@pytest.mark.asyncio
async def test_background_tool_selection_is_work_without_a_second_router(
    monkeypatch: pytest.MonkeyPatch, brain_config: Config
) -> None:
    request = _request("Process these files while I keep chatting")
    decisions: list[IntentDecision] = []
    brain = core.Brain(brain_config, on_intent_decision=decisions.append, register_panic_hotkey=False)
    calls = 0

    async def fake_completion(_payload: dict[str, Any], _generation: int) -> tuple[str, list[dict[str, Any]]]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return "", [{
                "id": "call-work",
                "name": "start_background_task",
                "arguments": {"text": "Process these files"},
            }]
        return "The work has started.", []

    async def fake_followup(_client: Any, _model: str, _payload: dict[str, Any], _generation: int, state: Any) -> Any:
        state.accumulated = "The work has started."
        state.tc_by_index = {}
        if False:
            yield ""

    async def fake_start(_arguments: dict[str, Any], **_kwargs: Any) -> str:
        return "Background task started."

    monkeypatch.setattr(brain, "_stream_completion", fake_completion)
    monkeypatch.setattr(brain, "_stream_followup_once", fake_followup)
    monkeypatch.setattr(brain, "_handle_start_background_task", fake_start)
    try:
        await _run_turn(brain, request)
    finally:
        await brain.close()

    assert len(decisions) == 1
    assert getattr(decisions[0], "execution_policy", None) == "work"
    assert getattr(decisions[0], "durable_work_required", False) is True
    assert getattr(decisions[0], "external_action_required", False) is False


@pytest.mark.asyncio
async def test_explicit_background_request_dispatches_only_the_work_capability(
    monkeypatch: pytest.MonkeyPatch, brain_config: Config
) -> None:
    request = _request(
        "Charlie, start a background task with two steps: run python --version, then taskkill /? for help only.",
        channel="telegram",
    )
    decisions: list[IntentDecision] = []
    operation_names: list[str] = []
    started: list[dict[str, Any]] = []
    brain = core.Brain(
        brain_config,
        on_intent_decision=decisions.append,
        on_tool_call=lambda name, *_args, **_kwargs: operation_names.append(name),
        register_panic_hotkey=False,
    )

    async def fake_start(arguments: dict[str, Any], **kwargs: Any) -> str:
        started.append({"arguments": arguments, **kwargs})
        return "Background task started (id=task-test, status=running)."

    async def unexpected_completion(*_args: Any, **_kwargs: Any) -> tuple[str, list[dict[str, Any]]]:
        raise AssertionError("explicit background dispatch must not ask the foreground model to act")

    monkeypatch.setattr(brain, "_handle_start_background_task", fake_start)
    monkeypatch.setattr(brain, "_stream_completion", unexpected_completion)
    try:
        chunks = await _run_turn(brain, request)
    finally:
        await brain.close()

    assert chunks == ["Background task started (id=task-test, status=running)."]
    assert [entry["arguments"] for entry in started] == [{"text": request.input}]
    assert started[0]["platform"] == "telegram"
    assert operation_names == ["start_background_task"]
    assert len(decisions) == 1
    assert decisions[0].execution_policy == "work"
    assert decisions[0].capabilities == ("task",)
    assert decisions[0].durable_work_required is True


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Charlie, start a background task to check the report", True),
        ("Please create a background job to check the report", True),
        ("Please do not start a background task", False),
        ("How do I start a background task in Python?", False),
    ],
)
def test_background_task_start_matcher_is_explicit(query: str, expected: bool) -> None:
    assert core.router.is_explicit_background_task_start(query) is expected


def test_calendar_capability_selects_automation_policy() -> None:
    policy, capabilities, freshness, external, durable, _rationale = core._execution_policy_from_tool_calls(
        [{"name": "calendar_create", "arguments": {"title": "Daily check"}}]
    )

    assert policy.value == "automation"
    assert capabilities == ("calendar",)
    assert freshness is None
    assert external is True
    assert durable is True


@pytest.mark.asyncio
async def test_tool_result_keeps_original_decision_and_turn_task_correlation(
    monkeypatch: pytest.MonkeyPatch, brain_config: Config
) -> None:
    request = _request("Read notes.txt")
    decisions: list[IntentDecision] = []
    results = []
    brain = core.Brain(
        brain_config,
        on_intent_decision=decisions.append,
        on_operation_result=lambda _name, envelope: results.append(envelope),
        register_panic_hotkey=False,
    )
    responses = iter(
        [
            ("", [{"id": "call-1", "name": "file_read", "arguments": {"path": "notes.txt"}}]),
            ("Notes loaded.", []),
        ]
    )

    async def fake_completion(_payload: dict[str, Any], _generation: int) -> tuple[str, list[dict[str, Any]]]:
        return next(responses)

    async def fake_followup(
        _client: Any,
        _model: str,
        _payload: dict[str, Any],
        _generation: int,
        state: Any,
    ) -> Any:
        state.accumulated = "Notes loaded."
        state.tc_by_index = {}
        if False:
            yield ""

    monkeypatch.setattr(brain, "_stream_completion", fake_completion)
    monkeypatch.setattr(brain, "_stream_followup_once", fake_followup)
    monkeypatch.setattr(core.tool_registry, "execute_tool", lambda _name, _args: "notes contents")
    try:
        chunks = await _run_turn(brain, request)
    finally:
        await brain.close()

    assert chunks == ["Notes loaded."]
    assert len(decisions) == 1
    assert len(results) == 1
    assert results[0].turn_id == request.turn_id
    assert results[0].session_id == request.session_id
    assert results[0].task_id == "task-separate"
    assert decisions[0].turn_id == results[0].turn_id
    assert decisions[0].session_id == results[0].session_id
    assert not hasattr(decisions[0], "task_id")


def test_decision_registry_deduplicates_competing_primary_decisions(brain_config: Config) -> None:
    seen: list[IntentDecision] = []
    brain = core.Brain(brain_config, on_intent_decision=seen.append, register_panic_hotkey=False)
    request = _request("same turn")
    try:
        first = brain.record_intent_decision(
            request,
            intent="conversation",
            routing_source="model",
        )
        second = brain.record_intent_decision(
            request,
            intent="research",
            capabilities=("research",),
            routing_source="research_router",
            freshness_requirement="live",
            confidence=1.0,
        )
    finally:
        # Brain's client has no pending network work; close is handled by the async tests.
        pass

    assert second is first
    assert seen == [first]
    asyncio.run(brain.close())


@pytest.mark.asyncio
async def test_turn_request_identity_is_required_when_propagating_a_decision(brain_config: Config) -> None:
    request = _request("identity check")
    brain = core.Brain(brain_config, register_panic_hotkey=False)
    decision = IntentDecision.for_request(request, intent="conversation", routing_source="model")
    try:
        with pytest.raises(TurnContractError, match="does not match"):
            [
                chunk
                async for chunk in brain.chat_stream(
                    "different input",
                    session_id=request.session_id,
                    turn_id=request.turn_id,
                    turn_request=request,
                    intent_decision=decision,
                )
            ]
    finally:
        await brain.close()


def test_intent_decision_contract_serializes_only_routing_metadata() -> None:
    request = _request("latest status")
    decision = IntentDecision.for_request(
        request,
        intent="research",
        capabilities=("research",),
        freshness_requirement="live",
        routing_source="research_router",
        confidence=1.0,
        rationale="freshness signal",
    )

    assert decision.to_dict() == {
        "turn_id": request.turn_id,
        "session_id": request.session_id,
        "original_request": request.input,
        "intent": "research",
        "capabilities": ["research"],
        "execution_policy": "conversation",
        "freshness_requirement": "live",
        "external_action_required": False,
        "durable_work_required": False,
        "routing_source": "research_router",
        "confidence": 1.0,
        "rationale": "freshness signal",
    }


@pytest.mark.asyncio
async def test_failed_turn_releases_its_temporary_decision(
    monkeypatch: pytest.MonkeyPatch, brain_config: Config
) -> None:
    request = _request("Explain failure cleanup")
    brain = core.Brain(brain_config, register_panic_hotkey=False)

    async def fail_completion(_payload: dict[str, Any], _generation: int) -> tuple[str, list[dict[str, Any]]]:
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(brain, "_stream_completion", fail_completion)
    try:
        with pytest.raises(RuntimeError, match="model unavailable"):
            await _run_turn(brain, request)
        assert brain._intent_decisions == {}
        assert brain.last_intent_decision is None
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_cancelled_turn_releases_its_temporary_decision(
    monkeypatch: pytest.MonkeyPatch, brain_config: Config
) -> None:
    request = _request("Explain cancellation cleanup")
    brain = core.Brain(brain_config, register_panic_hotkey=False)
    started = asyncio.Event()

    async def wait_for_cancel(_payload: dict[str, Any], _generation: int) -> tuple[str, list[dict[str, Any]]]:
        started.set()
        await asyncio.Event().wait()
        return "never", []

    monkeypatch.setattr(brain, "_stream_completion", wait_for_cancel)

    async def consume() -> list[str]:
        return await _run_turn(brain, request)

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(started.wait(), timeout=1.0)
        assert request.turn_id in brain._intent_decisions
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert brain._intent_decisions == {}
        assert brain.last_intent_decision is None
    finally:
        if not task.done():
            task.cancel()
            await task
        await brain.close()


@pytest.mark.asyncio
async def test_many_completed_turns_do_not_grow_the_registry(brain_config: Config) -> None:
    decisions: list[IntentDecision] = []
    brain = core.Brain(brain_config, on_intent_decision=decisions.append, register_panic_hotkey=False)
    try:
        for index in range(32):
            request = TurnRequest(
                turn_id=f"turn-many-{index}",
                session_id="session-many",
                input="What time is it?",
                channel="web",
            )
            assert await _run_turn(brain, request)
            assert brain._intent_decisions == {}
            assert brain.last_intent_decision is None
    finally:
        await brain.close()

    assert len(decisions) == 32


def test_separate_turn_ids_have_independent_temporary_entries(brain_config: Config) -> None:
    brain = core.Brain(brain_config, register_panic_hotkey=False)
    first_request = TurnRequest("turn-isolated-first", "session-isolated", "first isolated turn", "web")
    second_request = TurnRequest("turn-isolated-second", "session-isolated", "second isolated turn", "web")
    try:
        first = brain.record_intent_decision(first_request, intent="conversation", routing_source="model")
        second = brain.record_intent_decision(second_request, intent="conversation", routing_source="model")
        assert first is not second
        assert len(brain._intent_decisions) == 2
        brain.finalize_intent_decision(first_request.turn_id)
        assert list(brain._intent_decisions) == [second_request.turn_id]
    finally:
        brain.finalize_intent_decision(second_request.turn_id)
        asyncio.run(brain.close())


@pytest.mark.asyncio
async def test_ambient_brain_call_without_turn_request_creates_no_decision(
    monkeypatch: pytest.MonkeyPatch, brain_config: Config
) -> None:
    decisions: list[IntentDecision] = []
    brain = core.Brain(brain_config, on_intent_decision=decisions.append, register_panic_hotkey=False)

    async def fake_completion(_payload: dict[str, Any], _generation: int) -> tuple[str, list[dict[str, Any]]]:
        return "Ambient answer", []

    monkeypatch.setattr(brain, "_stream_completion", fake_completion)
    try:
        chunks = [chunk async for chunk in brain.chat_stream("ambient background step", skip_tools=True)]
    finally:
        await brain.close()

    assert chunks == ["Ambient answer"]
    assert decisions == []


@pytest.mark.asyncio
async def test_registry_does_not_select_execution_path(
    brain_config: Config,
) -> None:
    seen: list[IntentDecision] = []
    brain = core.Brain(brain_config, on_intent_decision=seen.append, register_panic_hotkey=False)
    request = _request("What time is it?")
    brain.record_intent_decision(request, intent="conversation", routing_source="model")
    try:
        chunks = await _run_turn(brain, request)
    finally:
        await brain.close()

    assert chunks
    assert seen[0].intent == "conversation"
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_live_metadata_does_not_force_research(
    monkeypatch: pytest.MonkeyPatch, brain_config: Config
) -> None:
    request = _request("latest internal status")
    decisions: list[IntentDecision] = []
    brain = core.Brain(brain_config, on_intent_decision=decisions.append, register_panic_hotkey=False)

    async def no_research(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def fake_completion(_payload: dict[str, Any], _generation: int) -> tuple[str, list[dict[str, Any]]]:
        return "Metadata-only answer", []

    monkeypatch.setattr(core, "route_research", lambda *_args, **_kwargs: ResearchDecision(False, None, "stub"))
    monkeypatch.setattr(brain, "_run_research_for_turn", no_research)
    monkeypatch.setattr(brain, "_stream_completion", fake_completion)
    try:
        chunks = await _run_turn(brain, request, skip_pre_search=False)
    finally:
        await brain.close()

    assert chunks == ["Metadata-only answer"]
    assert len(decisions) == 1
    assert decisions[0].intent == "conversation"
    assert decisions[0].freshness_requirement is None


def test_control_paths_remain_outside_ordinary_brain_routing() -> None:
    process_source = _function_source("_process")

    assert "pending_approval = get_active_tool_approval()" in process_source
    assert "record_primary_decision(" in process_source
    assert "brain.cancel_chat()" in process_source
    assert "voice.stop_tts()" in process_source
    assert "barge-in lifecycle command interrupted active speech" in process_source
    assert "speech echo cooldown suppressed the incoming utterance" in process_source


@pytest.mark.parametrize(
    ("turn_active", "approval_pending", "channel", "expected"),
    [
        (False, True, "telegram", False),
        (True, False, "telegram", True),
        (True, True, "telegram", True),
        (True, True, "voice", False),
    ],
)
def test_telegram_turns_queue_behind_active_approval(
    turn_active: bool, approval_pending: bool, channel: str, expected: bool
) -> None:
    assert main._should_queue_active_turn(turn_active, approval_pending, channel) is expected


@pytest.mark.parametrize(
    ("tool_name", "risk_class", "expected"),
    [
        (
            "shell_execute",
            "security_sensitive",
            "I can't confirm this command is read-only. Please review it before approving.",
        ),
        (
            "desktop_click",
            "destructive",
            "This action may change something on your PC. Please review it before approving.",
        ),
    ],
)
def test_telegram_approval_reason_is_human_readable(tool_name, risk_class, expected) -> None:
    assert main._telegram_approval_reason(tool_name, risk_class) == expected


def test_existing_matcher_order_and_outputs_are_unchanged() -> None:
    assert router.answer_time_date("What time is it?") is not None
    system_match = __import__("charlie.fastpaths", fromlist=["match_fast_path"]).match_fast_path(
        "What is the CPU usage?"
    )
    assert system_match is not None
    assert system_match.intent == "system_cpu"
    assert router.match_browser_task("Search mechanical keyboards on amazon") is not None
    assert router.match_browser_task("Research the latest AI news") is None
