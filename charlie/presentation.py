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
from charlie.utils import utc_now_iso

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class PresentationKind(StrEnum):
    """Canonical presentation modalities."""

    SILENT = "silent"
    CAPTION = "caption"
    NOTIFICATION = "notification"
    WIDGET = "widget"
    COMPOSED_SURFACE = "composed_surface"
    WORKSPACE = "workspace"
    ATTENTION = "attention"


class DismissPolicy(StrEnum):
    """Semantic lifecycle and auto-dismiss policy."""

    IMMEDIATE = "immediate"
    TIMED = "timed"
    MANUAL = "manual"
    PERSISTENT = "persistent"
    TASK_LIFETIME = "task_lifetime"


class AttentionLevel(StrEnum):
    """Normalized urgency / attention hierarchy."""

    NONE = "none"
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class PreferredZone(StrEnum):
    """Abstract spatial placement zone hint (no pixel coordinates)."""

    CONTEXTUAL = "contextual"
    TOP_RIGHT = "top_right"
    BOTTOM_RIGHT = "bottom_right"
    TOP_LEFT = "top_left"
    BOTTOM_LEFT = "bottom_left"
    CENTER = "center"


class AnchorTarget(StrEnum):
    """Visual anchor target."""

    CORE = "core"
    WORKSPACE = "workspace"
    SCREEN = "screen"
    WIDGET = "widget"


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
DEFAULT_SYSTEM_METRIC_WIDGET_MS = 5000
DEFAULT_MEDIA_WIDGET_MS = 6000
DEFAULT_NOTIFICATION_MS = 5000


# ---------------------------------------------------------------------------
# Helper Regexes for Intent Inferences
# ---------------------------------------------------------------------------

