# ruff: noqa: E402, I001
import asyncio
import dataclasses
import io
import logging
import logging.handlers
import os
import re
import sys
import time
import threading
from typing import Callable, Dict, Optional, Tuple

# Windows event-loop policy (must precede zmq/asyncio imports)
from charlie.runtime import configure as _configure_platform

_configure_platform()
import subprocess
import uuid

from charlie.text_utils import normalize_app_list as _normalize_app_list


from pathlib import Path


# 1. SETUP ENVIRONMENT FIRST
class SafeStreamWrapper:
    def __init__(self, stream):
        self.stream = stream

    def write(self, data):
        try:
            return self.stream.write(data)
        except OSError as e:
            if e.errno not in (22, 32, 9):
                raise
        except ValueError:
            pass

    def flush(self):
        try:
            return self.stream.flush()
        except OSError as e:
            if e.errno not in (22, 32, 9):
                raise
        except ValueError:
            pass

    def __getattr__(self, name):
        return getattr(self.stream, name)


if sys.platform == "win32":
    sys.stdout = SafeStreamWrapper(
        io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True, write_through=True)
    )
    sys.stderr = SafeStreamWrapper(
        io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", line_buffering=True, write_through=True)
    )
else:
    sys.stdout = SafeStreamWrapper(sys.stdout)
    sys.stderr = SafeStreamWrapper(sys.stderr)

os.makedirs("logs", exist_ok=True)
LOG_FILE = "logs/charlie.log"

# 2. CONFIGURE SPLIT LOGGING
root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)

file_formatter = logging.Formatter("%(asctime)s [%(name)s] [%(levelname)s] %(funcName)s:%(lineno)d - %(message)s")
file_handler = logging.handlers.RotatingFileHandler(
    LOG_FILE, encoding="utf-8", maxBytes=20 * 1024 * 1024, backupCount=5
)
file_handler.setFormatter(file_formatter)

console_formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(console_formatter)

root_logger.handlers = []
from charlie.log_redaction import SensitiveDataFilter

redaction_filter = SensitiveDataFilter()
file_handler.addFilter(redaction_filter)
console_handler.addFilter(redaction_filter)
root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

# 3. NOW IMPORT CHARLIE MODULES
from charlie import background_task, telemetry
from charlie.errors import ErrorClass, classify_exception
from charlie.config import Config, config
from charlie.core import Brain
from charlie.surface_intent import match_surface_request
from charlie.events import EventMeta, EventSource, EventType
from charlie.ipc import EventBus
from charlie.memory_store import MemoryStore
from charlie.personality import get_emotion_for_context, parse_voice_command, parse_yes_no
from charlie.session_store import SessionStore
from charlie.state import StateMachine
from charlie.subsystem_health import HealthRegistry, HealthStatus
from charlie.presentation import (
    AnchorTarget,
    AttentionLevel as PresentationAttention,
    DismissPolicy,
    PresentationIntent,
    PresentationKind,
    PreferredZone,
)
from charlie.voice import VoiceEngine
from charlie.attention import AttentionLevel
from charlie.watchers import (
    WatcherRegistry,
    cpu_ram_watcher,
    mcp_health_watcher,
    path_change_watcher,
    repeated_tool_failure_watcher,
    stalled_task_watcher,
    start_watcher_thread,
)

logger = logging.getLogger("charlie.main")
_LAUNCH_ID: str = str(uuid.uuid4())  # sidebar filters "this launch" vs "all history" by this
_state_machine = StateMachine()  # single authoritative CoreState instance for this process
_runtime_health = HealthRegistry(
    (
        "brain",
        "memory",
        "plugins",
        "mcp",
        "web",
        "companion",
        "hud",
        "telegram",
        "voice",
        "watchers",
    )
)


_SURFACE_REQUEST_IDS = {
    "calendar": "presentation:calendar",
    "chat": "presentation:chat",
    "mcp": "presentation:mcp",
    "media": "presentation:media",
    "settings": "presentation:settings",
    "system": "presentation:system",
    "tasks": "presentation:tasks",
    "terminal": "presentation:terminal",
    "tools": "presentation:tools",
}


def _surface_request_event(panel_id: str, action: str) -> tuple[str, dict, str]:
    """Translate an allowlisted summon request into the canonical surface contract."""
    surface_id = _SURFACE_REQUEST_IDS[panel_id]
    if action == "hide":
        return "presentation_dismiss", {"id": surface_id}, f"dismissed {panel_id} presentation"

    workspace_types = {
        "chat": "conversation",
        "settings": "settings",
        "system": "system",
        "tasks": "tasks",
        "terminal": "terminal",
    }
    workspace_type = workspace_types.get(panel_id)
    title = panel_id.replace("mcp", "MCP").upper()
    if workspace_type:
        intent = PresentationIntent(
            id=surface_id,
            kind=PresentationKind.WORKSPACE,
            title=title,
            summary=f"{title} workspace",
            content={"panel_id": panel_id, "source": "voice_surface_request"},
            priority=60,
            attention_level=PresentationAttention.NORMAL,
            dismiss_policy=DismissPolicy.PERSISTENT,
            workspace_type=workspace_type,
            preferred_zone=PreferredZone.CENTER,
            anchor=AnchorTarget.CORE,
            replayable=True,
            replace_key=surface_id,
        )
    else:
        intent = PresentationIntent(
            id=surface_id,
            kind=PresentationKind.WIDGET,
            title=title,
            summary=f"{title} context",
            content={"panel_id": panel_id, "source": "voice_surface_request"},
            priority=50,
            attention_level=PresentationAttention.NORMAL,
            dismiss_policy=DismissPolicy.TIMED,
            auto_dismiss_ms=8000,
            widget_type=panel_id,
            preferred_zone=PreferredZone.TOP_RIGHT,
            anchor=AnchorTarget.CORE,
            replace_key=surface_id,
        )
    return "presentation_intent", intent.to_dict(), f"opened {panel_id} presentation"


async def _publish_subsystem_health(bus: Optional[EventBus] = None) -> None:
    """Publish current public health snapshot when the IPC producer exists."""
    if bus is None:
        return
    event = _runtime_health.event()
    await bus.emit(event["type"], event["payload"], meta=EventMeta(source=EventSource.VOICE))


def _set_subsystem_health(name: str, status: HealthStatus) -> None:
    """Record one safe public subsystem transition."""
    _runtime_health.set(name, status)


def _start_subsystem_process(
    name: str,
    command: Tuple[str, ...],
    env: Optional[Dict[str, str]] = None,
) -> Optional[subprocess.Popen]:
    """Start one optional child process without taking down the core."""
    try:
        process = subprocess.Popen(
            command,
            cwd=os.path.dirname(__file__),
            env=env,
        )
    except Exception:
        logger.warning("Failed to start %s", name, exc_info=True)
        _set_subsystem_health(name, HealthStatus.DEGRADED)
        return None
    logger.info("%s subprocess started (PID: %s)", name.capitalize(), process.pid)
    _set_subsystem_health(name, HealthStatus.RUNNING)
    return process


class _UnavailableVoiceEngine:
    """No-op voice replacement that keeps non-voice Charlie features available."""

    is_available = False

    def __init__(self) -> None:
        self.is_speaking = threading.Event()
        self._muted = True
        self._volume = 0.0

    def speak(self, text: str, emotion: str) -> None:
        return None

    def stop(self) -> None:
        return None

    def stop_tts(self) -> None:
        return None

    def is_echo(self, text: str) -> bool:
        return False

    def set_event_bus(self, bus: EventBus) -> None:
        return None

    def set_wake_word_callback(self, callback: Callable[[], None]) -> None:
        return None

    def set_audio_state(self, muted: Optional[bool] = None, volume: Optional[float] = None) -> Dict[str, object]:
        if muted is not None:
            self._muted = muted
        if volume is not None:
            self._volume = volume
        return {"muted": self._muted, "volume": self._volume, "available": False}

    def set_mic_state(self, mic_muted: bool) -> Dict[str, object]:
        self._muted = mic_muted
        return {"mic_muted": self._muted, "available": False}

    def start_ptt(self) -> None:
        return None

    def stop_ptt(self) -> None:
        return None

    def cancel_ptt(self) -> None:
        return None


def _start_voice_or_degrade(
    voice_config: Config,
    on_speech: Callable[[str], None],
    on_tts_start: Callable[[], None],
    on_tts_stop: Callable[[], None],
) -> VoiceEngine | _UnavailableVoiceEngine:
    """Start voice or retain text and web operation after a voice failure."""
    try:
        voice = VoiceEngine(
            voice_config,
            on_speech=on_speech,
            on_tts_start=on_tts_start,
            on_tts_stop=on_tts_stop,
        )
        voice.start()
    except Exception:
        logger.warning("Failed to start voice", exc_info=True)
        _set_subsystem_health("voice", HealthStatus.DEGRADED)
        return _UnavailableVoiceEngine()
    _set_subsystem_health("voice", HealthStatus.RUNNING)
    return voice


