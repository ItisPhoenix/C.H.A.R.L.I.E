from dataclasses import dataclass

from charlie.attention import AttentionLevel
from charlie.events import EventType
from charlie.surfaces import Persistence, PresentationMode, SurfaceEngine, SurfaceSpec, UserIntent


@dataclass
class _FakeTask:
    id: str = "t1"
    visibility_hint: str = ""


def test_decide_returns_none_below_attention_l2_with_no_intent():
    engine = SurfaceEngine()
    event = {"type": EventType.ALERT, "payload": {"severity": "warning"}}

    result = engine.decide(event, attention=AttentionLevel.PASSIVE)

    assert result is None


def test_decide_hide_intent_short_circuits_even_at_high_attention():
    engine = SurfaceEngine()
    event = {"type": EventType.TOOL_APPROVAL_REQUEST, "payload": {}}

    result = engine.decide(event, attention=AttentionLevel.INTERRUPT, user_intent=UserIntent.HIDE)

    assert result is None


def test_decide_tell_intent_short_circuits_even_for_approvals():
    engine = SurfaceEngine()
    event = {"type": EventType.TOOL_APPROVAL_REQUEST, "payload": {}}

    result = engine.decide(event, attention=AttentionLevel.INTERRUPT, user_intent=UserIntent.TELL)

    assert result is None


def test_decide_approval_bypasses_attention_gate():
    engine = SurfaceEngine()
    event = {"type": EventType.TOOL_APPROVAL_REQUEST, "payload": {}}

    result = engine.decide(event, attention=AttentionLevel.SILENT)

    assert result is not None
    assert result.presentation == PresentationMode.MODAL
    assert result.persistence == Persistence.PERSISTENT
    assert result.density == int(AttentionLevel.INTERRUPT)


def test_decide_result_stored_is_modal_and_archived():
    engine = SurfaceEngine()
    event = {"type": EventType.RESULT_STORED, "payload": {"task_id": "t1"}}

    result = engine.decide(event, attention=AttentionLevel.INFORM)

    assert result.presentation == PresentationMode.MODAL
    assert result.persistence == Persistence.ARCHIVED


def test_decide_sustained_interaction_task_is_workspace_and_persistent():
    engine = SurfaceEngine()
    event = {"type": EventType.BACKGROUND_TASK, "payload": {"status": "running"}}
    task = _FakeTask(visibility_hint="workspace")

    result = engine.decide(event, task=task, attention=AttentionLevel.INFORM)

    assert result.presentation == PresentationMode.WORKSPACE
    assert result.persistence == Persistence.PERSISTENT
    assert result.task_id == "t1"


def test_decide_information_only_is_background_and_ephemeral():
    engine = SurfaceEngine()
    event = {"type": EventType.ALERT, "payload": {"severity": "warning"}}

    result = engine.decide(event, attention=AttentionLevel.INFORM)

    assert result.presentation == PresentationMode.BACKGROUND
    assert result.persistence == Persistence.EPHEMERAL


def test_decide_no_involvement_defaults_to_background():
    engine = SurfaceEngine()
    event = {"type": EventType.TOKEN, "payload": {}}

    result = engine.decide(event, attention=AttentionLevel.ATTENTION)

    assert result.presentation == PresentationMode.BACKGROUND


def test_decide_show_intent_forces_a_surface_below_gate():
    engine = SurfaceEngine()
    event = {"type": EventType.TOKEN, "payload": {}}

    result = engine.decide(event, attention=AttentionLevel.SILENT, user_intent=UserIntent.SHOW)

    assert result is not None
    assert result.presentation == PresentationMode.WIDGET


def test_decide_rationale_is_nonempty_string():
    engine = SurfaceEngine()
    event = {"type": EventType.ALERT, "payload": {"severity": "warning"}}

    result = engine.decide(event, attention=AttentionLevel.INFORM)

    assert isinstance(result.rationale, str) and result.rationale


# --- cap + eviction ---


def _widget_spec(density=2, persistence=Persistence.EPHEMERAL):
    return SurfaceSpec(
        presentation=PresentationMode.WIDGET, persistence=persistence, density=density, rationale="test"
    )


def test_spawn_under_cap_evicts_nothing():
    engine = SurfaceEngine(widget_cap=3)
    assert engine.spawn("w1", _widget_spec()) == []
    assert engine.spawn("w2", _widget_spec()) == []
    assert engine.spawn("w3", _widget_spec()) == []


