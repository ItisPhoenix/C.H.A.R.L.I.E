"""Unit and integration tests for Charlie V1 Phase 5 PresentationResolver."""

from charlie.events import EventType
from charlie.presentation import (
    AnchorTarget,
    AttentionLevel,
    DismissPolicy,
    ExecutionOutcome,
    PreferredZone,
    PresentationContext,
    PresentationIntent,
    PresentationKind,
    PresentationResolver,
)


class TestPresentationKindSelection:
    """Test 1: Kind selection rules for canonical capabilities and operations."""

    def test_cpu_telemetry_resolves_to_widget(self):
        resolver = PresentationResolver()
        outcome = ExecutionOutcome(
            request="what is the cpu usage?",
            capability="system",
            operation="system.metrics.read",
            result="CPU is currently 14%",
            status="completed",
        )
        intent = resolver.resolve(outcome)
        assert intent.kind == PresentationKind.WIDGET
        assert intent.widget_type == "system_metric"
        assert intent.auto_dismiss_ms == 5000
        assert intent.dismiss_policy == DismissPolicy.TIMED
        assert intent.replace_key == "widget:system_metric"
        assert intent.anchor == AnchorTarget.CORE

    def test_volume_mutation_resolves_to_caption(self):
        resolver = PresentationResolver()
        outcome = ExecutionOutcome(
            request="set volume to 50%",
            capability="media",
            operation="media.volume.set",
            result="Volume set to 50%.",
            status="completed",
            verification={"verified": True, "status": "completed"},
        )
        intent = resolver.resolve(outcome)
        assert intent.kind == PresentationKind.CAPTION
        assert intent.auto_dismiss_ms == 3000
        assert intent.spoken_text == "Volume set to 50%."
        assert intent.caption_text == "Volume set to 50%."
        assert intent.replace_key == "caption:media"

    def test_app_focus_resolves_to_caption(self):
        resolver = PresentationResolver()
        outcome = ExecutionOutcome(
            request="switch to Spotify",
            capability="desktop",
            operation="desktop.window.focus",
            result="Focused window 'Spotify Premium'.",
            status="completed",
            verification={"verified": True, "status": "completed"},
        )
        intent = resolver.resolve(outcome)
        assert intent.kind == PresentationKind.CAPTION
        assert intent.auto_dismiss_ms == 3000
        assert "Spotify" in (intent.spoken_text or "")

    def test_research_task_resolves_to_workspace(self):
        resolver = PresentationResolver()
        outcome = ExecutionOutcome(
            request="research NVIDIA Blackwell architecture",
            capability="research",
            operation="research.web.execute",
            result="NVIDIA Blackwell B200 features 208B transistors...",
            status="completed",
            data={"sources": ["https://nvidia.com"], "findings": ["208B transistors"]},
        )
        intent = resolver.resolve(outcome)
        assert intent.kind == PresentationKind.WORKSPACE
        assert intent.workspace_type == "research"
        assert intent.dismiss_policy == DismissPolicy.PERSISTENT
        assert intent.replayable is True
        assert intent.anchor == AnchorTarget.SCREEN

    def test_daily_briefing_resolves_to_workspace(self):
        resolver = PresentationResolver()
        outcome = ExecutionOutcome(
            request="what's happening today?",
            operation="news_briefing",
            result="You have 3 calendar events and 2 unread emails.",
            status="completed",
        )
        intent = resolver.resolve(outcome)
        assert intent.kind == PresentationKind.WORKSPACE
        assert intent.workspace_type == "briefing"
        assert intent.dismiss_policy == DismissPolicy.PERSISTENT
        assert intent.replayable is True

    def test_terminal_resolves_to_workspace(self):
        resolver = PresentationResolver()
        outcome = ExecutionOutcome(
            request="open terminal",
            capability="terminal",
            operation="terminal.command.execute",
            result="PowerShell 7 session initialized",
            status="completed",
        )
        intent = resolver.resolve(outcome)
        assert intent.kind == PresentationKind.WORKSPACE
        assert intent.workspace_type == "terminal"
        assert intent.replayable is True

    def test_approval_gate_resolves_to_attention(self):
        resolver = PresentationResolver()
        outcome = ExecutionOutcome(
            request="delete directory /tmp/build",
            capability="file",
            operation="file.system.write",
            requires_approval=True,
            risk_class="destructive",
            reason="Mass deletion of directory requires approval",
            status="approval_required",
        )
        intent = resolver.resolve(outcome)
        assert intent.kind == PresentationKind.ATTENTION
        assert intent.attention_level == AttentionLevel.HIGH
        assert intent.dismiss_policy == DismissPolicy.MANUAL
        assert intent.preferred_zone == PreferredZone.CENTER
        assert intent.replayable is True

    def test_background_task_completion_resolves_to_notification(self):
        resolver = PresentationResolver()
        outcome = ExecutionOutcome(
            task_id="task-999",
            capability="task",
            operation="background_task",
            status="completed",
            result="Backup completed successfully.",
            source="task_lifecycle",
        )
        intent = resolver.resolve(outcome)
        assert intent.kind == PresentationKind.NOTIFICATION
        assert intent.auto_dismiss_ms == 5000
        assert intent.replace_key == "task:task-999"

    def test_internal_heartbeat_resolves_to_silent(self):
        resolver = PresentationResolver()
        outcome = ExecutionOutcome(
            source="heartbeat",
            risk_class="safe",
            status="completed",
            data={"alert": False},
        )
        intent = resolver.resolve(outcome)
        assert intent.kind == PresentationKind.SILENT
        assert intent.dismiss_policy == DismissPolicy.IMMEDIATE


