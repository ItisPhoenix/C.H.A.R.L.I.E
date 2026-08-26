"""Phase 1 typed event envelope and replay contract tests."""

import pytest

from charlie.events import (
    EVENT_REGISTRY,
    EventCategory,
    EventMeta,
    EventSource,
    EventType,
    EventValidationError,
    build_event,
    event_spec,
    normalize_event,
    replay_event,
)


def test_build_event_creates_versioned_envelope_with_metadata_and_unique_id():
    first = build_event(
        "charlie_state",
        {"state": "thinking", "session_id": "session-1"},
        meta=EventMeta(source=EventSource.BRAIN, task_id="task-1", rationale="turn started"),
    )
    second = build_event("charlie_state", {"state": "idle"})

    assert first["version"] == 1
    assert first["id"] != second["id"]
    assert first["timestamp"]
    assert first["source"] == "brain"
    assert first["task_id"] == "task-1"
    assert first["session_id"] == "session-1"
    assert first["replay"] is False
    assert first["rationale"] == "turn started"


def test_registry_exposes_category_and_replay_policy():
    spec = event_spec("charlie_state")

    assert spec.category is EventCategory.SNAPSHOT
    assert spec.replay is True
    assert spec.version == 1


def test_python_event_vocabulary_matches_shared_contract_registry():
    assert {event.value for event in EventType} == set(EVENT_REGISTRY)


def test_legacy_event_adapts_without_breaking_old_emitters():
    adapted = normalize_event({"type": "alert", "payload": {"message": "hello"}})

    assert adapted["type"] == "alert"
    assert adapted["version"] == 1
    assert adapted["id"]
    assert adapted["replay"] is False
    assert adapted["payload"] == {"message": "hello"}


def test_replay_event_preserves_identity_but_marks_event_as_replay():
    live = build_event("charlie_state", {"state": "idle"})
    replayed = replay_event(live)

    assert replayed["id"] == live["id"]
    assert replayed["timestamp"] == live["timestamp"]
    assert replayed["replay"] is True
    assert live["replay"] is False


def test_presentation_command_contract_allows_known_hud_commands_and_is_not_replayable():
    event = build_event("presentation_command", {"action": "clear_screen"})
    assert event["replay"] is False

    focus_event = build_event("presentation_command", {"action": "focus_task", "task_id": "task-1"})
    assert focus_event["payload"]["task_id"] == "task-1"

    dismiss_event = build_event("presentation_command", {"action": "dismiss_widget", "id": "widget-1"})
    assert dismiss_event["payload"]["id"] == "widget-1"

    summon_event = build_event("presentation_command", {"action": "summon_hud"})
    assert summon_event["payload"]["action"] == "summon_hud"
    conversation_event = build_event("presentation_command", {"action": "open_conversation"})
    assert conversation_event["payload"]["action"] == "open_conversation"

    with pytest.raises(EventValidationError):
        build_event("presentation_command", {"action": "arbitrary_frontend_command"})
    with pytest.raises(EventValidationError):
        normalize_event(
            {
                "type": "presentation_command",
                "payload": {"action": "arbitrary_frontend_command"},
            }
        )


@pytest.mark.parametrize(
    "event",
    [
        None,
        {"type": "charlie_state", "payload": "not-an-object"},
        {"type": "charlie_state", "version": 2, "id": "x", "timestamp": "now", "payload": {}},
        {"type": "not_registered", "payload": {}},
    ],
)
def test_invalid_or_unknown_events_fail_validation(event):
    with pytest.raises(EventValidationError):
        normalize_event(event)


def test_initial_state_events_are_formal_replays():
    from charlie import web_server

    events = web_server._initial_state_events()

    assert events
    assert all(event["version"] == 1 for event in events)
    assert all(event["replay"] is True for event in events)
    assert all(event["id"] for event in events)
