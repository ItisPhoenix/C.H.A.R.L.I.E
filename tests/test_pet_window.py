from pathlib import Path

import charlie.pet_window as pet_window
from charlie.pet_window import (
    ANIMATION_CLIPS,
    CHARLIE_ATLAS_PATH,
    SPRITE_CLIP_FRAMES,
    PetActivityModel,
    PetAnimator,
    PetLayoutEngine,
    PositionRecord,
    ScreenInfo,
    _elide_text,
    _extract_last_sentence,
    _map_event_to_caption_desc,
    _map_event_to_state,
    _state_caption_title,
    _track_workspace_presentation,
    activity_orientation,
    clamp_position,
    detect_anchor,
    load_position_record,
    position_record_json,
    resolve_animation_clip,
    snapped_position,
)


def test_charlie_uses_one_original_sprite_atlas_for_all_visual_states():
    assert CHARLIE_ATLAS_PATH.is_file()
    assert len(SPRITE_CLIP_FRAMES) >= 18
    assert all(len(frames) >= 2 for frames in SPRITE_CLIP_FRAMES.values())
    assert max(frame for frames in SPRITE_CLIP_FRAMES.values() for frame in frames) < 20


def test_map_event_to_state_passes_through_all_nine_core_states():
    for state in ("idle", "listening", "thinking", "speaking", "working", "waiting", "attention", "completed", "error"):
        event = {"type": "charlie_state", "payload": {"state": state}}
        assert _map_event_to_state(event) == state


def test_map_event_to_state_ignores_non_charlie_state_events():
    assert _map_event_to_state({"type": "token", "payload": {}}) is None


def test_map_event_to_state_ignores_unknown_state_value():
    assert _map_event_to_state({"type": "charlie_state", "payload": {"state": "sleeping"}}) is None


def test_map_event_to_caption_desc_approval_family():
    event = {"type": "tool_approval_request", "payload": {"reason": "delete a file"}}
    assert _map_event_to_caption_desc(event) == "delete a file"


def test_map_event_to_caption_desc_alert_warning():
    event = {"type": "alert", "payload": {"severity": "warning", "message": "disk low"}}
    assert _map_event_to_caption_desc(event) == "disk low"


def test_map_event_to_caption_desc_alert_info_is_silent():
    event = {"type": "alert", "payload": {"severity": "info", "message": "fyi"}}
    assert _map_event_to_caption_desc(event) is None


def test_track_workspace_presentation_intent_then_dismiss():
    active: set = set()
    spawn = {"type": "presentation_intent", "payload": {"id": "ws1", "kind": "workspace"}}
    dismiss = {"type": "presentation_dismiss", "payload": {"id": "ws1"}}

    assert _track_workspace_presentation(active, spawn) is True
    assert _track_workspace_presentation(active, dismiss) is False


def test_track_workspace_presentation_ignores_non_workspace_intent():
    active: set = set()
    spawn = {"type": "presentation_intent", "payload": {"id": "w1", "kind": "widget"}}
    assert _track_workspace_presentation(active, spawn) is None


def test_track_workspace_presentation_second_intent_does_not_re_emit():
    active: set = set()
    spawn1 = {"type": "presentation_intent", "payload": {"id": "ws1", "kind": "workspace"}}
    spawn2 = {"type": "presentation_intent", "payload": {"id": "ws2", "kind": "workspace"}}

    assert _track_workspace_presentation(active, spawn1) is True
    assert _track_workspace_presentation(active, spawn2) is None


def test_map_event_to_caption_desc_speaking_stop_clears():
    assert _map_event_to_caption_desc({"type": "speaking_stop", "payload": {}}) == ""


def test_map_event_to_caption_desc_response_done_clears():
    assert _map_event_to_caption_desc({"type": "response_done", "payload": {}}) == ""


def test_map_event_to_caption_desc_unknown_event_is_not_a_clear_signal():
    # Must stay distinct from the explicit "" clear above, or _sub_loop would spam-clear captions.
    assert _map_event_to_caption_desc({"type": "system_status", "payload": {}}) is None


def test_map_event_to_caption_desc_speaking_start_has_no_mapping():
    # Fires once per TTS-flushed sentence chunk -- a mapping here would stomp live token-driven text.
    assert _map_event_to_caption_desc({"type": "speaking_start", "payload": {}}) is None


def test_state_caption_title_known_states():
    assert _state_caption_title("speaking") == "Speaking"
    assert _state_caption_title("attention") == "Needs attention"


def test_state_caption_title_unknown_state_falls_back_to_capitalized():
    assert _state_caption_title("sleeping") == "Sleeping"


def test_state_caption_title_empty_state():
    assert _state_caption_title("") == ""


def test_extract_last_sentence_returns_latest_complete_sentence():
    assert _extract_last_sentence("Hello there. How are you?") == "How are you?"


def test_extract_last_sentence_returns_trailing_incomplete_sentence():
    assert _extract_last_sentence("Hello there. I am still think") == "I am still think"


def test_extract_last_sentence_empty_buffer():
    assert _extract_last_sentence("   ") == ""


def test_elide_text_returns_unchanged_when_it_fits():
    assert _elide_text("short", len, max_width=20) == "short"


def test_elide_text_truncates_with_ellipsis_when_too_wide():
    result = _elide_text("this is a much too long caption line", len, max_width=10)
    assert result.endswith("...")
    assert len(result) <= 10


def test_elide_text_empty_string():
    assert _elide_text("", len, max_width=10) == ""


