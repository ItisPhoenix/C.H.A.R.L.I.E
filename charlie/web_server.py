"""FastAPI + WebSocket bridge for the Charlie React HUD.

Runs in a separate subprocess spawned by main.py.
Communicates with the voice process via ZeroMQ (EventBus).
"""

# ruff: noqa: I001 -- import order intentional: runtime must configure the
# Windows event-loop policy before asyncio or zmq are imported.
from charlie.runtime import configure as _configure_platform
_configure_platform()  # noqa: E402

import asyncio  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402

import ipaddress
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, WebSocketException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from charlie.config import Config, config
from charlie.ipc import DEFAULT_COMMAND_PORT, DEFAULT_EVENT_PORT, EventBus
from charlie.memory_graph import MemoryGraph
from charlie.session_store import SessionStore
from charlie.utils import build_auth_headers
from charlie.log_redaction import SensitiveDataFilter
from charlie.terminal_service import TerminalManager
from charlie.calendar_store import CalendarStore
from charlie.media_adapter import WindowsMediaAdapter
from charlie.audit_store import AuditStore
from charlie.backup_service import export_snapshot
from charlie.capabilities import build_capability_snapshot, get_capability_index
from charlie.events import EventValidationError, build_event, normalize_event, replay_event
from charlie.settings_service import SettingsService, SettingValidationError
from charlie.memory_service import MemoryService
from charlie.privacy_service import PrivacyService
from charlie.code_index import CodeIndex
from charlie.runtime_introspector import RuntimeIntrospector
from charlie.self_knowledge import SelfKnowledgeService
from charlie.doctor import CharlieDoctor

logger = logging.getLogger("charlie.web_server")
logger.addFilter(SensitiveDataFilter())

_memory_service = MemoryService()
_privacy_service = PrivacyService()
_code_index = CodeIndex()
_shared_capability_index = get_capability_index()
_runtime_introspector = RuntimeIntrospector(config=config, capability_index=_shared_capability_index)
_self_knowledge_service = SelfKnowledgeService(
    runtime_introspector=_runtime_introspector,
    code_index=_code_index,
    capability_index=_shared_capability_index,
    config=config,
)
_doctor = CharlieDoctor(
    config=config,
    introspector=_runtime_introspector,
    capability_index=_shared_capability_index,
)
_self_extension_events: List[Dict[str, Any]] = []

_START_TIME = time.time()

# Module-level state
active_connections: Set[WebSocket] = set()
# Maps each WS connection to the session_id it is currently viewing. Lets us
# scope per-session streams (token/transcript) instead of leaking them to all
# connected browsers.
ws_sessions: dict[WebSocket, str] = {}

# MCP/plugin registration can spawn subprocesses -- never at import time, only in lifespan()/_ensure_mcp_client().
from charlie.plugins import PluginManager

mcp_client = None
plugin_manager = PluginManager()


def validate_bind_host(host: str) -> Optional[str]:
    """Return an error when the unauthenticated server would leave loopback."""
    normalized = host.strip().lower()
    if normalized == "localhost":
        return None
    try:
        if ipaddress.ip_address(normalized).is_loopback:
            return None
    except ValueError:
        pass
    return "Charlie has no remote authentication; CHARLIE_HOST must be a loopback address"


def validate_ws_origin(origin: Optional[str]) -> bool:
    """Validate that the WebSocket Origin header is from an authorized local source."""
    if not origin:
        return True
    try:
        from urllib.parse import urlparse

        parsed = urlparse(origin)
        scheme = (parsed.scheme or "").lower()
        hostname = (parsed.hostname or "").lower()

        if scheme == "tauri" or origin.startswith("tauri://") or hostname == "tauri.localhost":
            return True

        if scheme in ("http", "https"):
            if hostname in ("localhost", "127.0.0.1", "::1", "tauri.localhost"):
                return True
        return False
    except Exception:
        return False


def _ensure_mcp_client():
    """Lazily start this process's own MCP client on first need, not a redundant second one at every launch."""
    global mcp_client
    if mcp_client is not None or not config.mcp_enabled:
        return mcp_client
    try:
        from charlie.mcp_client import start_mcp

        mcp_client = start_mcp(config)
        if mcp_client is None:
            logger.info("Web MCP subsystem not started (no servers configured)")
    except Exception as e:
        logger.warning("Web MCP subsystem failed to initialize: %s", e)
        mcp_client = None
    return mcp_client


async def _ensure_mcp_client_async():
    """Runs the lazy MCP start on a thread so it doesn't freeze this process's event loop."""
    return await asyncio.to_thread(_ensure_mcp_client)

# In-process registry of installed extensions -- see
# charlie/extensions/__init__.py's ExtensionManager docstring for the
# propose()/confirm() gate this drives and the no-cross-restart-persistence
# caveat.
from charlie.extensions import ExtensionManager, InstalledExtension  # noqa: E402

_extension_manager = ExtensionManager()


def _builtin_plugin(name: str):
    from charlie.extensions.install import builtin_plugin

    return builtin_plugin(name, config.plugin_allow_dirs)


def _declared_tools_for(kind: str, name: str, source: str, raw_text: str) -> List[str]:
    """Parse (without registering) so propose() can show real declared
    tools in the SkillCard before anything activates."""
    from charlie.extensions.install import declared_tools_for

    return declared_tools_for(kind, name, source, raw_text, config.plugin_allow_dirs)


def _install_extension(kind: str, name: str, source: str, raw_text: str) -> List[str]:
    """Parse and register an approved extension into this process's shared
    registry. Returns the registered tool names.

    This only ever touches the web-server process's own ToolRegistry --
    callers (confirm/enable/disable/uninstall handlers below) are
    responsible for also forwarding the same install/enable/disable/
    uninstall over the EventBus (see _forward_to_voice) so the voice
    process's Brain -- where the real chat tool-calling loop runs -- picks
    it up too. Without that forward, an extension installed here would only
    ever be visible to /api/extensions' introspection endpoints, never
    actually usable in a real conversation.
    """
    global mcp_client
    from charlie.extensions.install import install_extension
    from charlie.tools import registry

    tool_names, mcp_client = install_extension(
        kind, name, source, raw_text,
        registry=registry, plugin_manager=plugin_manager, mcp_client=mcp_client,
        plugin_allow_dirs=config.plugin_allow_dirs,
    )
    return tool_names


async def _stage_proposed_extension(payload: dict) -> None:
    """Chat-triggered tier-3 proposal (see Brain._handle_propose_new_tool):
    stage it in this process's ExtensionManager exactly like a dashboard-
    initiated /api/extensions/propose call, then broadcast the pending_id so
    a connected dashboard can render it and call /api/extensions/confirm --
    no frontend surface exists for this yet, this only stages the state.
    """
    from charlie.extensions import build_skill_card

    kind = payload.get("kind", "generated")
    name = payload.get("name", "")
    source = payload.get("source", "chat")
    raw_text = payload.get("raw_text", "")
    if not name or not raw_text:
        return
    card = build_skill_card(name, source, payload.get("declared_tools", [name]), raw_text)
    pending_id = _extension_manager.propose(card)
    await broadcast({
        "type": "extension_pending",
        "payload": {
            "pending_id": pending_id, "kind": kind, "name": name, "source": source,
            "raw_text": raw_text, "skill_card": card.describe(), "warnings": card.warnings,
        },
    })


async def _forward_to_voice(command_type: str, payload: dict) -> None:
    """Best-effort mirror of an extension install/enable/disable/uninstall
    into the voice process, so Charlie's actual chat Brain -- which runs in
    that separate process and never shares memory with this one -- learns
    about it too. Never raises: a voice process that isn't up yet (or a
    dropped socket) shouldn't fail the dashboard's REST response, since the
    extension is still correctly installed here for introspection either way.
    """
    if not event_bus:
        logger.debug("No event_bus -- skipping voice-process mirror of %s", command_type)
        return
    try:
        await event_bus.send_command({"type": command_type, "payload": payload})
    except Exception:
        logger.warning("Failed to mirror %s to voice process", command_type, exc_info=True)


# Per-turn events must only reach clients subscribed to their session.
# The process-level thinking indicator is intentionally broadcast to all clients.
_SESSION_SCOPED_EVENTS = (
    "token", "transcript", "desktop_frame", "thinking_update",
    "tool_call", "tool_result", "research_progress", "research_result",
    "response_done", "speaking_start", "speaking_stop",
)
event_bus: EventBus | None = None
LAUNCH_ID: str = config.charlie_launch_id
_store: SessionStore | None = None
_memory_graph_cache: "MemoryGraph | None" = None
_terminal_manager = TerminalManager()
_calendar_store: CalendarStore | None = None
_media_adapter = WindowsMediaAdapter()
_audit_store: AuditStore | None = None


def _get_calendar_store() -> CalendarStore:
    global _calendar_store
    if _calendar_store is None:
        _calendar_store = CalendarStore(config.session_db_path)
    return _calendar_store


