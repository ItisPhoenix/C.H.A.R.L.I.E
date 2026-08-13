"""FastAPI + WebSocket backend for Charlie web dashboard.

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
from typing import List, Optional, Set

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, WebSocketException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from charlie.config import Config, config
from charlie.ipc import DEFAULT_COMMAND_PORT, DEFAULT_EVENT_PORT, EventBus
from charlie.memory_graph import MemoryGraph
from charlie.session_store import SessionStore
from charlie.utils import build_auth_headers

logger = logging.getLogger("charlie.web_server")

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


# Events that carry a session_id and must only reach clients subscribed to it.
_SESSION_SCOPED_EVENTS = ("token", "transcript", "desktop_frame")
event_bus: EventBus | None = None
LAUNCH_ID: str = config.charlie_launch_id
_store: SessionStore | None = None
_memory_graph_cache: "MemoryGraph | None" = None


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
    global event_bus, plugin_manager
    if config.plugins_enabled:
        try:
            from charlie.tools import register_plugin_tools

            real_plugin_manager = register_plugin_tools(config)
            if real_plugin_manager is not None:
                plugin_manager = real_plugin_manager
        except Exception as e:
            logger.warning("Web plugin subsystem failed to initialize: %s", e)

    event_bus = EventBus(
        pub_port=DEFAULT_EVENT_PORT,
        pull_port=DEFAULT_COMMAND_PORT,
        is_producer=False,
    )
    await event_bus.__aenter__()
    asyncio.create_task(_event_bridge())
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
    if event_bus:
        try:
            await asyncio.wait_for(
                event_bus.__aexit__(None, None, None),
                timeout=2.0,
            )
        except (asyncio.TimeoutError, Exception) as exc:
            logger.debug("EventBus shutdown cleanup issue (non-fatal): %s", exc)
        event_bus = None

app = FastAPI(title="Charlie Dashboard", lifespan=lifespan)

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

_FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"
if _FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="surface-assets")


@app.get("/")
@app.get("/dashboard")
@app.get("/surface/{surface_id}")
async def serve_surface(surface_id: Optional[str] = None) -> FileResponse:
    """Single-page app entry -- the Dashboard is a plain web page, no Qt window involved."""
    index_path = _FRONTEND_DIST / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="frontend not built -- run `npm run build` in frontend/")
    return FileResponse(index_path)


async def broadcast(data: dict):
    """Send a message to connected WebSocket clients.

    Session-scoped events (token/transcript) are delivered only to clients
    subscribed to that session_id, preventing one browser from seeing another
    session's live stream. All other events go to every client.
    """
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


async def _event_bridge():
    """Background task: ZeroMQ events -> WebSocket broadcast."""
    global pipeline_state
    if not event_bus:
        return

    async def on_event(event: dict):
        logger.debug(f"Event received: {event}")
        global pipeline_state
        etype = event.get("type", "")
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
        elif etype == "extension_proposed":
            await _stage_proposed_extension(event.get("payload", {}))
            return
        elif etype in ("surface_spawn", "surface_update", "surface_dismiss"):
            _apply_surface_event(_active_surfaces, event)
            if etype in ("surface_spawn", "surface_update"):
                sid = event.get("payload", {}).get("surface_id")
                ttl = event.get("payload", {}).get("ttl_seconds")
                if sid and ttl:
                    asyncio.create_task(_expire_surface(sid, event, ttl))
        elif etype in ("tool_approval_request", "tool_approval_resolved"):
            _apply_approval_event(_pending_approvals, event)

        await broadcast(event)

    try:
        await event_bus.consume_events(on_event)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Event bridge error: {e}", exc_info=True)





@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    global _active_frontend_session
    origin = ws.headers.get("origin")
    if origin:
        allowed_origins = (
            "http://localhost:",
            "http://127.0.0.1:",
            "tauri://",
            "http://tauri.localhost",
        )
        if not any(origin.startswith(allowed) for allowed in allowed_origins):
            logger.warning("Blocked WebSocket connection from unauthorized origin: %s", origin)
            raise WebSocketException(code=1008)

    await ws.accept()
    active_connections.add(ws)
    ws_sessions[ws] = _active_frontend_session
    logger.info("WebSocket connected: %d active", len(active_connections))

    # Send initial cached state immediately to prevent empty UI states on connection
    try:
        for event in _initial_state_events():
            await ws.send_text(json.dumps(event))
    except Exception as e:
        logger.warning("Failed to send initial cached state to WebSocket: %s", e)

    if event_bus:
        await event_bus.send_command({"type": "ws_connection_count", "count": len(active_connections)})
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
                elif event_bus:
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


@app.get("/api/history")
async def history(limit: int = 50):
    store = _get_store()
    messages = store.get_recent(limit=limit)
    return {"messages": [{"role": r, "content": c} for r, c in messages]}


@app.get("/api/status")
async def status():
    import platform as _platform
    return {
        "state": pipeline_state,
        "launch_id": LAUNCH_ID,
        "uptime_seconds": int(time.time() - _START_TIME),
        "pid": os.getpid(),
        "desktop_control_enabled": config.desktop_control_enabled,
        "os_host": f"{_platform.system()} {_platform.machine()}",
    }


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
    text = data.get("text", "").strip()
    if not text:
        return {"status": "error", "detail": "empty message"}
    store = _get_store()
    store.append("user", text, session_id=session_id)
    if event_bus:
        await event_bus.send_command(
            {"type": "chat", "session_id": session_id, "text": text}
        )
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
# surface_id -> latest spawn/update payload, replayed to webviews that connect after their spawn event fired
_active_surfaces: dict = {}
# request_id -> tool_approval_request event, replayed like _active_surfaces so a late-connecting window's store gets it
_pending_approvals: dict = {}


def _initial_state_events() -> List[dict]:
    """Return cached events needed by a newly connected client."""
    events = [
        {"type": "charlie_state", "payload": _charlie_state},
        {"type": "system_status", "payload": _system_status},
        {"type": "subsystem_health", "payload": _subsystem_health},
        {"type": "task_snapshot", "payload": {"tasks": list(_background_tasks.values())}},
        {"type": "audio_state", "payload": _audio_state},
        {"type": "mic_state", "payload": _mic_state},
    ]
    events.extend(_active_surfaces.values())
    events.extend(_pending_approvals.values())
    return events


async def _expire_surface(surface_id: str, spawned_as: dict, ttl_seconds: float) -> None:
    await asyncio.sleep(ttl_seconds)
    if _active_surfaces.get(surface_id) is spawned_as:
        _active_surfaces.pop(surface_id, None)


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
        cache[task_id] = {
            "id": str(task_id),
            "title": str(payload.get("title", "")),
            "status": str(payload.get("status", "")),
            "current_step": int(payload.get("current_step", 0)),
            "total_steps": int(payload.get("total_steps", 0)),
        }


def _apply_surface_event(cache: dict, event: dict) -> None:
    """Mutate `cache` (surface_id -> spawn/update event) so it always reflects the currently-live surfaces."""
    etype = event.get("type", "")
    payload = event.get("payload", {})
    sid = payload.get("surface_id")
    if etype in ("surface_spawn", "surface_update") and sid:
        cache[sid] = event
    elif etype == "surface_dismiss":
        cache.pop(sid, None)


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


def _update_env_file(updates: dict):
    from pathlib import Path
    env_path = Path(".env")
    if not env_path.exists():
        env_path.touch()
    content = env_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    new_lines = []
    matched_keys = set()
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in line:
            parts = line.split("=", 1)
            key = parts[0].strip()
            if key in updates:
                val = updates[key]
                if isinstance(val, list):
                    val = ",".join(val)
                elif isinstance(val, bool):
                    val = "true" if val else "false"
                new_lines.append(f"{key}={val}")
                matched_keys.add(key)
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)
    for key, val in updates.items():
        if key not in matched_keys:
            if isinstance(val, list):
                val = ",".join(val)
            elif isinstance(val, bool):
                val = "true" if val else "false"
            new_lines.append(f"{key}={val}")
    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


@app.get("/api/config")
async def get_dashboard_config():
    """Describe every .env-backed setting for the settings page.

    Driven entirely by Config.editable_field_specs() (charlie/config.py) --
    adding a new Config field there is enough for it to appear here with no
    other file needing to know its name. Secret fields never echo their
    value, only whether one is set.
    """
    out = []
    for spec in Config.editable_field_specs():
        value = getattr(config, spec["field"])
        out.append(
            {
                "key": spec["key"],
                "group": spec["group"],
                "label": spec["label"],
                "type": spec["type"],
                "secret": spec["secret"],
                "restart": spec["restart"],
                "value": None if spec["secret"] else value,
                "is_set": bool(value) if spec["secret"] else None,
            }
        )
    return {"fields": out}


@app.post("/api/config")
async def update_dashboard_config(data: dict):
    """Persist one or more .env-backed settings -- on disk and in this process.

    `data` is {ENV_VAR_NAME: value}; unknown keys are ignored so this can't be
    used to inject arbitrary env vars. This only writes .env and updates the
    web-server process's own config copy (so GET /api/config echoes back the
    new value immediately) -- it does NOT push the change to the running
    voice process. The settings page's Save button calls this; its separate
    Reload button (POST /api/config/reload) is what actually applies saved
    settings to the live engine. Keeping those two steps distinct means
    nothing ever reloads a subsystem as a side effect of typing.
    """
    known_keys = {spec["key"] for spec in Config.editable_field_specs()}
    updates = {k: v for k, v in data.items() if k in known_keys}
    if not updates:
        return {"status": "error", "message": "no recognized settings in request"}

    try:
        touched = config.apply_env_updates(updates)
        _update_env_file(updates)
        return {"status": "ok", "touched": sorted(touched)}
    except Exception as e:
        logger.error(f"Error updating config: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


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
            async with httpx.AsyncClient(timeout=3.0) as client:
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
