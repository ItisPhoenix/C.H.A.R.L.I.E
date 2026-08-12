"""Regression test: a webview that connects after its surface_spawn event
already fired must still find its spec -- see charlie.web_server._apply_surface_event."""

import charlie.web_server as web_server


def test_spawn_then_replay_then_dismiss():
    cache: dict = {}
    spawn = {"type": "surface_spawn", "payload": {"surface_id": "s1", "presentation": "widget"}}
    web_server._apply_surface_event(cache, spawn)
    assert cache["s1"] is spawn

    update = {"type": "surface_update", "payload": {"surface_id": "s1", "presentation": "widget", "density": 2}}
    web_server._apply_surface_event(cache, update)
    assert cache["s1"] is update

    dismiss = {"type": "surface_dismiss", "payload": {"surface_id": "s1"}}
    web_server._apply_surface_event(cache, dismiss)
    assert "s1" not in cache


def test_unrelated_event_type_ignored():
    cache: dict = {}
    web_server._apply_surface_event(cache, {"type": "system_status", "payload": {}})
    assert cache == {}


def test_approval_request_then_resolved():
    cache: dict = {}
    request = {"type": "tool_approval_request", "payload": {"request_id": "r1", "tool_name": "shell_execute"}}
    web_server._apply_approval_event(cache, request)
    assert cache["r1"] is request

    resolved = {"type": "tool_approval_resolved", "payload": {"request_id": "r1"}}
    web_server._apply_approval_event(cache, resolved)
    assert "r1" not in cache