def _get_audit_store() -> AuditStore:
    global _audit_store
    if _audit_store is None:
        _audit_store = AuditStore(config.session_db_path)
    return _audit_store


def _get_store() -> SessionStore:
    global _store
    if _store is None:
        _store = SessionStore(config.session_db_path)
    return _store


def _get_memory_graph() -> "MemoryGraph | None":
    """Open the knowledge graph in this process (the web server runs in a child subprocess)."""
    global _memory_graph_cache
    if _memory_graph_cache is None:
        try:
            _memory_graph_cache = MemoryGraph(config.memory_graph_db)
        except Exception as e:
            logger.error(f"Failed to open MemoryGraph: {e}", exc_info=True)
            return None
    return _memory_graph_cache


pipeline_state: str = "idle"

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init EventBus + ZMQ guard + plugin tools. MCP starts lazily,
    see _ensure_mcp_client(). Shutdown: tear down EventBus."""
    # --- startup ---
    global event_bus, plugin_manager, _calendar_store, _audit_store
    if config.plugins_enabled:
        try:
            from charlie.tools import register_plugin_tools

            real_plugin_manager = register_plugin_tools(config)
            if real_plugin_manager is not None:
                plugin_manager = real_plugin_manager
        except Exception as e:
            logger.warning("Web plugin subsystem failed to initialize: %s", e)

    # EventBus resolves test-mode ports from the central pytest isolation setup;
    # production keeps its documented defaults.
    event_bus = EventBus(is_producer=False)
    await event_bus.__aenter__()
    asyncio.create_task(_event_bridge())
    # The producer may publish its initial health snapshot before the
    # subscriber task has completed its ZeroMQ connection. Request a replay
    # once the consumer is ready so REST/WebSocket health is authoritative.
    await event_bus.send_command({"type": "runtime_state_request"})
    logger.info("Web server started, event bridge active")

    await _ensure_mcp_client_async()
    _runtime_introspector._mcp_client = mcp_client
    _doctor._mcp_client = mcp_client

    # ZMQ guard -- suppress CancelledError traceback on Windows shutdown
    loop = asyncio.get_event_loop()
    _orig_call = loop.call_exception_handler
    def _guarded_call(context):
        exc = context.get("exception")
        if isinstance(exc, asyncio.CancelledError):
            return
        _orig_call(context)
    loop.call_exception_handler = _guarded_call

    yield

    # --- shutdown ---
    await _terminal_manager.close_all()
    if _calendar_store is not None:
        _calendar_store.close()
        _calendar_store = None
    if _audit_store is not None:
        _audit_store.close()
        _audit_store = None
    if event_bus:
        try:
            await asyncio.wait_for(
                event_bus.__aexit__(None, None, None),
                timeout=2.0,
            )
        except (asyncio.TimeoutError, Exception) as exc:
            logger.debug("EventBus shutdown cleanup issue (non-fatal): %s", exc)
        event_bus = None

app = FastAPI(title="Charlie React HUD", lifespan=lifespan)

# SECURITY: This server has no authentication. It is intended for localhost
# only. Never bind CHARLIE_HOST=0.0.0.0 (or any non-loopback address) without
# placing an authenticating proxy in front of it -- any process that can reach
# the port can read session history, run shell commands, and inject chat.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "tauri://localhost",
        "http://tauri.localhost",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_FRONTEND_DIST = Path(
    os.environ.get(
        "CHARLIE_FRONTEND_DIST",
        str(Path(__file__).parent.parent / "frontend" / "dist"),
    )
)


def _frontend_build_identity() -> dict[str, Any] | None:
    try:
        manifest = json.loads((_FRONTEND_DIST / "charlie-build.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return manifest if isinstance(manifest, dict) else None


_FRONTEND_ASSETS = _FRONTEND_DIST / "assets"
if _FRONTEND_ASSETS.is_dir():
    app.mount("/assets", StaticFiles(directory=_FRONTEND_ASSETS), name="surface-assets")


@app.get("/")
async def serve_hud() -> FileResponse:
    """Serve the one React HUD entry point."""
    index_path = _FRONTEND_DIST / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="frontend not built -- run `npm run build` in frontend/")
    return FileResponse(
        index_path,
        headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
    )


async def broadcast(data: dict):
    """Send a message to connected WebSocket clients.

    Session-scoped events (token/transcript) are delivered only to clients
    subscribed to that session_id, preventing one browser from seeing another
    session's live stream. All other events go to every client.
    """
    try:
        data = normalize_event(data, allow_unknown=True)
    except EventValidationError as exc:
        logger.warning("Dropping invalid event before WebSocket broadcast: %s", exc)
        return
    message = json.dumps(data)
    etype = data.get("type", "")
    event_session = data.get("session_id") or (data.get("payload") or {}).get("session_id")
    scoped = etype in _SESSION_SCOPED_EVENTS and event_session is not None
    disconnected: list[WebSocket] = []
    for ws in list(active_connections):
        if scoped and ws_sessions.get(ws) != event_session:
            continue
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        active_connections.discard(ws)
        ws_sessions.pop(ws, None)


_background_terminal_tasks: set[asyncio.Task] = set()


def _run_terminal_command_task(coro, task_id: str, command: str) -> asyncio.Task:
    """Schedule background terminal command with robust lifecycle tracking and error handling."""
    task = asyncio.create_task(coro)
    _background_terminal_tasks.add(task)

    def _on_done(t: asyncio.Task) -> None:
        _background_terminal_tasks.discard(t)
        if t.cancelled():
            logger.info("Terminal background execution cancelled: task_id=%s", task_id)
            return
        exc = t.exception()
        if exc is not None:
            logger.error(
                "Terminal background execution failed for task_id=%s, cmd=%s: %s",
                task_id,
                command,
                exc,
                exc_info=exc,
            )
            audit = _get_audit_store()
            if audit is not None and hasattr(audit, "record"):
                audit.record(
                    "terminal_exec",
                    {"command": command, "task_id": task_id, "source": "charlie"},
                    f"BACKGROUND_TASK_ERROR: {exc}",
                )

    task.add_done_callback(_on_done)
    return task


async def _event_bridge():
    """Background task: ZeroMQ events -> WebSocket broadcast."""
    global pipeline_state
    if not event_bus:
        return

    async def on_event(event: dict):
        try:
            event = normalize_event(event, allow_unknown=True)
        except EventValidationError as exc:
            logger.warning("Dropping invalid event from EventBus: %s", exc)
            return
        logger.debug(f"Event received: {event}")
        global pipeline_state
        etype = event.get("type", "")
        if etype.startswith("self_extension_"):
            _self_extension_events.append(event)
            del _self_extension_events[:-200]
        if etype == "charlie_state":
            pipeline_state = event.get("payload", {}).get("state", pipeline_state)

        # Keep web server cached state in sync
        if etype == "charlie_state":
            global _charlie_state
            _charlie_state = event.get("payload", {})
        elif etype == "background_task":
            _apply_background_task_event(_background_tasks, event)
        elif etype == "system_status":
            global _system_status
            _system_status = event.get("payload", {})
        elif etype == "subsystem_health":
            global _subsystem_health
            _subsystem_health = event.get("payload", {})
        elif etype == "audio_state":
            global _audio_state
            _audio_state = event.get("payload", {})
        elif etype == "mic_state":
            global _mic_state
            _mic_state = event.get("payload", {})
        elif etype == "hud_visibility":
            global _hud_visible
            _hud_visible = bool(event.get("payload", {}).get("visible", True))
        elif etype == "terminal_command_result":
            payload = event.get("payload", {})
            if payload.get("approved") is True:
                task_id = payload.get("task_id") or payload.get("request_id") or "charlie-agent"
                cmd = payload.get("command", "")
                session_id = payload.get("terminal_session_id") or "primary"
                _run_terminal_command_task(
                    _terminal_manager.execute_charlie_command(
                        session_id=session_id,
                        command=cmd,
                        task_id=task_id,
                        audit_store=_get_audit_store(),
                        approved=True,
                    ),
                    task_id=task_id,
                    command=cmd,
                )
        elif etype == "extension_proposed":
            await _stage_proposed_extension(event.get("payload", {}))
            return
        elif etype in ("presentation_intent", "presentation_update", "presentation_dismiss"):
            _apply_presentation_event(_active_presentation_intents, event)
            if etype in ("presentation_intent", "presentation_update"):
                pid = event.get("payload", {}).get("id")
                auto_ms = event.get("payload", {}).get("auto_dismiss_ms")
                if pid and auto_ms:
                    asyncio.create_task(_expire_presentation_intent(pid, event, float(auto_ms) / 1000.0))

        # Capture tool approvals in memory for REST cache
        if etype in ("tool_approval_request", "tool_approval_resolved"):
            _apply_approval_event(_pending_approvals, event)

        # Broadcast all valid events to connected WebSockets
        await broadcast(event)

    try:
        await event_bus.consume_events(on_event)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Event bridge error: {e}", exc_info=True)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    origin = ws.headers.get("origin")
    if not validate_ws_origin(origin):
        logger.warning("Rejected WebSocket connection from unauthorized origin: %s", origin)
        await ws.close(code=1008)
        return

    await ws.accept()
    global _active_frontend_session
    active_connections.add(ws)
    logger.info("WebSocket connected from origin: %s (%d active)", origin, len(active_connections))

    # Send initial cached state immediately to prevent empty UI states on connection
    try:
        for event in _initial_state_events():
            await ws.send_text(json.dumps(event))
    except Exception as e:
        logger.warning("Failed to send initial cached state to WebSocket: %s", e)
    if event_bus:
        await event_bus.send_command({"type": "ws_connection_count", "count": len(active_connections)})
        # Startup health publication can race the web subscriber. A live HUD
        # connection is the authoritative point at which a replay is useful.
        await event_bus.send_command({"type": "runtime_state_request"})
    try:
        while True:
            data = await ws.receive_text()
            logger.debug("WS received: %s", data)
            try:
                msg = json.loads(data)
                msg_type = msg.get("type", "")

                # Session sync: frontend tells us which session is active
                if msg_type == "session_active":
                    _active_frontend_session = msg.get("session_id") or msg.get("payload", {}).get("session_id")
                    ws_sessions[ws] = _active_frontend_session
                    logger.info("Active session synced: %s", _active_frontend_session)
                    if event_bus:
                        await event_bus.send_command(msg)
                elif msg_type in (
                    "terminal_command_result",
                    "tool_approval_request",
                    "tool_approval_resolved",
                    "activity",
                    "presentation_intent",
                ):
                    # Security: Frontend MUST NOT submit authoritative runtime or approval events.
                    logger.warning("Rejected unauthorized runtime event from client WebSocket: %s", msg_type)
                    continue
                elif event_bus:
                    # Forward legitimate client request/action commands to EventBus
                    await event_bus.send_command(msg)
                    logger.debug("WS forwarded command: %s", msg)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON from client: %s", data)
    except WebSocketDisconnect:
        active_connections.discard(ws)
        ws_sessions.pop(ws, None)
        logger.info("WebSocket disconnected: %d active", len(active_connections))
        if event_bus:
            await event_bus.send_command({"type": "ws_connection_count", "count": len(active_connections)})
    except Exception as e:
        active_connections.discard(ws)
        ws_sessions.pop(ws, None)
        logger.error("WebSocket error: %s", e)
        if event_bus:
            await event_bus.send_command({"type": "ws_connection_count", "count": len(active_connections)})


@app.websocket("/ws/terminal/{session_id}")
async def terminal_ws_endpoint(ws: WebSocket, session_id: str = "primary"):
    """Realtime bidirectional PTY stream for interactive terminal UI."""
    origin = ws.headers.get("origin")
    if origin and not validate_ws_origin(origin):
        logger.warning("Blocked Terminal WebSocket connection from unauthorized origin: %s", origin)
        raise WebSocketException(code=1008)

    if session_id != "primary" and session_id:
        session = _terminal_manager.get_session(session_id)
        if session is None:
            logger.warning("Rejected Terminal WebSocket connection for unknown non-primary session: %s", session_id)
            raise WebSocketException(code=1008)
    else:
        session = await _terminal_manager.get_or_create_primary()

    await ws.accept()

    # Initial session snapshot for instant hydration
    init_payload = {
        "type": "terminal_init",
        "session_id": session.session_id,
        "pid": session.pid,
        "shell": session.shell_name,
        "status": session.status,
        "cols": session.cols,
        "rows": session.rows,
        "scrollback": session.get_scrollback(),
    }
    await ws.send_text(json.dumps(init_payload))

    queue = session.subscribe()

    async def output_sender():
        try:
            while True:
                msg = await queue.get()
                await ws.send_text(json.dumps(msg))
        except (asyncio.CancelledError, WebSocketDisconnect):
            pass
        except Exception:
            logger.debug("Terminal WS output sender finished", exc_info=True)

    sender_task = asyncio.create_task(output_sender())

    try:
        while True:
            raw_text = await ws.receive_text()
            try:
                data = json.loads(raw_text)
                msg_type = data.get("type", "")
                if msg_type == "input":
                    input_data = data.get("data", "")
                    await _terminal_manager.write_bytes(session.session_id, input_data, source="user")
                elif msg_type == "resize":
                    cols = int(data.get("cols", 80))
                    rows = int(data.get("rows", 24))
                    await _terminal_manager.resize(session.session_id, cols, rows)
                elif msg_type == "interrupt":
                    await _terminal_manager.interrupt(session.session_id)
            except json.JSONDecodeError:
                # Raw keystroke string forwarded directly
                await _terminal_manager.write_bytes(session.session_id, raw_text, source="user")
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as e:
        logger.warning(f"Terminal WS error: {e}")
    finally:
        sender_task.cancel()
        session.unsubscribe(queue)


@app.get("/api/history")
async def history(limit: int = 50):
    store = _get_store()
    messages = store.get_recent(limit=limit)
    return {"messages": [{"role": r, "content": c} for r, c in messages]}


@app.get("/api/status")
async def status():
    import platform as _platform
    frontend_build = _frontend_build_identity()
    return {
        "state": pipeline_state,
        "launch_id": LAUNCH_ID,
        "uptime_seconds": int(time.time() - _START_TIME),
        "pid": os.getpid(),
        "frontend_build": frontend_build,
        "source_identity": (frontend_build or {}).get("git_sha"),
        "desktop_control_enabled": config.desktop_control_enabled,
        "os_host": f"{_platform.system()} {_platform.machine()}",
    }


@app.post("/api/terminal/sessions")
async def create_terminal_session():
    """Start or retrieve the primary local shell."""
    session = await _terminal_manager.get_or_create_primary()
    return _terminal_manager.snapshot(session.session_id)


@app.get("/api/terminal/sessions/{session_id}")
async def terminal_session(session_id: str):
    if session_id == "primary":
        session = await _terminal_manager.get_or_create_primary()
        return _terminal_manager.snapshot(session.session_id)
    try:
        return _terminal_manager.snapshot(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="terminal session not found") from exc


@app.post("/api/terminal/sessions/{session_id}/input")
async def terminal_input(session_id: str, data: dict):
    line = data.get("line")
    if not isinstance(line, str) or not line.strip():
        raise HTTPException(status_code=400, detail="line is required")
    if data.get("confirmed") is not True:
        raise HTTPException(
            status_code=409, detail="explicit confirmation is required before requesting command approval"
        )
    from charlie.autonomy import Requirement, evaluate

    requirement, _risk, reason = evaluate("shell_execute", {"command": line})
    if requirement is Requirement.BLOCK:
        raise HTTPException(status_code=409, detail={"approval_required": True, "reason": reason})
    if event_bus is None:
        raise HTTPException(status_code=503, detail="approval channel unavailable")
    try:
        if session_id == "primary":
            session = await _terminal_manager.get_or_create_primary()
            target_sid = session.session_id
        else:
            _terminal_manager.snapshot(session_id)
            target_sid = session_id
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="terminal session not found") from exc
    request_id = uuid.uuid4().hex
    await event_bus.send_command(
        {
            "type": "terminal_command_request",
            "payload": {
                "request_id": request_id,
                "terminal_session_id": target_sid,
                "command": line,
            },
        }
    )
    return {"status": "approval_pending", "request_id": request_id, "session_id": target_sid}


@app.delete("/api/terminal/sessions/{session_id}")
async def close_terminal_session(session_id: str):
    await _terminal_manager.close(session_id)
    return {"status": "closed", "session_id": session_id}


@app.get("/api/calendar/events")
async def list_calendar_events(day: Optional[str] = None):
    return {"events": _get_calendar_store().list_events(day)}


@app.post("/api/calendar/events")
async def create_calendar_event(data: dict):
    title = data.get("title")
    start_at = data.get("start_at")
    if not isinstance(title, str) or not title.strip() or not isinstance(start_at, str) or not start_at.strip():
        raise HTTPException(status_code=400, detail="title and start_at are required")
    event = _get_calendar_store().create_event(
        title,
        start_at,
        end_at=data.get("end_at") if isinstance(data.get("end_at"), str) else None,
        reminder_at=data.get("reminder_at") if isinstance(data.get("reminder_at"), str) else None,
    )
    return event


@app.put("/api/calendar/events/{event_id}")
async def update_calendar_event(event_id: str, data: dict):
    try:
        return _get_calendar_store().update_event(event_id, data)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="calendar event not found") from exc


@app.delete("/api/calendar/events/{event_id}")
async def delete_calendar_event(event_id: str):
    try:
        _get_calendar_store().delete_event(event_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="calendar event not found") from exc
    return {"status": "deleted", "id": event_id}


@app.get("/api/media")
async def media_snapshot():
    try:
        return await asyncio.wait_for(_media_adapter.snapshot(), timeout=2.0)
    except asyncio.TimeoutError:
        return {
            "available": False,
            "title": "",
            "artist": "",
            "album": "",
            "app": "",
            "status": "unavailable",
            "position_seconds": 0.0,
            "duration_seconds": 0.0,
            "art_uri": None,
            "volume_percent": None,
            "muted": None,
        }


@app.post("/api/media/control")
async def media_control(data: dict):
    action = data.get("action")
    if not isinstance(action, str):
        raise HTTPException(status_code=400, detail="action is required")
    result = await _media_adapter.control(action)
    if not result.get("ok") and result.get("reason") == "Unsupported media action":
        raise HTTPException(status_code=400, detail=result)
    return result


@app.get("/api/audit")
async def audit_entries(limit: int = 100):
    return {"entries": _get_audit_store().list(limit)}


@app.get("/api/audit/export")
async def audit_export(limit: int = 500):
    return {"format": "json", "entries": _get_audit_store().list(limit)}


@app.get("/api/backup/status")
async def backup_status():
    return {
        "available": True,
        "encrypted": False,
        "default_encrypted": False,
        "encryption": "scrypt-aesgcm-passphrase",
        "message": "Encrypted export requires an explicit passphrase; unencrypted export remains visibly labeled.",
    }


@app.post("/api/backup/export")
async def backup_export(data: dict | None = None):
    from datetime import datetime, timezone

    passphrase = data.get("passphrase") if isinstance(data, dict) else None
    if passphrase is not None and not isinstance(passphrase, str):
        raise HTTPException(status_code=400, detail="passphrase must be text")
    suffix = ".charlie" if passphrase else ".zip"
    target = Path("backups") / f"charlie-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}{suffix}"
    try:
        manifest = export_snapshot(
            target,
            {
                "sessions.sqlite3": Path(config.session_db_path),
            },
            passphrase=passphrase or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="passphrase must be at least 12 characters") from exc
    return {"path": str(target), "manifest": manifest}


@app.get("/api/health")
async def health():
    from charlie import telemetry
    last_success = telemetry.last_llm_success_timestamp()
    log_path = "logs/charlie.log"
    log_stat = os.stat(log_path) if os.path.exists(log_path) else None
    return {
        "llm_last_success_seconds_ago": (time.time() - last_success) if last_success else None,
        "llm_error_rate": telemetry.llm_error_rate(),
        "tool_error_rate": telemetry.tool_error_rate(),
        "approval_channel_connected": len(active_connections) > 0,
        "log_file_size_bytes": log_stat.st_size if log_stat else 0,
        "log_file_age_seconds": (time.time() - log_stat.st_mtime) if log_stat else None,
        "uptime_seconds": int(time.time() - _START_TIME),
        "subsystems": _subsystem_health,
    }


@app.get("/api/metrics")
async def metrics():
    from charlie import telemetry
    return {
        "llm_error_rate": telemetry.llm_error_rate(),
        "tool_error_rate": telemetry.tool_error_rate(),
        "tool_error_rate_by_tool": telemetry.tool_error_rate_by_name(),
    }


@app.get("/api/background_task")
async def background_task_status():
    """Current background-task state, for dashboard resync (otherwise push-only over WS)."""
    from charlie import background_task

    task = background_task.get_current_task()
    return {"task": task.to_public_event() if task is not None else None}


@app.get("/api/tasks")
async def list_tasks():
    """Replayable public task snapshot from the runtime event bridge."""
    return {"tasks": list(_background_tasks.values())}


@app.get("/api/sessions")
async def list_sessions(request: Request):
    """List sessions, optionally filtered by launch_id or source."""
    store = _get_store()
    launch_id = request.query_params.get("launch_id")
    source = request.query_params.get("source")
    sessions = store.get_sessions(source=source, launch_id=launch_id)
    return {
        "sessions": [
            {
                "id": s[0],
                "title": s[1],
                "created_at": s[2],
                "updated_at": s[3],
                "launch_id": s[4],
            }
            for s in sessions
        ]
    }


@app.post("/api/sessions")
async def create_session(data: dict):
    """Create a new session."""
    session_id = data.get("session_id", str(uuid.uuid4()))
    title = data.get("title", "New Chat")
    source = data.get("source", "web")
    # Fall back to the process-level launch_id so web-created sessions are
    # captured by the "This Launch" sidebar filter.
    launch_id = data.get("launch_id") or config.charlie_launch_id or None
    store = _get_store()
    store.create_session(session_id, title, source=source, launch_id=launch_id)
    return {
        "session_id": session_id,
        "title": title,
        "source": source,
        "launch_id": launch_id,
    }


@app.get("/api/sessions/{session_id}/messages")
async def session_messages(session_id: str, limit: int = 50):
    """Get messages for a specific session.

    Filters out tool and system role rows so raw tool output
    (e.g. [web_search args=...]) never reaches the chat UI.
    """
    _HIDDEN_ROLES = {"tool", "system"}
    store = _get_store()
    messages = store.get_session_messages(session_id, limit=limit)
    return {
        "messages": [
            {"role": r, "content": c}
            for r, c in messages
            if r not in _HIDDEN_ROLES
        ]
    }


@app.put("/api/sessions/{session_id}")
async def update_session(session_id: str, data: dict):
    """Update session title."""
    title = data.get("title", "New Chat")
    store = _get_store()
    store.update_session_title(session_id, title)
    # Broadcast title update to all connected WebSocket clients
    await broadcast({
        "type": "session_updated",
        "session_id": session_id,
        "title": title,
    })
    return {"session_id": session_id, "title": title}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a session and all its messages."""
    store = _get_store()
    store.delete_session(session_id)
    await broadcast(
        {
            "type": "session_updated",
            "payload": {"session_id": session_id, "deleted": True},
        }
    )
    return {"session_id": session_id, "deleted": True}

