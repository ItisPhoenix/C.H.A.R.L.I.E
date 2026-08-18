"""Shared, versioned event envelope and registry for Charlie runtime/UI traffic."""

import json
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Optional

from charlie.utils import utc_now_iso

_CONTRACT_PATH = Path(__file__).resolve().parents[1] / "shared" / "event_contract.json"
with _CONTRACT_PATH.open(encoding="utf-8") as _contract_file:
    EVENT_CONTRACT: dict[str, Any] = json.load(_contract_file)

CONTRACT_VERSION = int(EVENT_CONTRACT["contract_version"])


class EventType(StrEnum):
    """Every event type currently on the wire."""

    TRANSCRIPT = "transcript"
    THINKING = "thinking"
    THINKING_UPDATE = "thinking_update"
    TOKEN = "token"
    RESPONSE_DONE = "response_done"
    SPEAKING_START = "speaking_start"
    SPEAKING_STOP = "speaking_stop"
    WAKE_WORD = "wake_word"
    VAD_START = "vad_start"
    AUDIO_STATE = "audio_state"
    MIC_STATE = "mic_state"
    AUDIO_LEVEL = "audio_level"
    PTT_START = "ptt_start"
    PTT_STOP = "ptt_stop"
    PTT_CANCEL = "ptt_cancel"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TOOL_APPROVAL_REQUEST = "tool_approval_request"
    TOOL_APPROVAL_RESOLVED = "tool_approval_resolved"
    RESEARCH_PROGRESS = "research_progress"
    RESEARCH_RESULT = "research_result"
    SESSION_UPDATED = "session_updated"
    SESSION_ACTIVE = "session_active"
    SYSTEM_STATUS = "system_status"
    SUBSYSTEM_HEALTH = "subsystem_health"
    ALERT = "alert"
    LOG = "log"
    DESKTOP_FRAME = "desktop_frame"
    RECOVERY_PROPOSAL = "recovery_proposal"
    EXTENSION_PROPOSED = "extension_proposed"
    EXTENSION_PENDING = "extension_pending"
    BACKGROUND_TASK = "background_task"
    TASK_SNAPSHOT = "task_snapshot"
    CHARLIE_STATE = "charlie_state"
    RESULT_STORED = "result_stored"
    BROWSER_TASK_STARTED = "browser_task_started"
    BROWSER_TASK_DONE = "browser_task_done"
    MCP_STATUS_CHANGED = "mcp_status_changed"
    MEMORY_UPDATED = "memory_updated"
    VISION_OBSERVED = "vision_observed"
    HUD_VISIBILITY = "hud_visibility"
    TERMINAL_COMMAND_RESULT = "terminal_command_result"
    CHAT = "chat"
    PRESENTATION_INTENT = "presentation_intent"
    PRESENTATION_UPDATE = "presentation_update"
    PRESENTATION_DISMISS = "presentation_dismiss"
    SURFACE_ACTION = "surface_action"
    SELF_EXTENSION_REQUESTED = "self_extension_requested"
    SELF_EXTENSION_CLASSIFIED = "self_extension_classified"
    SELF_EXTENSION_PLANNED = "self_extension_planned"
    SELF_EXTENSION_APPROVAL_REQUIRED = "self_extension_approval_required"
    SELF_EXTENSION_APPLYING = "self_extension_applying"
    SELF_EXTENSION_TESTING = "self_extension_testing"
    SELF_EXTENSION_HEALTH_CHECK = "self_extension_health_check"
    SELF_EXTENSION_RESTARTING = "self_extension_restarting"
    SELF_EXTENSION_VERIFYING = "self_extension_verifying"
    SELF_EXTENSION_COMPLETED = "self_extension_completed"
    SELF_EXTENSION_RESULT = "self_extension_result"
    SELF_EXTENSION_FAILED = "self_extension_failed"
    SELF_EXTENSION_ROLLBACK_STARTED = "self_extension_rollback_started"
    SELF_EXTENSION_ROLLED_BACK = "self_extension_rolled_back"



class EventSource(StrEnum):
    """Which subsystem originated an event."""

    VOICE = "voice"
    BRAIN = "brain"
    TASK = "task"
    WATCHER = "watcher"
    SURFACE = "surface"
    RUNTIME = "runtime"


class EventCategory(StrEnum):
    SNAPSHOT = "snapshot"
    TRANSIENT = "transient"
    REQUEST = "request"
    ACKNOWLEDGEMENT = "acknowledgement"
    PROGRESS = "progress"
    RESULT = "result"


@dataclass(frozen=True)
class EventSpec:
    name: str
    category: EventCategory
    version: int = CONTRACT_VERSION
    replay: bool = False
    required_payload: tuple[str, ...] = ()


