from charlie.pet_window import (
    _elide_text,
    _extract_last_sentence,
    _map_event_to_caption_desc,
    _map_event_to_state,
    _state_caption_title,
    _track_workspace_surface,
)


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


def test_track_workspace_surface_spawn_then_dismiss():
    active: set = set()
    spawn = {"type": "surface_spawn", "payload": {"surface_id": "ws1", "presentation": "workspace"}}
    dismiss = {"type": "surface_dismiss", "payload": {"surface_id": "ws1"}}

    assert _track_workspace_surface(active, spawn) is True
    assert _track_workspace_surface(active, dismiss) is False


def test_track_workspace_surface_ignores_non_workspace_spawn():
    active: set = set()
    spawn = {"type": "surface_spawn", "payload": {"surface_id": "w1", "presentation": "widget"}}
    assert _track_workspace_surface(active, spawn) is None


def test_track_workspace_surface_second_spawn_does_not_re_emit():
    active: set = set()
    spawn1 = {"type": "surface_spawn", "payload": {"surface_id": "ws1", "presentation": "workspace"}}
    spawn2 = {"type": "surface_spawn", "payload": {"surface_id": "ws2", "presentation": "workspace"}}

    assert _track_workspace_surface(active, spawn1) is True
    assert _track_workspace_surface(active, spawn2) is None


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