class TestPresentationContextOverrides:
    """Test 2: Contextual overrides (explicit show, tell, hide)."""

    def test_explicit_tell_forces_caption_suppressing_widget(self):
        resolver = PresentationResolver()
        outcome = ExecutionOutcome(
            request="just tell me the cpu usage",
            capability="system",
            operation="system.metrics.read",
            result="CPU is at 22%",
            status="completed",
        )
        ctx = PresentationContext(user_intent="tell")
        intent = resolver.resolve(outcome, ctx)
        assert intent.kind == PresentationKind.CAPTION
        assert intent.spoken_text == "CPU is at 22%"

    def test_explicit_show_upgrades_media_to_widget(self):
        resolver = PresentationResolver()
        outcome = ExecutionOutcome(
            request="show media player",
            capability="media",
            operation="media.volume.set",
            result="Spotify playing: Starboy by The Weeknd",
            status="completed",
        )
        ctx = PresentationContext(user_intent="show")
        intent = resolver.resolve(outcome, ctx)
        assert intent.kind == PresentationKind.WIDGET
        assert intent.widget_type == "media_control"
        assert intent.auto_dismiss_ms == 6000

    def test_explicit_hide_forces_silent(self):
        resolver = PresentationResolver()
        outcome = ExecutionOutcome(
            request="don't show me anything",
            capability="system",
            operation="system.metrics.read",
            result="CPU 10%",
            status="completed",
        )
        ctx = PresentationContext(user_intent="hide")
        intent = resolver.resolve(outcome, ctx)
        assert intent.kind == PresentationKind.SILENT


class TestVerificationAwarePresentation:
    """Test 3: Verification status influence on presentation."""

    def test_unverified_action_truthfully_reflects_status(self):
        resolver = PresentationResolver()
        outcome = ExecutionOutcome(
            request="switch to Spotify",
            capability="desktop",
            operation="desktop.window.focus",
            result="Requested window focus.",
            status="completed",
            verification={
                "verified": False,
                "status": "unverified",
                "message": "Spotify window was not found in foreground.",
            },
        )
        intent = resolver.resolve(outcome)
        assert intent.kind == PresentationKind.CAPTION
        assert "could not verify" in (intent.spoken_text or "").lower()

    def test_failed_operation_resolves_to_notification(self):
        resolver = PresentationResolver()
        outcome = ExecutionOutcome(
            request="kill process 99999",
            capability="system",
            operation="system_control",
            status="failed",
            verification={
                "verified": False,
                "status": "failed",
                "message": "Process 99999 not found",
            },
        )
        intent = resolver.resolve(outcome)
        assert intent.kind == PresentationKind.NOTIFICATION
        assert "Process 99999 not found" in intent.summary