EVENT_REGISTRY: dict[str, EventSpec] = {
    name: EventSpec(
        name=name,
        category=EventCategory(definition["category"]),
        replay=bool(definition["replay"]),
        required_payload=tuple(definition.get("required", ())),
    )
    for name, definition in EVENT_CONTRACT["event_types"].items()
}


class EventValidationError(ValueError):
    """Raised when an event cannot cross a runtime/UI boundary safely."""


@dataclass(frozen=True)
class EventMeta:
    """Optional producer metadata retained in the versioned envelope.

    ``ts`` remains public for compatibility; it is serialized as
    ``timestamp`` on the wire.
    """

    source: EventSource
    task_id: Optional[str] = None
    rationale: Optional[str] = None
    ts: str = field(default_factory=utc_now_iso)
    session_id: Optional[str] = None


def event_spec(event_type: str) -> EventSpec:
    try:
        return EVENT_REGISTRY[event_type]
    except KeyError as exc:
        raise EventValidationError(f"unknown event type: {event_type}") from exc


def _require_payload(payload: Any, spec: EventSpec) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise EventValidationError("event payload must be an object")
    missing = [key for key in spec.required_payload if key not in payload]
    if missing:
        raise EventValidationError(f"event {spec.name} missing payload fields: {', '.join(missing)}")
    return payload


def _validate_envelope(event: dict[str, Any], *, allow_unknown: bool = False) -> dict[str, Any]:
    event_type = event.get("type")
    if not isinstance(event_type, str) or not event_type:
        raise EventValidationError("event type must be a non-empty string")
    spec = EVENT_REGISTRY.get(event_type)
    if spec is None and not allow_unknown:
        raise EventValidationError(f"unknown event type: {event_type}")
    if not isinstance(event.get("version"), int) or event["version"] != CONTRACT_VERSION:
        raise EventValidationError(f"unsupported event version for {event_type}")
    for field_name in ("id", "timestamp", "source"):
        if not isinstance(event.get(field_name), str) or not event[field_name]:
            raise EventValidationError(f"event {field_name} must be a non-empty string")
    if not isinstance(event.get("replay"), bool):
        raise EventValidationError("event replay flag must be boolean")
    if event.get("session_id") is not None and not isinstance(event["session_id"], str):
        raise EventValidationError("event session_id must be string or null")
    if event.get("task_id") is not None and not isinstance(event["task_id"], str):
        raise EventValidationError("event task_id must be string or null")
    _require_payload(event.get("payload"), spec or EventSpec(event_type, EventCategory.TRANSIENT))
    return event


def build_event(event_type: str, payload: dict[str, Any], meta: Optional[EventMeta] = None) -> dict[str, Any]:
    """Build one canonical live event. IDs are created at this boundary."""

    spec = event_spec(event_type)
    payload = _require_payload(payload, spec)
    event: dict[str, Any] = {
        "type": event_type,
        "version": spec.version,
        "id": uuid.uuid4().hex,
        "timestamp": meta.ts if meta is not None else utc_now_iso(),
        "source": meta.source.value if meta is not None else EventSource.RUNTIME.value,
        "session_id": (meta.session_id if meta is not None else None) or payload.get("session_id"),
        "task_id": meta.task_id if meta is not None else None,
        "replay": False,
        "payload": payload,
    }
    if meta is not None and meta.rationale is not None:
        event["rationale"] = meta.rationale
    return _validate_envelope(event)


def normalize_event(event: Any, *, replay: Optional[bool] = None, allow_unknown: bool = False) -> dict[str, Any]:
    """Adapt legacy ``{type, payload}`` events into canonical envelopes."""

    if not isinstance(event, dict):
        raise EventValidationError("event must be an object")
    event_type = event.get("type")
    if not isinstance(event_type, str) or not event_type:
        raise EventValidationError("event type must be a non-empty string")
    spec = EVENT_REGISTRY.get(event_type)
    if spec is None and not allow_unknown:
        raise EventValidationError(f"unknown event type: {event_type}")
    payload = event.get("payload", {})
    _require_payload(payload, spec or EventSpec(event_type, EventCategory.TRANSIENT))
    normalized = {
        "type": event_type,
        "version": event.get("version", CONTRACT_VERSION),
        "id": event.get("id") or uuid.uuid4().hex,
        "timestamp": event.get("timestamp") or event.get("ts") or utc_now_iso(),
        "source": event.get("source") or EventSource.RUNTIME.value,
        "session_id": event.get("session_id") or payload.get("session_id"),
        "task_id": event.get("task_id"),
        "replay": event.get("replay", False) if replay is None else replay,
        "payload": payload,
    }
    if event.get("rationale") is not None:
        normalized["rationale"] = event["rationale"]
    return _validate_envelope(normalized, allow_unknown=allow_unknown)


def replay_event(event: Any, *, allow_unknown: bool = False) -> dict[str, Any]:
    """Return same event identity with explicit hydration/replay semantics."""

    return normalize_event(event, replay=True, allow_unknown=allow_unknown)
