from charlie.pet_window import _map_event_to_caption, _map_event_to_state, _track_workspace_surface


def test_map_event_to_state_passes_through_all_nine_core_states():
    for state in ("idle", "listening", "thinking", "speaking", "working", "waiting", "attention", "completed", "error"):
        event = {"type": "charlie_state", "payload": {"state": state}}
        assert _map_event_to_state(event) == state


def test_map_event_to_state_ignores_non_charlie_state_events():
    assert _map_event_to_state({"type": "token", "payload": {}}) is None


def test_map_event_to_state_ignores_unknown_state_value():
    assert _map_event_to_state({"type": "charlie_state", "payload": {"state": "sleeping"}}) is None


def test_map_event_to_caption_approval_family():
    event = {"type": "tool_approval_request", "payload": {"reason": "delete a file"}}
    assert _map_event_to_caption(event) == ("Needs your approval", "delete a file")


def test_map_event_to_caption_alert_warning():
    event = {"type": "alert", "payload": {"severity": "warning", "message": "disk low"}}
    assert _map_event_to_caption(event) == ("Attention", "disk low")


def test_map_event_to_caption_alert_info_is_silent():
    event = {"type": "alert", "payload": {"severity": "info", "message": "fyi"}}
    assert _map_event_to_caption(event) == (None, None)


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