@app.post("/api/sessions/{session_id}/chat")
async def session_chat(session_id: str, data: dict):
    """HTTP fallback for chat when WebSocket is down.

    Persists the user turn and forwards it to the voice process as a `chat`
    command so the brain generates a reply and streams `token` events back
    over the WebSocket, exactly like the live path.
    """
    text = str(data.get("text") or data.get("message") or "").strip()
    if not text:
        return {"status": "error", "detail": "empty message"}
    if event_bus:
        await event_bus.send_command(
            {"type": "chat", "session_id": session_id, "text": text}
        )
    else:
        # In web-only mode there is no main-process turn handler to persist the
        # user message. Full mode persists it exactly once in main.py.
        _get_store().append("user", text, session_id=session_id)
    return {"status": "ok"}
# ---------------------------------------------------------------------------
_system_status: dict = {}
_subsystem_health: dict = {}
_charlie_state: dict = {"state": "idle", "activities": []}
_background_tasks: dict = {}
_active_frontend_session: str | None = None
_audio_state: dict = {
    "muted": False,
    "volume": 1.0,
}
_mic_state: dict = {
    "mic_muted": False,
}
_hud_visible = True
# request_id -> tool_approval_request event, replayed so a late-connecting client can render it
_pending_approvals: dict = {}
# presentation_intent_id -> canonical presentation intent event, replayed for persistent workspaces/modals
_active_presentation_intents: dict = {}


