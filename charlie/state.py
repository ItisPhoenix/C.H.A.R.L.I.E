"""Authoritative CoreState machine for the voice process (Phase 2, plan section "Phase 2").

Single instance lives in main.py. web_server.py and pet_window.py stop re-deriving
state from raw events and become consumers of the charlie_state event this emits.
"""

import time
from enum import StrEnum
from typing import FrozenSet, Optional

from charlie.background_task import ACTIVE_STATUSES as _BACKGROUND_TASK_ACTIVE_STATUSES
from charlie.events import EventType
from charlie.utils import utc_now_iso

_TRANSIENT_STATE_TTL_SECONDS = 3.0


class CoreState(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    WORKING = "working"
    WAITING = "waiting"
    ATTENTION = "attention"
    COMPLETED = "completed"
    ERROR = "error"


_TRANSIENT_STATES = frozenset({CoreState.COMPLETED, CoreState.ERROR, CoreState.ATTENTION})

_DIRECT_TRANSITIONS = {
    EventType.VAD_START: CoreState.LISTENING,
    EventType.WAKE_WORD: CoreState.LISTENING,
    EventType.THINKING: CoreState.THINKING,
    EventType.SPEAKING_START: CoreState.SPEAKING,
    EventType.TOOL_CALL: CoreState.WORKING,
    EventType.TOOL_RESULT: CoreState.THINKING,
    EventType.TOOL_APPROVAL_REQUEST: CoreState.WAITING,
    EventType.SPEAKING_STOP: CoreState.COMPLETED,
    EventType.RESPONSE_DONE: CoreState.COMPLETED,
}

_BACKGROUND_TASK_STATUS_TO_STATE = {
    "planning": CoreState.WORKING,
    "running": CoreState.WORKING,
    "paused": CoreState.WAITING,
    "done": CoreState.COMPLETED,
    "cancelled": CoreState.COMPLETED,
    "failed": CoreState.ERROR,
}

_ALERT_SEVERITY_TO_STATE = {
    "error": CoreState.ERROR,
    "warning": CoreState.ATTENTION,
}


class StateMachine:
    """Deterministic CoreState transitions driven by EventBus events."""

    def __init__(self) -> None:
        self._state = CoreState.IDLE
        self._prior_stable = CoreState.IDLE
        self._expiry: Optional[float] = None
        self._since = utc_now_iso()
        self._activities: set[str] = set()

    @property
    def state(self) -> CoreState:
        return self._state

    @property
    def since(self) -> str:
        return self._since

    def activities(self) -> FrozenSet[str]:
        return frozenset(self._activities)

    def apply(self, event: dict, now: Optional[float] = None) -> Optional[CoreState]:
        """Feed one EventBus event dict in; returns the new state, or None if unmapped."""
        now = now if now is not None else time.monotonic()
        new_state = self._resolve(event.get("type"), event.get("payload") or {})
        if new_state is None:
            return None
        return self._transition(new_state, now)

    def expire_if_due(self, now: Optional[float] = None) -> Optional[CoreState]:
        """Revert an expired transient state (COMPLETED/ERROR/ATTENTION) to what preceded it."""
        now = now if now is not None else time.monotonic()
        if self._expiry is None or now < self._expiry:
            return None
        return self._transition(self._prior_stable, now)

    def _resolve(self, event_type: Optional[str], payload: dict) -> Optional[CoreState]:
        if event_type == EventType.BACKGROUND_TASK:
            return self._resolve_background_task(payload)
        if event_type == EventType.ALERT:
            return _ALERT_SEVERITY_TO_STATE.get(payload.get("severity"))
        try:
            return _DIRECT_TRANSITIONS.get(EventType(event_type))
        except ValueError:
            return None

    def _resolve_background_task(self, payload: dict) -> Optional[CoreState]:
        status = payload.get("status")
        task_id = payload.get("id")
        if task_id:
            if status in _BACKGROUND_TASK_ACTIVE_STATUSES:
                self._activities.add(task_id)
            else:
                self._activities.discard(task_id)
        return _BACKGROUND_TASK_STATUS_TO_STATE.get(status)

    def _transition(self, new_state: CoreState, now: float) -> CoreState:
        if new_state not in _TRANSIENT_STATES:
            self._prior_stable = new_state
            self._expiry = None
        else:
            if self._state not in _TRANSIENT_STATES:
                self._prior_stable = self._state
            self._expiry = now + _TRANSIENT_STATE_TTL_SECONDS
        self._state = new_state
        self._since = utc_now_iso()
        return new_state