def _charlie_state_envelope() -> dict:
    return {
        "type": EventType.CHARLIE_STATE.value,
        "payload": {
            "state": _state_machine.state.value,
            "activities": sorted(_state_machine.activities()),
            "since": _state_machine.since,
        },
        **dataclasses.asdict(EventMeta(source=EventSource.VOICE)),
    }


def _on_event_for_state(envelope: dict) -> Optional[dict]:
    if _state_machine.apply(envelope) is None:
        return None
    return _charlie_state_envelope()


# Streaming TTS flush thresholds (chars, not words)
# First sentence: speak after first sentence boundary. Force-flush at 200 chars if no boundary.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")
_CLAUSE_BOUNDARY = re.compile(r"(?<=[,;])\s+")
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_MAX_FLUSH_CHARS = 200  # Force-flush at word boundary if no sentence boundary seen
_IDLE_RETURN_THRESHOLD_S = 60.0  # idle_seconds below this after being above it counts as "user returned"


def _flush_complete_sentences(buffer: str, sink: "Callable[[str], None]") -> Tuple[str, bool]:
    """Split `buffer` on sentence boundaries and feed complete sentences to `sink`.

    Returns the leftover (incomplete trailing sentence) and whether any complete
    sentence was flushed. The trailing `parts[-1]` is the carry-over for the
    next chunk; `parts[:-1]` are complete sentences.
    """
    if not _SENTENCE_BOUNDARY.search(buffer):
        return buffer, False
    parts = _SENTENCE_BOUNDARY.split(buffer)
    for part in parts[:-1]:
        if part.strip():
            sink(part)
    return parts[-1], len(parts) > 1


def _strip_think(text: str) -> str:
    """Remove reasoning/thought blocks so they never reach the chat UI."""
    return _THINK_RE.sub("", text).strip()


_SEARCH_RESULTS_RE = re.compile(
    r"\[SEARCH RESULTS.*?\]|\[END SEARCH RESULTS\]",
    re.DOTALL | re.IGNORECASE,
)
_TOOL_LINE_RE = re.compile(r"(?m)^(TOOL:.*|\s*\{.*\}.*)$")


def _strip_search_result_tags(text: str) -> str:
    """Remove [SEARCH RESULTS] blocks and their end markers from text."""
    return _SEARCH_RESULTS_RE.sub("", text).strip()


def _strip_tool_lines(text: str) -> str:
    """Remove TOOL: ... lines and raw JSON tool-call artifacts from text."""
    lines = text.splitlines()
    kept = [ln for ln in lines if not _TOOL_LINE_RE.match(ln)]
    return "\n".join(kept).strip()


def _safe_speak(voice, text: str, emotion: str, label: str = "") -> None:
    """Speak text, logging (not swallowing) any TTS failure.

    A mid-stream TTS error must never abort the answer generation loop --
    the UI token stream and message persistence downstream must still run.
    """
    if not text or not text.strip():
        return
    try:
        voice.speak(text.strip(), emotion)
    except Exception:
        logger.warning(
            "TTS speak failed%s: dropping audio only, answer continues",
            f" ({label})" if label else "",
            exc_info=True,
        )


def _schedule_process(coro, loop):
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        fut.add_done_callback(
            lambda f: logger.error("Answer turn failed", exc_info=f.exception()) if f.exception() is not None else None
        )
    except Exception:  # pragma: no cover - add_done_callback itself failed
        logger.warning("Could not attach failure callback to answer task", exc_info=True)
    return fut


async def _restart_mcp_client(old_client, config):
    """Stop old_client and start a fresh one, both off the event loop.

    mcp_client.stop() and start_mcp() are synchronous and block on
    subprocess handshakes (up to config.timeout, default 30s per server);
    called directly inside an async def they freeze the whole event loop,
    including consume_web_commands, for that long.
    """
    if old_client is not None:
        await asyncio.to_thread(old_client.stop)
    if not config.mcp_enabled:
        return None
    from charlie.mcp_client import start_mcp

    return await asyncio.to_thread(start_mcp, config)


