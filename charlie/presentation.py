"""PresentationResolver: canonical decision engine for Charlie presentation intent.

Decides *what* the user should see and hear (SILENT, CAPTION, NOTIFICATION,
WIDGET, COMPOSED_SURFACE, WORKSPACE, ATTENTION) from structured execution
outcomes, tasks, and events.

Presentation is completely decoupled from capability execution: capabilities
produce canonical ExecutionOutcomes; the PresentationResolver determines the
semantic PresentationIntent.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Dict, Optional, Set

from charlie.events import EventMeta, EventSource, EventType, build_event
from charlie.presentation_contract_generated import (
    AnchorTarget,
    DismissPolicy,
    PreferredZone,
    PresentationKind,
)
from charlie.utils import utc_now_iso
from charlie.research.router import is_briefing_query

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AttentionLevel(StrEnum):
    """Normalized urgency / attention hierarchy."""

    NONE = "none"
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


@dataclass
class PresentationIntent:
    """Canonical presentation intent emitted by the PresentationResolver."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    kind: PresentationKind = PresentationKind.SILENT
    source_event_id: Optional[str] = None
    task_id: Optional[str] = None
    session_id: Optional[str] = None
    capability: Optional[str] = None
    operation: Optional[str] = None

    title: str = ""
    summary: str = ""
    content: Dict[str, Any] = field(default_factory=dict)

    priority: int = 50  # 0 to 100
    attention_level: AttentionLevel = AttentionLevel.NORMAL

    dismiss_policy: DismissPolicy = DismissPolicy.TIMED
    auto_dismiss_ms: Optional[int] = None

    workspace_type: Optional[str] = None
    widget_type: Optional[str] = None
    overlay_type: Optional[str] = None
    surface_spec: Optional[Dict[str, Any]] = None

    preferred_zone: PreferredZone = PreferredZone.CONTEXTUAL
    anchor: AnchorTarget = AnchorTarget.CORE

    spoken_text: Optional[str] = None
    caption_text: Optional[str] = None

    created_at: str = field(default_factory=utc_now_iso)
    expires_at: Optional[str] = None

    replace_key: Optional[str] = None
    correlation_id: Optional[str] = None
    replayable: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Serialize canonical representation for wire transport and storage."""
        data: Dict[str, Any] = {
            "id": self.id,
            "kind": self.kind.value if hasattr(self.kind, "value") else str(self.kind),
            "priority": self.priority,
            "attention_level": (
                self.attention_level.value if hasattr(self.attention_level, "value") else str(self.attention_level)
            ),
            "dismiss_policy": (
                self.dismiss_policy.value if hasattr(self.dismiss_policy, "value") else str(self.dismiss_policy)
            ),
            "preferred_zone": (
                self.preferred_zone.value if hasattr(self.preferred_zone, "value") else str(self.preferred_zone)
            ),
            "anchor": self.anchor.value if hasattr(self.anchor, "value") else str(self.anchor),
            "created_at": self.created_at,
            "replayable": self.replayable,
        }
        if self.source_event_id is not None:
            data["source_event_id"] = self.source_event_id
        if self.task_id is not None:
            data["task_id"] = self.task_id
        if self.session_id is not None:
            data["session_id"] = self.session_id
        if self.capability is not None:
            data["capability"] = self.capability
        if self.operation is not None:
            data["operation"] = self.operation
        if self.title:
            data["title"] = self.title
        if self.summary:
            data["summary"] = self.summary
        if self.content:
            data["content"] = self.content
        if self.auto_dismiss_ms is not None:
            data["auto_dismiss_ms"] = self.auto_dismiss_ms
        if self.workspace_type is not None:
            data["workspace_type"] = self.workspace_type
        if self.widget_type is not None:
            data["widget_type"] = self.widget_type
        if self.overlay_type is not None:
            data["overlay_type"] = self.overlay_type
        if self.surface_spec is not None:
            data["surface_spec"] = self.surface_spec
        if self.spoken_text is not None:
            data["spoken_text"] = self.spoken_text
        if self.caption_text is not None:
            data["caption_text"] = self.caption_text
        if self.expires_at is not None:
            data["expires_at"] = self.expires_at
        if self.replace_key is not None:
            data["replace_key"] = self.replace_key
        if self.correlation_id is not None:
            data["correlation_id"] = self.correlation_id
        return data

    def to_event(
        self,
        event_type: str = EventType.PRESENTATION_INTENT.value,
        source: EventSource = EventSource.BRAIN,
    ) -> Dict[str, Any]:
        """Convert into a typed Phase-1 event envelope."""
        payload = self.to_dict()
        meta = EventMeta(
            source=source,
            task_id=self.task_id,
            session_id=self.session_id,
            rationale=f"presentation.{self.kind}",
        )
        return build_event(event_type, payload, meta)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PresentationIntent:
        """Hydrate a PresentationIntent from a dictionary."""
        kind_str = data.get("kind", "silent")
        try:
            kind = PresentationKind(kind_str)
        except ValueError:
            kind = PresentationKind.SILENT

        dismiss_str = data.get("dismiss_policy", "timed")
        try:
            dismiss_policy = DismissPolicy(dismiss_str)
        except ValueError:
            dismiss_policy = DismissPolicy.TIMED

        att_str = data.get("attention_level", "normal")
        try:
            attention_level = AttentionLevel(att_str)
        except ValueError:
            attention_level = AttentionLevel.NORMAL

        zone_str = data.get("preferred_zone", "contextual")
        try:
            preferred_zone = PreferredZone(zone_str)
        except ValueError:
            preferred_zone = PreferredZone.CONTEXTUAL

        anchor_str = data.get("anchor", "core")
        try:
            anchor = AnchorTarget(anchor_str)
        except ValueError:
            anchor = AnchorTarget.CORE

        return cls(
            id=data.get("id") or uuid.uuid4().hex,
            kind=kind,
            source_event_id=data.get("source_event_id"),
            task_id=data.get("task_id"),
            session_id=data.get("session_id"),
            capability=data.get("capability"),
            operation=data.get("operation"),
            title=str(data.get("title", "")),
            summary=str(data.get("summary", "")),
            content=data.get("content") or {},
            priority=int(data.get("priority", 50)),
            attention_level=attention_level,
            dismiss_policy=dismiss_policy,
            auto_dismiss_ms=data.get("auto_dismiss_ms"),
            workspace_type=data.get("workspace_type"),
            widget_type=data.get("widget_type"),
            overlay_type=data.get("overlay_type"),
            surface_spec=data.get("surface_spec"),
            preferred_zone=preferred_zone,
            anchor=anchor,
            spoken_text=data.get("spoken_text"),
            caption_text=data.get("caption_text"),
            created_at=data.get("created_at") or utc_now_iso(),
            expires_at=data.get("expires_at"),
            replace_key=data.get("replace_key"),
            correlation_id=data.get("correlation_id"),
            replayable=bool(data.get("replayable", False)),
        )


@dataclass
class PresentationContext:
    """Current presentation runtime and user context."""

    hud_open: bool = False
    active_workspace: Optional[str] = None
    user_intent: Optional[str] = None  # "show", "tell", "hide", "show_workspace", "quiet"
    platform: str = "voice"  # "voice", "chat", "desktop", "background"
    foreground: bool = True
    pinned_items: Set[str] = field(default_factory=set)


@dataclass
class ExecutionOutcome:
    """Canonical structure representing an execution or lifecycle result."""

    request: str = ""
    task_id: Optional[str] = None
    session_id: Optional[str] = None
    capability: Optional[str] = None
    operation: Optional[str] = None
    # status: completed, failed, partially_completed, unverified, running, waiting, approval_required, cancelled
    status: str = "completed"
    progress: float = 1.0
    result: Any = None
    verification: Optional[Dict[str, Any]] = None  # {verified: bool, status: str, message: str}
    risk_class: str = "safe"
    requires_approval: bool = False
    reason: str = ""
    # source: deterministic_fastpath, tool_execution, task_lifecycle, proactive_watcher
    source: str = "direct"
    data: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Default Timing Constants (ms)
# ---------------------------------------------------------------------------

DEFAULT_CAPTION_MS = 3000
DEFAULT_NOTIFICATION_MS = 5000


# ---------------------------------------------------------------------------
# Helper Regexes for Intent Inferences
# ---------------------------------------------------------------------------

_EXPLICIT_SHOW_RE = re.compile(r"\b(show|open|display|bring up|view|inspect)\b", re.IGNORECASE)
_EXPLICIT_TELL_RE = re.compile(r"\b(tell|say|read|speak|just tell|only tell)\b", re.IGNORECASE)
_MAP_QUERY_RE = re.compile(
    r"\b(where is|where's|route from|take me to|fly to|show earthquakes|"
    r"show conflicts|show map|open map|spatial map)\b",
    re.IGNORECASE,
)


def _preferred_zone(value: str) -> PreferredZone:
    """Convert registry zone metadata to the canonical enum with safe fallback."""
    try:
        return PreferredZone(value)
    except ValueError:
        return PreferredZone.CONTEXTUAL


# ---------------------------------------------------------------------------
# Canonical PresentationResolver
# ---------------------------------------------------------------------------


class PresentationResolver:
    """Authoritative decision engine for all Charlie presentation intent."""

    def __init__(self, presentation_registry: Optional[Any] = None):
        self._active_intents: Dict[str, PresentationIntent] = {}
        if presentation_registry is None:
            from charlie.presentation_registry import get_presentation_registry

            presentation_registry = get_presentation_registry()
        self._presentation_registry = presentation_registry

    def _build_surface_intent(
        self,
        outcome: ExecutionOutcome,
        *,
        semantic_role: Optional[str] = None,
        taxonomy: Optional[str] = None,
        canonical_surface: Optional[str] = None,
        content: Optional[Dict[str, Any]] = None,
        capability: Optional[str] = None,
        operation: Optional[str] = None,
        title: Optional[str] = None,
        summary: str = "",
        priority: int = 60,
        anchor_override: Optional[AnchorTarget] = None,
        spoken_text: Optional[str] = None,
        caption_text: Optional[str] = None,
        replace_key: Optional[str] = None,
        replayable: Optional[bool] = None,
    ) -> PresentationIntent:
        """Construct and validate one registry-backed canonical surface intent."""
        if semantic_role is not None:
            resolution = self._presentation_registry.resolve_semantic_target(semantic_role)
        elif taxonomy is not None and canonical_surface is not None:
            resolution = self._presentation_registry.resolve_typed_surface(taxonomy, canonical_surface)
        else:
            resolution = None

        if resolution is None or not resolution.resolved:
            return self._build_unavailable_surface(outcome, semantic_role or canonical_surface or "unknown")

        resolved_taxonomy = resolution.taxonomy
        resolved_surface = resolution.canonical
        descriptor = resolution.descriptor
        if not resolved_taxonomy or not resolved_surface or not getattr(descriptor, "implemented", True):
            return self._build_unavailable_surface(outcome, semantic_role or canonical_surface or "unknown")

        if resolved_taxonomy == "workspace":
            kind = PresentationKind.WORKSPACE
            dismiss_policy = DismissPolicy(descriptor.dismiss_policy)
            auto_dismiss_ms = None
            preferred_zone = PreferredZone.CENTER
            anchor = AnchorTarget.SCREEN
            default_replayable = True
        elif resolved_taxonomy == "widget":
            kind = PresentationKind.WIDGET
            dismiss_policy = DismissPolicy(descriptor.default_dismiss_policy)
            auto_dismiss_ms = descriptor.default_auto_dismiss_ms
            preferred_zone = _preferred_zone(descriptor.default_zone)
            anchor = AnchorTarget.CORE
            default_replayable = False
        else:
            kind = PresentationKind.OVERLAY
            dismiss_policy = DismissPolicy(descriptor.dismiss_policy)
            auto_dismiss_ms = None
            preferred_zone = PreferredZone.CENTER
            try:
                anchor = AnchorTarget(descriptor.anchor)
            except ValueError:
                anchor = AnchorTarget.SCREEN
            default_replayable = False

        surface_label = resolved_surface.replace("_", " ")
        intent = PresentationIntent(
            id=f"presentation:{resolved_taxonomy}:{resolved_surface}",
            kind=kind,
            task_id=outcome.task_id,
            session_id=outcome.session_id,
            capability=capability or outcome.capability,
            operation=operation or outcome.operation,
            title=title or surface_label.upper(),
            summary=summary,
            content=(
                content
                if content is not None
                else {
                    "surface": resolved_surface,
                    "taxonomy": resolved_taxonomy,
                    "source": outcome.source,
                }
            ),
            priority=priority,
            attention_level=AttentionLevel.NORMAL,
            dismiss_policy=dismiss_policy,
            auto_dismiss_ms=auto_dismiss_ms,
            workspace_type=resolved_surface if resolved_taxonomy == "workspace" else None,
            widget_type=resolved_surface if resolved_taxonomy == "widget" else None,
            overlay_type=resolved_surface if resolved_taxonomy == "overlay" else None,
            preferred_zone=preferred_zone,
            anchor=anchor_override or anchor,
            spoken_text=spoken_text,
            caption_text=caption_text,
            replace_key=replace_key or f"{resolved_taxonomy}:{resolved_surface}",
            replayable=default_replayable if replayable is None else replayable,
        )
        self._validate_surface_intent(intent)
        return intent

    def _validate_surface_intent(self, intent: PresentationIntent) -> None:
        """Reject unknown canonical surfaces before they reach EventBus/HUD."""
        checks = (
            ("workspace", intent.workspace_type),
            ("widget", intent.widget_type),
            ("overlay", intent.overlay_type),
        )
        for taxonomy, surface in checks:
            if surface is not None:
                resolution = self._presentation_registry.resolve_typed_surface(taxonomy, surface)
                if not resolution.resolved or not getattr(resolution.descriptor, "implemented", True):
                    raise ValueError(f"Unregistered presentation surface: {taxonomy}/{surface}")

    def _build_unavailable_surface(self, outcome: ExecutionOutcome, target: str) -> PresentationIntent:
        """Safe fallback for contract drift; never fabricate an unknown surface."""
        return PresentationIntent(
            kind=PresentationKind.NOTIFICATION,
            task_id=outcome.task_id,
            session_id=outcome.session_id,
            capability=outcome.capability,
            operation=outcome.operation,
            title="Presentation Unavailable",
            summary=f"Presentation surface '{target}' is unavailable.",
            priority=60,
            attention_level=AttentionLevel.NORMAL,
            dismiss_policy=DismissPolicy.TIMED,
            auto_dismiss_ms=DEFAULT_NOTIFICATION_MS,
            preferred_zone=PreferredZone.TOP_RIGHT,
            spoken_text=f"I couldn't open the requested presentation surface: {target}.",
            caption_text=f"Presentation unavailable: {target}",
            replace_key=f"presentation:unavailable:{target}",
            replayable=False,
        )

    def resolve_explicit(
        self,
        outcome: ExecutionOutcome,
        taxonomy: str,
        canonical_surface: str,
        descriptor: Any,
        context: Optional[PresentationContext] = None,
    ) -> PresentationIntent:
        """Resolve explicit semantic target through the same registry path as automatic routing."""
        intent = self._build_surface_intent(
            outcome,
            taxonomy=taxonomy,
            canonical_surface=canonical_surface,
            content={"surface": canonical_surface, "taxonomy": taxonomy, "source": outcome.source},
            summary=f"{canonical_surface.replace('_', ' ')} {taxonomy}",
            capability="presentation",
            anchor_override=AnchorTarget.CORE if taxonomy == "workspace" else None,
            spoken_text=f"Showing {canonical_surface.replace('_', ' ')}.",
            replace_key=f"presentation:{taxonomy}:{canonical_surface}",
        )
        intent.operation = f"presentation.{outcome.data.get('action', 'show')}"
        self._active_intents[intent.replace_key or intent.id] = intent
        return intent

    def resolve(
        self,
        outcome: ExecutionOutcome | Dict[str, Any],
        context: Optional[PresentationContext] = None,
    ) -> PresentationIntent:
        """Resolve an execution outcome into a canonical PresentationIntent."""
        if isinstance(outcome, dict):
            outcome = self._dict_to_outcome(outcome)

        ctx = context or PresentationContext()
        intent = self._evaluate_rules(outcome, ctx)

        key = intent.replace_key or intent.id
        self._active_intents[key] = intent
        return intent

    def _dict_to_outcome(self, d: Dict[str, Any]) -> ExecutionOutcome:
        return ExecutionOutcome(
            request=str(d.get("request", "")),
            task_id=d.get("task_id"),
            session_id=d.get("session_id"),
            capability=d.get("capability"),
            operation=d.get("operation") or d.get("tool_name"),
            status=str(d.get("status", "completed")),
            progress=float(d.get("progress", 1.0)),
            result=d.get("result"),
            verification=d.get("verification"),
            risk_class=str(d.get("risk_class", "safe")),
            requires_approval=bool(d.get("requires_approval", False)),
            reason=str(d.get("reason", "")),
            source=str(d.get("source", "direct")),
            data=d.get("data") or {},
        )

    def _evaluate_rules(self, outcome: ExecutionOutcome, ctx: PresentationContext) -> PresentationIntent:
        # 1. Approval Gate / Destructive Action -> ATTENTION
        if (
            outcome.requires_approval
            or outcome.status == "approval_required"
            or outcome.risk_class in ("destructive", "irreversible")
            and outcome.status == "waiting"
        ):
            return self._build_approval_attention(outcome, ctx)

        # 2. Extract text representations
        result_text = str(outcome.result) if outcome.result is not None else (outcome.reason or "")

        # 3. Detect user intent overrides (show vs tell vs hide)
        effective_user_intent = ctx.user_intent
        if effective_user_intent is None and outcome.request:
            if _EXPLICIT_TELL_RE.search(outcome.request):
                effective_user_intent = "tell"
            elif _EXPLICIT_SHOW_RE.search(outcome.request):
                effective_user_intent = "show"

        # Explicit "hide" -> SILENT
        if effective_user_intent == "hide":
            return PresentationIntent(
                kind=PresentationKind.SILENT,
                task_id=outcome.task_id,
                session_id=outcome.session_id,
                capability=outcome.capability,
                operation=outcome.operation,
                dismiss_policy=DismissPolicy.IMMEDIATE,
            )

        # 4. Verification Check & Result Text Adjustment
        is_verified = True
        verification_msg = ""
        if outcome.verification:
            is_verified = outcome.verification.get("verified", True)
            v_status = outcome.verification.get("status", "")
            verification_msg = outcome.verification.get("message", "")
            if (not is_verified or v_status in ("unverified", "partially_completed")) and outcome.status == "completed":
                outcome.status = v_status or "unverified"

        # 5. Failed execution -> NOTIFICATION or ATTENTION
        if outcome.status == "failed":
            return self._build_failure_presentation(outcome, ctx, result_text, verification_msg)

        # 6. Domain-specific Resolver Rules

        # Full system requests use the canonical SystemWorkspace. Narrow metric
        # requests remain compact system_metric widgets.
        if outcome.operation == "system.workspace.read":
            return self._build_surface_intent(
                outcome,
                taxonomy="workspace",
                canonical_surface="system",
                capability="system",
                operation=outcome.operation,
                title="Machine Diagnostics & System Telemetry",
                summary=result_text,
                content=outcome.data,
                spoken_text=result_text,
                caption_text=result_text,
                replace_key="workspace:system",
            )

        # System Telemetry (CPU / RAM / Disk / Battery / Health)
        if outcome.capability in ("system", "charlie.system") or (
            outcome.operation
            and outcome.operation
            in (
                "system.metrics.read",
                "system_diagnostics",
                "cpu_usage",
                "ram_usage",
            )
        ):
            return self._resolve_system_telemetry(outcome, ctx, result_text, effective_user_intent)

        # Media Control (Volume, Mute, Track controls)
        if outcome.capability in ("media", "charlie.media") or (
            outcome.operation and outcome.operation in ("media.volume.set", "media.playback.control", "set_volume")
        ):
            return self._resolve_media_control(outcome, ctx, result_text, effective_user_intent)

        # Desktop App Launch / Focus / Close
        if outcome.capability in ("desktop", "charlie.desktop") or (
            outcome.operation
            and outcome.operation
            in (
                "desktop.window.focus",
                "desktop.app.launch",
                "desktop.app.close",
                "desktop_focus",
                "desktop_launch",
                "desktop_close",
            )
        ):
            return self._resolve_desktop_action(outcome, ctx, result_text, effective_user_intent, is_verified)

        # Daily Briefing / News -> WORKSPACE (briefing)
        if outcome.operation in ("news_briefing", "daily_summary") or is_briefing_query(outcome.request or ""):
            return self._resolve_briefing_workspace(outcome, ctx, result_text)

        # Research & Deep Analysis -> WORKSPACE (research)
        if outcome.capability in ("research", "charlie.research") or (
            outcome.operation and outcome.operation in ("research.web.execute", "research_task", "deep_research")
        ):
            return self._resolve_research_workspace(outcome, ctx, result_text)

        # Geospatial Map & Intelligence -> WORKSPACE (map)
        if (
            outcome.capability in ("map", "spatial", "geospatial", "charlie.map")
            or (
                outcome.operation
                and outcome.operation
                in (
                    "map.view",
                    "map.navigate",
                    "map.route",
                    "map.layer",
                    "open_map",
                    "geocoding",
                )
            )
            or (outcome.request and _MAP_QUERY_RE.search(outcome.request))
        ):
            return self._resolve_map_workspace(outcome, ctx, result_text)

        # Terminal Session -> WORKSPACE (terminal)
        if outcome.capability in ("terminal", "charlie.terminal") or (
            outcome.operation
            and outcome.operation
            in (
                "terminal.command.execute",
                "terminal_session",
                "open_terminal",
            )
        ):
            return self._resolve_terminal_workspace(outcome, ctx, result_text)

        # Filesystem Query / Directory Listing
        if outcome.capability in ("file", "charlie.file") or (
            outcome.operation and outcome.operation in ("file.system.read", "file_read", "fs_list_dir")
        ):
            return self._resolve_file_operation(outcome, ctx, result_text, effective_user_intent)

        # Background Task Lifecycle updates (meaningful milestones only)
        if outcome.source == "task_lifecycle" or outcome.operation == "background_task":
            return self._resolve_task_lifecycle(outcome, ctx, result_text)

        # Proactive Watcher / Heartbeat / Silent Sync
        if outcome.source in ("watcher", "heartbeat", "internal"):
            return self._resolve_proactive_event(outcome, ctx, result_text)

        # Structurally rich data -> COMPOSED_SURFACE
        if isinstance(outcome.result, dict) and outcome.result.get("type") == "composed_surface":
            return self._resolve_composed_surface(outcome, ctx)

        # General Assistant Voice / Chat Response -> CAPTION
        return self._resolve_generic_caption(outcome, ctx, result_text)

    # -----------------------------------------------------------------------
    # Specific Domain Builders
    # -----------------------------------------------------------------------

    def _build_approval_attention(self, outcome: ExecutionOutcome, ctx: PresentationContext) -> PresentationIntent:
        return PresentationIntent(
            kind=PresentationKind.ATTENTION,
            task_id=outcome.task_id,
            session_id=outcome.session_id,
            capability=outcome.capability,
            operation=outcome.operation,
            title="Approval Required",
            summary=outcome.reason or f"Action '{outcome.operation}' requires your confirmation.",
            content={
                "tool_name": outcome.operation,
                "risk_class": outcome.risk_class,
                "reason": outcome.reason,
                "arguments": outcome.data.get("arguments", {}),
            },
            priority=90,
            attention_level=AttentionLevel.HIGH,
            dismiss_policy=DismissPolicy.MANUAL,
            preferred_zone=PreferredZone.CENTER,
            anchor=AnchorTarget.CORE,
            spoken_text="I need your approval before proceeding.",
            caption_text=f"Approval needed: {outcome.reason or outcome.operation}",
            replace_key=f"approval:{outcome.task_id or outcome.operation}",
            replayable=True,
        )

    def _build_failure_presentation(
        self,
        outcome: ExecutionOutcome,
        ctx: PresentationContext,
        result_text: str,
        verification_msg: str,
    ) -> PresentationIntent:
        msg = verification_msg or result_text or f"Operation '{outcome.operation}' failed."
        attention = AttentionLevel.CRITICAL if outcome.risk_class == "destructive" else AttentionLevel.NORMAL
        return PresentationIntent(
            kind=PresentationKind.NOTIFICATION,
            task_id=outcome.task_id,
            session_id=outcome.session_id,
            capability=outcome.capability,
            operation=outcome.operation,
            title="Action Failed",
            summary=msg,
            content={"error": msg, "details": outcome.data},
            priority=75,
            attention_level=attention,
            dismiss_policy=DismissPolicy.TIMED,
            auto_dismiss_ms=DEFAULT_NOTIFICATION_MS,
            preferred_zone=PreferredZone.TOP_RIGHT,
            spoken_text=f"I couldn't complete that: {msg}",
            caption_text=f"Failed: {msg}",
            replace_key=f"failure:{outcome.task_id or outcome.operation}",
            replayable=False,
        )

    def _resolve_system_telemetry(
        self,
        outcome: ExecutionOutcome,
        ctx: PresentationContext,
        result_text: str,
        user_intent: Optional[str],
    ) -> PresentationIntent:
        # If user explicitly said "tell me...", emit voice caption only
        if user_intent == "tell":
            return PresentationIntent(
                kind=PresentationKind.CAPTION,
                task_id=outcome.task_id,
                session_id=outcome.session_id,
                capability="system",
                operation=outcome.operation,
                title="System Telemetry",
                summary=result_text,
                spoken_text=result_text,
                caption_text=result_text,
                dismiss_policy=DismissPolicy.TIMED,
                auto_dismiss_ms=DEFAULT_CAPTION_MS,
                replayable=False,
            )

        # Default query/show: Emit WIDGET (system_metric)
        return self._build_surface_intent(
            outcome,
            semantic_role="system_metrics",
            capability="system",
            operation=outcome.operation or "system.metrics.read",
            title="System Telemetry",
            summary=result_text,
            priority=50,
            content={
                "metrics": outcome.data.get("metrics", outcome.data),
                "text": result_text,
            },
            spoken_text=result_text,
            caption_text=result_text,
            replace_key="widget:system_metric",
        )

    def _resolve_media_control(
        self,
        outcome: ExecutionOutcome,
        ctx: PresentationContext,
        result_text: str,
        user_intent: Optional[str],
    ) -> PresentationIntent:
        if user_intent == "show":
            return self._build_surface_intent(
                outcome,
                semantic_role="media_control",
                capability="media",
                priority=50,
                title="Media Player",
                summary=result_text,
                content=outcome.data,
                spoken_text=result_text,
                caption_text=result_text,
                replace_key="widget:media_control",
            )

        return PresentationIntent(
            kind=PresentationKind.CAPTION,
            task_id=outcome.task_id,
            session_id=outcome.session_id,
            capability="media",
            operation=outcome.operation,
            title="Media",
            summary=result_text,
            spoken_text=result_text,
            caption_text=result_text,
            dismiss_policy=DismissPolicy.TIMED,
            auto_dismiss_ms=DEFAULT_CAPTION_MS,
            replace_key="caption:media",
            replayable=False,
        )

    def _resolve_desktop_action(
        self,
        outcome: ExecutionOutcome,
        ctx: PresentationContext,
        result_text: str,
        user_intent: Optional[str],
        is_verified: bool,
    ) -> PresentationIntent:
        spoken = result_text
        if not is_verified and outcome.status == "unverified":
            spoken = f"Performed action, but could not verify window state: {result_text}"

        return PresentationIntent(
            kind=PresentationKind.CAPTION,
            task_id=outcome.task_id,
            session_id=outcome.session_id,
            capability="desktop",
            operation=outcome.operation,
            title="Desktop Action",
            summary=result_text,
            spoken_text=spoken,
            caption_text=result_text,
            dismiss_policy=DismissPolicy.TIMED,
            auto_dismiss_ms=DEFAULT_CAPTION_MS,
            replace_key="caption:desktop",
            replayable=False,
        )

    def _resolve_research_workspace(
        self,
        outcome: ExecutionOutcome,
        ctx: PresentationContext,
        result_text: str,
    ) -> PresentationIntent:
        from charlie.research.presentation import validate_workspace_payload, workspace_payload_spec

        spec = workspace_payload_spec("research")
        payload = dict(outcome.data) if validate_workspace_payload(outcome.data, "research") else {
            "schema": spec["schema"],
            "version": spec["version"],
            "query": outcome.request,
            "mode": "standard",
            "title": "Research & Synthesis",
            "summary": result_text[:1200],
            "status": "partial",
            "confidence": 0.0,
            "findings": [],
            "sources": [],
            "timeline_items": [],
        }
        return self._build_surface_intent(
            outcome,
            semantic_role="research_result",
            capability="research",
            priority=65,
            title=payload.get("title", "Research & Synthesis"),
            summary=payload.get("summary", "Research completed."),
            content=payload,
            spoken_text="I've compiled the research findings on your canvas.",
            caption_text="Research Workspace opened",
            replace_key=f"workspace:research:{outcome.task_id or 'main'}",
        )

    def _resolve_briefing_workspace(
        self,
        outcome: ExecutionOutcome,
        ctx: PresentationContext,
        result_text: str,
    ) -> PresentationIntent:
        from charlie.research.presentation import validate_workspace_payload, workspace_payload_spec

        spec = workspace_payload_spec("briefing")
        if validate_workspace_payload(outcome.data, "briefing"):
            payload = dict(outcome.data)
        else:
            payload = {
                "schema": spec["schema"],
                "version": spec["version"],
                "title": "Daily Briefing",
                "headline": "Daily Intelligence Briefing",
                "summary": result_text[:1200],
                "stories": [],
                "summaries": [],
                "timeline_items": [],
                "sources": [],
                "status": "partial",
                "confidence": 0.0,
            }
        content_dict: Dict[str, Any] = dict(payload)
        return self._build_surface_intent(
            outcome,
            semantic_role="daily_briefing",
            capability="system",
            operation=outcome.operation or "news_briefing",
            title=payload.get("title", "Daily Briefing"),
            summary=payload.get("summary", "Today's briefing."),
            priority=60,
            content=content_dict,
            spoken_text="Here is your daily briefing.",
            caption_text="Daily Briefing opened",
            replace_key="workspace:briefing",
        )

    def _resolve_terminal_workspace(
        self,
        outcome: ExecutionOutcome,
        ctx: PresentationContext,
        result_text: str,
    ) -> PresentationIntent:
        return self._build_surface_intent(
            outcome,
            semantic_role="terminal",
            capability="terminal",
            title="Terminal",
            summary="Terminal Session Active",
            priority=60,
            content={"output": result_text, "cwd": outcome.data.get("cwd", "")},
            caption_text="Terminal Workspace active",
            replace_key="workspace:terminal",
        )

    def _resolve_map_workspace(
        self,
        outcome: ExecutionOutcome,
        ctx: PresentationContext,
        result_text: str,
    ) -> PresentationIntent:
        content_dict: Dict[str, Any] = {"mode": "geo"}
        if isinstance(outcome.result, dict):
            content_dict.update(outcome.result)
        if outcome.data:
            content_dict.update(outcome.data)

        title = content_dict.get("title") or outcome.request or "Spatial Intelligence Map"
        spoken = (
            content_dict.get("spoken_summary")
            or result_text
            or f"Navigating to {title} on the spatial map."
        )

        return self._build_surface_intent(
            outcome,
            semantic_role="geospatial",
            operation=outcome.operation or "map.view",
            title=title,
            summary=result_text[:120] if result_text else "Spatial map navigation active.",
            content=content_dict,
            priority=65,
            spoken_text=spoken,
            caption_text=f"Map: {title}",
            replace_key=f"workspace:map:{outcome.task_id or 'main'}",
        )

    def _resolve_file_operation(
        self,
        outcome: ExecutionOutcome,
        ctx: PresentationContext,
        result_text: str,
        user_intent: Optional[str],
    ) -> PresentationIntent:
        if user_intent == "show" or (outcome.request and "show" in outcome.request.lower()):
            return self._build_surface_intent(
                outcome,
                semantic_role="file_viewer",
                capability="file",
                title="Directory Listing",
                summary=result_text,
                priority=50,
                content={"listing": result_text, "path": outcome.data.get("path")},
                spoken_text=result_text[:100],
                caption_text=result_text[:80],
                replace_key=f"file:{outcome.data.get('path', 'current')}",
            )

        return PresentationIntent(
            kind=PresentationKind.CAPTION,
            task_id=outcome.task_id,
            session_id=outcome.session_id,
            capability="file",
            operation=outcome.operation,
            title="File Operation",
            summary=result_text,
            spoken_text=result_text,
            caption_text=result_text,
            dismiss_policy=DismissPolicy.TIMED,
            auto_dismiss_ms=DEFAULT_CAPTION_MS,
            replace_key="caption:file",
            replayable=False,
        )

    def _resolve_task_lifecycle(
        self,
        outcome: ExecutionOutcome,
        ctx: PresentationContext,
        result_text: str,
    ) -> PresentationIntent:
        if outcome.status in ("running", "verifying", "planning", "queued"):
            return PresentationIntent(
                kind=PresentationKind.SILENT,
                task_id=outcome.task_id,
                session_id=outcome.session_id,
                capability="task",
                operation=outcome.operation,
                dismiss_policy=DismissPolicy.IMMEDIATE,
            )

        if outcome.status in ("completed", "done"):
            return PresentationIntent(
                kind=PresentationKind.NOTIFICATION,
                task_id=outcome.task_id,
                session_id=outcome.session_id,
                capability="task",
                operation=outcome.operation,
                title="Task Completed",
                summary=result_text or f"Background task {outcome.task_id} completed.",
                content={"task_id": outcome.task_id, "result": outcome.result},
                priority=50,
                attention_level=AttentionLevel.LOW,
                dismiss_policy=DismissPolicy.TIMED,
                auto_dismiss_ms=DEFAULT_NOTIFICATION_MS,
                preferred_zone=PreferredZone.TOP_RIGHT,
                spoken_text=f"Task {outcome.task_id or ''} is finished.",
                caption_text=f"Task completed: {outcome.task_id or ''}",
                replace_key=f"task:{outcome.task_id}",
                replayable=False,
            )

        if outcome.status == "cancelled":
            return PresentationIntent(
                kind=PresentationKind.NOTIFICATION,
                task_id=outcome.task_id,
                session_id=outcome.session_id,
                capability="task",
                operation=outcome.operation,
                title="Task Cancelled",
                summary=f"Task {outcome.task_id} was cancelled.",
                priority=40,
                attention_level=AttentionLevel.LOW,
                dismiss_policy=DismissPolicy.TIMED,
                auto_dismiss_ms=DEFAULT_NOTIFICATION_MS,
                preferred_zone=PreferredZone.TOP_RIGHT,
                caption_text="Task cancelled",
                replace_key=f"task:{outcome.task_id}",
                replayable=False,
            )

        return PresentationIntent(
            kind=PresentationKind.SILENT,
            task_id=outcome.task_id,
            session_id=outcome.session_id,
        )

    def _resolve_proactive_event(
        self,
        outcome: ExecutionOutcome,
        ctx: PresentationContext,
        result_text: str,
    ) -> PresentationIntent:
        if outcome.risk_class == "safe" and not outcome.data.get("alert"):
            return PresentationIntent(
                kind=PresentationKind.SILENT,
                task_id=outcome.task_id,
                session_id=outcome.session_id,
                dismiss_policy=DismissPolicy.IMMEDIATE,
            )

        return PresentationIntent(
            kind=PresentationKind.NOTIFICATION,
            task_id=outcome.task_id,
            session_id=outcome.session_id,
            capability=outcome.capability,
            title="System Alert",
            summary=result_text or "Proactive system notice",
            content=outcome.data,
            priority=60,
            attention_level=AttentionLevel.NORMAL,
            dismiss_policy=DismissPolicy.TIMED,
            auto_dismiss_ms=DEFAULT_NOTIFICATION_MS,
            preferred_zone=PreferredZone.TOP_RIGHT,
            caption_text=result_text,
            replace_key="notification:proactive",
            replayable=False,
        )

    def _resolve_composed_surface(
        self,
        outcome: ExecutionOutcome,
        ctx: PresentationContext,
    ) -> PresentationIntent:
        data = outcome.result if isinstance(outcome.result, dict) else {}
        surface_type = data.get("surface_type", "comparison")
        target = data.get("target", "workspace")
        is_workspace = target == "workspace"
        intent = self._build_surface_intent(
            outcome,
            semantic_role="composed_workspace" if is_workspace else "composed_widget",
            title=data.get("title", "Composed Surface"),
            summary=data.get("summary", ""),
            content=data.get("data", data),
            priority=55,
            spoken_text=data.get("spoken_summary"),
            caption_text=data.get("caption_summary"),
            replace_key=f"surface:{data.get('surface_id', surface_type)}",
            replayable=True,
        )
        if intent.kind in (PresentationKind.WORKSPACE, PresentationKind.WIDGET):
            intent.kind = PresentationKind.COMPOSED_SURFACE
            intent.surface_spec = data
            self._validate_surface_intent(intent)
        return intent

    def _resolve_generic_caption(
        self,
        outcome: ExecutionOutcome,
        ctx: PresentationContext,
        result_text: str,
    ) -> PresentationIntent:
        return PresentationIntent(
            kind=PresentationKind.CAPTION,
            task_id=outcome.task_id,
            session_id=outcome.session_id,
            capability=outcome.capability,
            operation=outcome.operation,
            title="Assistant Response",
            summary=result_text,
            spoken_text=result_text,
            caption_text=result_text,
            dismiss_policy=DismissPolicy.TIMED,
            auto_dismiss_ms=DEFAULT_CAPTION_MS,
            replace_key="caption:chat",
            replayable=False,
        )

# Global singleton resolver instance
default_presentation_resolver = PresentationResolver()
