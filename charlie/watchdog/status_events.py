"""
charlie/watchdog/status_events.py

Single source of truth for status_q -> dashboard WebSocket event mapping
(Req 6.3).

This module owns the one canonical ``STATUS_EVENT_MAP`` that translates
internal ``status_q`` event types into the WebSocket event names the
dashboard understands.  It is imported by both ``IPCBridge`` (the sole
``status_q`` consumer) and ``ControlServer`` (the WS broadcaster) so that
there is exactly one mapping and no two consumers can diverge.

The map is the UNION of the historical maps that previously lived in
``control_server.py`` (``_forward_status_queue`` event_map) and
``ipc_bridge.py`` (``_map_to_ws_type``).  Where the two disagreed the
``control_server`` name wins, except for ``VOICE_ACTIVITY`` which is
``"voice_activity"`` to match the dashboard voice-orb contract (Req 6.4).
"""

# ---------------------------------------------------------------------------
# Canonical event map (single source of truth — Req 6.3)
# ---------------------------------------------------------------------------

STATUS_EVENT_MAP: dict[str, str] = {
    "PHASE": "phase_change",
    "CHAT_MSG": "chat_message",
    "VOICE_ACTIVITY": "voice_activity",
    "VRAM": "vram_update",
    "INTEGRATION_UPDATE": "integration_update",
    "PHOENIX_ALERT": "subsystem_failure",
    "CONFIRM_REQUIRED": "approval_pending",
    "TOOL_EXECUTION": "tool_execution",
    "RESEARCH_STATUS": "research_status",
    "RESEARCH_LOG": "research_log",
    "RESEARCH_PARTIAL": "research_partial",
    "RESEARCH_RESULT": "research_result",
    "RESEARCH_FOLLOWUP": "research_followup",
    "STATUS_UPDATE": "status_update",
    "SUBSYSTEM_STATUS": "status_update",
    "TASK_UPDATE": "task_update",
    "TASK_COMPLETE": "task_update",
    "TASK_FAIL": "task_update",
    "VOICE_COMMAND": "voice_command",
    "USER_TRANSCRIPT": "user_transcript",
    # --- additional types carried over from prior consumers (union) ---
    "THINKING_STATUS": "thinking_status",
    "WELCOME_SUMMARY": "welcome_summary",
    "WIDGET_SHOW": "widget_show",
    "WIDGET_HIDE": "widget_hide",
    "PROACTIVE_CHAT": "proactive_chat",
    "SKILL_CREATED": "skill_created",
    "ORCHESTRATOR_UPDATE": "orchestrator_update",
    "ORCHESTRATOR_UPDATE": "orchestrator_update",
    "TIME_UPDATE": "time_update",
}

# Membership set so consumers can quickly check whether a type is forwarded.
WS_FORWARD_TYPES: set[str] = set(STATUS_EVENT_MAP.keys())


# ---------------------------------------------------------------------------
# Mapping helper
# ---------------------------------------------------------------------------

def map_event_type(msg_type: str) -> str | None:
    """Return the WS event name for ``msg_type`` or ``None`` if unmapped."""
    return STATUS_EVENT_MAP.get(msg_type)


# ---------------------------------------------------------------------------
# Payload extractor
# ---------------------------------------------------------------------------

def extract_ws_data(msg: dict) -> dict:
    """Produce a WebSocket-ready payload from a ``status_q`` message.

    VOICE_ACTIVITY (Req 6.4): the audio producer emits
    ``{"type": "VOICE_ACTIVITY", "peak": <float>, "waveform": [...]}``.
    The dashboard voice orb expects ``level`` (float) and ``active`` (bool),
    so we map ``peak`` -> ``level`` and derive ``active`` from a small
    threshold, preserving ``waveform`` for the orb animation.

    For every other type we preserve the historical behaviour:
    - ``content`` is a dict   -> merge it with ``source``/``raw_type``
    - ``content`` is a string -> ``{"message": content, "raw_type": ...}``
    - otherwise               -> ``{"content": str(content), "raw_type": ...}``

    Pure function: no I/O.
    """
    msg_type = msg.get("type", "")

    if msg_type == "VOICE_ACTIVITY":
        peak = float(msg.get("peak", 0.0))
        return {
            "level": peak,
            "active": peak > 0.01,
            "waveform": msg.get("waveform", []),
            "is_speaking": bool(msg.get("is_speaking", False)),
            "is_listening": bool(msg.get("is_listening", False)),
        }

    content = msg.get("content", {})

    if isinstance(content, dict):
        return {
            **content,
            "source": msg.get("source", "unknown"),
            "raw_type": msg_type,
        }

    if isinstance(content, str):
        return {"message": content, "raw_type": msg_type}

    return {"content": str(content), "raw_type": msg_type}
