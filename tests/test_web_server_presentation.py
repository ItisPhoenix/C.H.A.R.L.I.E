"""Regression tests for canonical presentation replay and dismissal."""

import charlie.web_server as web_server


def test_non_loopback_host_is_rejected_without_authentication():
    assert web_server.validate_bind_host("127.0.0.1") is None
    assert web_server.validate_bind_host("::1") is None
    assert "loopback" in web_server.validate_bind_host("192.168.1.10").lower()


def test_presentation_intent_update_replay_then_dismiss():
    cache: dict = {}
    intent = {
        "type": "presentation_intent",
        "payload": {"id": "p1", "kind": "workspace", "replayable": True},
    }
    web_server._apply_presentation_event(cache, intent)
    assert cache["p1"] is intent

    update = {
        "type": "presentation_update",
        "payload": {"id": "p1", "kind": "workspace", "summary": "updated"},
    }
    web_server._apply_presentation_event(cache, update)
    assert cache["p1"] is update

    dismiss = {"type": "presentation_dismiss", "payload": {"id": "p1"}}
    web_server._apply_presentation_event(cache, dismiss)
    assert "p1" not in cache


def test_non_replayable_presentation_is_not_cached():
    cache: dict = {}
    web_server._apply_presentation_event(
        cache,
        {"type": "presentation_intent", "payload": {"id": "caption-1", "kind": "caption"}},
    )
    assert cache == {}


def test_unrelated_event_type_ignored():
    cache: dict = {}
    web_server._apply_presentation_event(cache, {"type": "system_status", "payload": {}})
    assert cache == {}


def test_approval_request_then_resolved():
    cache: dict = {}
    request = {"type": "tool_approval_request", "payload": {"request_id": "r1", "tool_name": "shell_execute"}}
    web_server._apply_approval_event(cache, request)
    assert cache["r1"] is request

    resolved = {"type": "tool_approval_resolved", "payload": {"request_id": "r1"}}
    web_server._apply_approval_event(cache, resolved)
    assert "r1" not in cache
