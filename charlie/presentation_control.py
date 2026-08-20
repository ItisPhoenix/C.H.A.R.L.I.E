"""Canonical semantic presentation control for explicit HUD requests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from charlie.events import EventMeta, EventSource, build_event
from charlie.presentation import ExecutionOutcome, PresentationContext, PresentationIntent
from charlie.presentation_registry import PresentationRegistry, get_presentation_registry


@dataclass(frozen=True)
class PresentationRequest:
    """Small semantic request; contains no renderer or layout coordinates."""

    action: str
    surface: Optional[str] = None
    source: EventSource = EventSource.BRAIN
    session_id: Optional[str] = None
    task_id: Optional[str] = None


@dataclass(frozen=True)
class PresentationControlResult:
    accepted: bool
    action: str
    raw_surface: Optional[str]
    canonical_surface: Optional[str]
    taxonomy: Optional[str]
    event: Optional[dict[str, Any]]
    message: str


class PresentationController:
    """Resolve explicit semantic requests and emit canonical event payloads."""

    _ACTIONS = frozenset(("show", "hide", "clear_screen"))

    def __init__(
        self,
        registry: Optional[PresentationRegistry] = None,
        resolver: Optional[Any] = None,
        event_sink: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> None:
        self.registry = registry or get_presentation_registry()
        if resolver is None:
            from charlie.presentation import PresentationResolver

            resolver = PresentationResolver(self.registry)
        self.resolver = resolver
        self._event_sink = event_sink

    def set_event_sink(self, event_sink: Optional[Callable[[dict[str, Any]], None]]) -> None:
        self._event_sink = event_sink

    def execute(self, request: PresentationRequest) -> PresentationControlResult:
        result = self.resolve(request)
        if result.accepted and result.event is not None and self._event_sink is not None:
            self._event_sink(result.event)
        return result

    def resolve(self, request: PresentationRequest) -> PresentationControlResult:
        action = request.action.strip().lower()
        if action not in self._ACTIONS:
            return self._rejected(request, f"Unsupported presentation action: {request.action}")

        if action == "clear_screen":
            if request.surface is not None:
                return self._rejected(request, "clear_screen does not accept a surface")
            event = build_event(
                "presentation_command",
                {"action": "clear_screen"},
                EventMeta(
                    source=request.source,
                    session_id=request.session_id,
                    task_id=request.task_id,
                    rationale="explicit semantic presentation request",
                ),
            )
            return PresentationControlResult(True, action, None, None, None, event, "Cleared the screen.")

        if not request.surface or not request.surface.strip():
            return self._rejected(request, f"{action} requires a presentation surface")

        resolved = self.registry.resolve_surface(request.surface)
        if resolved.status == "unknown":
            return self._rejected(request, f"Unsupported presentation surface: {request.surface}")
        if resolved.status == "ambiguous":
            choices = ", ".join(f"{taxonomy}/{canonical}" for taxonomy, canonical in resolved.matches)
            return self._rejected(request, f"Ambiguous presentation surface '{request.surface}': {choices}")
        taxonomy = resolved.taxonomy
        canonical = resolved.canonical
        descriptor = resolved.descriptor
        assert taxonomy is not None and canonical is not None
        if not getattr(descriptor, "implemented", True):
            return self._rejected(request, f"Presentation surface is not implemented: {canonical}")

        event_id = f"presentation:{taxonomy}:{canonical}"
        if action == "hide":
            event = build_event(
                "presentation_dismiss",
                {"id": event_id},
                EventMeta(
                    source=request.source,
                    session_id=request.session_id,
                    task_id=request.task_id,
                    rationale="explicit semantic presentation request",
                ),
            )
            return PresentationControlResult(
                True, action, request.surface, canonical, taxonomy, event, f"Hidden {canonical}."
            )

        outcome = ExecutionOutcome(
            request=f"show {request.surface}",
            task_id=request.task_id,
            session_id=request.session_id,
            source="presentation_control",
            data={"action": action, "surface": canonical, "taxonomy": taxonomy},
        )
        intent: PresentationIntent = self.resolver.resolve_explicit(
            outcome,
            taxonomy,
            canonical,
            descriptor,
            PresentationContext(user_intent="show", platform=request.source.value),
        )
        event = intent.to_event(source=request.source)
        return PresentationControlResult(
            True,
            action,
            request.surface,
            canonical,
            taxonomy,
            event,
            intent.spoken_text or f"Showing {canonical}.",
        )

    @staticmethod
    def _rejected(request: PresentationRequest, message: str) -> PresentationControlResult:
        return PresentationControlResult(False, request.action, request.surface, None, None, None, message)


_GLOBAL_CONTROLLER: Optional[PresentationController] = None


def get_presentation_controller() -> PresentationController:
    """Return the process-wide controller shared by voice and Brain tools."""
    global _GLOBAL_CONTROLLER
    if _GLOBAL_CONTROLLER is None:
        _GLOBAL_CONTROLLER = PresentationController()
    return _GLOBAL_CONTROLLER