def test_spawn_over_cap_evicts_lowest_density_oldest():
    engine = SurfaceEngine(widget_cap=3)
    engine.spawn("w1", _widget_spec(density=2))
    engine.spawn("w2", _widget_spec(density=3))
    engine.spawn("w3", _widget_spec(density=2))

    evicted = engine.spawn("w4", _widget_spec(density=4))

    assert evicted == ["w1"]  # lowest density (2) among ties, oldest of the two


def test_spawn_over_cap_never_evicts_persistent():
    engine = SurfaceEngine(widget_cap=1)
    engine.spawn("w1", _widget_spec(density=1, persistence=Persistence.PERSISTENT))

    evicted = engine.spawn("w2", _widget_spec(density=4))

    assert evicted == []  # w1 is PERSISTENT, over cap but nothing evictable


def test_workspace_cap_evicts_across_separate_pool_from_widgets():
    engine = SurfaceEngine(widget_cap=3, workspace_cap=1)
    workspace_spec = SurfaceSpec(
        presentation=PresentationMode.WORKSPACE, persistence=Persistence.EPHEMERAL, density=2, rationale="t"
    )
    engine.spawn("ws1", workspace_spec)
    engine.spawn("w1", _widget_spec())  # widget pool, must not interfere

    evicted = engine.spawn("ws2", workspace_spec)

    assert evicted == ["ws1"]


def test_dismiss_frees_a_cap_slot():
    engine = SurfaceEngine(widget_cap=1)
    engine.spawn("w1", _widget_spec())
    engine.dismiss("w1")

    evicted = engine.spawn("w2", _widget_spec())

    assert evicted == []


# --- region assignment (priority-ordered, overflow stacks) ---


def test_spawn_assigns_priority_ordered_regions_to_widgets():
    engine = SurfaceEngine(widget_cap=3)
    engine.spawn("w1", spec1 := _widget_spec())
    engine.spawn("w2", spec2 := _widget_spec())

    assert spec1.region == "top_right"
    assert spec2.region == "bottom_right"


def test_spawn_overflow_stacks_back_onto_first_region():
    engine = SurfaceEngine(widget_cap=10)
    for i in range(5):
        engine.spawn(f"w{i}", _widget_spec())

    assert engine._active["w0"].region == "top_right"
    assert engine._active["w4"].region == "top_right"  # wraps after 4 named regions


def test_spawn_assigns_center_region_to_workspace():
    engine = SurfaceEngine()
    spec = SurfaceSpec(
        presentation=PresentationMode.WORKSPACE, persistence=Persistence.EPHEMERAL, density=2, rationale="t"
    )

    engine.spawn("ws1", spec)

    assert spec.region == "center"


# --- lifecycle event builders (SPAWN -> ACTIVE -> UPDATE -> COMPLETE -> DISMISS) ---


def test_spawn_event_shape():
    engine = SurfaceEngine()
    spec = _widget_spec(density=3)
    engine.spawn("w1", spec)

    event = engine.spawn_event("w1", spec)

    assert event["type"] == EventType.SURFACE_SPAWN
    assert event["payload"]["surface_id"] == "w1"
    assert event["payload"]["presentation"] == "widget"
    assert event["payload"]["density"] == 3


def test_spawn_event_carries_ttl_for_ephemeral_only():
    engine = SurfaceEngine()
    ephemeral = _widget_spec(persistence=Persistence.EPHEMERAL)
    persistent = _widget_spec(persistence=Persistence.PERSISTENT)

    assert "ttl_seconds" in engine.spawn_event("w1", ephemeral)["payload"]
    assert "ttl_seconds" not in engine.spawn_event("w2", persistent)["payload"]


def test_update_event_shape():
    engine = SurfaceEngine()
    spec = _widget_spec(density=4)

    event = engine.update_event("w1", spec)

    assert event["type"] == EventType.SURFACE_UPDATE
    assert event["payload"]["density"] == 4


def test_dismiss_event_shape():
    engine = SurfaceEngine()
    spec = _widget_spec()

    event = engine.dismiss_event("w1", spec)

    assert event["type"] == EventType.SURFACE_DISMISS
    assert event["payload"]["surface_id"] == "w1"
