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
# Importing charlie.tools registers built-in callables and synchronizes their
# semantic operations before this process captures its capability index. It
# does not start plugins or MCP; those remain lazy in lifespan/endpoints.
import charlie.tools  # noqa: F401
from charlie.events import CONTRACT_VERSION, EventValidationError, build_event, normalize_event, replay_event
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


class _IpcTaskProjection:
    """Read-only RuntimeIntrospector source backed by the IPC task cache."""

    def snapshot(self) -> list[dict]:
        return _projected_task_list()


class _IpcHealthProjection:
    """Read-only RuntimeIntrospector source backed by IPC health events."""

    def snapshot(self) -> dict:
        return dict(_subsystem_health)


class _UnavailableLeaseProjection:
    """Prevent RuntimeIntrospector from falling back to web-local lease state."""

    def snapshot(self) -> dict:
        return {}


class _IpcMcpProjection:
    """Read-only MCP projection hydrated from main-runtime IPC events."""

    def list_servers_detailed(self) -> list[dict[str, Any]]:
        snapshot = _mcp_snapshot
        if snapshot is None:
            return []
        return [
            {
                **server,
                "args": list(server.get("args", [])),
                "tools": [dict(tool) for tool in server.get("tools", [])],
            }
            for server in snapshot["servers"]
        ]

    def health_check(self) -> dict[str, bool]:
        return {
            server["name"]: bool(server["running"])
            for server in self.list_servers_detailed()
        }


def _unavailable_lease_info() -> dict[str, Any]:
    """Describe the main-process lease authority without inventing a count."""
    return {
        "status": "unavailable",
        "authority": "main_runtime",
        "detail": "Main-process lease state is unavailable over IPC.",
        "active_leases": {},
        "leased_resources_count": None,
    }


_runtime_introspector = RuntimeIntrospector(
    config,
    _shared_capability_index,
    _IpcHealthProjection(),
    _IpcTaskProjection(),
    _UnavailableLeaseProjection(),
    _IpcMcpProjection(),
)
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

# Plugin registration remains a web-local proposal mirror; executable MCP
# ownership and all MCP runtime state stay in main and cross IPC.
from charlie.plugins import PluginManager

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


# In-process registry of installed extensions -- see
# charlie/extensions/__init__.py's ExtensionManager docstring for the
# propose()/confirm() gate this drives and the no-cross-restart-persistence
# caveat.
from charlie.extensions import ExtensionManager, InstalledExtension  # noqa: E402

_extension_manager = ExtensionManager()


def _declared_tools_for(kind: str, name: str, source: str, raw_text: str) -> List[str]:
    """Parse (without registering) so propose() can show real declared
    tools in the SkillCard before anything activates."""
    from charlie.extensions.install import declared_tools_for

    return declared_tools_for(kind, name, source, raw_text, config.plugin_allow_dirs)


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


# Per-turn events must only reach clients subscribed to their session.
# The process-level thinking indicator is intentionally broadcast to all clients.
_SESSION_SCOPED_EVENTS = (
    "token", "transcript", "desktop_frame", "thinking_update",
    "tool_call", "tool_result", "research_progress", "research_result",
    "response_done", "speaking_start", "speaking_stop",
)
event_bus: EventBus | None = None
EXTENSION_OPERATION_TIMEOUT_SECONDS = 10.0
_pending_extension_operations: dict[str, asyncio.Future[dict[str, Any]]] = {}
MCP_OPERATION_TIMEOUT_SECONDS = 10.0
_pending_mcp_operations: dict[str, asyncio.Future[dict[str, Any]]] = {}
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
    """Startup: init EventBus + ZMQ guard. Executable tools and MCP state
    arrive from main over IPC.
    Shutdown: tear down EventBus."""
    # --- startup ---
    global event_bus, plugin_manager, _calendar_store, _audit_store
    global _tool_snapshot, _tool_snapshot_event, _mcp_snapshot, _mcp_snapshot_event
    # This process never owns executable tool activation. Start each web
    # lifecycle without a stale projection and wait for main's replay.
    _tool_snapshot = None
    _tool_snapshot_event = None
    _mcp_snapshot = None
    _mcp_snapshot_event = None

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
        if (
            isinstance(event, dict)
            and event.get("type") == "task_snapshot"
            and (
                type(event.get("version")) is not int
                or event.get("version") != CONTRACT_VERSION
            )
        ):
            logger.warning("Dropping task snapshot with invalid raw contract version")
            return
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
        elif etype == "task_snapshot":
            _apply_task_snapshot_event(_background_tasks, event)
        elif etype == "tool_snapshot":
            if not _apply_tool_snapshot_event(event):
                logger.warning("Ignoring malformed main tool snapshot")
        elif etype == "mcp_snapshot":
            if not _apply_mcp_snapshot_event(event):
                logger.warning("Ignoring malformed main MCP snapshot")
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
        elif etype == "extension_operation_result":
            _resolve_extension_operation_result(event.get("payload", {}))
        elif etype == "mcp_operation_result":
            _resolve_mcp_operation_result(event.get("payload", {}))
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
    tasks = _projected_task_list()
    if not tasks:
        return {"task": None}
    active_statuses = {"planning", "queued", "running", "paused", "waiting", "verifying", "approval_required"}
    task = next((item for item in reversed(tasks) if item.get("status") in active_statuses), tasks[-1])
    return {"task": task}