async def main():
    loop = asyncio.get_running_loop()
    _orig_handler = loop.call_exception_handler

    def _guarded_handler(ctx):
        if not isinstance(ctx.get("exception"), asyncio.CancelledError):
            _orig_handler(ctx)

    loop.call_exception_handler = _guarded_handler

    logger.info("Charlie is waking up...")
    voice = None
    store = None
    speech_echo_cooldown = 0.0
    last_emotion = "neutral"
    # VAD-fragmented duplicate text within this window is suppressed (see on_speech).
    recent_turn_texts: Dict[str, float] = {}
    _DEDUPE_WINDOW_SEC = 20.0
    web_proc = None
    pet_proc = None
    telegram_bot = None
    # True while a chat turn's LLM/tool loop runs -- see _dispatch_or_queue.
    turn_active = False
    pending_turns: list = []

    try:
        store = SessionStore(config.session_db_path)
    except Exception as e:
        logger.error(f"Failed to initialize SessionStore: {e}")
        return
    from charlie.audit_store import AuditStore

    audit_store = AuditStore(config.session_db_path)
    # Initialize vector memory store (graceful degradation if no embedding backend)
    memory_store = None
    try:
        memory_store = MemoryStore(config)
        _set_subsystem_health("memory", HealthStatus.RUNNING)
    except Exception as e:
        logger.warning(f"Vector memory disabled: {e}")
        _set_subsystem_health("memory", HealthStatus.DEGRADED)

    def speaking_callback(text):
        if voice:
            voice.speak(text, last_emotion)

    loop = asyncio.get_running_loop()

    def on_tool_call(name, args):
        try:
            active_audit_store = audit_store
        except NameError:
            active_audit_store = None
        if active_audit_store is not None:
            active_audit_store.record(name, args, "requested")
        if event_bus:
            asyncio.run_coroutine_threadsafe(
                event_bus.emit(
                    "tool_call",
                    {"name": name, "args": args, "session_id": current_web_session_id},
                    meta=EventMeta(source=EventSource.BRAIN),
                ),
                loop,
            )

    def on_tool_result(name, result):
        try:
            active_audit_store = audit_store
        except NameError:
            active_audit_store = None
        if active_audit_store is not None:
            active_audit_store.record(name, {}, "completed" if not str(result).startswith("Error") else "failed")
        if event_bus:
            asyncio.run_coroutine_threadsafe(
                event_bus.emit(
                    "tool_result",
                    {"name": name, "text": result, "session_id": current_web_session_id},
                    meta=EventMeta(source=EventSource.BRAIN),
                ),
                loop,
            )

    def on_thinking_update(name, args):
        if event_bus:
            desc = f"I'll use the {name} tool"
            if args:
                summary = str(args)[:80]
                desc += f" with {summary}"
            asyncio.run_coroutine_threadsafe(
                event_bus.emit(
                    "thinking_update",
                    {"text": desc, "session_id": current_web_session_id},
                    meta=EventMeta(source=EventSource.BRAIN),
                ),
                loop,
            )

    _CONVERSATION_SUMMON_RE = re.compile(r"\b(?:show|open) (?:me )?(?:the )?(?:chat|conversation)\b", re.IGNORECASE)

    async def _summon_conversation_workspace(toggle: bool = False):
        """Summon the one React HUD surface; never open a legacy dashboard route."""
        nonlocal hud_visible, hud_browser_opened
        from charlie.utils import open_url_in_browser

        if toggle:
            hud_visible = not hud_visible
        elif not hud_visible:
            hud_visible = True
        host = "127.0.0.1" if config.charlie_host == "0.0.0.0" else config.charlie_host
        if hud_visible and not hud_browser_opened:
            hud_browser_opened = open_url_in_browser(f"http://{host}:{config.charlie_port}/")
        if event_bus:
            await event_bus.emit(
                "hud_visibility",
                {"visible": hud_visible},
                meta=EventMeta(source=EventSource.SURFACE, rationale="pet or hotkey toggled React HUD"),
            )

    def _resolve_tool_approval_and_notify(request_id: str, approved: bool) -> None:
        """Resolve pending future and dismiss its canonical attention intent."""
        from charlie.core import resolve_tool_approval

        resolve_tool_approval(request_id, approved)
        if event_bus is not None:
            asyncio.run_coroutine_threadsafe(
                event_bus.emit(
                    "tool_approval_resolved",
                    {"request_id": request_id},
                    meta=EventMeta(source=EventSource.BRAIN),
                ),
                loop,
            )
            asyncio.run_coroutine_threadsafe(
                event_bus.emit(
                    "presentation_dismiss",
                    {"id": request_id},
                    meta=EventMeta(source=EventSource.BRAIN, rationale="approval resolved"),
                ),
                loop,
            )

    def on_tool_approval_request(request_id, tool_name, reason, platform, risk_class):
        # telegram_bot is None until its startup block below runs -- read at call time, not def time.
        if platform == "telegram" and telegram_bot and should_relay_approval(True, config.telegram_user_id):
            asyncio.run_coroutine_threadsafe(
                telegram_bot.send_approval_request(config.telegram_user_id, request_id, tool_name, reason), loop
            )
        if event_bus is None:
            return
        intent = PresentationIntent(
            id=request_id,
            kind=PresentationKind.ATTENTION,
            title=f"Approval needed: {tool_name}",
            summary=reason,
            content={
                "request_id": request_id,
                "tool_name": tool_name,
                "reason": reason,
                "arguments": {},
                "risk_class": risk_class,
            },
            priority=95,
            attention_level=PresentationAttention.HIGH,
            dismiss_policy=DismissPolicy.MANUAL,
            preferred_zone=PreferredZone.CENTER,
            anchor=AnchorTarget.CORE,
            replayable=True,
            replace_key=f"approval:{request_id}",
        )
        asyncio.run_coroutine_threadsafe(
            event_bus.emit(
                "presentation_intent",
                intent.to_dict(),
                meta=EventMeta(source=EventSource.BRAIN, rationale="tool approval requires attention"),
            ),
            loop,
        )

    def on_result_stored(task_id, summary, attention_level):
        if event_bus is None:
            return
        from charlie.utils import make_id

        if AttentionLevel(attention_level) < AttentionLevel.INFORM:
            return
        intent = PresentationIntent(
            id=make_id(),
            kind=PresentationKind.NOTIFICATION,
            task_id=task_id,
            title="Task finished",
            summary=summary,
            content={"task_id": task_id},
            priority=60,
            attention_level=PresentationAttention.NORMAL,
            dismiss_policy=DismissPolicy.TIMED,
            auto_dismiss_ms=60000,
            preferred_zone=PreferredZone.TOP_RIGHT,
            anchor=AnchorTarget.CORE,
        )
        asyncio.run_coroutine_threadsafe(
            event_bus.emit(
                "presentation_intent",
                intent.to_dict(),
                meta=EventMeta(source=EventSource.TASK, task_id=task_id, rationale="task result ready"),
            ),
            loop,
        )

    def on_research_result(report):
        """Forward typed research cards; chat remains the text fallback."""
        if event_bus is None:
            return
        payload = report.structured_payload()
        payload["session_id"] = current_web_session_id
        asyncio.run_coroutine_threadsafe(
            event_bus.emit(
                "research_result",
                payload,
                meta=EventMeta(source=EventSource.TASK),
            ),
            loop,
        )

    try:
        brain = Brain(
            config,
            on_thought_callback=speaking_callback,
            session_store=store,
            memory_store=memory_store,
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
            on_thinking_update=on_thinking_update,
            on_tool_approval_request=on_tool_approval_request,
            on_result_stored=on_result_stored,
            on_research_result=on_research_result,
        )
        _set_subsystem_health("brain", HealthStatus.RUNNING)
    except Exception as e:
        logger.error(f"Failed to initialize Brain: {e}")
        _set_subsystem_health("brain", HealthStatus.DEGRADED)
        if store:
            store.close()
        return

    # Wire vector memory store into tool registry
    from charlie.tools import registry as tool_registry

    if memory_store is not None:
        tool_registry.set_memory_store(memory_store)
    # Wire knowledge graph into tool registry
    if brain is not None and hasattr(brain, "memory_graph"):
        tool_registry.set_memory_graph(brain.memory_graph)

    # Wire the plugin system into the tool registry (no-op unless enabled).
    # The SAME registry the LLM calls, so when PLUGINS_ENABLED=true the
    # plugin_* tools appear alongside the built-in tools and are gated by
    # the flag off by default.
    from charlie.tools import register_plugin_tools

    try:
        plugin_manager = register_plugin_tools(config)
        if plugin_manager is None:
            logger.info("Plugin system disabled (PLUGINS_ENABLED=false).")
            _set_subsystem_health("plugins", HealthStatus.DISABLED)
        else:
            logger.info("Plugin system ACTIVE: plugin_* tools registered.")
            _set_subsystem_health("plugins", HealthStatus.RUNNING)
    except Exception as e:
        logger.warning(f"Plugin system failed to initialize: {e}")
        plugin_manager = None
        _set_subsystem_health("plugins", HealthStatus.DEGRADED)
    if plugin_manager is None:
        # Always keep a manager available so a mirrored "extension_enabled"/
        # "extension_disabled" command (see consume_web_commands below) can
        # enable/disable one built-in plugin even when the blanket
        # PLUGINS_ENABLED flag is off -- matches charlie/web_server.py's
        # identical fallback, needed for the same per-plugin-control reason.
        from charlie.plugins import PluginManager

        plugin_manager = PluginManager()

    # Wire the MCP subsystem into the SAME shared tool registry (no-op unless enabled).
    # Runs on a thread, awaited later, so it overlaps with VoiceEngine/STT startup instead of blocking it.
    mcp_client = None
    self_extension_orchestrator = None
    if config.mcp_enabled:
        _set_subsystem_health("mcp", HealthStatus.STARTING)

    async def _start_mcp_task():
        nonlocal mcp_client
        try:
            if config.mcp_enabled:
                from charlie.mcp_client import start_mcp

                mcp_client = await asyncio.to_thread(start_mcp, config)
                if mcp_client is None:
                    logger.info("MCP subsystem not started (no servers configured)")
                    _set_subsystem_health("mcp", HealthStatus.DEGRADED)
                else:
                    _set_subsystem_health("mcp", HealthStatus.RUNNING)
            else:
                logger.info("MCP subsystem not enabled (MCP_ENABLED=false)")
                _set_subsystem_health("mcp", HealthStatus.DISABLED)
        except Exception as e:
            logger.warning(f"MCP subsystem failed to initialize: {e}")
            mcp_client = None
            _set_subsystem_health("mcp", HealthStatus.DEGRADED)
        finally:
            await _publish_subsystem_health(event_bus)

    mcp_start_task = asyncio.create_task(_start_mcp_task())

    # Placeholder for event_bus (set later in async context)
    event_bus = None
    # Per-launch fallback, not the old shared "default" bucket across all launches.
    current_web_session_id = f"voice_{_LAUNCH_ID}"
    _voice_fallback_session_id = current_web_session_id
    hud_visible = False
    hud_browser_opened = False

    def ensure_session_ready(session_id: str):
        if not session_id:
            return
        try:
            store.create_session(session_id, title="New Chat", source="voice", launch_id=_LAUNCH_ID)
        except Exception as exc:
            logger.debug(f"ensure_session_ready skipped: {exc}")

    def update_session_title_from_text(session_id: str, user_text: str) -> None:
        if not session_id or not user_text:
            return
        try:
            rows = store.get_sessions()
            session_map = {row[0]: row for row in rows}
            session = session_map.get(session_id)
            if not session:
                return
            current_title = session[1] or "New Chat"
            if current_title != "New Chat":
                return
            candidate = " ".join(user_text.strip().split()[:6]).strip()
            if not candidate:
                return
            store.update_session_title(session_id, candidate)
            if event_bus:
                asyncio.run_coroutine_threadsafe(
                    event_bus.emit(
                        "session_updated",
                        {"session_id": session_id, "title": candidate},
                        meta=EventMeta(source=EventSource.VOICE),
                    ),
                    loop,
                )
        except Exception as exc:
            logger.debug(f"update_session_title_from_text skipped: {exc}")

    def on_speech(text: str):
        nonlocal current_web_session_id
        text = _normalize_app_list(text)
        logger.info(f"Speech detected: {text}")

        now = time.time()
        normalized = text.strip().lower()
        for stale in [k for k, t in recent_turn_texts.items() if now - t >= _DEDUPE_WINDOW_SEC]:
            del recent_turn_texts[stale]
        last_dispatch = recent_turn_texts.get(normalized)
        if last_dispatch is not None and now - last_dispatch < _DEDUPE_WINDOW_SEC:
            logger.info(f"Duplicate utterance suppressed ({now - last_dispatch:.1f}s ago): {text}")
            return
        recent_turn_texts[normalized] = now

        session_id = _voice_fallback_session_id
        if current_web_session_id not in (None, _voice_fallback_session_id, ""):
            session_id = current_web_session_id
        ensure_session_ready(session_id)
        _schedule_process(_dispatch_or_queue(text, session_id), loop)

    async def _dispatch_or_queue(text, session_id, platform="voice"):
        """Run the turn now, or queue it if one is already running tool calls.

        Only ever called via _schedule_process (run_coroutine_threadsafe), so
        this always executes on the loop thread -- the turn_active check and
        pending_turns mutation below are a single synchronous span with no
        await in between, making them atomic with respect to any other
        coroutine on this loop without needing a lock.
        """
        nonlocal turn_active
        from charlie.core import get_active_voice_approval

        # A gated tool call inside the still-running turn is waiting on a
        # spoken yes/no -- that answer must reach _process() immediately
        # (it routes to resolve_tool_approval), never queued behind the
        # very turn it's meant to unblock.
        if turn_active and not voice.is_speaking.is_set() and not get_active_voice_approval():
            pending_turns.append((text, session_id, platform))
            logger.info(f"Queued utterance (a turn is already running tool calls): {text}")
            return
        await _process(text, brain, voice, session_id=session_id, platform=platform)

    async def _process(text, brain, voice, session_id="default", platform="voice"):
        nonlocal speech_echo_cooldown, last_emotion, turn_active
        if time.time() < speech_echo_cooldown:
            logger.info(f"Echo suppressed: {text}")
            return

        # A gated tool call (destructive shell command / sensitive file path)
        # is waiting on a spoken yes/no -- route this utterance to the answer
        # instead of starting a new chat turn. See
        # charlie.core.Brain.request_tool_approval / get_active_voice_approval.
        from charlie.core import get_active_voice_approval

        pending_approval_id = get_active_voice_approval()
        if pending_approval_id:
            answer = parse_yes_no(text)
            if answer is None:
                voice.speak("Sorry, I didn't catch that. Say yes to continue or no to cancel.", last_emotion)
                return
            _resolve_tool_approval_and_notify(pending_approval_id, answer)
            voice.speak("Okay, running it." if answer else "Cancelled.", last_emotion)
            return

        print(f"\rHeard: {text}", flush=True)
        if config.enable_barge_in and voice.is_speaking.is_set():
            # Barge-in detection: command words always interrupt immediately
            _BARGE_COMMANDS = {
                "stop",
                "wait",
                "no",
                "cancel",
                "quiet",
                "shut",
                "enough",
            }
            words = set(text.lower().strip().split())
            if words & _BARGE_COMMANDS:
                logger.info("Barge-in: Command word detected. Stopping TTS.")
                voice.stop_tts()
                brain.cancel_chat()
                speech_echo_cooldown = time.time() + 1.5
            else:
                # Echo detection: is this a subset of what Charlie is currently saying?
                if voice.is_echo(text):
                    logger.info(f"Echo suppressed (during TTS): {text}")
                    return
                # New content during TTS -- barge in (cancel current turn)
                logger.info("Barge-in: New user input during TTS. Canceling.")
                voice.stop_tts()
                brain.cancel_chat()
                speech_echo_cooldown = time.time() + 0.8

        # Route !search command
        if text.strip().startswith("!search "):
            query = text.strip()[len("!search ") :].strip()
            print("Searching history...", end="\r", flush=True)
            results = store.search(query)
            if not results:
                response_str = "No matching history found."
            else:
                response_str = f"Found {len(results)} result(s):\n"
                for role, content in results:
                    truncated = content[:120] + "..." if len(content) > 120 else content
                    response_str += f"- [{role}]: {truncated}\n"
            print(f"\n{response_str}", flush=True)
            voice.speak(response_str, last_emotion)
            return
        # Route /memory-review command
        if text.strip().lower() in ("/memory-review", "!memory-review"):
            if brain is None:
                response_str = "Brain not initialized."
            else:
                graph = brain.memory_graph
                facts = graph.get_all_facts()
                if not facts:
                    response_str = "Knowledge graph is empty."
                else:
                    # Build summary
                    subjects = {}
                    for s, p, o in facts:
                        subjects.setdefault(s, []).append(f"{p} -> {o}")
                    response_str = f"Knowledge graph: {len(facts)} facts.\n"
                    for subj, preds in sorted(subjects.items()):
                        response_str += f"  {subj}:\n"
                        for pred in preds[:3]:
                            response_str += f"    {pred}\n"
                        if len(preds) > 3:
                            response_str += f"    ... +{len(preds) - 3} more\n"
            print(f"\n{response_str}", flush=True)
            voice.speak(response_str, last_emotion)
            return
        panel_intent = match_surface_request(text)
        if panel_intent is not None:
            await _summon_conversation_workspace()
            if event_bus:
                event_type, payload, rationale = _surface_request_event(
                    panel_intent.panel_id,
                    panel_intent.action,
                )
                await event_bus.emit(
                    event_type,
                    payload,
                    meta=EventMeta(source=EventSource.VOICE, rationale=rationale),
                )
            voice.speak("Here you go." if panel_intent.action == "show" else "Hidden.", last_emotion)
            return

        # Route conversation-only phrase to the normal HUD summon path.
        if _CONVERSATION_SUMMON_RE.search(text):
            await _summon_conversation_workspace()
            voice.speak("Here you go.", last_emotion)
            return

        # Emit transcript event for voice-originated turns only. The web
        # client already renders its own optimistic user bubble the instant
        # it sends the chat command (see handleSendMessage in page.tsx), so
        # echoing a "transcript" event for platform="web" too produced a
        # duplicate user bubble on every web chat message. Voice has no
        # client-side echo of its own -- this event is its only way to get
        # recognized speech into the web UI transcript feed.
        if event_bus and platform == "voice":
            asyncio.create_task(
                event_bus.emit(
                    "transcript",
                    {"text": text, "source": platform, "session_id": session_id},
                    meta=EventMeta(source=EventSource.VOICE),
                )
            )

        # Store user message
        try:
            store.append("user", text, session_id=session_id)
            store.touch_session(session_id)
            update_session_title_from_text(session_id, text)
        except Exception as e:
            logger.warning(f"Failed to archive user message or touch session: {e}")
        # Voice command detection (before LLM call)
        cmd_emotion = parse_voice_command(text)
        if cmd_emotion is not None:
            last_emotion = cmd_emotion
            ack_map = {
                "energetic": "Got it. Switching to energetic.",
                "calm": "Got it, calming down.",
            }
            ack = ack_map.get(cmd_emotion, "Got it.")
            voice.speak(ack, cmd_emotion)
            return

        # Detect emotion for this turn
        detected_emotion = get_emotion_for_context(text)

        # Sparkle announcements on emotion change
        sparkle = ""
        if detected_emotion != last_emotion:
            sparkle_map = {
                "energetic": "Oh, exciting! ",
                "calm": "Got it, calming down. ",
                "sad": "I hear you. ",
            }
            sparkle = sparkle_map.get(detected_emotion, "")
        last_emotion = detected_emotion

        # Emit thinking event
        if event_bus:
            asyncio.create_task(
                event_bus.emit("thinking", {"session_id": session_id}, meta=EventMeta(source=EventSource.BRAIN))
            )

        print("Charlie is thinking...", end="\r", flush=True)

        # Streaming buffer
        sentence_buffer = ""
        web_buffer = ""  # sentence buffer for web UI token events
        full_reply_buffer = ""
        is_first_chunk = True

        is_first_flush = True
        turn_active = True
        try:
            async for chunk in brain.chat_stream(text, platform=platform, session_id=session_id):
                if is_first_chunk:
                    print("\r" + " " * 30 + "\r", end="", flush=True)
                    is_first_chunk = False
                print(chunk, end="", flush=True)
                sentence_buffer += chunk
                full_reply_buffer += chunk
                web_buffer += chunk

                # Real-time UI token stream: emit whole sentences as they complete.
                # This is the ONLY source of "token" events for the chat UI, so the
                # text accumulates without duplication. Internal model text like
                # <think>...</think>, [SEARCH RESULTS]...[/SEARCH RESULTS], and
                # TOOL: ... lines are stripped here so reasoning/tool metadata
                # never leaks into the chat.
                if event_bus and _SENTENCE_BOUNDARY.search(web_buffer):
                    parts = _SENTENCE_BOUNDARY.split(web_buffer)
                    for part in parts[:-1]:
                        if part.strip():
                            safe = _strip_search_result_tags(part.strip())
                            safe = _strip_tool_lines(safe)
                            safe = _strip_think(safe)
                            if safe:
                                await event_bus.emit(
                                    "token",
                                    {
                                        "text": safe if safe.endswith((".", "!", "?")) else safe + ". ",
                                        "session_id": session_id,
                                    },
                                    meta=EventMeta(source=EventSource.BRAIN),
                                )
                    web_buffer = parts[-1]

                # Progressive flush: sentence boundary > clause boundary > force-flush.
                flushed = False

                # Early first-flush: wait for first sentence boundary, or force at 150 chars
                if is_first_flush:
                    sentence_buffer, flushed = _flush_complete_sentences(
                        sentence_buffer,
                        lambda part: _safe_speak(voice, part, detected_emotion, "first-flush"),
                    )
                    if flushed:
                        is_first_flush = False
                    elif len(sentence_buffer) >= 150:
                        idx = sentence_buffer.rfind(" ", 0, 150)
                        if idx > 0:
                            _safe_speak(voice, sentence_buffer[:idx], detected_emotion, "first-force")
                            sentence_buffer = sentence_buffer[idx:].lstrip()
                        is_first_flush = False
                        flushed = True

                if not flushed:
                    sentence_buffer, flushed = _flush_complete_sentences(
                        sentence_buffer,
                        lambda part: _safe_speak(voice, part, detected_emotion, "sentence"),
                    )

                if not flushed and len(sentence_buffer) >= _MAX_FLUSH_CHARS:
                    # Force-flush: prefer clause (comma/semicolon) boundary,
                    # fall back to word boundary to avoid mid-word splits.
                    clause_idx = _CLAUSE_BOUNDARY.search(sentence_buffer[:_MAX_FLUSH_CHARS])
                    if clause_idx:
                        flush_end = clause_idx.end()
                        _safe_speak(voice, sentence_buffer[:flush_end], detected_emotion, "clause")
                        sentence_buffer = sentence_buffer[flush_end:].lstrip()
                    else:
                        word_idx = sentence_buffer.rfind(" ", 0, _MAX_FLUSH_CHARS)
                        if word_idx > 0:
                            _safe_speak(voice, sentence_buffer[:word_idx], detected_emotion, "word")
                            sentence_buffer = sentence_buffer[word_idx:].lstrip()
                        elif sentence_buffer.strip():
                            _safe_speak(
                                voice,
                                sentence_buffer[:_MAX_FLUSH_CHARS],
                                detected_emotion,
                                "force",
                            )
                            sentence_buffer = sentence_buffer[_MAX_FLUSH_CHARS:]

            # Final web UI flush - emit any remaining text stuck in web_buffer
            if event_bus and web_buffer.strip():
                await event_bus.emit(
                    "token",
                    {
                        "text": _strip_think(_strip_tool_lines(_strip_search_result_tags(web_buffer.strip()))),
                        "session_id": session_id,
                    },
                    meta=EventMeta(source=EventSource.BRAIN),
                )

            # Final TTS
            if sentence_buffer.strip():
                _safe_speak(voice, sparkle + sentence_buffer, detected_emotion, "final")

            # Persist the generated reply, falling back to web_buffer if cancelled.
            final_reply = full_reply_buffer.strip() or web_buffer.strip()
            if final_reply:
                try:
                    store.append("assistant", final_reply, session_id=session_id)
                    store.touch_session(session_id)
                except Exception as e:
                    logger.warning(f"Failed to archive assistant message or touch session: {e}")
                if platform == "telegram" and telegram_bot:
                    try:
                        await telegram_bot.send_message(config.telegram_user_id, final_reply)
                    except Exception:
                        logger.warning("Failed to send Telegram reply", exc_info=True)

            # Emit response_done event so the UI can stop its typing indicator.
            if event_bus:
                await event_bus.emit(
                    "response_done",
                    {"session_id": session_id},
                    meta=EventMeta(source=EventSource.BRAIN),
                )
        except Exception as exc:
            logger.error("Turn failed", exc_info=True)
            error_class, message = classify_exception(exc)
            _safe_speak(voice, message, last_emotion, "turn-failed")
            if event_bus:
                severity = "error" if error_class == ErrorClass.CRITICAL else "warning"
                await event_bus.emit(
                    "alert",
                    {"severity": severity, "message": message},
                    meta=EventMeta(source=EventSource.BRAIN, rationale=f"turn failed: {error_class.value}"),
                )
                await event_bus.emit(
                    "response_done",
                    {"session_id": session_id},
                    meta=EventMeta(source=EventSource.BRAIN, rationale="turn failed with an unhandled exception"),
                )
            raise
        finally:
            turn_active = False
            if pending_turns:
                next_text, next_session, next_platform = pending_turns.pop(0)
                logger.info(f"Dequeuing pending turn: {next_text}")
                _schedule_process(_dispatch_or_queue(next_text, next_session, next_platform), loop)

        # Learning loop: deferred to background -- doesn't block next turn.
        # Skipped for screen-content queries -- the reply is a description of
        # whatever's on screen at that moment, never a genuine user preference,
        # and storing it as one pollutes memory with stale screen snapshots that
        # resurface on later "what's on my screen" queries.
        from charlie.router import SCREEN_QUERY_RE as _screen_query_re

        if full_reply_buffer.strip() and text.strip() and not _screen_query_re.search(text):

            async def _background_learn(user_text: str, reply_text: str):
                try:
                    if not config.llm_url:
                        return
                    learning_prompt = (
                        f"User said: {user_text}\n"
                        f"Charlie replied: {reply_text}\n"
                        "Extract 0-1 new user preferences (e.g., 'prefers short answers'). "
                        "Output ONLY the preference line, or output nothing if nothing new."
                    )
                    response = await brain.client.post(
                        "chat/completions",
                        json={
                            "model": config.llm_model,
                            "messages": [{"role": "user", "content": learning_prompt}],
                            "temperature": 0.0,
                            "max_tokens": 120,
                            "stream": False,
                        },
                    )
                    response.raise_for_status()
                    content = response.json()["choices"][0]["message"].get("content")
                    learning = content.strip() if isinstance(content, str) else ""
                    clean_learning = learning.lower().rstrip(".")
                    if not learning or any(
                        clean_learning.startswith(p)
                        for p in ("nothing", "none", "no new", "no preference", "no change", "no update")
                    ):
                        return

                    from charlie.tools import registry as tool_registry

                    existing = ""
                    u_path = Path(config.user_file)
                    if u_path.exists():
                        existing = u_path.read_text(encoding="utf-8")

                    if learning not in existing:
                        await asyncio.get_running_loop().run_in_executor(
                            None,
                            tool_registry.execute_tool,
                            "memory",
                            {
                                "action": "add",
                                "target": "user",
                                "content": learning,
                            },
                        )
                        brain.reload_context()
                        logger.info(f"Learning: {learning}")
                except Exception as e:
                    logger.debug(f"Learning loop skipped: {e}")

            # Fire-and-forget: learning runs in background, doesn't block user
            asyncio.create_task(_background_learn(text, full_reply_buffer))

    async def _reload_voice_engine():
        """Stop and respawn VoiceEngine so mic/VAD/ASR/TTS-model/wake-word settings take effect.

        These are all baked into VoiceEngine.__init__ or the ASR worker subprocess it
        spawns (see charlie/config.py's "voice" restart tier), so a live attribute
        change alone never reaches them -- only recreating the engine does.
        """
        nonlocal voice
        try:
            voice.stop()
        except Exception as ex:
            logger.warning(f"Error stopping voice engine on reload: {ex}")
        try:
            voice = VoiceEngine(
                config,
                on_speech=on_speech,
                on_tts_start=on_tts_start,
                on_tts_stop=on_tts_stop,
            )
            voice.start()
            voice.set_wake_word_callback(on_wake_word)
            logger.info("VoiceEngine reloaded.")
        except Exception as ex:
            logger.error(f"Error reloading VoiceEngine: {ex}", exc_info=True)

    async def _reload_mcp_client():
        """Stop the MCP subprocess client and restart it if still enabled."""
        nonlocal mcp_client
        from charlie.tools import registry

        for k in [k for k in registry._tools if k.startswith("mcp_")]:
            registry._tools.pop(k, None)
        try:
            mcp_client = await _restart_mcp_client(mcp_client, config)
        except Exception as ex:
            logger.warning(f"Error reloading MCP client: {ex}")
            mcp_client = None

    def _reload_plugin_tools():
        """Re-register plugin tools to match the current enabled flag / allow-dirs."""
        from charlie.tools import registry

        for k in [k for k in registry._tools if k.startswith("plugin_")]:
            registry._tools.pop(k, None)
        if config.plugins_enabled:
            try:
                from charlie.tools import register_plugin_tools

                register_plugin_tools(config)
            except Exception as ex:
                logger.warning(f"Error registering plugins on reload: {ex}")

    async def consume_web_commands(event_bus, brain):
        """Read commands from the web UI and dispatch them."""
        nonlocal current_web_session_id, voice, mcp_client
        while True:
            try:
                cmd = await event_bus.next_command()
                logger.debug(f"ZMQ received command: {cmd}")
                cmd_type = cmd.get("type")
                if cmd_type == "chat":
                    payload_sid = cmd.get("payload", {}).get("session_id")
                    current_web_session_id = cmd.get("session_id") or payload_sid or _voice_fallback_session_id
                    from charlie.recovery import set_active_session_id

                    set_active_session_id(current_web_session_id)
                    chat_text = cmd.get("text") or cmd.get("payload", {}).get("text", "")
                    await _dispatch_or_queue(chat_text, current_web_session_id, platform="web")
                elif cmd_type == "session_active":
                    payload_sid = cmd.get("payload", {}).get("session_id")
                    current_web_session_id = cmd.get("session_id") or payload_sid or _voice_fallback_session_id
                    from charlie.recovery import set_active_session_id

                    set_active_session_id(current_web_session_id)
                    logger.info(f"Active session updated to: {current_web_session_id}")
                elif cmd_type == "ws_connection_count":
                    from charlie.recovery import set_active_ws_count

                    set_active_ws_count(cmd.get("count", 0))
                elif cmd_type == "recovery_approve":
                    payload = cmd.get("payload", {})
                    proposal_id = payload.get("proposal_id")
                    if proposal_id:
                        from charlie.recovery import pending_proposals

                        fut = pending_proposals.get(proposal_id)
                        if fut and not fut.done():
                            fut.set_result(True)
                elif cmd_type == "recovery_reject":
                    payload = cmd.get("payload", {})
                    proposal_id = payload.get("proposal_id")
                    if proposal_id:
                        from charlie.recovery import pending_proposals

                        fut = pending_proposals.get(proposal_id)
                        if fut and not fut.done():
                            fut.set_result(False)
                elif cmd_type == "tool_approve":
                    payload = cmd.get("payload", {})
                    request_id = payload.get("request_id")
                    if request_id:
                        _resolve_tool_approval_and_notify(request_id, True)
                elif cmd_type == "tool_reject":
                    payload = cmd.get("payload", {})
                    request_id = payload.get("request_id")
                    if request_id:
                        _resolve_tool_approval_and_notify(request_id, False)
                elif cmd_type == "terminal_command_request":
                    payload = cmd.get("payload", {})
                    request_id = payload.get("request_id")
                    terminal_session_id = payload.get("terminal_session_id")
                    command = payload.get("command")
                    if request_id and terminal_session_id and isinstance(command, str):
                        async def _handle_terminal_command_request(req_id: str, term_sid: str, cmd_str: str):
                            approved = await brain.request_tool_approval(
                                "shell_execute",
                                {"command": cmd_str},
                                "A terminal command needs approval",
                                platform="web",
                                risk_class="security_sensitive",
                            )
                            await event_bus.emit(
                                "terminal_command_result",
                                {
                                    "request_id": req_id,
                                    "terminal_session_id": term_sid,
                                    "command": cmd_str,
                                    "approved": approved,
                                },
                                meta=EventMeta(
                                    source=EventSource.BRAIN,
                                    rationale="terminal command approval resolved",
                                ),
                            )

                        asyncio.create_task(_handle_terminal_command_request(request_id, terminal_session_id, command))
                elif cmd_type == "stop":
                    voice.stop_tts()
                    brain.cancel_chat()
                elif cmd_type == "hud_invoke":
                    await _summon_conversation_workspace(toggle=True)
                elif cmd_type == "audio_control":
                    payload = cmd.get("payload", {})
                    state = voice.set_audio_state(
                        muted=payload.get("muted"),
                        volume=payload.get("volume"),
                    )
                    await event_bus.emit("audio_state", state, meta=EventMeta(source=EventSource.VOICE))
                elif cmd_type == "mic_control":
                    payload = cmd.get("payload", {})
                    mic_state = voice.set_mic_state(bool(payload.get("mic_muted", True)))
                    await event_bus.emit("mic_state", mic_state, meta=EventMeta(source=EventSource.VOICE))
                elif cmd_type == "self_extension_request":
                    payload = cmd.get("payload", {})
                    request_id = str(payload.get("request_id") or cmd.get("request_id") or uuid.uuid4().hex)

                    async def _run_self_extension(request_payload: dict, req_id: str) -> None:
                        if self_extension_orchestrator is None:
                            result = {
                                "success": False,
                                "status": "failed",
                                "message": "Self-extension runtime is not initialized.",
                            }
                        else:
                            request = self_extension_orchestrator.plan_request(
                                str(request_payload.get("prompt", "")),
                                explicit_user_request=bool(request_payload.get("explicit", True)),
                                affected_settings=dict(request_payload.get("settings") or {}),
                            )
                            extension_result = await asyncio.to_thread(
                                self_extension_orchestrator.execute_transaction,
                                request,
                            )
                            result = extension_result.to_dict()
                        await event_bus.emit(
                            "self_extension_result",
                            {"request_id": req_id, **result},
                            meta=EventMeta(
                                source=EventSource.BRAIN,
                                rationale="authoritative self-extension transaction result",
                            ),
                        )

                    asyncio.create_task(_run_self_extension(payload, request_id))
                elif cmd_type == "self_extension_rollback":
                    payload = cmd.get("payload", {})
                    tx_id = str(payload.get("tx_id", ""))
                    if tx_id and self_extension_orchestrator is not None:
                        await asyncio.to_thread(self_extension_orchestrator.rollback_transaction, tx_id)
                elif cmd_type == "ptt_start":
                    voice.start_ptt()
                    await event_bus.emit("ptt_start", {}, meta=EventMeta(source=EventSource.VOICE))
                    await event_bus.emit("vad_start", {"source": "ptt"}, meta=EventMeta(source=EventSource.VOICE))
                elif cmd_type == "ptt_stop":
                    voice.stop_ptt()
                    await event_bus.emit("ptt_stop", {}, meta=EventMeta(source=EventSource.VOICE))
                elif cmd_type == "ptt_cancel":
                    voice.cancel_ptt()
                    await event_bus.emit("ptt_cancel", {}, meta=EventMeta(source=EventSource.VOICE))
                elif cmd_type == "extension_installed":
                    # Mirrors charlie/web_server.py's confirm_extension(): the
                    # dashboard's Extensions tab only registers tools into that
                    # process's own registry, which the actual chat loop here
                    # never sees. Re-run the same install against this
                    # process's registry/mcp_client/plugin_manager so Charlie
                    # can actually call the extension in a real conversation.
                    payload = cmd.get("payload", {})
                    try:
                        from charlie.extensions.install import install_extension
                        from charlie.tools import registry as _ext_registry

                        tool_names, mcp_client = install_extension(
                            payload.get("kind", ""),
                            payload.get("name", ""),
                            payload.get("source", ""),
                            payload.get("raw_text", ""),
                            registry=_ext_registry,
                            plugin_manager=plugin_manager,
                            mcp_client=mcp_client,
                            plugin_allow_dirs=config.plugin_allow_dirs,
                        )
                        if payload.get("kind") == "skill":
                            from charlie.extensions.skills import format_skill_block, parse_skill_md

                            manifest = parse_skill_md(payload.get("raw_text", ""))
                            brain.add_installed_skill_block(payload.get("name", ""), format_skill_block(manifest))
                        logger.info(
                            "Mirrored extension install '%s' (%s) into voice process: %s",
                            payload.get("name"),
                            payload.get("kind"),
                            tool_names,
                        )
                    except Exception as ex:
                        logger.warning(
                            f"Failed to mirror extension install '{payload.get('name')}': {ex}",
                            exc_info=True,
                        )
                    brain.rebuild_stable_tier()
                elif cmd_type == "extension_enabled":
                    payload = cmd.get("payload", {})
                    kind = payload.get("kind", "")
                    ext_name = payload.get("name", "")
                    try:
                        from charlie.tools import registry as _ext_registry

                        if kind == "mcp" and mcp_client is not None:
                            mcp_client.enable_server(_ext_registry, ext_name)
                        elif kind == "plugin":
                            from charlie.extensions.install import builtin_plugin
                            from charlie.tools import enable_plugin

                            enable_plugin(
                                _ext_registry,
                                plugin_manager,
                                builtin_plugin(ext_name, config.plugin_allow_dirs),
                            )
                        # skill/openapi: nothing to do, disable_extension() never
                        # unregisters those tools (see web_server.py's comment).
                    except Exception as ex:
                        logger.warning(f"Failed to mirror extension enable '{ext_name}': {ex}", exc_info=True)
                    brain.rebuild_stable_tier()
                elif cmd_type == "extension_disabled":
                    payload = cmd.get("payload", {})
                    kind = payload.get("kind", "")
                    ext_name = payload.get("name", "")
                    try:
                        from charlie.tools import registry as _ext_registry

                        if kind == "mcp" and mcp_client is not None:
                            mcp_client.disable_server(_ext_registry, ext_name)
                        elif kind == "plugin":
                            from charlie.tools import disable_plugin

                            disable_plugin(_ext_registry, plugin_manager, ext_name)
                    except Exception as ex:
                        logger.warning(f"Failed to mirror extension disable '{ext_name}': {ex}", exc_info=True)
                    brain.rebuild_stable_tier()
                elif cmd_type == "extension_uninstalled":
                    payload = cmd.get("payload", {})
                    kind = payload.get("kind", "")
                    ext_name = payload.get("name", "")
                    try:
                        from charlie.tools import registry as _ext_registry

                        if kind == "mcp" and mcp_client is not None:
                            mcp_client.remove_server(_ext_registry, ext_name)
                        elif kind in ("skill", "openapi"):
                            for tool_name in payload.get("tool_names", []):
                                _ext_registry.unregister_tool(tool_name)
                        if kind == "skill":
                            brain.remove_installed_skill_block(ext_name)
                    except Exception as ex:
                        logger.warning(f"Failed to mirror extension uninstall '{ext_name}': {ex}", exc_info=True)
                    brain.rebuild_stable_tier()
                elif cmd_type == "system_restart":
                    logger.info("System restart command received. Reloading configuration and engine...")

                    from dotenv import load_dotenv

                    load_dotenv(override=True)

                    env_values = {
                        spec["key"]: os.getenv(spec["key"])
                        for spec in Config.editable_field_specs()
                        if os.getenv(spec["key"]) is not None
                    }
                    config.apply_env_updates(env_values)

                    await _reload_mcp_client()
                    await asyncio.to_thread(_reload_plugin_tools)
                    await _reload_voice_engine()
                    brain.rebuild_stable_tier()

                    await event_bus.emit(
                        "alert",
                        {
                            "severity": "success",
                            "message": "System configuration successfully reloaded and engine restarted.",
                        },
                        meta=EventMeta(source=EventSource.VOICE),
                    )
                elif cmd_type == "background_task_start":
                    payload = cmd.get("payload", {})
                    from charlie import background_task

                    try:
                        await background_task.start(
                            config,
                            event_bus,
                            payload.get("text", ""),
                            session_store=store,
                            memory_store=memory_store,
                            voice=voice,
                        )
                    except RuntimeError as ex:
                        await event_bus.emit(
                            "alert",
                            {"severity": "warning", "message": str(ex)},
                            meta=EventMeta(source=EventSource.TASK, rationale=str(ex)),
                        )
                elif cmd_type == "background_task_cancel":
                    payload = cmd.get("payload", {})
                    from charlie import background_task

                    background_task.cancel(payload.get("task_id", ""))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error handling web command: {e}", exc_info=True)

    # Start web server subprocess.
    web_entry = os.path.join(os.path.dirname(__file__), "charlie", "web_server_entry.py")
    _web_env = os.environ.copy()
    _web_env["CHARLIE_LAUNCH_ID"] = _LAUNCH_ID
    web_proc = _start_subsystem_process("web", (sys.executable, web_entry), _web_env)

    # Start desktop companion subprocess (Windows-only, PySide6)
    if config.pet_enabled:
        pet_entry = os.path.join(os.path.dirname(__file__), "charlie", "pet_entry.py")
        pet_proc = _start_subsystem_process("companion", (sys.executable, pet_entry))

    # Telegram runs in-process (needs direct access to _dispatch_or_queue), not a subprocess like web/pet/hud.
    if config.telegram_enabled:
        try:
            from charlie.telegram_bot import TelegramBot, should_relay_approval

            async def on_telegram_message(text, chat_id):
                await _dispatch_or_queue(text, current_web_session_id, platform="telegram")

            def on_telegram_approval(request_id, approved):
                _resolve_tool_approval_and_notify(request_id, approved)

            telegram_bot = TelegramBot(
                config.telegram_bot_token, config.telegram_user_id, on_telegram_message, on_telegram_approval
            )
            await telegram_bot.start()
            logger.info("Telegram bot started")
            _set_subsystem_health("telegram", HealthStatus.RUNNING)
        except Exception as e:
            logger.warning(f"Failed to start Telegram bot: {e}")
            _set_subsystem_health("telegram", HealthStatus.DEGRADED)

    logger.info("Loading AI models (Whisper, VAD, Kokoro)...")
    try:
        # TTS lifecycle callbacks for IPC events
        def on_tts_start():
            if event_bus:
                asyncio.run_coroutine_threadsafe(
                    event_bus.emit(
                        "speaking_start",
                        {"session_id": current_web_session_id},
                        meta=EventMeta(source=EventSource.VOICE),
                    ),
                    loop,
                )

        def on_tts_stop():
            if event_bus:
                asyncio.run_coroutine_threadsafe(
                    event_bus.emit(
                        "speaking_stop",
                        {"session_id": current_web_session_id},
                        meta=EventMeta(source=EventSource.VOICE),
                    ),
                    loop,
                )

        voice = _start_voice_or_degrade(
            config,
            on_speech,
            on_tts_start,
            on_tts_stop,
        )

        def on_wake_word():
            if event_bus:
                asyncio.run_coroutine_threadsafe(
                    event_bus.emit("wake_word", {}, meta=EventMeta(source=EventSource.VOICE)), loop
                )
            if config.browser_enabled and config.browser_warm_on_wake:
                from charlie.browser import controller as browser_controller

                browser_controller.warm()

        voice.set_wake_word_callback(on_wake_word)

        # Connection test & Dynamic Welcome
        logger.debug("Requesting dynamic welcome message from LLM...")
        welcome_msg = ""
        # Wrap the generator in a timeout to avoid hangs if LLM IP is unreachable
        try:
            async with asyncio.timeout(25.0):
                async for chunk in brain.chat_stream(
                    "Give me a very brief, one-sentence startup welcome. Be warm, natural, "
                    "and speak like a human colleague (not an AI assistant). "
                    "Do NOT say 'How can I help you' or 'How can I assist'. Speak only in English.",
                    skip_tools=True,
                ):
                    welcome_msg += chunk
        except asyncio.TimeoutError:
            logger.warning("Dynamic welcome timed out after 25s. Using fallback.")
            welcome_msg = "Hey there. I'm online and listening."
        except Exception as e:
            logger.warning(f"Dynamic welcome failed: {type(e).__name__}: {e}. Using fallback.")
            welcome_msg = "Hey there. I'm online and listening."

        print("=" * 40, flush=True)
        print("   Charlie is online and listening", flush=True)
        print("=" * 40, flush=True)
        print(f"\rCharlie: {welcome_msg}", flush=True)
        voice.speak(welcome_msg, "neutral")

        # Real GPU utilization, re-read every tick so the dashboard reflects
        # live load. Cached briefly (1s) to avoid hammering nvidia-smi on every
        # status emit; falls back to 0.0 only when no NVIDIA GPU is present.
        _gpu_reader: dict = {"value": 0.0, "ts": 0.0}

        def _read_gpu_percent() -> float:
            now = time.monotonic()
            if now - _gpu_reader["ts"] < 1.0:
                return _gpu_reader["value"]
            _gpu_reader["ts"] = now
            try:
                out = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=utilization.gpu",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                    check=False,
                )
                if out.returncode == 0 and out.stdout.strip():
                    _gpu_reader["value"] = float(out.stdout.strip().splitlines()[0].strip())
                else:
                    _gpu_reader["value"] = 0.0
            except (FileNotFoundError, subprocess.SubprocessError, ValueError, OSError):
                _gpu_reader["value"] = 0.0
            return _gpu_reader["value"]

        async def _emit_system_status(bus):
            import psutil
            from charlie.results import ResultsStore

            results_store = ResultsStore(db_path=config.session_db_path)
            was_idle = False
            boot_time = psutil.boot_time()
            try:
                last_net = psutil.net_io_counters()
            except (OSError, psutil.Error) as e:
                logger.debug(f"net_io_counters unavailable: {type(e).__name__}: {e}")
                last_net = None
            try:
                while True:
                    cpu_percent = psutil.cpu_percent()
                    ram_percent = psutil.virtual_memory().percent
                    net_kbps = 0.0
                    if last_net is not None:
                        try:
                            net_now = psutil.net_io_counters()
                            net_kbps = (
                                (net_now.bytes_sent + net_now.bytes_recv) - (last_net.bytes_sent + last_net.bytes_recv)
                            ) / 1024.0
                            last_net = net_now
                        except (OSError, psutil.Error) as e:
                            logger.debug(f"net_io_counters read failed: {type(e).__name__}: {e}")
                    battery_percent = None
                    try:
                        battery = psutil.sensors_battery()
                        battery_percent = battery.percent if battery else None
                    except (OSError, psutil.Error, NotImplementedError) as e:
                        logger.debug(f"sensors_battery unavailable: {type(e).__name__}: {e}")
                    disk_percent = None
                    try:
                        disk_percent = psutil.disk_usage(Path.cwd().anchor or "C:\\").percent
                    except (OSError, psutil.Error) as e:
                        logger.debug(f"disk_usage unavailable: {type(e).__name__}: {e}")
                    await bus.emit(
                        "system_status",
                        {
                            "cpu": cpu_percent,
                            "ram": ram_percent,
                            "gpu": await asyncio.to_thread(_read_gpu_percent),
                            "net_kbps": max(0.0, net_kbps),
                            "uptime_seconds": time.time() - boot_time,
                            "battery_percent": battery_percent,
                            "disk_percent": disk_percent,
                        },
                        meta=EventMeta(source=EventSource.VOICE),
                    )
                    if _state_machine.expire_if_due() is not None:
                        envelope = _charlie_state_envelope()
                        await bus.emit(envelope["type"], envelope["payload"], meta=EventMeta(source=EventSource.VOICE))
                    if sys.platform == "win32":
                        from charlie.desktop.session import user_idle_seconds

                        idle_s = await asyncio.to_thread(user_idle_seconds)
                        is_idle = idle_s >= _IDLE_RETURN_THRESHOLD_S
                        if was_idle and not is_idle:
                            catchup_msg = await asyncio.to_thread(results_store.consume_catchup)
                            if catchup_msg:
                                await bus.emit(
                                    "alert",
                                    {"severity": "info", "message": catchup_msg},
                                    meta=EventMeta(source=EventSource.TASK, rationale="idle-return catch-up"),
                                )
                                voice.speak(catchup_msg, "neutral")
                                from charlie.utils import make_id
                                catchup_intent = PresentationIntent(
                                    id=make_id(),
                                    kind=PresentationKind.NOTIFICATION,
                                    title="While you were away",
                                    summary=catchup_msg,
                                    priority=60,
                                    attention_level=PresentationAttention.NORMAL,
                                    dismiss_policy=DismissPolicy.TIMED,
                                    auto_dismiss_ms=8000,
                                    preferred_zone=PreferredZone.TOP_RIGHT,
                                    anchor=AnchorTarget.CORE,
                                )
                                await bus.emit(
                                    "presentation_intent",
                                    catchup_intent.to_dict(),
                                    meta=EventMeta(source=EventSource.TASK, rationale="idle-return catch-up"),
                                )
                        was_idle = is_idle
                    await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Metric emitter error: {e}")

        # Run voice loop + web command consumer concurrently via ZeroMQ
        async with EventBus(pub_port=5555, pull_port=5556, is_producer=True) as bus:
            event_bus = bus
            await _publish_subsystem_health(bus)
            bus.set_state_listener(_on_event_for_state)
            voice.set_event_bus(bus)
            import charlie.recovery

            charlie.recovery._event_bus = bus
            charlie.recovery.set_active_session_id(current_web_session_id)
            import charlie.tools

            charlie.tools.set_event_bus(bus, asyncio.get_running_loop())
            import charlie.mcp_client

            charlie.mcp_client.set_event_bus(bus, asyncio.get_running_loop())

            # Build one authoritative self-extension service only after the
            # real EventBus loop and MCP subsystem are available. Chat tools
            # delegate to this instance; they never construct an orchestrator.
            await mcp_start_task
            from charlie.capabilities import get_capability_index
            from charlie.code_index import CodeIndex
            from charlie.doctor import CharlieDoctor
            from charlie.runtime_introspector import RuntimeIntrospector
            from charlie.self_extension import SelfExtensionOrchestrator
            from charlie.self_knowledge import SelfKnowledgeService
            from charlie.settings_service import SettingsService

            shared_capability_index = get_capability_index()
            runtime_introspector = RuntimeIntrospector(
                config=config,
                capability_index=shared_capability_index,
                mcp_client=mcp_client,
            )
            code_index = CodeIndex()
            self_knowledge = SelfKnowledgeService(
                runtime_introspector=runtime_introspector,
                code_index=code_index,
                capability_index=shared_capability_index,
                config=config,
            )
            doctor = CharlieDoctor(
                config=config,
                introspector=runtime_introspector,
                capability_index=shared_capability_index,
                mcp_client=mcp_client,
            )
            from charlie.tools import configure_runtime_services, registry as tool_registry

            self_extension_orchestrator = SelfExtensionOrchestrator(
                repo_root=Path(__file__).resolve().parent,
                settings_service=SettingsService(config_instance=config),
                config=config,
                capability_index=shared_capability_index,
                event_bus=bus,
                event_loop=asyncio.get_running_loop(),
                mcp_client=mcp_client,
                tool_registry=tool_registry,
                doctor=doctor,
                code_index=code_index,
                self_knowledge=self_knowledge,
                introspector=runtime_introspector,
            )
            configure_runtime_services(
                self_extension_orchestrator=self_extension_orchestrator,
                runtime_introspector=runtime_introspector,
                self_knowledge_service=self_knowledge,
                doctor=doctor,
            )

            from charlie.calendar_scheduler import deliver_due_reminders
            from charlie.calendar_store import CalendarStore
            from charlie.utils import utc_now_iso

            calendar_store = CalendarStore(config.session_db_path)

            async def _calendar_reminder_loop() -> None:
                while True:

                    async def _deliver(event: dict) -> None:
                        message = f"Reminder: {event['title']}"
                        await bus.emit(
                            "alert",
                            {"severity": "info", "message": message, "reminder_id": event["id"]},
                            meta=EventMeta(source=EventSource.WATCHER, rationale="local reminder became due"),
                        )
                        voice.speak(message, "neutral")

                    await deliver_due_reminders(calendar_store, utc_now_iso(), _deliver)
                    await asyncio.sleep(15)

            from charlie import background_task as _background_task

            interrupted_task = _background_task.check_interrupted_task()
            if interrupted_task is not None:
                _interrupted_msg = (
                    f'Note: your background task "{interrupted_task.get("text", "")}" was '
                    f"interrupted by a restart at step {interrupted_task.get('current_step', 0) + 1} "
                    f"of {len(interrupted_task.get('steps', []))}."
                )
                logger.info(_interrupted_msg)
                await bus.emit(
                    "alert",
                    {"severity": "warning", "message": _interrupted_msg},
                    meta=EventMeta(
                        source=EventSource.TASK,
                        rationale="process restarted while a background task was still running",
                    ),
                )
                voice.speak(_interrupted_msg, "neutral")

            def _read_cpu_ram_percent() -> Tuple[float, float]:
                import psutil

                return psutil.cpu_percent(), psutil.virtual_memory().percent

            def _get_mcp_status() -> Dict[str, bool]:
                return mcp_client.health_check() if mcp_client is not None else {}

            _watcher_loop = asyncio.get_running_loop()

            async def _spawn_watcher_surface(event: dict, message: str, reason: str) -> None:
                from charlie.utils import make_id
                watcher_intent = PresentationIntent(
                    id=make_id(),
                    kind=PresentationKind.NOTIFICATION,
                    title="Heads up",
                    summary=message,
                    priority=65,
                    attention_level=PresentationAttention.HIGH,
                    dismiss_policy=DismissPolicy.TIMED,
                    auto_dismiss_ms=8000,
                    preferred_zone=PreferredZone.TOP_RIGHT,
                    anchor=AnchorTarget.CORE,
                )
                await bus.emit(
                    "presentation_intent",
                    watcher_intent.to_dict(),
                    meta=EventMeta(source=EventSource.WATCHER, rationale=reason),
                )

            def _on_watcher_signal(event: dict, level: AttentionLevel, reason: str) -> None:
                # Re-emit through the normal alert path -- state.py/pet_window.py already react to it.
                payload = event.get("payload") or {}
                message = payload.get("message", reason)
                logger.warning(f"Watcher signal: {message}")
                try:
                    asyncio.run_coroutine_threadsafe(
                        bus.emit(
                            event.get("type", "alert"),
                            payload,
                            meta=EventMeta(source=EventSource.WATCHER, rationale=reason),
                        ),
                        _watcher_loop,
                    )
                except Exception:
                    logger.warning("Failed to emit watcher alert event", exc_info=True)
                if level >= AttentionLevel.ATTENTION:
                    try:
                        voice.speak(message, "neutral")
                    except Exception:
                        logger.warning("Failed to speak watcher alert", exc_info=True)
                    try:
                        asyncio.run_coroutine_threadsafe(_spawn_watcher_surface(event, message, reason), _watcher_loop)
                    except Exception:
                        logger.warning("Failed to spawn watcher alert surface", exc_info=True)

            _watcher_registry = WatcherRegistry()
            _watcher_registry.register(
                cpu_ram_watcher(_read_cpu_ram_percent, config.alert_cpu_pct, config.alert_ram_pct)
            )
            _watcher_registry.register(mcp_health_watcher(_get_mcp_status))
            _watcher_registry.register(stalled_task_watcher(background_task.list_tasks))
            _watcher_registry.register(repeated_tool_failure_watcher(telemetry.unreliable_tools))
            if config.watch_paths:
                _watcher_registry.register(path_change_watcher(config.watch_paths))

            try:
                start_watcher_thread(_watcher_registry, _on_watcher_signal)
                _set_subsystem_health("watchers", HealthStatus.RUNNING)
                await _publish_subsystem_health(bus)
            except Exception:
                logger.error("Failed to start watcher thread", exc_info=True)
                _set_subsystem_health("watchers", HealthStatus.DEGRADED)
                await _publish_subsystem_health(bus)

            class ZmqLogHandler(logging.Handler):
                def emit(self, record):
                    try:
                        log_entry = self.format(record)
                        try:
                            loop = asyncio.get_running_loop()
                            loop.create_task(
                                bus.emit("log", {"line": log_entry}, meta=EventMeta(source=EventSource.VOICE))
                            )
                        except RuntimeError:
                            pass
                    except Exception:
                        pass

            zmq_handler = ZmqLogHandler()
            from charlie.log_redaction import SensitiveDataFilter

            zmq_handler.addFilter(SensitiveDataFilter())
            zmq_handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] [%(levelname)s] - %(message)s"))
            zmq_handler.setLevel(logging.INFO)
            logging.getLogger().addHandler(zmq_handler)

            try:
                await asyncio.gather(
                    _voice_loop_idle(voice),
                    consume_web_commands(bus, brain),
                    _emit_system_status(bus),
                    mcp_start_task,
                    _calendar_reminder_loop(),
                )
            finally:
                logging.getLogger().removeHandler(zmq_handler)
                calendar_store.close()
    except KeyboardInterrupt:
        logger.info("Interrupt received, shutting down...")
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
    finally:
        if "voice" in locals() and voice is not None:
            voice.stop()
        if "brain" in locals():
            await brain.close()
        if "store" in locals() and store is not None:
            store.close()
        if "audit_store" in locals() and audit_store is not None:
            audit_store.close()
        if mcp_client is not None:
            try:
                mcp_client.stop()
                logger.info("MCP subsystem stopped")
            except Exception as e:
                logger.warning(f"MCP subsystem stop error: {e}")
        if web_proc is not None:
            web_proc.terminate()
            try:
                web_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                web_proc.kill()
        if pet_proc is not None:
            pet_proc.terminate()
            try:
                pet_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pet_proc.kill()
        if telegram_bot is not None:
            try:
                await telegram_bot.stop()
            except Exception as e:
                logger.warning(f"Telegram bot stop error: {e}")

        logging.shutdown()
        # Force exit to ensure background threads don't hang the process on Windows
        os._exit(0)


async def _voice_loop_idle(voice):
    """Keep the main coroutine alive while voice threads run."""
    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        os._exit(0)
