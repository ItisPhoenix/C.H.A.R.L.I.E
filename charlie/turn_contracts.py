"""Small contracts for one assistant interaction.

These types define the boundary between channel ingress, orchestration, and
execution results.  They deliberately contain no routing, persistence, or
renderer behavior.  Existing runtime callers can adopt them incrementally;
``ResultEnvelope`` retains the fields used by the current presentation rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Optional
from uuid import uuid4

from charlie.utils import utc_now_iso


class TurnContractError(ValueError):
    """Raised when a turn boundary object violates its correlation contract."""


class ResultStatus(StrEnum):
    """Supported statuses for a capability operation result."""

    COMPLETED = "completed"
    FAILED = "failed"
    PARTIALLY_COMPLETED = "partially_completed"
    UNVERIFIED = "unverified"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


def _require_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise TurnContractError(f"{field_name} must be a non-empty string")


@dataclass(frozen=True)
class TurnRequest:
    """The single identity-bearing request created by a channel adapter."""

    turn_id: str
    session_id: str
    input: str
    channel: str
    created_at: str = field(default_factory=utc_now_iso)
    task_id: Optional[str] = None
    # Opaque to the contract layer; the runtime may attach an asyncio/event
    # handle without making the shared contract depend on a cancellation type.
    cancellation_context: Any = None

    @classmethod
    def allocate(
        cls,
        user_input: str,
        session_id: str,
        channel: str,
        *,
        cancellation_context: Any = None,
    ) -> "TurnRequest":
        """Allocate one request identity at a normalized channel boundary."""

        return cls(
            turn_id=uuid4().hex,
            session_id=session_id,
            input=user_input,
            channel=channel,
            cancellation_context=cancellation_context,
        )

    def __post_init__(self) -> None:
        _require_text(self.turn_id, "turn_id")
        _require_text(self.session_id, "session_id")
        _require_text(self.input, "input")
        _require_text(self.channel, "channel")
        if self.task_id is not None:
            _require_text(self.task_id, "task_id")
        _require_text(self.created_at, "created_at")


@dataclass(frozen=True)
class TurnContext:
    """Read-only context assembled for one ``TurnRequest``."""

    turn_id: str
    session_id: str
    channel: str
    recent_conversation: tuple[Mapping[str, Any], ...] = ()
    relevant_memory: tuple[Mapping[str, Any], ...] = ()
    active_tasks: tuple[Mapping[str, Any], ...] = ()
    world_state: Mapping[str, Any] = field(default_factory=dict)
    permissions: Mapping[str, Any] = field(default_factory=dict)
    runtime_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.turn_id, "turn_id")
        _require_text(self.session_id, "session_id")
        _require_text(self.channel, "channel")

    @classmethod
    def for_request(cls, request: TurnRequest, **kwargs: Any) -> "TurnContext":
        """Create a context with request identity copied exactly once."""

        return cls(
            turn_id=request.turn_id,
            session_id=request.session_id,
            channel=request.channel,
            **kwargs,
        )


@dataclass(frozen=True)
class IntentDecision:
    """The routing decision produced after deterministic/LLM classification."""

    turn_id: str
    session_id: str
    original_request: str
    intent: str
    capabilities: tuple[str, ...] = ()
    freshness_requirement: Optional[str] = None
    routing_source: str = "deterministic"
    confidence: Optional[float] = None
    rationale: str = ""
    presentation_expectation: Optional[str] = None

    def __post_init__(self) -> None:
        _require_text(self.turn_id, "turn_id")
        _require_text(self.session_id, "session_id")
        _require_text(self.original_request, "original_request")
        _require_text(self.intent, "intent")
        _require_text(self.routing_source, "routing_source")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise TurnContractError("confidence must be between 0 and 1")
        object.__setattr__(self, "capabilities", tuple(self.capabilities))


@dataclass
class ResultEnvelope:
    """Execution/lifecycle result shared by presentation and future channels.

    The first fields preserve the existing ``ExecutionOutcome`` constructor
    shape.  ``turn_id``, evidence, artifacts, and errors are additive so the
    current resolver can be reused while the runtime adopts explicit turns.
    """

    request: str = ""
    task_id: Optional[str] = None
    session_id: Optional[str] = None
    capability: Optional[str] = None
    operation: Optional[str] = None
    status: str = ResultStatus.COMPLETED.value
    progress: float = 1.0
    result: Any = None
    verification: Optional[dict[str, Any]] = None
    risk_class: str = "safe"
    requires_approval: bool = False
    reason: str = ""
    source: str = "direct"
    data: dict[str, Any] = field(default_factory=dict)
    turn_id: Optional[str] = None
    evidence: list[Any] = field(default_factory=list)
    artifacts: list[Any] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.turn_id is not None:
            _require_text(self.turn_id, "turn_id")
        if self.task_id is not None:
            _require_text(self.task_id, "task_id")
        if self.session_id is not None:
            _require_text(self.session_id, "session_id")

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-shaped form used at process/event boundaries."""

        return {
            "request": self.request,
            "turn_id": self.turn_id,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "capability": self.capability,
            "operation": self.operation,
            "status": self.status,
            "progress": self.progress,
            "result": self.result,
            "evidence": list(self.evidence),
            "artifacts": list(self.artifacts),
            "verification": self.verification,
            "errors": list(self.errors),
            "risk_class": self.risk_class,
            "requires_approval": self.requires_approval,
            "reason": self.reason,
            "source": self.source,
            "data": dict(self.data),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ResultEnvelope":
        """Hydrate a result without inventing missing correlation IDs."""

        return cls(
            request=str(data.get("request", "")),
            turn_id=data.get("turn_id"),
            task_id=data.get("task_id"),
            session_id=data.get("session_id"),
            capability=data.get("capability"),
            operation=data.get("operation") or data.get("tool_name"),
            status=str(data.get("status", "completed")),
            progress=float(data.get("progress", 1.0)),
            result=data.get("result"),
            evidence=list(data.get("evidence") or []),
            artifacts=list(data.get("artifacts") or []),
            verification=data.get("verification"),
            errors=[str(error) for error in (data.get("errors") or [])],
            risk_class=str(data.get("risk_class", "safe")),
            requires_approval=bool(data.get("requires_approval", False)),
            reason=str(data.get("reason", "")),
            source=str(data.get("source", "direct")),
            data=dict(data.get("data") or {}),
        )


def validate_turn_chain(
    request: TurnRequest,
    *,
    context: Optional[TurnContext] = None,
    decision: Optional[IntentDecision] = None,
    result: Optional[ResultEnvelope] = None,
    executable: bool = False,
) -> None:
    """Validate identity continuity across the turn's planned boundaries."""

    if context is not None and (
        context.turn_id != request.turn_id
        or context.session_id != request.session_id
        or context.channel != request.channel
    ):
        raise TurnContractError("TurnContext identity does not match TurnRequest")

    if decision is not None and (
        decision.turn_id != request.turn_id or decision.session_id != request.session_id
    ):
        raise TurnContractError("IntentDecision identity does not match TurnRequest")

    if result is not None:
        if result.turn_id != request.turn_id or result.session_id != request.session_id:
            raise TurnContractError("ResultEnvelope identity does not match TurnRequest")
        if request.task_id is not None and result.task_id != request.task_id:
            raise TurnContractError("ResultEnvelope task_id does not match TurnRequest")

    if executable and request.task_id is None:
        raise TurnContractError("executable turns require a task_id")


__all__ = [
    "IntentDecision",
    "ResultEnvelope",
    "ResultStatus",
    "TurnContext",
    "TurnContractError",
    "TurnRequest",
    "validate_turn_chain",
]