def _primary_session_id() -> str:
    return _active_frontend_session or f"voice_{config.charlie_launch_id}"


@app.get("/api/session/active")
async def get_active_session():
    session_id = _primary_session_id()
    return {"session_id": session_id, "active_session": session_id}


def _initial_state_events() -> List[dict]:
    """Return cached events needed by a newly connected client."""
    events = [
        build_event("charlie_state", _charlie_state),
        build_event("system_status", _system_status),
        build_event("subsystem_health", _subsystem_health),
        build_event("task_snapshot", {"tasks": list(_background_tasks.values())}),
        build_event("audio_state", _audio_state),
        build_event("mic_state", _mic_state),
        build_event("hud_visibility", {"visible": _hud_visible}),
    ]
    events.extend(_pending_approvals.values())
    events.extend(_active_presentation_intents.values())
    return [replay_event(event, allow_unknown=True) for event in events]


def _apply_presentation_event(cache: dict, event: dict) -> None:
    """Cache active presentation intents for reconnection replay."""
    etype = event.get("type", "")
    payload = event.get("payload", {})
    pid = payload.get("id")
    if not pid:
        return
    if etype == "presentation_dismiss":
        cache.pop(pid, None)
    elif etype in ("presentation_intent", "presentation_update"):
        # Reinsert updates so replay order reflects runtime's latest focus.
        cache.pop(pid, None)
        if payload.get("replayable") or payload.get("kind") in ("workspace", "attention", "composed_surface"):
            cache[pid] = event
        else:
            cache.pop(pid, None)


async def _expire_presentation_intent(intent_id: str, spawned_as: dict, ttl_seconds: float) -> None:
    await asyncio.sleep(ttl_seconds)
    if _active_presentation_intents.get(intent_id) is spawned_as:
        _active_presentation_intents.pop(intent_id, None)