_EXPLICIT_SHOW_RE = re.compile(r"\b(show|open|display|bring up|view|inspect)\b", re.IGNORECASE)
_EXPLICIT_TELL_RE = re.compile(r"\b(tell|say|read|speak|just tell|only tell)\b", re.IGNORECASE)
_MAP_QUERY_RE = re.compile(r"\b(where is|where's|route from|take me to|fly to|show earthquakes|show conflicts|show map|open map|spatial map)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Canonical PresentationResolver
# ---------------------------------------------------------------------------


class PresentationResolver:
    """Authoritative decision engine for all Charlie presentation intent."""

    def __init__(self):
        self._active_intents: Dict[str, PresentationIntent] = {}

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

        # Daily Briefing / News -> WORKSPACE (briefing)
        if outcome.operation in ("news_briefing", "daily_summary") or (
            outcome.request
            and any(k in outcome.request.lower() for k in ("what's happening today", "daily briefing", "news"))
        ):
            return self._resolve_briefing_workspace(outcome, ctx, result_text)

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
        return PresentationIntent(
            kind=PresentationKind.WIDGET,
            task_id=outcome.task_id,
            session_id=outcome.session_id,
            capability="system",
            operation=outcome.operation or "system.metrics.read",
            title="System Telemetry",
            summary=result_text,
            content={
                "metrics": outcome.data.get("metrics", {}),
                "text": result_text,
            },
            priority=50,
            attention_level=AttentionLevel.NORMAL,
            dismiss_policy=DismissPolicy.TIMED,
            auto_dismiss_ms=DEFAULT_SYSTEM_METRIC_WIDGET_MS,
            widget_type="system_metric",
            preferred_zone=PreferredZone.TOP_RIGHT,
            anchor=AnchorTarget.CORE,
            spoken_text=result_text,
            caption_text=result_text,
            replace_key="widget:system_metric",
            replayable=False,
        )

    def _resolve_media_control(
        self,
        outcome: ExecutionOutcome,
        ctx: PresentationContext,
        result_text: str,
        user_intent: Optional[str],
    ) -> PresentationIntent:
        if user_intent == "show":
            return PresentationIntent(
                kind=PresentationKind.WIDGET,
                task_id=outcome.task_id,
                session_id=outcome.session_id,
                capability="media",
                operation=outcome.operation,
                title="Media Player",
                summary=result_text,
                content=outcome.data,
                dismiss_policy=DismissPolicy.TIMED,
                auto_dismiss_ms=DEFAULT_MEDIA_WIDGET_MS,
                widget_type="media_control",
                preferred_zone=PreferredZone.BOTTOM_RIGHT,
                spoken_text=result_text,
                caption_text=result_text,
                replace_key="widget:media_control",
                replayable=False,
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
        return PresentationIntent(
            kind=PresentationKind.WORKSPACE,
            task_id=outcome.task_id,
            session_id=outcome.session_id,
            capability="research",
            operation=outcome.operation,
            title="Research Findings",
            summary=result_text[:120] if result_text else "Research completed.",
            content={
                "report": result_text,
                "sources": outcome.data.get("sources", []),
                "findings": outcome.data.get("findings", []),
            },
            priority=65,
            attention_level=AttentionLevel.NORMAL,
            dismiss_policy=DismissPolicy.PERSISTENT,
            workspace_type="research",
            preferred_zone=PreferredZone.CENTER,
            anchor=AnchorTarget.SCREEN,
            spoken_text="I've compiled the research findings on your canvas.",
            caption_text="Research Workspace opened",
            replace_key=f"workspace:research:{outcome.task_id or 'main'}",
            replayable=True,
        )

    def _resolve_briefing_workspace(
        self,
        outcome: ExecutionOutcome,
        ctx: PresentationContext,
        result_text: str,
    ) -> PresentationIntent:
        return PresentationIntent(
            kind=PresentationKind.WORKSPACE,
            task_id=outcome.task_id,
            session_id=outcome.session_id,
            capability="system",
            operation=outcome.operation or "news_briefing",
            title="Daily Briefing",
            summary=result_text[:120] if result_text else "Today's briefing.",
            content={"briefing": result_text, "data": outcome.data},
            priority=60,
            attention_level=AttentionLevel.NORMAL,
            dismiss_policy=DismissPolicy.PERSISTENT,
            workspace_type="briefing",
            preferred_zone=PreferredZone.CENTER,
            anchor=AnchorTarget.SCREEN,
            spoken_text="Here is your daily briefing.",
            caption_text="Daily Briefing opened",
            replace_key="workspace:briefing",
            replayable=True,
        )

    def _resolve_terminal_workspace(
        self,
        outcome: ExecutionOutcome,
        ctx: PresentationContext,
        result_text: str,
    ) -> PresentationIntent:
        return PresentationIntent(
            kind=PresentationKind.WORKSPACE,
            task_id=outcome.task_id,
            session_id=outcome.session_id,
            capability="terminal",
            operation=outcome.operation,
            title="Terminal",
            summary="Terminal Session Active",
            content={"output": result_text, "cwd": outcome.data.get("cwd", "")},
            priority=60,
            attention_level=AttentionLevel.NORMAL,
            dismiss_policy=DismissPolicy.PERSISTENT,
            workspace_type="terminal",
            preferred_zone=PreferredZone.CENTER,
            anchor=AnchorTarget.SCREEN,
            caption_text="Terminal Workspace active",
            replace_key="workspace:terminal",
            replayable=True,
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

        return PresentationIntent(
            kind=PresentationKind.WORKSPACE,
            task_id=outcome.task_id,
            session_id=outcome.session_id,
            capability=outcome.capability or "map",
            operation=outcome.operation or "map.view",
            title=title,
            summary=result_text[:120] if result_text else "Spatial map navigation active.",
            content=content_dict,
            priority=65,
            attention_level=AttentionLevel.NORMAL,
            dismiss_policy=DismissPolicy.PERSISTENT,
            workspace_type="map",
            preferred_zone=PreferredZone.CENTER,
            anchor=AnchorTarget.SCREEN,
            spoken_text=spoken,
            caption_text=f"Map: {title}",
            replace_key=f"workspace:map:{outcome.task_id or 'main'}",
            replayable=True,
        )

    def _resolve_file_operation(
        self,
        outcome: ExecutionOutcome,
        ctx: PresentationContext,
        result_text: str,
        user_intent: Optional[str],
    ) -> PresentationIntent:
        if user_intent == "show" or (outcome.request and "show" in outcome.request.lower()):
            return PresentationIntent(
                kind=PresentationKind.WIDGET,
                task_id=outcome.task_id,
                session_id=outcome.session_id,
                capability="file",
                operation=outcome.operation,
                title="Directory Listing",
                summary=result_text,
                content={"listing": result_text, "path": outcome.data.get("path")},
                dismiss_policy=DismissPolicy.TIMED,
                auto_dismiss_ms=DEFAULT_SYSTEM_METRIC_WIDGET_MS,
                widget_type="file_viewer",
                preferred_zone=PreferredZone.TOP_RIGHT,
                spoken_text=result_text[:100],
                caption_text=result_text[:80],
                replace_key=f"file:{outcome.data.get('path', 'current')}",
                replayable=False,
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

        return PresentationIntent(
            kind=PresentationKind.COMPOSED_SURFACE,
            task_id=outcome.task_id,
            session_id=outcome.session_id,
            capability=outcome.capability,
            operation=outcome.operation,
            title=data.get("title", "Composed Surface"),
            summary=data.get("summary", ""),
            content=data.get("data", data),
            surface_spec=data,
            priority=55,
            attention_level=AttentionLevel.NORMAL,
            dismiss_policy=DismissPolicy.PERSISTENT if is_workspace else DismissPolicy.TIMED,
            auto_dismiss_ms=None if is_workspace else 8000,
            workspace_type="composed_surface" if is_workspace else None,
            widget_type="composed_surface" if not is_workspace else None,
            preferred_zone=PreferredZone.CENTER if is_workspace else PreferredZone.TOP_RIGHT,
            anchor=AnchorTarget.SCREEN if is_workspace else AnchorTarget.CORE,
            spoken_text=data.get("spoken_summary"),
            caption_text=data.get("caption_summary"),
            replace_key=f"surface:{data.get('surface_id', surface_type)}",
            replayable=True,
        )

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

    # -----------------------------------------------------------------------
    # Legacy SurfaceEngine Adapter
    # -----------------------------------------------------------------------

    @staticmethod
    def to_legacy_surface_spec(intent: PresentationIntent) -> Any:
        """Translate a canonical PresentationIntent into a legacy SurfaceSpec."""
        from charlie.surfaces import Persistence, PresentationMode, SurfaceSpec

        mode_map = {
            PresentationKind.SILENT: PresentationMode.BACKGROUND,
            PresentationKind.CAPTION: PresentationMode.NOTIFICATION,
            PresentationKind.NOTIFICATION: PresentationMode.NOTIFICATION,
            PresentationKind.WIDGET: PresentationMode.WIDGET,
            PresentationKind.COMPOSED_SURFACE: PresentationMode.MODAL,
            PresentationKind.WORKSPACE: PresentationMode.WORKSPACE,
            PresentationKind.ATTENTION: PresentationMode.MODAL,
        }
        persistence_map = {
            DismissPolicy.IMMEDIATE: Persistence.EPHEMERAL,
            DismissPolicy.TIMED: Persistence.EPHEMERAL,
            DismissPolicy.TASK_LIFETIME: Persistence.EPHEMERAL,
            DismissPolicy.MANUAL: Persistence.PERSISTENT,
            DismissPolicy.PERSISTENT: Persistence.PERSISTENT,
        }

        ttl = float(intent.auto_dismiss_ms) / 1000.0 if intent.auto_dismiss_ms else None
        return SurfaceSpec(
            presentation=mode_map.get(intent.kind, PresentationMode.WIDGET),
            persistence=persistence_map.get(intent.dismiss_policy, Persistence.EPHEMERAL),
            density=3 if intent.attention_level == AttentionLevel.HIGH else 2,
            rationale=f"Resolved from {intent.kind}",
            task_id=intent.task_id,
            title=intent.title,
            body=intent.summary,
            role="danger" if intent.kind == PresentationKind.ATTENTION else "info",
            ttl_seconds=ttl,
            kind=intent.widget_type or intent.workspace_type or "generic",
        )


# Global singleton resolver instance
default_presentation_resolver = PresentationResolver()
