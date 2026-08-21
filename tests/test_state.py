"""Transition-table tests for charlie.state.StateMachine (Phase 2)."""

from charlie.events import EventType
from charlie.state import CoreState, StateMachine


def _event(event_type: EventType, payload: dict | None = None) -> dict:
    return {"type": event_type.value, "payload": payload or {}}


def test_starts_idle():
    sm = StateMachine()
    assert sm.state == CoreState.IDLE


def test_vad_start_transitions_to_listening():
    sm = StateMachine()
    new_state = sm.apply(_event(EventType.VAD_START))
    assert new_state == CoreState.LISTENING
    assert sm.state == CoreState.LISTENING


def test_wake_word_transitions_to_listening():
    sm = StateMachine()
    assert sm.apply(_event(EventType.WAKE_WORD)) == CoreState.LISTENING


def test_thinking_transitions_to_thinking():
    sm = StateMachine()
    assert sm.apply(_event(EventType.THINKING)) == CoreState.THINKING


def test_speaking_start_transitions_to_speaking():
    sm = StateMachine()
    assert sm.apply(_event(EventType.SPEAKING_START)) == CoreState.SPEAKING


def test_tool_call_transitions_to_working():
    sm = StateMachine()
    assert sm.apply(_event(EventType.TOOL_CALL)) == CoreState.WORKING


def test_tool_result_transitions_back_to_thinking():
    sm = StateMachine()
    sm.apply(_event(EventType.TOOL_CALL))
    assert sm.apply(_event(EventType.TOOL_RESULT)) == CoreState.THINKING


def test_tool_approval_request_transitions_to_waiting():
    sm = StateMachine()
    assert sm.apply(_event(EventType.TOOL_APPROVAL_REQUEST)) == CoreState.WAITING


def test_background_task_running_transitions_to_working():
    sm = StateMachine()
    assert sm.apply(_event(EventType.BACKGROUND_TASK, {"status": "running"})) == CoreState.WORKING


def test_background_task_planning_transitions_to_working():
    sm = StateMachine()
    assert sm.apply(_event(EventType.BACKGROUND_TASK, {"status": "planning"})) == CoreState.WORKING


def test_background_task_paused_transitions_to_waiting():
    sm = StateMachine()
    assert sm.apply(_event(EventType.BACKGROUND_TASK, {"status": "paused"})) == CoreState.WAITING


def test_background_task_done_transitions_to_completed():
    sm = StateMachine()
    assert sm.apply(_event(EventType.BACKGROUND_TASK, {"status": "done"})) == CoreState.COMPLETED


def test_background_task_failed_transitions_to_error():
    sm = StateMachine()
    assert sm.apply(_event(EventType.BACKGROUND_TASK, {"status": "failed"})) == CoreState.ERROR


def test_background_task_cancelled_transitions_to_completed():
    sm = StateMachine()
    assert sm.apply(_event(EventType.BACKGROUND_TASK, {"status": "cancelled"})) == CoreState.COMPLETED


def test_alert_error_severity_transitions_to_error():
    sm = StateMachine()
    assert sm.apply(_event(EventType.ALERT, {"severity": "error"})) == CoreState.ERROR


def test_alert_warning_severity_transitions_to_attention():
    sm = StateMachine()
    assert sm.apply(_event(EventType.ALERT, {"severity": "warning"})) == CoreState.ATTENTION


def test_alert_info_severity_does_not_transition():
    sm = StateMachine()
    sm.apply(_event(EventType.THINKING))
    assert sm.apply(_event(EventType.ALERT, {"severity": "info"})) is None
    assert sm.state == CoreState.THINKING


def test_alert_success_severity_does_not_transition():
    sm = StateMachine()
    sm.apply(_event(EventType.THINKING))
    assert sm.apply(_event(EventType.ALERT, {"severity": "success"})) is None
    assert sm.state == CoreState.THINKING


def test_speaking_stop_returns_to_idle():
    sm = StateMachine()
    sm.apply(_event(EventType.SPEAKING_START))
    assert sm.apply(_event(EventType.SPEAKING_STOP)) == CoreState.IDLE
    assert sm.state == CoreState.IDLE


def test_response_done_transitions_to_completed():
    sm = StateMachine()
    assert sm.apply(_event(EventType.RESPONSE_DONE)) == CoreState.COMPLETED


def test_unmapped_event_returns_none_and_state_unchanged():
    sm = StateMachine()
    sm.apply(_event(EventType.THINKING))
    assert sm.apply(_event(EventType.AUDIO_LEVEL)) is None
    assert sm.state == CoreState.THINKING


def test_completed_auto_expires_to_prior_state():
    sm = StateMachine()
    sm.apply(_event(EventType.THINKING), now=0.0)
    sm.apply(_event(EventType.RESPONSE_DONE), now=1.0)
    assert sm.state == CoreState.COMPLETED
    assert sm.expire_if_due(now=1.0) is None
    assert sm.state == CoreState.COMPLETED
    assert sm.expire_if_due(now=4.1) == CoreState.THINKING
    assert sm.state == CoreState.THINKING


def test_error_auto_expires_to_prior_state():
    sm = StateMachine()
    sm.apply(_event(EventType.SPEAKING_START), now=0.0)
    sm.apply(_event(EventType.ALERT, {"severity": "error"}), now=1.0)
    assert sm.state == CoreState.ERROR
    assert sm.expire_if_due(now=4.1) == CoreState.SPEAKING
    assert sm.state == CoreState.SPEAKING


def test_attention_auto_expires_to_prior_state():
    sm = StateMachine()
    sm.apply(_event(EventType.VAD_START), now=0.0)
    sm.apply(_event(EventType.ALERT, {"severity": "warning"}), now=1.0)
    assert sm.state == CoreState.ATTENTION
    assert sm.expire_if_due(now=4.1) == CoreState.LISTENING
    assert sm.state == CoreState.LISTENING


def test_new_event_before_ttl_overrides_transient_state():
    sm = StateMachine()
    sm.apply(_event(EventType.THINKING), now=0.0)
    sm.apply(_event(EventType.RESPONSE_DONE), now=1.0)
    assert sm.apply(_event(EventType.VAD_START), now=1.5) == CoreState.LISTENING


def test_activities_tracks_running_task_id():
    sm = StateMachine()
    sm.apply(_event(EventType.BACKGROUND_TASK, {"status": "running", "id": "t1"}))
    assert "t1" in sm.activities()
    sm.apply(_event(EventType.BACKGROUND_TASK, {"status": "done", "id": "t1"}))
    assert "t1" not in sm.activities()


def test_since_updates_on_transition():
    sm = StateMachine()
    before = sm.since
    sm.apply(_event(EventType.VAD_START), now=5.0)
    assert sm.since != before
