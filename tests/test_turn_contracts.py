"""Focused identity and result-boundary contract tests."""

from dataclasses import replace

import pytest

from charlie.presentation import ExecutionOutcome, PresentationResolver
from charlie.turn_contracts import (
    IntentDecision,
    ResultEnvelope,
    TurnContext,
    TurnContractError,
    TurnRequest,
    validate_turn_chain,
)


def _turn_chain() -> tuple[TurnRequest, TurnContext, IntentDecision, ResultEnvelope]:
    request = TurnRequest(
        turn_id="turn-1",
        session_id="session-1",
        input="show current system status",
        channel="web",
        task_id="task-1",
    )
    context = TurnContext.for_request(
        request,
        recent_conversation=({"role": "user", "content": "hello"},),
        runtime_capabilities=("system",),
    )
    decision = IntentDecision(
        turn_id=request.turn_id,
        session_id=request.session_id,
        original_request=request.input,
        intent="system_status",
        capabilities=("system",),
        freshness_requirement="live",
        rationale="deterministic system-status matcher",
        presentation_expectation="widget_or_workspace",
    )
    result = ResultEnvelope(
        request=request.input,
        turn_id=request.turn_id,
        task_id=request.task_id,
        session_id=request.session_id,
        capability="system",
        operation="system.metrics.read",
        result="CPU is at 10%",
        evidence=[{"source": "system_api"}],
        artifacts=[{"kind": "metric", "name": "cpu"}],
        verification={"verified": True, "status": "completed"},
    )
    return request, context, decision, result


def test_existing_execution_outcome_name_is_a_compatibility_alias() -> None:
    assert ExecutionOutcome is ResultEnvelope


def test_turn_chain_keeps_one_identity_across_context_decision_and_result() -> None:
    request, context, decision, result = _turn_chain()

    validate_turn_chain(
        request,
        context=context,
        decision=decision,
        result=result,
        executable=True,
    )

    serialized = result.to_dict()
    assert serialized["turn_id"] == request.turn_id
    assert serialized["session_id"] == request.session_id
    assert serialized["task_id"] == request.task_id
    assert serialized["evidence"] == [{"source": "system_api"}]
    assert serialized["artifacts"] == [{"kind": "metric", "name": "cpu"}]


@pytest.mark.parametrize(
    "field, value",
    [
        ("context", "turn-other"),
        ("decision", "session-other"),
        ("result", "turn-other"),
    ],
)
def test_turn_chain_rejects_mismatched_correlation(field: str, value: str) -> None:
    request, context, decision, result = _turn_chain()
    if field == "context":
        context = replace(context, turn_id=value)
    elif field == "decision":
        decision = replace(decision, session_id=value)
    else:
        result = replace(result, turn_id=value)

    with pytest.raises(TurnContractError, match="does not match"):
        validate_turn_chain(request, context=context, decision=decision, result=result)


def test_executable_turn_requires_a_task_identity() -> None:
    request = TurnRequest(
        turn_id="turn-2",
        session_id="session-2",
        input="run a task",
        channel="voice",
    )

    with pytest.raises(TurnContractError, match="task_id"):
        validate_turn_chain(request, executable=True)


def test_presentation_resolver_preserves_result_turn_identity() -> None:
    _request, _context, _decision, result = _turn_chain()

    intent = PresentationResolver().resolve(result)

    assert intent.turn_id == result.turn_id
    assert intent.to_event()["turn_id"] == result.turn_id