def _apply_approval_event(cache: dict, event: dict) -> None:
    """Mutate `cache` (request_id -> tool_approval_request event) so a late-connecting window still finds it."""
    payload = event.get("payload", {})
    rid = payload.get("request_id")
    if not rid:
        return
    if event.get("type") == "tool_approval_request":
        cache[rid] = event
    else:
        cache.pop(rid, None)


def _apply_background_task_event(cache: dict, event: dict) -> None:
    """Cache the latest safe event for each background task."""
    payload = event.get("payload", {})
    task_id = payload.get("id")
    if task_id:
        status = str(payload.get("status", ""))
        if event.get("schema_version"):
            from charlie.task_journal import normalize_task_status

            try:
                status = normalize_task_status(status).value
            except ValueError:
                pass
        safe = {
            "id": str(task_id),
            "title": str(payload.get("title", "")),
            "status": status,
            "current_step": int(payload.get("current_step", 0)),
            "total_steps": int(payload.get("total_steps", 0)),
        }
        for key in (
            "origin", "priority", "session_id", "parent_task_id", "progress",
            "current_action", "waiting_reason", "result_reference", "approval_reference",
            "capability_requirements",
        ):
            if key in payload:
                safe[key] = payload[key]
        cache[task_id] = safe


@app.get("/api/audio")
async def get_audio_state():
    """Return current speaker mute/volume state."""
    return _audio_state


@app.get("/api/mic")
async def get_mic_state():
    """Return current microphone mute state."""
    return _mic_state


@app.get("/api/memory/facts")
async def get_memory_facts():
    """Retrieve all known facts (subject/predicate/object triples) from the
    knowledge graph's edges, as stored by MemoryGraph.add_fact."""
    graph = _get_memory_graph()
    if graph:
        try:
            facts = [
                {"subject": s, "predicate": p, "object": o}
                for s, p, o in graph.get_all_facts()
            ]
            return {"facts": facts}
        except Exception as e:
            logger.error(f"Error fetching facts: {e}", exc_info=True)
    return {"facts": []}


@app.get("/api/memory/items")
async def get_memory_items(category: Optional[str] = None, limit: int = 200):
    """List memory items with optional category filtering."""
    service = _memory_service
    if service._graph is None:
        service._graph = _get_memory_graph()
    try:
        return {"items": service.list_items(category=category, limit=limit)}
    except Exception as e:
        logger.error(f"Error listing memory items: {e}", exc_info=True)
        return {"items": []}


@app.get("/api/memory/search")
async def search_memory_items(q: str = "", category: Optional[str] = None, limit: int = 50):
    """Search memory items by query string and category."""
    service = _memory_service
    if service._graph is None:
        service._graph = _get_memory_graph()
    try:
        return {"items": service.search_items(query=q, category=category, limit=limit)}
    except Exception as e:
        logger.error(f"Error searching memory: {e}", exc_info=True)
        return {"items": []}


@app.post("/api/memory/items")
async def create_memory_item(data: dict):
    """Create a new memory item."""
    service = _memory_service
    if service._graph is None:
        service._graph = _get_memory_graph()
    try:
        category = str(data.get("category", "fact")).strip()
        content = str(data.get("content", "")).strip()
        subject = str(data.get("subject", "")).strip()
        predicate = str(data.get("predicate", "")).strip()
        obj = str(data.get("object", "")).strip()
        metadata = data.get("metadata")

        item = service.add_item(
            category=category,
            content=content,
            subject=subject,
            predicate=predicate,
            obj=obj,
            metadata=metadata,
        )
        return {"status": "ok", "item": item}
    except Exception as e:
        logger.error(f"Error creating memory item: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/memory/items/{item_id}")
async def update_memory_item(item_id: str, data: dict):
    """Update an existing memory item."""
    service = _memory_service
    if service._graph is None:
        service._graph = _get_memory_graph()
    try:
        content = str(data.get("content", "")).strip()
        category = data.get("category")
        metadata = data.get("metadata")

        updated = service.update_item(item_id, content=content, category=category, metadata=metadata)
        if not updated:
            raise HTTPException(status_code=404, detail="Memory item not found")
        return {"status": "ok", "item": updated}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating memory item: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/memory/items/{item_id}")
async def delete_memory_item(item_id: str):
    """Delete a memory item by ID."""
    service = _memory_service
    if service._graph is None:
        service._graph = _get_memory_graph()
    try:
        success = service.delete_item(item_id)
        if not success:
            raise HTTPException(status_code=404, detail="Memory item not found")
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting memory item: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/memory/clear")
async def clear_memory(data: Optional[dict] = None):
    """Clear memory items by category or completely."""
    service = _memory_service
    if service._graph is None:
        service._graph = _get_memory_graph()
    try:
        category = (data or {}).get("category")
        cleared_count = service.clear_category(category=category)
        return {"status": "ok", "cleared_count": cleared_count}
    except Exception as e:
        logger.error(f"Error clearing memory: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/memory/export")
async def export_memory():
    """Export complete memory dataset as structured JSON."""
    service = _memory_service
    if service._graph is None:
        service._graph = _get_memory_graph()
    try:
        return service.export_all()
    except Exception as e:
        logger.error(f"Error exporting memory: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/memory/stats")
async def get_memory_stats():
    """Get memory statistics and category counts."""
    service = _memory_service
    if service._graph is None:
        service._graph = _get_memory_graph()
    try:
        return service.get_stats()
    except Exception as e:
        logger.error(f"Error fetching memory stats: {e}", exc_info=True)
        return {"stats": {}}


@app.get("/api/privacy/summary")
async def get_privacy_summary():
    """Live storage usage summary across transcripts, terminal, audit, browser, memory, and logs."""
    try:
        return _privacy_service.get_storage_summary()
    except Exception as e:
        logger.error(f"Error fetching privacy summary: {e}", exc_info=True)
        return {"total_bytes": 0, "categories": {}}


@app.post("/api/privacy/purge")
async def purge_privacy_data(data: dict):
    """Selectively purge stored privacy data by category."""
    category = str(data.get("category", "")).strip()
    older_than_days = data.get("older_than_days")
    if older_than_days is not None:
        try:
            older_than_days = int(older_than_days)
        except (ValueError, TypeError):
            older_than_days = None

    if not category:
        raise HTTPException(status_code=400, detail="Category is required")

    result = _privacy_service.purge_category(category, older_than_days=older_than_days)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("message", "Purge failed"))
    return result


_DEV_LOGS_PATH = Path("logs/charlie.log")


@app.get("/api/developer/diagnostics")
async def get_developer_diagnostics():
    """Developer-only detailed diagnostics: task journal, capability leases, telemetry, system metrics."""
    dev_enabled = getattr(config, "developer_mode_enabled", False)

    journal_snapshot = []
    try:
        from charlie.task_journal import TaskJournal
        journal = TaskJournal()
        journal_snapshot = journal.snapshot()
    except Exception:
        pass

    leases = {}
    try:
        from charlie.resource_locks import get_all_leases
        leases = get_all_leases()
    except Exception:
        pass

    telemetry_data = {}
    try:
        from charlie.telemetry import llm_error_rate, tool_error_rate, tool_error_rate_by_name, unreliable_tools
        telemetry_data = {
            "llm_error_rate": llm_error_rate(),
            "tool_error_rate": tool_error_rate(),
            "tool_stats": tool_error_rate_by_name(),
            "unreliable_tools": unreliable_tools(),
        }
    except Exception:
        pass

    import threading
    system_metrics = {
        "uptime_seconds": round(time.time() - _START_TIME, 2),
        "active_threads": threading.active_count(),
        "active_ws_connections": len(active_connections),
        "subsystems": dict(_subsystem_health),
    }

    return {
        "developer_mode_enabled": dev_enabled,
        "diagnostics": {
            "tasks": journal_snapshot,
            "leases": leases,
            "telemetry": telemetry_data,
            "system": system_metrics,
        },
    }


@app.get("/api/developer/logs")
async def get_developer_logs(limit: int = 100):
    """Retrieve recent log lines for the developer inspection console."""
    lines: List[str] = []
    log_path = _DEV_LOGS_PATH
    if log_path.exists():
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
                lines = [line.strip() for line in all_lines[-limit:]]
        except Exception as e:
            logger.warning("Could not read developer logs: %s", e)
    return {"lines": lines, "total_lines": len(lines)}


# ---------------------------------------------------------------------------
# SelfKnowledge, CodeIndex & Charlie Doctor Endpoints
# ---------------------------------------------------------------------------


@app.get("/api/self/introspect")
async def get_self_introspection():
    """Return live runtime introspection snapshot with strict secret masking."""
    return _runtime_introspector.get_snapshot()


@app.post("/api/self/query")
async def query_self_knowledge(payload: Dict[str, Any]):
    """Answer a grounded self-question about Charlie's code, capabilities, or runtime."""
    q = str(payload.get("query", "")).strip()
    if not q:
        return JSONResponse(status_code=400, content={"error": "query parameter required"})
    res = _self_knowledge_service.answer_self_question(q)
    return res