@app.get("/api/tasks")
async def list_tasks():
    """Replayable public task snapshot from the runtime event bridge."""
    return {"tasks": _projected_task_list()}


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
_tool_snapshot: dict[str, Any] | None = None
_tool_snapshot_event: dict[str, Any] | None = None
_mcp_snapshot: dict[str, Any] | None = None
_mcp_snapshot_event: dict[str, Any] | None = None
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
    if _tool_snapshot_event is not None:
        events.append(replay_event(_tool_snapshot_event, allow_unknown=True))
    if _mcp_snapshot_event is not None:
        events.append(replay_event(_mcp_snapshot_event, allow_unknown=True))
    events.extend(_pending_approvals.values())
    events.extend(_active_presentation_intents.values())
    return [replay_event(event, allow_unknown=True) for event in events]


def _projected_task_list() -> list[dict]:
    """Return copies of the read-only task projection for API consumers."""
    return [dict(task) for task in _background_tasks.values()]


_PROJECTED_TASK_STATUSES = frozenset(
    {
        "queued",
        "planning",
        "waiting",
        "running",
        "paused",
        "approval_required",
        "verifying",
        "completed",
        "failed",
        "cancelled",
    }
)
_PROJECTED_TASK_STATUS_ALIASES = {
    "done": "completed",
    "awaiting_approval": "approval_required",
}


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


def _resolve_extension_operation_result(payload: object) -> None:
    """Resolve only the web request identified by the result's request_id."""
    if not isinstance(payload, dict):
        return
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        return
    future = _pending_extension_operations.get(request_id)
    if future is None or future.done():
        return
    future.set_result(dict(payload))


def _resolve_mcp_operation_result(payload: object) -> None:
    """Resolve only the web request identified by the MCP result request_id."""
    if not isinstance(payload, dict):
        return
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        return
    future = _pending_mcp_operations.get(request_id)
    if future is None or future.done():
        return
    future.set_result(dict(payload))


def _extension_operation_error(
    request_id: str,
    operation: str,
    runtime_status: str,
    message: str,
) -> dict[str, Any]:
    return {
        "success": False,
        "request_id": request_id,
        "operation": operation,
        "runtime_status": runtime_status,
        "error": message,
    }


def _extension_api_error(result: dict[str, Any]) -> dict[str, Any]:
    """Return safe REST failure semantics for a non-authoritative result."""
    message = str(result.get("error") or "Main runtime did not apply extension operation")
    return {
        "status": "error",
        "request_id": result.get("request_id"),
        "operation": result.get("operation"),
        "runtime_status": result.get("runtime_status", "failed"),
        "message": message[:500],
    }


def _result_tool_names(result: dict[str, Any]) -> Optional[List[str]]:
    names = result.get("tool_names")
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        return None
    return list(names)


def _mcp_operation_error(
    request_id: str,
    operation: str,
    server_name: str,
    runtime_status: str,
    message: str,
) -> dict[str, Any]:
    return {
        "success": False,
        "request_id": request_id,
        "operation": operation,
        "server_name": server_name,
        "runtime_status": runtime_status,
        "error": message,
    }


def _mcp_api_error(result: dict[str, Any]) -> dict[str, Any]:
    """Return safe REST failure semantics for a non-authoritative result."""
    return {
        "status": "error",
        "request_id": result.get("request_id"),
        "operation": result.get("operation"),
        "server_name": result.get("server_name"),
        "runtime_status": result.get("runtime_status", "failed"),
        "message": str(result.get("error") or "Main runtime did not apply MCP operation")[:500],
    }


