"""Focused behavioral coverage for one primary IntentDecision per turn."""

import ast
import asyncio
from pathlib import Path
from typing import Any

import pytest

from charlie import core, router
from charlie.autonomy import Requirement, RiskClass
from charlie.config import Config
from charlie.fastpaths import FastPathResult
from charlie.research.models import ResearchMode, ResearchReport
from charlie.research.router import ResearchDecision
from charlie.turn_contracts import (
    IntentDecision,
    TurnContractError,
    TurnRequest,
)

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
    assert decisions[0].routing_source == "model"
    assert decisions[0].confidence is None


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
        "freshness_requirement": "live",
        "routing_source": "research_router",
        "confidence": 1.0,
        "rationale": "freshness signal",
        "presentation_expectation": None,
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
    command_source = _function_source("consume_web_commands")

    assert "pending_approval_id = get_active_voice_approval()" in process_source
    assert "record_primary_decision(" in process_source
    assert "brain.cancel_chat()" in process_source
    assert "voice.stop_tts()" in process_source
    assert "barge-in lifecycle command interrupted active speech" in process_source
    assert "speech echo cooldown suppressed the incoming utterance" in process_source
    assert "elif cmd_type == \"stop\":" in command_source
    assert "elif cmd_type == \"tool_approve\":" in command_source
    assert "elif cmd_type == \"tool_reject\":" in command_source


def test_existing_matcher_order_and_outputs_are_unchanged() -> None:
    assert router.answer_time_date("What time is it?") is not None
    system_match = __import__("charlie.fastpaths", fromlist=["match_fast_path"]).match_fast_path(
        "What is the CPU usage?"
    )
    assert system_match is not None
    assert system_match.intent == "system_cpu"
    assert router.match_browser_task("Search mechanical keyboards on amazon") is not None
    assert router.match_browser_task("Research the latest AI news") is None
