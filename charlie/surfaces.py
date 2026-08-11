"""Surface Engine: pure presentation-mode decision logic, no GUI import.

Decides where/how a result surfaces -- never renders anything, runs headless
in CI. Two orthogonal axes per SurfaceSpec: PresentationMode (where/how) and
density 0-4 (how much is visible); Persistence is a third, independent
lifecycle class. Decision order, highest priority first: explicit
user_intent -> attention-level gate (approvals exempt, same as
charlie.attention's own INTERRUPT-always design) -> event/task category.
Explicit intent short-circuits everything below it.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Dict, List, Optional

from charlie.attention import AttentionLevel
from charlie.events import EventType

_MIN_ATTENTION_FOR_SURFACE = AttentionLevel.INFORM

_APPROVAL_EVENT_TYPES = frozenset({
    EventType.TOOL_APPROVAL_REQUEST, EventType.EXTENSION_PENDING, EventType.RECOVERY_PROPOSAL,
})
_RESULT_EVENT_TYPES = frozenset({EventType.RESULT_STORED})
_INFO_EVENT_TYPES = frozenset({EventType.ALERT, EventType.SYSTEM_STATUS})

# Cap pools ("3 widgets + 1 workspace" default) -- MODAL/BACKGROUND are exempt, never evicted.
_WIDGET_MODES = frozenset({"widget", "floating", "notification"})
_WORKSPACE_MODES = frozenset({"workspace"})

# Priority-ordered abstract regions (charlie/hud/placement.py maps these to real screen rects); overflow wraps.
_REGION_ORDER = ("top_right", "bottom_right", "top_left", "bottom_left")
_EPHEMERAL_SPAWN_TTL_SECONDS = 30.0


class PresentationMode(StrEnum):
    BACKGROUND = "background"
    NOTIFICATION = "notification"
    WIDGET = "widget"
    FLOATING = "floating"
    MODAL = "modal"
    WORKSPACE = "workspace"


class Persistence(StrEnum):
    EPHEMERAL = "ephemeral"
    PERSISTENT = "persistent"
    ARCHIVED = "archived"


class UserIntent(StrEnum):
    HIDE = "hide"    # "don't show me anything"
    SHOW = "show"    # "show me what you're doing"
    TELL = "tell"    # "just tell me" -- voice only


_CATEGORY_TO_MODE: Dict[str, PresentationMode] = {
    "approval": PresentationMode.MODAL,
    "result_interaction": PresentationMode.MODAL,
    "sustained_interaction": PresentationMode.WORKSPACE,
    "conversation": PresentationMode.WORKSPACE,
    "information_only": PresentationMode.BACKGROUND,
    "no_involvement": PresentationMode.BACKGROUND,
}

_CATEGORY_TO_PERSISTENCE: Dict[str, Persistence] = {
    "approval": Persistence.PERSISTENT,
    "sustained_interaction": Persistence.PERSISTENT,
    "conversation": Persistence.PERSISTENT,
    "result_interaction": Persistence.ARCHIVED,
    "information_only": Persistence.EPHEMERAL,
    "no_involvement": Persistence.EPHEMERAL,
}


@dataclass
class SurfaceSpec:
    presentation: PresentationMode
    persistence: Persistence
    density: int
    rationale: str
    task_id: Optional[str] = None
    region: str = ""


def _categorize(event: Dict[str, Any], task: Optional[Any]) -> str:
    try:
        etype = EventType(event.get("type"))
    except ValueError:
        etype = None

    if etype in _APPROVAL_EVENT_TYPES:
        return "approval"
    if etype in _RESULT_EVENT_TYPES:
        return "result_interaction"
    if etype == EventType.CONVERSATION_SUMMON:
        return "conversation"
    if task is not None and getattr(task, "visibility_hint", None) == "workspace":
        return "sustained_interaction"
    if etype in _INFO_EVENT_TYPES:
        return "information_only"
    return "no_involvement"


class SurfaceEngine:
    """decide() is stateless; spawn()/dismiss() track ACTIVE surfaces only to enforce the cap."""

    def __init__(self, widget_cap: int = 3, workspace_cap: int = 1):
        self.widget_cap = widget_cap
        self.workspace_cap = workspace_cap
        self._active: Dict[str, SurfaceSpec] = {}
        self._order: List[str] = []

    def decide(
        self,
        event: Dict[str, Any],
        task: Optional[Any] = None,
        attention: AttentionLevel = AttentionLevel.SILENT,
        ctx: Optional[Any] = None,
        user_intent: Optional[UserIntent] = None,
    ) -> Optional[SurfaceSpec]:
        category = _categorize(event, task)

        if user_intent in (UserIntent.HIDE, UserIntent.TELL):
            return None
        if category != "approval" and user_intent != UserIntent.SHOW and attention < _MIN_ATTENTION_FOR_SURFACE:
            return None

        mode = _CATEGORY_TO_MODE[category]
        persistence = _CATEGORY_TO_PERSISTENCE[category]
        density = int(AttentionLevel.INTERRUPT) if category == "approval" else max(
            int(attention), int(_MIN_ATTENTION_FOR_SURFACE)
        )
        if user_intent == UserIntent.SHOW and mode == PresentationMode.BACKGROUND:
            mode = PresentationMode.WIDGET

        return SurfaceSpec(
            presentation=mode,
            persistence=persistence,
            density=density,
            rationale=f"{category} at attention {attention.name}",
            task_id=getattr(task, "id", None),
        )

    def spawn(self, surface_id: str, spec: SurfaceSpec) -> List[str]:
        """Register spec as ACTIVE and enforce the cap for its presentation class.

        Returns ids evicted (non-destructive -- caller persists them via
        charlie.results.ResultsStore before dropping the window).
        """
        self._active[surface_id] = spec
        self._order.append(surface_id)
        spec.region = self._assign_region(spec.presentation)
        return self._evict_over_cap(spec.presentation, protect=surface_id)

    def dismiss(self, surface_id: str) -> None:
        self._active.pop(surface_id, None)
        if surface_id in self._order:
            self._order.remove(surface_id)

    def _assign_region(self, mode: PresentationMode) -> str:
        if mode in _WORKSPACE_MODES:
            return "center"
        if mode not in _WIDGET_MODES:
            return ""
        class_ids = [sid for sid in self._order if self._active[sid].presentation in _WIDGET_MODES]
        return _REGION_ORDER[(len(class_ids) - 1) % len(_REGION_ORDER)]

    def spawn_event(self, surface_id: str, spec: SurfaceSpec) -> Dict[str, Any]:
        ttl = _EPHEMERAL_SPAWN_TTL_SECONDS if spec.persistence == Persistence.EPHEMERAL else None
        return self._build_event(EventType.SURFACE_SPAWN, surface_id, spec, ttl=ttl)

    def update_event(self, surface_id: str, spec: SurfaceSpec) -> Dict[str, Any]:
        return self._build_event(EventType.SURFACE_UPDATE, surface_id, spec)

    def dismiss_event(self, surface_id: str, spec: SurfaceSpec) -> Dict[str, Any]:
        return self._build_event(EventType.SURFACE_DISMISS, surface_id, spec)

    @staticmethod
    def _build_event(
        event_type: EventType, surface_id: str, spec: SurfaceSpec, ttl: Optional[float] = None
    ) -> Dict[str, Any]:
        payload = {
            "surface_id": surface_id,
            "presentation": spec.presentation.value,
            "persistence": spec.persistence.value,
            "density": spec.density,
            "region": spec.region,
            "task_id": spec.task_id,
            "rationale": spec.rationale,
        }
        if ttl is not None:
            payload["ttl_seconds"] = ttl
        return {"type": event_type, "payload": payload}

    def _evict_over_cap(self, mode: PresentationMode, protect: str) -> List[str]:
        if mode in _WORKSPACE_MODES:
            class_modes, cap = _WORKSPACE_MODES, self.workspace_cap
        elif mode in _WIDGET_MODES:
            class_modes, cap = _WIDGET_MODES, self.widget_cap
        else:
            return []

        class_ids = [sid for sid in self._order if self._active[sid].presentation in class_modes]
        evicted: List[str] = []
        while len(class_ids) > cap:
            candidates = [
                sid for sid in class_ids
                if sid != protect and self._active[sid].persistence != Persistence.PERSISTENT
            ]
            if not candidates:
                break
            victim = min(candidates, key=lambda sid: (self._active[sid].density, self._order.index(sid)))
            self.dismiss(victim)
            class_ids.remove(victim)
            evicted.append(victim)
        return evicted