@app.get("/api/code_index/search")
async def search_code_index(q: str = "", type: str = "symbols", limit: int = 20):
    """Search repository symbols or files using AST-grounded CodeIndex."""
    if _code_index.is_stale():
        _code_index.refresh()
    if type == "files":
        return {"files": _code_index.search_files(q, limit=limit)}
    return {"symbols": _code_index.search_symbols(q, limit=limit)}


@app.get("/api/doctor/diagnose")
async def run_doctor_diagnostics():
    """Execute full Charlie Doctor diagnostic checks and return structured report."""
    report = _doctor.diagnose()
    return report.to_dict()


@app.post("/api/doctor/repair")
async def execute_doctor_repair(payload: Dict[str, Any]):
    """Execute a safe automated repair or request approval for consequential repairs."""
    repair_id = str(payload.get("repair_id", "")).strip()
    approved = bool(payload.get("approved", False))
    if not repair_id:
        return JSONResponse(status_code=400, content={"error": "repair_id parameter required"})
    res = _doctor.execute_repair(repair_id, approved=approved)
    return res


@app.get("/api/mcp/tools")
async def get_mcp_tools():
    """Return discovered MCP tool definitions.

    When MCP is disabled this returns an empty list rather than every tool in
    the shared registry, so the endpoint honestly reflects the toggle. When
    enabled it returns the tools auto-registered with the ``mcp_`` prefix.
    """
    try:
        from charlie.tools import registry

        if not config.mcp_enabled:
            return {"tools": []}
        await _ensure_mcp_client_async()
        defs = [
            d for d in registry.get_tool_definitions()
            if d.get("function", {}).get("name", "").startswith("mcp_")
        ]
        return {"tools": defs}
    except Exception as e:
        logger.error(f"Error fetching tools: {e}")
    return {"tools": []}


@app.get("/api/tools")
async def get_tools():
    """Full tool roster (built-in + MCP + plugin + extension) for the Tools-grid HUD widget."""
    try:
        from charlie.tools import registry
        return {"tools": registry.list_metadata()}
    except Exception as e:
        logger.error(f"Error fetching tool roster: {e}", exc_info=True)
        return {"tools": []}


@app.get("/api/capabilities")
async def get_capabilities():
    """Expose the live capability view used to describe Charlie to the model."""
    from charlie.tools import registry

    snapshot = build_capability_snapshot(registry, config)
    snapshot["runtime"] = dict(_subsystem_health)
    return snapshot


@app.get("/api/mcp/status")
async def get_mcp_status():
    """Per-server MCP connection status for the Connections HUD widget."""
    if not config.mcp_enabled or mcp_client is None:
        return {"servers": {}}
    try:
        return {"servers": mcp_client.health_check()}
    except Exception as e:
        logger.error(f"Error fetching MCP status: {e}", exc_info=True)
        return {"servers": {}}


@app.get("/api/mcp/servers")
async def get_mcp_servers():
    """List configured MCP servers with connection state and exposed tools."""
    client = mcp_client
    if client is None and config.mcp_enabled:
        client = await _ensure_mcp_client_async()
    if client is None:
        from charlie.mcp_client import load_config_file, parse_server_spec
        configured = []
        for spec in config.mcp_servers:
            try:
                cfg = parse_server_spec(spec)
                configured.append({
                    "name": cfg.name,
                    "command": cfg.command,
                    "args": cfg.args,
                    "running": False,
                    "status": "disconnected",
                    "tools_count": 0,
                    "tools": [],
                })
            except Exception:
                pass
        for cfg in load_config_file(config.mcp_config_path):
            if not any(c["name"] == cfg.name for c in configured):
                configured.append({
                    "name": cfg.name,
                    "command": cfg.command,
                    "args": cfg.args,
                    "running": False,
                    "status": "disconnected",
                    "tools_count": 0,
                    "tools": [],
                })
        return {"servers": configured}

    try:
        return {"servers": client.list_servers_detailed()}
    except Exception as e:
        logger.error(f"Error fetching MCP servers: {e}", exc_info=True)
        return {"servers": []}


@app.post("/api/mcp/servers")
async def add_mcp_server(data: dict):
    """Add or update an MCP server configuration."""
    name = str(data.get("name", "")).strip()
    command = str(data.get("command", "")).strip()
    args_raw = data.get("args", [])
    if isinstance(args_raw, str):
        args = [a.strip() for a in args_raw.split(",") if a.strip()]
    elif isinstance(args_raw, list):
        args = [str(a).strip() for a in args_raw if str(a).strip()]
    else:
        args = []

    if not name or not command:
        raise HTTPException(status_code=400, detail="Server name and command are required")

    from charlie.mcp_client import MCPServerConfig
    srv_config = MCPServerConfig(name=name, command=command, args=args)

    global mcp_client
    if mcp_client is None:
        from charlie.mcp_client import MCPClient
        mcp_client = MCPClient()

    mcp_client.add_server(srv_config)
    return {"status": "ok", "message": f"Server '{name}' configured"}