class TestReplayAndDeduplication:
    """Test 4: Replay filtering and replace_key deduplication."""

    def test_workspace_and_approval_are_replayable(self):
        resolver = PresentationResolver()
        res_outcome = ExecutionOutcome(
            capability="research",
            operation="research.web.execute",
            result="Research data",
            status="completed",
        )
        res_intent = resolver.resolve(res_outcome)
        assert res_intent.replayable is True

        appr_outcome = ExecutionOutcome(
            capability="system",
            operation="shell_execute",
            requires_approval=True,
            reason="Gated command",
            status="approval_required",
        )
        appr_intent = resolver.resolve(appr_outcome)
        assert appr_intent.replayable is True

    def test_transient_caption_and_telemetry_are_not_replayable(self):
        resolver = PresentationResolver()
        cap_outcome = ExecutionOutcome(
            capability="media",
            operation="media.volume.set",
            result="Volume 50%",
            status="completed",
        )
        cap_intent = resolver.resolve(cap_outcome)
        assert cap_intent.replayable is False

        tel_outcome = ExecutionOutcome(
            capability="system",
            operation="system.metrics.read",
            result="CPU 15%",
            status="completed",
        )
        tel_intent = resolver.resolve(tel_outcome)
        assert tel_intent.replayable is False

    def test_replace_key_deduplication(self):
        resolver = PresentationResolver()
        o1 = ExecutionOutcome(
            capability="system",
            operation="system.metrics.read",
            result="CPU is at 10%",
            status="completed",
        )
        i1 = resolver.resolve(o1)
        assert i1.replace_key == "widget:system_metric"

        o2 = ExecutionOutcome(
            capability="system",
            operation="system.metrics.read",
            result="CPU is at 25%",
            status="completed",
        )
        i2 = resolver.resolve(o2)
        assert i2.replace_key == "widget:system_metric"


class TestLegacySurfaceEngineAdapter:
    """Test 5: Adapter translation to legacy SurfaceSpec."""

    def test_to_legacy_surface_spec_widget(self):
        intent = PresentationIntent(
            id="test_widget",
            kind=PresentationKind.WIDGET,
            widget_type="system_metric",
            title="System Telemetry",
            summary="CPU is 15%",
            auto_dismiss_ms=5000,
            dismiss_policy=DismissPolicy.TIMED,
        )
        spec = PresentationResolver.to_legacy_surface_spec(intent)
        assert spec.presentation.value == "widget"
        assert spec.persistence.value == "ephemeral"
        assert spec.ttl_seconds == 5.0
        assert spec.title == "System Telemetry"
        assert spec.body == "CPU is 15%"

    def test_to_legacy_surface_spec_workspace(self):
        intent = PresentationIntent(
            id="test_ws",
            kind=PresentationKind.WORKSPACE,
            workspace_type="research",
            title="Research Workspace",
            summary="Deep analysis findings",
            dismiss_policy=DismissPolicy.PERSISTENT,
            replayable=True,
        )
        spec = PresentationResolver.to_legacy_surface_spec(intent)
        assert spec.presentation.value == "workspace"
        assert spec.persistence.value == "persistent"
        assert spec.ttl_seconds is None


class TestEventContractDrift:
    """Test 6: PresentationIntent wire serialization satisfies event contract."""

    def test_presentation_intent_to_event(self):
        intent = PresentationIntent(
            id="intent_abc",
            kind=PresentationKind.WIDGET,
            widget_type="system_metric",
            title="CPU Usage",
            summary="CPU 12%",
            auto_dismiss_ms=5000,
        )
        evt = intent.to_event()
        assert evt["type"] == EventType.PRESENTATION_INTENT.value
        assert evt["payload"]["id"] == "intent_abc"
        assert evt["payload"]["kind"] == "widget"
        assert evt["payload"]["auto_dismiss_ms"] == 5000
