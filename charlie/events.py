"""Typed EventBus envelope metadata: EventType vocabulary + EventMeta.

Values are the exact wire strings already used across the codebase, plus a
small set of newer types. EventMeta is additive to EventBus.emit()'s
existing 2-arg envelope, not a signature change -- see charlie/ipc.py.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Optional

from charlie.utils import utc_now_iso


class EventType(StrEnum):
    """Every event type currently on the wire, plus near-term additions."""

    # Voice pipeline / chat turn
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

    # Tool loop
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TOOL_APPROVAL_REQUEST = "tool_approval_request"

    # Session / system
    SESSION_UPDATED = "session_updated"
    SYSTEM_STATUS = "system_status"
    SUBSYSTEM_HEALTH = "subsystem_health"
    ALERT = "alert"
    LOG = "log"

    # Desktop / recovery / extensions / background tasks
    DESKTOP_FRAME = "desktop_frame"
    RECOVERY_PROPOSAL = "recovery_proposal"
    EXTENSION_PROPOSED = "extension_proposed"
    EXTENSION_PENDING = "extension_pending"
    BACKGROUND_TASK = "background_task"
    TASK_SNAPSHOT = "task_snapshot"

    # Adaptive Agentic OS additions
    CHARLIE_STATE = "charlie_state"
    RESULT_STORED = "result_stored"
    SURFACE_SPAWN = "surface_spawn"
    SURFACE_UPDATE = "surface_update"
    SURFACE_DISMISS = "surface_dismiss"
    BROWSER_TASK_STARTED = "browser_task_started"
    BROWSER_TASK_DONE = "browser_task_done"
    MCP_STATUS_CHANGED = "mcp_status_changed"
    MEMORY_UPDATED = "memory_updated"
    VISION_OBSERVED = "vision_observed"
    DASHBOARD_PANEL = "dashboard_panel"
    DASHBOARD_VISIBILITY = "dashboard_visibility"
    TERMINAL_COMMAND_RESULT = "terminal_command_result"


class EventSource(StrEnum):
    """Which subsystem originated an event."""

    VOICE = "voice"
    BRAIN = "brain"
    TASK = "task"
    WATCHER = "watcher"
    SURFACE = "surface"


@dataclass(frozen=True)
class EventMeta:
    """Envelope metadata injected into EventBus.emit()'s payload dict.

    ts defaults to now at construction time. rationale is a one-line "why"
    for an autonomous action -- leave it None for pure relays (streamed
    tokens, state mirrors) where there's no decision to explain.
    """

    source: EventSource
    task_id: Optional[str] = None
    rationale: Optional[str] = None
    ts: str = field(default_factory=utc_now_iso)