@app.post("/api/mcp/servers/{name}/connect")
async def connect_mcp_server(name: str):
    """Connect/enable a registered MCP server."""
    global mcp_client
    if mcp_client is None:
        await _ensure_mcp_client_async()
    if mcp_client is None:
        raise HTTPException(status_code=500, detail="MCP client unavailable")

    from charlie.tools import registry

    try:
        mcp_client.enable_server(registry, name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    return {"status": "ok"}


@app.post("/api/mcp/servers/{name}/disconnect")
async def disconnect_mcp_server(name: str):
    """Disconnect/disable a registered MCP server."""
    global mcp_client
    if mcp_client is None:
        return {"status": "ok"}

    from charlie.tools import registry

    if not mcp_client.disable_server(registry, name):
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    return {"status": "ok"}


@app.post("/api/mcp/servers/{name}/restart")
async def restart_mcp_server(name: str):
    """Restart a registered MCP server."""
    global mcp_client
    if mcp_client is None:
        await _ensure_mcp_client_async()
    if mcp_client is None:
        raise HTTPException(status_code=500, detail="MCP client unavailable")

    from charlie.tools import registry

    if not mcp_client.restart_server(registry, name):
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    return {"status": "ok"}


@app.delete("/api/mcp/servers/{name}")
async def delete_mcp_server(name: str):
    """Remove an MCP server."""
    global mcp_client
    if mcp_client is None:
        return {"status": "ok"}

    from charlie.tools import registry

    if not mcp_client.remove_server(registry, name):
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    return {"status": "ok"}


@app.get("/api/extensions")
async def list_extensions():
    """List installed extensions across all four adapters."""
    return {
        "extensions": [
            {
                "name": e.name,
                "kind": e.kind,
                "source": e.source,
                "enabled": e.enabled,
                "tool_names": e.tool_names,
                "warnings": e.card.warnings,
                "content_hash": e.card.content_hash,
            }
            for e in _extension_manager.list()
        ]
    }


@app.post("/api/extensions/propose")
async def propose_extension(data: dict):
    """Stage an extension install for approval. Parses the given kind's
    manifest/spec, builds a provenance SkillCard (content hash + heuristic
    scan), and returns it for the dashboard to show a confirm dialog --
    nothing is registered yet."""
    from charlie.extensions import build_skill_card

    kind = data.get("kind", "")
    name = data.get("name", "")
    source = data.get("source", "")
    raw_text = data.get("raw_text", "")
    if not kind or not name:
        return {"status": "error", "message": "kind and name are required"}

    try:
        declared_tools = _declared_tools_for(kind, name, source, raw_text)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    card = build_skill_card(name, source or kind, declared_tools, raw_text or name)
    pending_id = _extension_manager.propose(card)
    return {
        "status": "ok",
        "pending_id": pending_id,
        "skill_card": card.describe(),
        "warnings": card.warnings,
    }


@app.post("/api/extensions/confirm")
async def confirm_extension(data: dict):
    """Approve (or decline) a proposed install. Only on approval does the
    extension get parsed into live tools and registered -- the gate."""
    pending_id = data.get("pending_id", "")
    approved = bool(data.get("approved", False))
    kind = data.get("kind", "")
    source = data.get("source", "")
    raw_text = data.get("raw_text", "")

    card = _extension_manager.pop_pending(pending_id)
    if card is None:
        return {"status": "error", "message": "Unknown or already-resolved pending_id"}
    if not approved:
        return {"status": "ok", "installed": False}

    try:
        tool_names = _install_extension(kind, card.name, source, raw_text)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    _extension_manager.record(
        InstalledExtension(
            name=card.name, kind=kind, source=source, card=card, tool_names=tool_names
        )
    )
    await _forward_to_voice(
        "extension_installed",
        {"kind": kind, "name": card.name, "source": source, "raw_text": raw_text},
    )
    return {"status": "ok", "installed": True, "tool_names": tool_names}


@app.post("/api/extensions/{name}/enable")
async def enable_extension(name: str):
    """Re-activate a disabled extension's tools without reinstalling it."""
    from charlie.tools import registry

    ext = _extension_manager.get(name)
    if ext is None:
        return {"status": "error", "message": f"Unknown extension '{name}'"}

    if ext.kind == "mcp":
        await _ensure_mcp_client_async()
    if ext.kind == "mcp" and mcp_client is not None:
        tool_names = mcp_client.enable_server(registry, name)
    elif ext.kind == "plugin":
        from charlie.tools import enable_plugin

        tool_names = enable_plugin(registry, plugin_manager, _builtin_plugin(name))
    else:
        # skill/openapi: disable_extension() doesn't drop these tools (see
        # its comment), so re-enabling is a no-op restoring the same names.
        tool_names = ext.tool_names

    ext.enabled = True
    ext.tool_names = tool_names
    await _forward_to_voice("extension_enabled", {"kind": ext.kind, "name": name})
    return {"status": "ok", "tool_names": tool_names}


@app.post("/api/extensions/{name}/disable")
async def disable_extension(name: str):
    """Deactivate an extension's tools while keeping its install record, so
    enable_extension() can bring it back without re-parsing the source."""
    from charlie.tools import registry

    ext = _extension_manager.get(name)
    if ext is None:
        return {"status": "error", "message": f"Unknown extension '{name}'"}

    if ext.kind == "mcp" and mcp_client is not None:
        mcp_client.disable_server(registry, name)
    elif ext.kind == "plugin":
        from charlie.tools import disable_plugin

        disable_plugin(registry, plugin_manager, name)
    # skill/openapi tools are left registered on disable -- they're stateless
    # wrappers (no subprocess/connection to tear down like MCP or a plugin
    # instance), so unregistering and immediately re-registering on the next
    # enable_extension() would be pure overhead with no resource actually
    # freed. Re-parsing would need raw_text persisted, which this pass
    # doesn't do (see ExtensionManager's docstring).

    ext.enabled = False
    await _forward_to_voice("extension_disabled", {"kind": ext.kind, "name": name})
    return {"status": "ok"}


@app.delete("/api/extensions/{name}")
async def uninstall_extension(name: str):
    """Fully remove an extension: disable its tools, drop it from the
    registry, and forget it (unlike disable, cannot be re-enabled)."""
    from charlie.tools import registry

    ext = _extension_manager.get(name)
    if ext is None:
        return {"status": "error", "message": f"Unknown extension '{name}'"}

    if ext.enabled:
        await disable_extension(name)
    if ext.kind == "mcp" and mcp_client is not None:
        mcp_client.remove_server(registry, name)
    elif ext.kind in ("skill", "openapi"):
        for tool_name in ext.tool_names:
            registry.unregister_tool(tool_name)

    _extension_manager.remove(name)
    await _forward_to_voice(
        "extension_uninstalled", {"kind": ext.kind, "name": name, "tool_names": ext.tool_names}
    )
    return {"status": "ok"}


@app.post("/api/extensions/request")
async def request_self_extension(payload: Dict[str, Any]):
    """Delegate controlled self-extension to authoritative voice runtime."""
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        return JSONResponse(status_code=400, content={"error": "prompt parameter required"})

    explicit = bool(payload.get("explicit", True))
    settings = dict(payload.get("settings") or {})

    if event_bus is None:
        return JSONResponse(status_code=503, content={"error": "voice runtime is not connected"})
    request_id = uuid.uuid4().hex
    await event_bus.send_command(
        {
            "type": "self_extension_request",
            "payload": {
                "request_id": request_id,
                "prompt": prompt,
                "explicit": explicit,
                "settings": settings,
            },
        }
    )
    return JSONResponse(status_code=202, content={"request_id": request_id, "status": "requested"})


@app.get("/api/extensions/transactions")
async def list_extension_transactions():
    """Return read-only lifecycle/result events received from voice runtime."""
    return {"transactions": list(_self_extension_events)}


@app.get("/api/extensions/transactions/{tx_id}")
async def get_extension_transaction(tx_id: str):
    """Retrieve read-only result/lifecycle evidence for a transaction."""
    tx = next(
        (
            event
            for event in reversed(_self_extension_events)
            if event.get("payload", {}).get("tx_id") == tx_id
        ),
        None,
    )
    if tx is None:
        raise HTTPException(status_code=404, detail=f"Transaction '{tx_id}' not found")
    return tx


@app.post("/api/extensions/transactions/{tx_id}/rollback")
async def rollback_extension_transaction(tx_id: str):
    """Request rollback from authoritative voice runtime."""
    if event_bus is None:
        return JSONResponse(status_code=503, content={"error": "voice runtime is not connected"})
    await event_bus.send_command(
        {"type": "self_extension_rollback", "payload": {"tx_id": tx_id}}
    )
    return JSONResponse(status_code=202, content={"tx_id": tx_id, "status": "rollback_requested"})


@app.post("/api/session/active")
async def set_active_session(data: dict):
    """Frontend signals which session is active (for voice routing)."""
    global _active_frontend_session
    _active_frontend_session = data.get("session_id")
    logger.info("Active frontend session: %s", _active_frontend_session)
    # Also update WS client subscriptions and route the switch to the voice
    # process so microphone speech lands in the right session. The WS
    # `session_active` path already does this; the POST path must too.
    for ws in active_connections:
        ws_sessions[ws] = _active_frontend_session
    if event_bus:
        await event_bus.send_command(
            {"type": "session_active", "session_id": _active_frontend_session}
        )
    return {"active_session": _active_frontend_session}


_settings_service = SettingsService(config)


def _update_env_file(updates: dict):
    _settings_service._atomic_write_env(updates, source="dashboard_config_api")


@app.get("/api/config")
async def get_dashboard_config():
    """Describe every .env-backed setting for the settings page.

    Driven entirely by SettingsService and Config metadata.
    Secret fields never echo their value, only whether one is set.
    """
    return {"fields": _settings_service.get_field_specs()}


@app.post("/api/config")
async def update_dashboard_config(data: dict):
    """Persist one or more .env-backed settings -- safely and atomically.

    `data` is {ENV_VAR_NAME: value}; unknown keys are ignored so this can't be
    used to inject arbitrary env vars. Validates types and atomically writes to .env.
    """
    known_keys = {spec["key"] for spec in Config.editable_field_specs()}
    updates = {k: v for k, v in data.items() if k in known_keys}
    if not updates:
        return {"status": "error", "message": "no recognized settings in request"}

    try:
        validated = _settings_service.validate_updates(updates)
        touched = config.apply_env_updates(validated)
        _update_env_file(validated)
        return {"status": "ok", "touched": sorted(touched)}
    except SettingValidationError as exc:
        logger.warning("Setting validation error: %s", exc)
        return {"status": "error", "message": "One or more settings have an invalid value."}
    except Exception:
        logger.error("Error updating config", exc_info=True)
        return {"status": "error", "message": "One or more settings have an invalid value."}


@app.post("/api/config/reload")
async def reload_engine_config():
    """Apply the current .env to the running voice process: on demand only.

    Re-reads every editable setting from .env into the voice process's config
    singleton and reloads whichever of the voice engine / MCP client / plugin
    tools that process needs to pick the new values up (see main.py's
    "system_restart" command handler). This is the only path that ever
    touches the live engine -- POST /api/config (Save) deliberately doesn't.
    """
    if not event_bus:
        return {"status": "error", "message": "voice process not connected"}
    await event_bus.send_command({"type": "system_restart"})
    return {"status": "ok"}


@app.delete("/api/memory/facts")
async def delete_memory_fact(subject: str, predicate: str, object: str):
    """Delete a fact from the memory graph SQLite database."""
    graph = _get_memory_graph()
    if graph:
        try:
            success = graph.remove_fact(subject, predicate, object)
            if success:
                return {"status": "ok"}
            else:
                return {"status": "error", "message": "Failed to remove fact"}
        except Exception as e:
            logger.error(f"Error deleting fact: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}
@app.get("/api/workspace/files")
async def list_workspace_files():
    """Return real tree structure of workspace files."""
    from pathlib import Path
    root_dir = Path(__file__).parent.parent
    allowed_exts = {".py", ".md", ".json", ".css", ".ts", ".tsx", ".js", ".html"}
    files_list = []
    try:
        for p in root_dir.rglob("*"):
            if p.is_file() and p.suffix in allowed_exts:
                rel = p.relative_to(root_dir).as_posix()
                parts = p.relative_to(root_dir).parts
                ignored = (".", "node_modules", "venv", "__pycache__", "dist", "out")
                if not any(part.startswith(".") or part in ignored for part in parts):
                    files_list.append(rel)
    except Exception as e:
        logger.error(f"Error listing workspace files: {e}", exc_info=True)
    return {"files": sorted(files_list)}


@app.get("/api/workspace/file")
async def get_workspace_file(path: str):
    """Return contents of a workspace file."""
    from fastapi import HTTPException
    from pathlib import Path
    root_dir = Path(__file__).parent.parent
    target = (root_dir / path).resolve()
    if not str(target).startswith(str(root_dir)) or not target.exists() or not target.is_file():
        raise HTTPException(status_code=400, detail="Invalid path")
    try:
        content = target.read_text(encoding="utf-8", errors="ignore")
        return {"path": path, "content": content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/models")
async def get_available_models():
    """Return live configured model plus auto-discovered local & provider API key models."""
    import httpx

    current_model = config.llm_model or ""
    # Only seed the currently configured model - no phantom defaults
    models_set: set[str] = {current_model} if current_model else set()

    # 1. Query configured LLM provider endpoint if API key is set
    if config.llm_key and config.llm_key not in ("no-key", "no_key") and config.llm_url:
        try:
            headers = build_auth_headers(config.llm_key)
            url = config.llm_url.rstrip("/")
            endpoint = f"{url}/models" if url.endswith("/v1") else f"{url}/v1/models"
            async with httpx.AsyncClient(
                timeout=3.0,
                trust_env=config.llm_trust_env,
            ) as client:
                r = await client.get(endpoint, headers=headers)
                if r.status_code == 200:
                    data = r.json()
                    for item in data.get("data", []):
                        if isinstance(item, dict) and item.get("id"):
                            models_set.add(item["id"])
        except Exception as e:
            logger.warning(f"Could not fetch models from provider endpoint: {e}")

    # 2. Discover local Ollama models (port 11434)
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            r = await client.get("http://127.0.0.1:11434/api/tags")
            if r.status_code == 200:
                for m in r.json().get("models", []):
                    if m.get("name"):
                        models_set.add(m["name"])
    except Exception:
        pass

    # 3. Discover local LM Studio models (port 1234)
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            r = await client.get("http://127.0.0.1:1234/v1/models")
            if r.status_code == 200:
                for item in r.json().get("data", []):
                    if isinstance(item, dict) and item.get("id"):
                        models_set.add(item["id"])
    except Exception:
        pass

    return {
        "active_model": current_model,
        "has_api_key": bool(config.llm_key and config.llm_key not in ("no-key", "no_key")),
        "models": sorted(list(models_set)),
    }


@app.get("/api/local_models")
async def get_local_models():
    """Return ONLY locally hosted models (Ollama :11434, LM Studio :1234)."""
    import httpx

    local_models = []
    # 1. Discover local Ollama models (port 11434)
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            r = await client.get("http://127.0.0.1:11434/api/tags")
            if r.status_code == 200:
                for m in r.json().get("models", []):
                    if m.get("name"):
                        local_models.append({"name": m["name"], "source": "Ollama (:11434)"})
    except Exception:
        pass

    # 2. Discover local LM Studio models (port 1234)
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            r = await client.get("http://127.0.0.1:1234/v1/models")
            if r.status_code == 200:
                for item in r.json().get("data", []):
                    if isinstance(item, dict) and item.get("id"):
                        local_models.append({"name": item["id"], "source": "LM Studio (:1234)"})
    except Exception:
        pass

    return {
        "count": len(local_models),
        "models": local_models,
    }


@app.get("/api/services/status")
async def get_services_status():
    """Return the current runtime health snapshot without inventing services."""
    services = []
    for name, raw in sorted(_subsystem_health.items()):
        if not isinstance(raw, dict):
            continue
        services.append(
            {
                "name": name,
                "status": str(raw.get("status", "unknown")),
                "details": str(raw.get("detail", "Unknown")),
                "type": "subsystem",
            }
        )
    return {"services": services}


# ---------------------------------------------------------------------------
# Geospatial Subsystem Endpoints
# ---------------------------------------------------------------------------


@app.get("/api/geo/geocode")
async def geo_geocode(q: str, limit: int = 5):
    """Geocode a place name or address via normalized backend provider."""
    from charlie.geo import geo_service

    results = await geo_service.geocode(q, limit=limit)
    return {"query": q, "results": [r.to_dict() for r in results]}


@app.get("/api/geo/route")
async def geo_route(
    start_lon: float,
    start_lat: float,
    dest_lon: float,
    dest_lat: float,
    start_label: str = "Origin",
    dest_label: str = "Destination",
    mode: str = "driving",
):
    """Calculate vehicular/corridor route between two points."""
    from charlie.geo import geo_service

    route_res = await geo_service.get_route(
        start=[start_lon, start_lat],
        destination=[dest_lon, dest_lat],
        start_label=start_label,
        destination_label=dest_label,
        mode=mode,
    )
    if not route_res:
        raise HTTPException(status_code=404, detail="Route could not be calculated")
    return route_res.to_dict()


@app.get("/api/geo/layer/{layer_id}")
async def geo_layer(layer_id: str):
    """Fetch real-time spatial intelligence features for a specific layer."""
    from charlie.geo import geo_service

    layer_res = await geo_service.get_layer_data(layer_id)
    return layer_res.to_dict()


@app.get("/api/geo/layers")
async def geo_layers():
    """List operational spatial intelligence layers."""
    from charlie.geo import geo_service

    return {"layers": geo_service.get_registered_layers()}


@app.get("/api/geo/pmtiles/list")
async def geo_pmtiles_list():
    """List available offline PMTiles dataset archives with capability metadata."""
    from charlie.geo import geo_service

    return {"archives": geo_service.pmtiles.list_archives()}


@app.get("/api/geo/pmtiles/{archive_name}")
@app.head("/api/geo/pmtiles/{archive_name}")
async def geo_pmtiles_serve(archive_name: str, request: Request):
    """Serve PMTiles archives supporting HTTP Range requests for pmtiles.js protocol with streaming."""
    from fastapi.responses import StreamingResponse
    from charlie.geo import geo_service

    safe_path = geo_service.pmtiles.resolve_safe_path(archive_name)
    if not safe_path or not safe_path.is_file():
        raise HTTPException(status_code=404, detail="PMTiles archive not found or access denied")

    file_size = safe_path.stat().st_size
    range_header = request.headers.get("range")

    common_headers = {
        "Accept-Ranges": "bytes",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Expose-Headers": "Content-Range, Accept-Ranges, Content-Length",
        "Content-Type": "application/octet-stream",
    }

    if request.method == "HEAD":
        return Response(
            status_code=200,
            headers={**common_headers, "Content-Length": str(file_size)},
        )

    if range_header and range_header.startswith("bytes="):
        try:
            byte_range = range_header[6:].strip()
            parts = byte_range.split("-")

            if len(parts) == 2:
                if parts[0] == "" and parts[1] != "":
                    # Suffix byte range: bytes=-500 (last 500 bytes)
                    suffix_len = int(parts[1])
                    if suffix_len <= 0:
                        return Response(
                            status_code=416,
                            headers={"Content-Range": f"bytes */{file_size}", **common_headers},
                        )
                    start = max(0, file_size - suffix_len)
                    end = file_size - 1
                elif parts[0] != "" and parts[1] == "":
                    # Open ended range: bytes=500- (from 500 to end)
                    start = int(parts[0])
                    end = file_size - 1
                elif parts[0] != "" and parts[1] != "":
                    start = int(parts[0])
                    end = int(parts[1])
                else:
                    return Response(
                        status_code=416,
                        headers={"Content-Range": f"bytes */{file_size}", **common_headers},
                    )
            else:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{file_size}", **common_headers})

            if start < 0 or start >= file_size or end >= file_size or start > end:
                return Response(
                    status_code=416,
                    headers={"Content-Range": f"bytes */{file_size}", **common_headers},
                )

            length = end - start + 1
            with open(safe_path, "rb") as f:
                f.seek(start)
                data = f.read(length)

            range_headers = {
                **common_headers,
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Content-Length": str(len(data)),
            }
            return Response(content=data, status_code=206, headers=range_headers)
        except Exception as e:
            logger.warning(f"Error serving PMTiles range {range_header}: {e}")
            raise HTTPException(status_code=500, detail="Error reading PMTiles byte range")

    # Non-range full download: stream in chunks without loading entire file into memory
    def file_chunk_generator(path: Path, chunk_size: int = 65536):
        with open(path, "rb") as f:
            while chunk := f.read(chunk_size):
                yield chunk

    return StreamingResponse(
        file_chunk_generator(safe_path),
        status_code=200,
        headers={**common_headers, "Content-Length": str(file_size)},
    )



def start_server(
    pub_port: int = DEFAULT_EVENT_PORT, pull_port: int = DEFAULT_COMMAND_PORT
):
    """Entry point for the web server subprocess."""
    _configure_platform()
    import uvicorn

    host = config.charlie_host
    bind_error = validate_bind_host(host)
    if bind_error:
        raise RuntimeError(bind_error)
    logger.info("Starting web server on %s:%s", host, config.charlie_port)
    server_config = uvicorn.Config(
        app,
        host=host,
        port=config.charlie_port,
        log_level="info",
        # "asyncio" hardcodes ProactorEventLoop on win32 regardless of the process-wide
        # event loop policy, which breaks pyzmq (needs add_reader, Proactor doesn't have it).
        # "none" defers loop creation to the current policy, which _configure_platform()
        # already set to WindowsSelectorEventLoopPolicy.
        loop="none",
    )
    server = uvicorn.Server(server_config)
    server.run()