def test_animation_clip_catalog_covers_required_motion_language():
    required = {
        "idle",
        "idle_blink",
        "idle_look",
        "hover",
        "pressed",
        "drag_left",
        "drag_right",
        "drag_up",
        "drag_down",
        "landing",
        "listening",
        "thinking",
        "speaking",
        "working",
        "waiting",
        "attention",
        "completed",
        "error",
        "ptt_listening",
    }
    assert required <= ANIMATION_CLIPS.keys()


def test_interaction_resolver_preserves_semantic_state_under_drag():
    assert resolve_animation_clip("working", "dragging") == "drag_right"
    assert resolve_animation_clip("error", "dragging") == "drag_right"
    assert resolve_animation_clip("working", "normal") == "working"
    assert resolve_animation_clip("working", "normal", ptt=True) == "ptt_listening"


def test_animator_returns_to_semantic_clip_after_landing():
    animator = PetAnimator()
    animator.set_semantic("working")
    animator.set_interaction("landing")
    for _ in range(8):
        animator.tick(0.1)
    assert animator.semantic == "working"
    assert animator.clip_name == "working"


def test_animator_returns_from_completion_clip_to_stable_motion():
    animator = PetAnimator()
    animator.set_semantic("working")
    animator.set_semantic("completed")
    for _ in range(8):
        animator.tick(0.1)
    assert animator.semantic == "completed"
    assert animator.clip_name == "idle"


def test_geometry_detects_only_near_edges_and_corners():
    screen = ScreenInfo("main", 0, 0, 1920, 1080)
    assert detect_anchor(10, 20, 200, 120, screen) == "top_left"
    assert detect_anchor(1710, 940, 200, 120, screen) == "bottom_right"
    assert detect_anchor(800, 400, 200, 120, screen) == "free"


def test_snapping_is_soft_and_clamped():
    screen = ScreenInfo("main", 0, 0, 1920, 1080)
    position, anchor = snapped_position(4, 500, 200, 120, screen)
    assert anchor == "left"
    assert position == (14, 500)
    assert clamp_position(-100, 1000, 200, 120, screen) == (0, 960)


def test_activity_orientation_points_toward_screen_space():
    assert activity_orientation("bottom_right") == "left"
    assert activity_orientation("top_left") == "right"
    assert activity_orientation("top") == "down"
    assert activity_orientation("free") == "up"


def test_position_record_json_is_v2_and_old_shape_migrates():
    path = Path("pet_position_test.json")
    try:
        path.write_text('{"x": 12, "y": 34, "scale": 3}', encoding="utf-8")
        record = load_position_record(path)
        assert record.x == 12 and record.y == 34
        assert record.scale == 2.0
        assert position_record_json(PositionRecord(1, 2))["version"] == 2
    finally:
        path.unlink(missing_ok=True)


def test_invalid_position_json_uses_safe_defaults():
    path = Path("pet_position_invalid_test.json")
    try:
        path.write_text("not json", encoding="utf-8")
        record = load_position_record(path, default_scale=0.8)
        assert record == PositionRecord(0, 0, 0.8)
    finally:
        path.unlink(missing_ok=True)


def test_activity_model_tracks_tasks_and_approval():
    model = PetActivityModel()
    model.apply({"type": "background_task", "payload": {"id": "t1", "title": "Index files", "status": "running"}})
    assert model.active_count == 1
    model.apply(
        {
            "type": "tool_approval_request",
            "payload": {"request_id": "r1", "tool_name": "delete", "reason": "Remove temp"},
        }
    )
    assert model.approval is not None and model.approval.request_id == "r1"
    model.apply({"type": "tool_approval_resolved", "payload": {"request_id": "r1"}})
    assert model.approval is None


def test_activity_model_accepts_canonical_approval_presentation():
    model = PetActivityModel()
    model.apply({
        "type": "presentation_intent",
        "payload": {
            "id": "r2",
            "kind": "attention",
            "title": "Approval needed: delete",
            "summary": "Remove temporary file?",
            "content": {"request_id": "r2", "reason": "Remove temporary file?"},
        },
    })
    assert model.approval is not None
    assert model.approval.request_id == "r2"


def test_expanded_layout_stays_inside_logical_window_for_each_edge():
    engine = PetLayoutEngine()
    for anchor in ("top", "bottom", "left", "right", "top_left", "bottom_right"):
        layout = engine.calculate(anchor, expanded=True, approval=True)
        assert layout.activity.left() >= 0
        assert layout.activity.top() >= 0
        assert layout.activity.right() <= engine.width
        assert layout.activity.bottom() <= engine.height


def test_normal_layout_is_one_compact_attached_cluster():
    layout = PetLayoutEngine().calculate("bottom", expanded=False, approval=False)
    assert 160 <= layout.window_width <= 230
    assert layout.title.bottom() + 10 == layout.body.top()
    assert layout.badge.top() == layout.body.bottom() + 8
    assert layout.ptt.top() == layout.body.bottom() + 8
    assert layout.body.width() >= 100


def test_activity_model_terminal_task_is_bounded():
    model = PetActivityModel()
    model.apply({"type": "background_task", "payload": {"id": "t1", "title": "Done", "status": "done"}})
    model._updated["t1"] -= 5
    model.prune()
    assert "t1" not in model.tasks


def test_pet_qt_dependency_state_is_explicit():
    assert isinstance(pet_window.QT_AVAILABLE, bool)
    if pet_window.QT_AVAILABLE:
        assert pet_window.QWidget is not object
    else:
        assert pet_window.QT_IMPORT_ERROR is not None


def test_pet_main_degrades_without_qt(monkeypatch):
    monkeypatch.setattr(pet_window, "QT_AVAILABLE", False)
    monkeypatch.setattr(pet_window, "QT_IMPORT_ERROR", ImportError("Qt unavailable"))
    pet_window.main()