async def _request_authoritative_mcp_operation(
    operation: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Send one MCP mutation to main and wait for its correlated result."""
    request_id = uuid.uuid4().hex
    request_payload = dict(payload)
    request_payload["request_id"] = request_id
    request_payload["operation"] = operation
    server_name = str(request_payload.get("server_name", ""))

    if event_bus is None:
        return _mcp_operation_error(
            request_id,
            operation,
            server_name,
            "unavailable",
            "Main runtime is unavailable; MCP operation was not applied.",
        )

    loop = asyncio.get_running_loop()
    result_future: asyncio.Future[dict[str, Any]] = loop.create_future()
    _pending_mcp_operations[request_id] = result_future
    try:
        try:
            sent = await event_bus.send_command({"type": "mcp_operation", "payload": request_payload})
            if sent is False:
                return _mcp_operation_error(
                    request_id,
                    operation,
                    server_name,
                    "unavailable",
                    "Main runtime is unavailable; MCP operation was not applied.",
                )
        except Exception:
            logger.warning("Failed to send MCP operation to main", exc_info=True)
            return _mcp_operation_error(
                request_id,
                operation,
                server_name,
                "unavailable",
                "Main runtime is unavailable; MCP operation was not applied.",
            )

        try:
            result = await asyncio.wait_for(result_future, timeout=MCP_OPERATION_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            return _mcp_operation_error(
                request_id,
                operation,
                server_name,
                "timeout",
                "Main runtime did not acknowledge MCP operation before timeout.",
            )
    finally:
        _pending_mcp_operations.pop(request_id, None)

    if not isinstance(result, dict):
        return _mcp_operation_error(
            request_id,
            operation,
            server_name,
            "unknown",
            "Main runtime returned an invalid MCP operation result.",
        )
    result = dict(result)
    if (
        result.get("request_id") != request_id
        or result.get("operation") != operation
        or result.get("server_name") != server_name
        or type(result.get("success")) is not bool
    ):
        return _mcp_operation_error(
            request_id,
            operation,
            server_name,
            "unknown",
            "Main runtime returned a mismatched MCP operation result.",
        )
    if result.get("success") is not True:
        result.setdefault("runtime_status", "failed")
        result.setdefault("error", "Main runtime rejected MCP operation.")
        return result
    return result


async def _request_authoritative_extension_operation(
    operation: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Send one extension mutation to main and wait for its correlated result."""
    request_id = uuid.uuid4().hex
    request_payload = dict(payload)
    request_payload["request_id"] = request_id
    request_payload["operation"] = operation

    if event_bus is None:
        return _extension_operation_error(
            request_id,
            operation,
            "unavailable",
            "Main runtime is unavailable; extension operation was not applied.",
        )

    loop = asyncio.get_running_loop()
    result_future: asyncio.Future[dict[str, Any]] = loop.create_future()
    _pending_extension_operations[request_id] = result_future
    try:
        try:
            await event_bus.send_command({"type": "extension_operation", "payload": request_payload})
        except Exception:
            logger.warning("Failed to send extension operation to main", exc_info=True)
            return _extension_operation_error(
                request_id,
                operation,
                "unavailable",
                "Main runtime is unavailable; extension operation was not applied.",
            )

        try:
            result = await asyncio.wait_for(result_future, timeout=EXTENSION_OPERATION_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            return _extension_operation_error(
                request_id,
                operation,
                "timeout",
                "Main runtime did not acknowledge extension operation before timeout.",
            )
    finally:
        _pending_extension_operations.pop(request_id, None)

    if not isinstance(result, dict):
        return _extension_operation_error(
            request_id,
            operation,
            "invalid_result",
            "Main runtime returned an invalid extension operation result.",
        )
    result = dict(result)
    for key in ("request_id", "operation", "kind", "name"):
        if result.get(key) != request_payload.get(key):
            return _extension_operation_error(
                request_id,
                operation,
                "invalid_result",
                "Main runtime returned a mismatched extension operation result.",
            )
    if result.get("success") is not True:
        result.setdefault("runtime_status", "failed")
        result.setdefault("error", "Main runtime rejected extension operation.")
        return result
    if _result_tool_names(result) is None:
        return _extension_operation_error(
            request_id,
            operation,
            "invalid_result",
            "Main runtime returned invalid extension tool names.",
        )
    return result


def _apply_background_task_event(cache: dict, event: dict) -> None:
    """Cache the latest safe event for each background task."""
    projected = _project_background_task_event(event)
    if projected is None:
        return
    task_id, safe = projected
    existing = cache.get(task_id)
    existing_status = existing.get("status") if isinstance(existing, dict) else None
    if existing_status in {"completed", "failed", "cancelled", "done"} and safe["status"] not in {
        "completed", "failed", "cancelled",
    }:
        return
    cache[task_id] = safe


def _project_background_task_event(event: object) -> Optional[tuple[str, dict]]:
    """Validate one IPC task event without importing task ownership modules."""
    if not isinstance(event, dict):
        return None
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None

    raw_task_id = payload.get("id")
    if not isinstance(raw_task_id, str) or not raw_task_id.strip():
        return None
    raw_status = payload.get("status")
    if not isinstance(raw_status, str) or not raw_status.strip():
        return None
    if raw_status not in _PROJECTED_TASK_STATUSES and raw_status not in _PROJECTED_TASK_STATUS_ALIASES:
        return None

    try:
        current_step = int(payload.get("current_step", 0))
        total_steps = int(payload.get("total_steps", 0))
    except (TypeError, ValueError, OverflowError):
        return None
    if current_step < 0 or total_steps < 0:
        return None

    status = raw_status
    if event.get("schema_version"):
        status = {
            "done": "completed",
            "awaiting_approval": "approval_required",
        }.get(status, status)
    safe = {
        "id": raw_task_id,
        "title": str(payload.get("title", "")),
        "status": status,
        "current_step": current_step,
        "total_steps": total_steps,
    }
    for key in (
        "origin", "priority", "session_id", "parent_task_id", "progress",
        "current_action", "waiting_reason", "result_reference", "approval_reference",
        "capability_requirements",
    ):
        if key in payload:
            safe[key] = payload[key]
    return raw_task_id, safe


def _apply_task_snapshot_event(cache: dict, event: dict) -> None:
    """Atomically replace read-only task projection from a canonical snapshot."""
    if (
        not isinstance(event, dict)
        or event.get("type") != "task_snapshot"
        or type(event.get("version")) is not int
        or event.get("version") != CONTRACT_VERSION
    ):
        return
    payload = event.get("payload")
    if not isinstance(payload, dict) or "tasks" not in payload or not isinstance(payload["tasks"], list):
        return

    replacement: dict = {}
    projection_event = {"schema_version": event.get("version") or event.get("schema_version")}
    for row in payload["tasks"]:
        projected = _project_background_task_event({**projection_event, "payload": row})
        if projected is None:
            return
        task_id, safe = projected
        replacement[task_id] = safe

    cache.clear()
    cache.update(replacement)


def _project_tool_snapshot_payload(payload: Any) -> dict[str, Any] | None:
    """Validate and reduce a main tool snapshot to safe public metadata."""
    if not isinstance(payload, dict) or not isinstance(payload.get("tools"), list):
        return None
    if "authority" in payload and payload["authority"] != "main_runtime":
        return None

    replacement: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for item in payload["tools"]:
        if not isinstance(item, dict):
            return None
        name = item.get("name")
        if not isinstance(name, str) or not name.strip() or name in seen_names:
            return None
        seen_names.add(name)

        safe_item: dict[str, Any] = {"name": name}
        for key in ("description", "owner"):
            if key in item:
                value = item[key]
                if not isinstance(value, str):
                    return None
                safe_item[key] = value
        if "risk_class" in item:
            risk_class = item["risk_class"]
            if risk_class is not None and not isinstance(risk_class, str):
                return None
            safe_item["risk_class"] = risk_class
        replacement.append(safe_item)

    return {"authority": "main_runtime", "tools": replacement}


def _apply_tool_snapshot_event(event: dict) -> bool:
    """Atomically replace the web's IPC-derived tool projection."""
    if (
        not isinstance(event, dict)
        or event.get("type") != "tool_snapshot"
        or type(event.get("version")) is not int
        or event.get("version") != CONTRACT_VERSION
    ):
        return False
    projection = _project_tool_snapshot_payload(event.get("payload"))
    if projection is None:
        return False

    global _tool_snapshot, _tool_snapshot_event
    _tool_snapshot = projection
    stored_event = dict(event)
    stored_event["payload"] = projection
    _tool_snapshot_event = stored_event
    return True


def _project_mcp_snapshot_payload(payload: Any) -> dict[str, Any] | None:
    """Validate and reduce the main MCP snapshot to safe UI metadata."""
    if not isinstance(payload, dict) or not isinstance(payload.get("servers"), list):
        return None
    if "authority" in payload and payload["authority"] != "main_runtime":
        return None
    enabled = payload.get("enabled", True)
    if type(enabled) is not bool:
        return None

    replacement: list[dict[str, Any]] = []
    seen_server_names: set[str] = set()
    for item in payload["servers"]:
        if not isinstance(item, dict):
            return None
        name = item.get("name")
        command = item.get("command", "")
        args = item.get("args", [])
        running = item.get("running")
        status = item.get("status")
        tools = item.get("tools", [])
        tools_count = item.get("tools_count")
        if (
            not isinstance(name, str)
            or not name.strip()
            or name in seen_server_names
            or not isinstance(command, str)
            or not isinstance(args, list)
            or any(not isinstance(arg, str) for arg in args)
            or type(running) is not bool
            or not isinstance(status, str)
            or not status.strip()
            or not isinstance(tools, list)
            or type(tools_count) is not int
            or tools_count < 0
            or tools_count != len(tools)
        ):
            return None

        safe_tools: list[dict[str, str]] = []
        seen_tool_names: set[str] = set()
        for tool in tools:
            if not isinstance(tool, dict):
                return None
            tool_name = tool.get("name")
            description = tool.get("description", "")
            if (
                not isinstance(tool_name, str)
                or not tool_name.strip()
                or tool_name in seen_tool_names
                or not isinstance(description, str)
            ):
                return None
            seen_tool_names.add(tool_name)
            safe_tools.append({"name": tool_name, "description": description})

        replacement.append(
            {
                "name": name,
                "command": command,
                "args": list(args),
                "running": running,
                "status": status,
                "tools_count": tools_count,
                "tools": safe_tools,
            }
        )
        seen_server_names.add(name)

    return {"authority": "main_runtime", "enabled": enabled, "servers": replacement}


def _apply_mcp_snapshot_event(event: dict) -> bool:
    """Atomically replace the web's IPC-derived MCP projection."""
    if (
        not isinstance(event, dict)
        or event.get("type") != "mcp_snapshot"
        or type(event.get("version")) is not int
        or event.get("version") != CONTRACT_VERSION
    ):
        return False
    projection = _project_mcp_snapshot_payload(event.get("payload"))
    if projection is None:
        return False

    global _mcp_snapshot, _mcp_snapshot_event
    _mcp_snapshot = projection
    stored_event = dict(event)
    stored_event["payload"] = projection
    _mcp_snapshot_event = stored_event
    return True


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
    """Developer-only diagnostics over the web process's read-only projections."""
    dev_enabled = getattr(config, "developer_mode_enabled", False)

    journal_snapshot = _projected_task_list()
    # Preserve legacy `leases` mapping while refusing to present web-local
    # locks as main-process truth. No cross-process lease snapshot contract
    # exists yet, so authority remains explicitly unavailable.
    leases = {}
    lease_authority = {
        "status": "unavailable",
        "detail": "Main-process lease state is unavailable over IPC.",
    }

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
            "lease_authority": lease_authority,
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
    snapshot = _runtime_introspector.get_snapshot()
    snapshot["leases"] = _unavailable_lease_info()
    return snapshot


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
    """Return the main-runtime IPC projection of active MCP tool metadata."""
    if _tool_snapshot is None:
        return {
            "status": "unavailable",
            "runtime_status": "unavailable",
            "synchronized": False,
            "authority": "main_runtime",
            "detail": "Main runtime tool snapshot has not arrived over IPC.",
            "tools": [],
        }
    if _mcp_snapshot is not None and not _mcp_snapshot.get("enabled", True):
        return {
            "status": "disabled",
            "runtime_status": "available",
            "synchronized": True,
            "authority": "main_runtime",
            "tools": [],
        }
    return {
        "status": "ok",
        "runtime_status": "available",
        "synchronized": True,
        "authority": "main_runtime",
        "tools": [
            dict(tool)
            for tool in _tool_snapshot["tools"]
            if tool.get("owner") == "mcp"
        ],
    }


@app.get("/api/tools")
async def get_tools():
    """Return only the main-runtime IPC projection of executable tools."""
    if _tool_snapshot is None:
        return {
            "status": "unavailable",
            "runtime_status": "unavailable",
            "synchronized": False,
            "authority": "main_runtime",
            "detail": "Main runtime tool snapshot has not arrived over IPC.",
            "tools": [],
        }
    return {
        "status": "ok",
        "runtime_status": "available",
        "synchronized": True,
        "authority": "main_runtime",
        "tools": [dict(tool) for tool in _tool_snapshot["tools"]],
    }


@app.get("/api/capabilities")
async def get_capabilities():
    """Expose the live capability view used to describe Charlie to the model."""
    snapshot = build_capability_snapshot(_shared_capability_index, config)
    # CapabilityIndex remains useful for static domain/subsystem metadata, but
    # its web-process tool list is not executable runtime truth. Replace that
    # list with the main-owned IPC projection and make the synchronization
    # state explicit when the producer has not replayed it yet.
    snapshot["tool_authority"] = "main_runtime"
    if _tool_snapshot is None:
        snapshot["tool_status"] = "unavailable"
        snapshot["tools"] = []
    else:
        snapshot["tool_status"] = "available"
        snapshot["tools"] = [dict(tool) for tool in _tool_snapshot["tools"]]
    snapshot["runtime"] = dict(_subsystem_health)
    return snapshot


@app.get("/api/mcp/status")
async def get_mcp_status():
    """Per-server MCP connection status for the Connections HUD widget."""
    if _mcp_snapshot is None:
        return {
            "status": "unavailable",
            "runtime_status": "unavailable",
            "synchronized": False,
            "authority": "main_runtime",
            "detail": "Main runtime MCP snapshot has not arrived over IPC.",
            "servers": {},
        }
    return {
        "status": "ok" if _mcp_snapshot.get("enabled", True) else "disabled",
        "runtime_status": "available",
        "synchronized": True,
        "authority": "main_runtime",
        "servers": {
            server["name"]: bool(server["running"])
            for server in _mcp_snapshot["servers"]
        },
    }


@app.get("/api/mcp/servers")
async def get_mcp_servers():
    """List configured MCP servers with connection state and exposed tools."""
    if _mcp_snapshot is None:
        return {
            "status": "unavailable",
            "runtime_status": "unavailable",
            "synchronized": False,
            "authority": "main_runtime",
            "detail": "Main runtime MCP snapshot has not arrived over IPC.",
            "servers": [],
        }
    return {
        "status": "ok" if _mcp_snapshot.get("enabled", True) else "disabled",
        "runtime_status": "available",
        "synchronized": True,
        "authority": "main_runtime",
        "servers": [
            {
                **server,
                "args": list(server.get("args", [])),
                "tools": [dict(tool) for tool in server.get("tools", [])],
            }
            for server in _mcp_snapshot["servers"]
        ],
    }


@app.post("/api/mcp/servers")
async def add_mcp_server(data: dict):
    """Ask main to add one MCP server to its canonical runtime client."""
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

    result = await _request_authoritative_mcp_operation(
        "add",
        {"server_name": name, "command": command, "args": args},
    )
    if result.get("success") is not True:
        return _mcp_api_error(result)
    return {
        "status": "ok",
        "message": f"Server '{name}' configured",
        "request_id": result.get("request_id"),
        "operation": result.get("operation"),
        "server_name": result.get("server_name"),
    }


@app.post("/api/mcp/servers/{name}/connect")
async def connect_mcp_server(name: str):
    """Ask main to connect one registered MCP server."""
    result = await _request_authoritative_mcp_operation("connect", {"server_name": name})
    if result.get("success") is not True:
        return _mcp_api_error(result)
    return {"status": "ok", "request_id": result.get("request_id"), "server_name": name}


@app.post("/api/mcp/servers/{name}/disconnect")
async def disconnect_mcp_server(name: str):
    """Ask main to disconnect one registered MCP server."""
    result = await _request_authoritative_mcp_operation("disconnect", {"server_name": name})
    if result.get("success") is not True:
        return _mcp_api_error(result)
    return {"status": "ok", "request_id": result.get("request_id"), "server_name": name}


@app.post("/api/mcp/servers/{name}/restart")
async def restart_mcp_server(name: str):
    """Ask main to restart one registered MCP server."""
    result = await _request_authoritative_mcp_operation("restart", {"server_name": name})
    if result.get("success") is not True:
        return _mcp_api_error(result)
    return {"status": "ok", "request_id": result.get("request_id"), "server_name": name}


@app.delete("/api/mcp/servers/{name}")
async def delete_mcp_server(name: str):
    """Ask main to remove one registered MCP server."""
    result = await _request_authoritative_mcp_operation("delete", {"server_name": name})
    if result.get("success") is not True:
        return _mcp_api_error(result)
    return {"status": "ok", "request_id": result.get("request_id"), "server_name": name}


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
    """Approve a proposal, then ask main to perform the authoritative install."""
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

    if not kind:
        return {"status": "error", "message": "kind is required for an approved extension"}

    result = await _request_authoritative_extension_operation(
        "install",
        {
            "kind": kind,
            "name": card.name,
            "source": source or card.source,
            "raw_text": raw_text,
        },
    )
    if result.get("success") is not True:
        return _extension_api_error(result)

    tool_names = _result_tool_names(result)
    if tool_names is None:
        return _extension_api_error(
            _extension_operation_error(
                str(result.get("request_id", "")),
                "install",
                "invalid_result",
                "Main runtime returned invalid extension tool names.",
            )
        )

    _extension_manager.record(
        InstalledExtension(
            name=card.name,
            kind=kind,
            source=source or card.source,
            card=card,
            tool_names=tool_names,
        )
    )
    return {
        "status": "ok",
        "installed": True,
        "request_id": result.get("request_id"),
        "tool_names": tool_names,
    }


@app.post("/api/extensions/{name}/enable")
async def enable_extension(name: str):
    """Ask main to re-activate an installed extension before updating the mirror."""
    ext = _extension_manager.get(name)
    if ext is None:
        return {"status": "error", "message": f"Unknown extension '{name}'"}

    result = await _request_authoritative_extension_operation(
        "enable",
        {"kind": ext.kind, "name": name, "source": ext.source, "tool_names": list(ext.tool_names)},
    )
    if result.get("success") is not True:
        return _extension_api_error(result)
    tool_names = _result_tool_names(result)
    if tool_names is None:
        return _extension_api_error(
            _extension_operation_error(
                str(result.get("request_id", "")),
                "enable",
                "invalid_result",
                "Main runtime returned invalid extension tool names.",
            )
        )
    ext.enabled = True
    ext.tool_names = tool_names
    return {"status": "ok", "request_id": result.get("request_id"), "tool_names": tool_names}


@app.post("/api/extensions/{name}/disable")
async def disable_extension(name: str):
    """Ask main to deactivate an extension before updating the mirror."""
    ext = _extension_manager.get(name)
    if ext is None:
        return {"status": "error", "message": f"Unknown extension '{name}'"}

    result = await _request_authoritative_extension_operation(
        "disable",
        {"kind": ext.kind, "name": name, "source": ext.source, "tool_names": list(ext.tool_names)},
    )
    if result.get("success") is not True:
        return _extension_api_error(result)
    tool_names = _result_tool_names(result)
    if tool_names is None:
        return _extension_api_error(
            _extension_operation_error(
                str(result.get("request_id", "")),
                "disable",
                "invalid_result",
                "Main runtime returned invalid extension tool names.",
            )
        )
    ext.enabled = False
    ext.tool_names = tool_names
    return {"status": "ok", "request_id": result.get("request_id"), "tool_names": tool_names}


@app.delete("/api/extensions/{name}")
async def uninstall_extension(name: str):
    """Ask main to remove runtime activation before deleting the web mirror."""
    ext = _extension_manager.get(name)
    if ext is None:
        return {"status": "error", "message": f"Unknown extension '{name}'"}

    result = await _request_authoritative_extension_operation(
        "uninstall",
        {"kind": ext.kind, "name": name, "source": ext.source, "tool_names": list(ext.tool_names)},
    )
    if result.get("success") is not True:
        return _extension_api_error(result)
    tool_names = _result_tool_names(result)
    if tool_names is None:
        return _extension_api_error(
            _extension_operation_error(
                str(result.get("request_id", "")),
                "uninstall",
                "invalid_result",
                "Main runtime returned invalid extension tool names.",
            )
        )
    _extension_manager.remove(name)
    return {"status": "ok", "request_id": result.get("request_id"), "tool_names": tool_names}


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
