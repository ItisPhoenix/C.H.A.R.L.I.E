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

import json
import logging
import os
import time
import uuid
from typing import List, Set

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, WebSocketException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from charlie.config import Config, config
from charlie.ipc import DEFAULT_COMMAND_PORT, DEFAULT_EVENT_PORT, EventBus
from charlie.projects import Projects
from charlie.scratchpad import Scratchpad
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


def _save_extensions() -> None:
    """Persist installed extensions to disk so they survive a web-server
    restart -- see lifespan()'s startup reload for the counterpart."""
    try:
        data = [
            {
                "name": e.name,
                "kind": e.kind,
                "source": e.source,
                "raw_text": e.raw_text,
                "enabled": e.enabled,
            }
            for e in _extension_manager.list()
        ]
        with open(config.extensions_state_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        logger.warning("Failed to save extensions state", exc_info=True)


def _load_extensions() -> None:
    """Reload previously-installed extensions on web-server startup -- the
    counterpart to _save_extensions(). Each entry was already approved once
    (the propose/confirm gate ran at original install time), so a restart
    restores prior state without re-prompting. One bad entry is logged and
    skipped rather than blocking the rest or server startup."""
    from charlie.extensions import build_skill_card

    try:
        with open(config.extensions_state_path, "r", encoding="utf-8") as f:
            entries = json.load(f)
    except FileNotFoundError:
        return
    except Exception:
        logger.warning("Failed to read extensions state file", exc_info=True)
        return

    for entry in entries:
        name = entry.get("name", "")
        kind = entry.get("kind", "")
        source = entry.get("source", "")
        raw_text = entry.get("raw_text", "")
        enabled = bool(entry.get("enabled", True))
        try:
            tool_names: List[str] = _install_extension(kind, name, source, raw_text) if enabled else []
            card = build_skill_card(name, source or kind, tool_names, raw_text or name)
            _extension_manager.record(
                InstalledExtension(
                    name=name, kind=kind, source=source, card=card,
                    enabled=enabled, tool_names=tool_names, raw_text=raw_text,
                )
            )
        except Exception:
            logger.warning("Failed to reload extension '%s' on startup", name, exc_info=True)


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
_TOOL_EVENT_MAX_CHARS = 500  # cap persisted tool_event text, matches session_store's _TOOL_PERSIST_MAX_CHARS
event_bus: EventBus | None = None
LAUNCH_ID: str = config.charlie_launch_id
_store: SessionStore | None = None


def _get_store() -> SessionStore:
    global _store
    if _store is None:
        _store = SessionStore(config.session_db_path)
    return _store


_scratchpad: Scratchpad | None = None


def _get_scratchpad() -> Scratchpad:
    global _scratchpad
    if _scratchpad is None:
        _scratchpad = Scratchpad(config.scratchpad_db_path)
    return _scratchpad


def _get_projects() -> Projects:
    return Projects(config.projects_dir)


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

    _load_extensions()

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
        # Update pipeline state for status endpoint
        if etype == "vad_start":
            pipeline_state = "listening"
        elif etype == "thinking":
            pipeline_state = "thinking"
        elif etype == "speaking_start":
            pipeline_state = "speaking"
        elif etype in ("speaking_stop", "response_done"):
            pipeline_state = "idle"
        elif etype == "wake_word":
            pipeline_state = "listening"

        # Keep web server cached state in sync
        if etype == "system_status":
            global _system_status, _system_status_received_at
            _system_status = event.get("payload", {})
            _system_status_received_at = time.time()
        elif etype == "audio_state":
            global _audio_state
            _audio_state = event.get("payload", {})
        elif etype == "audio_level":
            global _audio_level
            payload = event.get("payload", {})
            _audio_level = float(payload.get("level", 0.0))
        elif etype == "mic_state":
            global _mic_state
            _mic_state = event.get("payload", {})
        elif etype == "tool_call":
            payload = event.get("payload", {})
            _get_store().append_tool_event(
                payload.get("session_id") or "default",
                payload.get("turn_id"),
                "tool_call",
                payload.get("name", ""),
                json.dumps(payload.get("args", {}), ensure_ascii=False)[:_TOOL_EVENT_MAX_CHARS],
            )
        elif etype == "tool_result":
            payload = event.get("payload", {})
            _get_store().append_tool_event(
                payload.get("session_id") or "default",
                payload.get("turn_id"),
                "tool_result",
                payload.get("name", ""),
                (payload.get("text") or "")[:_TOOL_EVENT_MAX_CHARS],
            )
        elif etype == "agent_spawned":
            payload = event.get("payload", {})
            _get_store().create_agent_run(
                payload.get("agent_id", ""), payload.get("task", ""), payload.get("session_id") or "default"
            )
        elif etype == "agent_status":
            payload = event.get("payload", {})
            _get_store().update_agent_run(payload.get("agent_id", ""), last_tool=payload.get("tool_name"))
        elif etype == "agent_result":
            payload = event.get("payload", {})
            result = payload.get("result", "")
            status = "timeout" if "timed out" in result else "cancelled" if "cancelled" in result else "done"
            _get_store().update_agent_run(payload.get("agent_id", ""), status=status, result=result)
        elif etype == "skill_installed":
            from charlie.extensions import build_skill_card

            payload = event.get("payload", {})
            name, raw_text = payload.get("name", ""), payload.get("raw_text", "")
            source = "auto-generated (spawn_agent)"
            try:
                tool_names = _install_extension("skill", name, source, raw_text)
                card = build_skill_card(name, source, tool_names, raw_text)
                _extension_manager.record(
                    InstalledExtension(name=name, kind="skill", source=source, card=card,
                                        tool_names=tool_names, raw_text=raw_text)
                )
                _save_extensions()
            except Exception as e:
                logger.error(f"Failed to mirror auto-drafted skill '{name}': {e}", exc_info=True)

        await broadcast(event)

    async def on_event_guarded(event: dict) -> None:
        try:
            await on_event(event)
        except Exception as e:
            logger.error(f"Event bridge: error handling event {event.get('type')}: {e}", exc_info=True)

    try:
        await event_bus.consume_events(on_event_guarded)
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
        await ws.send_text(json.dumps({"type": "system_status", "payload": _system_status}))
        await ws.send_text(json.dumps({"type": "audio_state", "payload": _audio_state}))
        await ws.send_text(json.dumps({"type": "mic_state", "payload": _mic_state}))
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
                    if event_bus:
                        await event_bus.send_command(
                            {"type": "session_active", "session_id": _active_frontend_session}
                        )
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
    messages = await asyncio.to_thread(store.get_recent, limit=limit)
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


@app.get("/api/background_task")
async def background_task_status():
    """Current background-task state, for dashboard resync (otherwise push-only over WS)."""
    from charlie import background_task

    task = background_task.get_current_task()
    return {"task": task.to_event() if task is not None else None}


@app.get("/api/scratchpad")
async def list_scratchpad():
    """List all scratchpad entries."""
    pad = _get_scratchpad()
    entries = await asyncio.to_thread(pad.list)
    return {"entries": [{"index": i, "text": text, "created_at": c} for i, text, c in entries]}


@app.post("/api/scratchpad")
async def add_scratchpad(data: dict):
    """Append a scratchpad entry."""
    pad = _get_scratchpad()
    try:
        index = await asyncio.to_thread(pad.add, data.get("text", ""))
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    return {"index": index}


@app.put("/api/scratchpad/{index}")
async def edit_scratchpad(index: int, data: dict):
    """Replace the text of a scratchpad entry by its 1-based index."""
    pad = _get_scratchpad()
    try:
        ok = await asyncio.to_thread(pad.edit, index, data.get("text", ""))
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    if not ok:
        return JSONResponse(status_code=404, content={"error": f"No entry at index {index}"})
    return {"index": index}


@app.delete("/api/scratchpad/{index}")
async def delete_scratchpad(index: int):
    """Delete a scratchpad entry by its 1-based index."""
    pad = _get_scratchpad()
    ok = await asyncio.to_thread(pad.delete, index)
    if not ok:
        return JSONResponse(status_code=404, content={"error": f"No entry at index {index}"})
    return {"deleted": index}


@app.delete("/api/scratchpad")
async def clear_scratchpad():
    """Delete all scratchpad entries."""
    pad = _get_scratchpad()
    await asyncio.to_thread(pad.clear)
    return {"cleared": True}


@app.get("/api/projects")
async def list_projects():
    """List project workspaces and which one is active."""
    store = _get_projects()
    names = await asyncio.to_thread(store.list)
    active = await asyncio.to_thread(store.get_active)
    return {"projects": names, "active": active}


@app.post("/api/projects")
async def create_project(data: dict):
    """Create a new project workspace."""
    store = _get_projects()
    try:
        slug = await asyncio.to_thread(store.create, data.get("name", ""))
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    return {"slug": slug}


@app.put("/api/projects/active")
async def switch_project(data: dict):
    """Switch the active project workspace; pass slug=null to go back to global."""
    store = _get_projects()
    try:
        await asyncio.to_thread(store.set_active, data.get("slug"))
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    return {"active": data.get("slug")}


@app.get("/api/sessions")
async def list_sessions(request: Request):
    """List sessions, optionally filtered by launch_id or source."""
    store = _get_store()
    launch_id = request.query_params.get("launch_id")
    source = request.query_params.get("source")
    sessions = await asyncio.to_thread(store.get_sessions, source=source, launch_id=launch_id)
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
    await asyncio.to_thread(store.create_session, session_id, title, source=source, launch_id=launch_id)
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
    messages = await asyncio.to_thread(store.get_session_messages_with_turn_id, session_id, limit=limit)
    return {
        "messages": [
            {"role": r, "content": c, "turnId": t}
            for r, c, t in messages
            if r not in _HIDDEN_ROLES
        ]
    }


@app.get("/api/sessions/{session_id}/tool_events")
async def session_tool_events(session_id: str):
    """Structured execution trace (tool calls/results) for a session, grouped
    by turnId so the frontend can attach a 'Show Execution' trace per message."""
    store = _get_store()
    events = await asyncio.to_thread(store.get_tool_events, session_id)
    return {
        "events": [
            {"turnId": turn_id, "kind": kind, "name": name, "text": text}
            for turn_id, kind, name, text in events
        ]
    }


@app.put("/api/sessions/{session_id}")
async def update_session(session_id: str, data: dict):
    """Update session title."""
    title = data.get("title", "New Chat")
    store = _get_store()
    await asyncio.to_thread(store.update_session_title, session_id, title)
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
    await asyncio.to_thread(store.delete_session, session_id)
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

    Forwards the message to the voice process as a `chat` command, exactly
    like the WS path -- main.py's _process() is the single place that
    persists the user turn (touch_session, title update, then the append).
    Persisting here too used to double-write every REST-originated message,
    same bug the WS path never had since it never pre-persists either.
    """
    text = data.get("text", "").strip()
    if not text:
        return {"status": "error", "detail": "empty message"}
    if event_bus:
        await event_bus.send_command(
            {"type": "chat", "session_id": session_id, "text": text}
        )
    return {"status": "ok"}
# ---------------------------------------------------------------------------
_system_status: dict = {
    "cpu": 0.0,
    "ram": 0.0,
    "gpu": 0.0,
}
# Age of _system_status is a real liveness signal for the voice process --
# it emits this roughly once/sec, so a stale value means that process is down.
_system_status_received_at: float = 0.0
_active_frontend_session: str | None = None
_audio_state: dict = {
    "muted": False,
    "volume": 1.0,
}
_mic_state: dict = {
    "mic_muted": False,
}
_audio_level: float = 0.0



@app.get("/api/audio")
async def get_audio_state():
    """Return current speaker mute/volume state."""
    return _audio_state


@app.get("/api/mic")
async def get_mic_state():
    """Return current microphone mute state."""
    return _mic_state


@app.get("/api/audio-level")
async def get_audio_level():
    """Return the latest real-time audio amplitude (0.0-1.0)."""
    return {"level": _audio_level}


@app.get("/api/tools")
async def get_registered_tools():
    """Return every tool in the shared registry -- unlike /api/mcp/tools
    (MCP-prefixed subset only), this is the true total the dashboard's
    Registered Tools count should reflect.

    Must ensure the MCP client itself first -- the dashboard never calls
    /api/mcp/tools or /api/mcp/status, so without this the MCP subprocess
    never boots and mcp_-prefixed tools never join the registry, even with
    MCP_ENABLED=true and servers configured."""
    try:
        from charlie.tools import registry

        if config.mcp_enabled:
            await _ensure_mcp_client_async()
        return {"tools": registry.get_tool_definitions()}
    except Exception as e:
        logger.error(f"Error fetching registered tools: {e}")
    return {"tools": []}


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


@app.get("/api/mcp/status")
async def get_mcp_status():
    """Report whether MCP is enabled and whether tools are connected."""
    try:
        from charlie.tools import registry

        enabled = config.mcp_enabled
        if enabled:
            await _ensure_mcp_client_async()
        connected = enabled and any(
            d.get("function", {}).get("name", "").startswith("mcp_")
            for d in registry.get_tool_definitions()
        )
        return {"enabled": enabled, "connected": connected}
    except Exception as e:
        logger.error(f"Error fetching MCP status: {e}")
    return {"enabled": False, "connected": False}


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
            name=card.name, kind=kind, source=source, card=card, tool_names=tool_names,
            raw_text=raw_text,
        )
    )
    _save_extensions()
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
    _save_extensions()
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
    _save_extensions()
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
    _save_extensions()
    await _forward_to_voice(
        "extension_uninstalled", {"kind": ext.kind, "name": name, "tool_names": ext.tool_names}
    )
    return {"status": "ok"}


@app.get("/api/agents")
async def list_agents(session_id: str | None = None, limit: int = 100):
    """List persisted sub-agent runs, most recent first."""
    store = _get_store()
    rows = await asyncio.to_thread(store.get_agent_runs, session_id=session_id, limit=limit)
    return {
        "agents": [
            {
                "agentId": r[0],
                "sessionId": r[1],
                "task": r[2],
                "status": r[3],
                "lastTool": r[4],
                "result": r[5],
                "spawnedAt": r[6],
                "finishedAt": r[7] if r[3] != "running" else None,
            }
            for r in rows
        ]
    }


@app.post("/api/agents/{agent_id}/cancel")
async def cancel_agent(agent_id: str):
    """Cancel a running sub-agent. Brain.spawn_agent's own on_agent_result
    callback emits the graceful cancelled result -- no separate emit here."""
    if event_bus:
        await event_bus.send_command({"type": "cancel_agent", "payload": {"agent_id": agent_id}})
    return {"status": "ok"}


@app.post("/api/session/active")
async def set_active_session(data: dict):
    """Frontend signals which session is active (for voice routing)."""
    global _active_frontend_session
    _active_frontend_session = data.get("session_id")
    logger.info("Active frontend session: %s", _active_frontend_session)
    # Also update WS client subscriptions and route the switch to the voice process for mic routing.
    for ws in active_connections:
        ws_sessions[ws] = _active_frontend_session
    if event_bus:
        await event_bus.send_command(
            {"type": "session_active", "session_id": _active_frontend_session}
        )
    return {"active_session": _active_frontend_session}


@app.get("/api/session/active")
async def get_active_session():
    """Get the currently active frontend session."""
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


_WORKSPACE_IGNORED_DIRS = {"node_modules", "venv", "__pycache__", "dist", "out", ".next"}
_WORKSPACE_ALLOWED_EXTS = {".py", ".md", ".json", ".css", ".ts", ".tsx", ".js", ".html"}


def _scan_workspace_files() -> list[str]:
    """os.walk with in-place dirname pruning -- never descends into node_modules/
    .git/venv in the first place, unlike Path.rglob('*') which walks everything
    and filters after (blocked the whole event loop on this repo's frontend/
    node_modules tree, since this used to run inline in the async endpoint)."""
    import os as _os
    from pathlib import Path
    root_dir = Path(__file__).parent.parent
    files_list = []
    for dirpath, dirnames, filenames in _os.walk(root_dir):
        dirnames[:] = [
            d for d in dirnames if not d.startswith(".") and d not in _WORKSPACE_IGNORED_DIRS
        ]
        for name in filenames:
            if Path(name).suffix in _WORKSPACE_ALLOWED_EXTS:
                rel = (Path(dirpath) / name).relative_to(root_dir).as_posix()
                files_list.append(rel)
    return sorted(files_list)


@app.get("/api/workspace/files")
async def list_workspace_files():
    """Return real tree structure of workspace files."""
    try:
        return {"files": await asyncio.to_thread(_scan_workspace_files)}
    except Exception as e:
        logger.error(f"Error listing workspace files: {e}", exc_info=True)
    return {"files": []}


@app.get("/api/workspace/file")
async def get_workspace_file(path: str):
    """Return contents of a workspace file."""
    from fastapi import HTTPException
    from pathlib import Path
    root_dir = Path(__file__).parent.parent
    target = (root_dir / path).resolve()
    if (
        Path(path).suffix not in _WORKSPACE_ALLOWED_EXTS
        or not str(target).startswith(str(root_dir) + os.sep)
        or not target.exists()
        or not target.is_file()
    ):
        raise HTTPException(status_code=400, detail="Invalid path")
    try:
        content = target.read_text(encoding="utf-8", errors="ignore")
        return {"path": path, "content": content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/workspace/file")
async def put_workspace_file(data: dict):
    """Create or overwrite a workspace file (edit-save and new-file share
    this endpoint -- PUT is create-or-replace either way)."""
    from fastapi import HTTPException
    from pathlib import Path
    path = data.get("path", "")
    content = data.get("content", "")
    if not path or Path(path).suffix not in _WORKSPACE_ALLOWED_EXTS:
        raise HTTPException(status_code=400, detail="Invalid path or extension")
    root_dir = Path(__file__).parent.parent
    target = (root_dir / path).resolve()
    if not str(target).startswith(str(root_dir) + os.sep):
        raise HTTPException(status_code=400, detail="Invalid path")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"path": path, "status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/workspace/file")
async def delete_workspace_file(path: str):
    """Delete a workspace file."""
    from fastapi import HTTPException
    from pathlib import Path
    root_dir = Path(__file__).parent.parent
    target = (root_dir / path).resolve()
    if (
        Path(path).suffix not in _WORKSPACE_ALLOWED_EXTS
        or not str(target).startswith(str(root_dir) + os.sep)
        or not target.exists()
        or not target.is_file()
    ):
        raise HTTPException(status_code=400, detail="Invalid path")
    try:
        target.unlink()
        return {"path": path, "deleted": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/docker/status")
async def get_docker_status():
    """Check if docker daemon is reachable and list containers."""
    import subprocess
    try:
        res = subprocess.run(["docker", "ps", "--format", "{{json .}}"], capture_output=True, text=True, timeout=2)
        if res.returncode == 0:
            lines = [line.strip() for line in res.stdout.strip().split("\n") if line.strip()]
            containers = []
            for line in lines:
                try:
                    containers.append(json.loads(line))
                except Exception as e:
                    logger.debug(f"Skipping unparseable docker ps line: {e}")
            return {"available": True, "containers": containers}
    except Exception as e:
        logger.debug(f"Docker daemon unreachable: {e}")
    return {"available": False, "containers": []}


@app.get("/api/ollama/status")
async def get_ollama_status():
    """Check if local Ollama daemon is running."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get("http://127.0.0.1:11434/api/tags")
            if r.status_code == 200:
                models = r.json().get("models", [])
                return {"available": True, "models": [m.get("name") for m in models]}
    except Exception as e:
        logger.debug(f"Ollama daemon unreachable: {e}")
    return {"available": False, "models": []}


@app.get("/api/models")
async def get_available_models():
    """Return live configured model plus auto-discovered local & provider API key models."""
    import httpx

    current_model = config.llm_model or ""
    # Only seed the currently configured model - no phantom defaults
    models_set: set[str] = {current_model} if current_model else set()

    # Query configured LLM provider endpoint if API key is set
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

    # Discover local Ollama models (port 11434)
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            r = await client.get("http://127.0.0.1:11434/api/tags")
            if r.status_code == 200:
                for m in r.json().get("models", []):
                    if m.get("name"):
                        models_set.add(m["name"])
    except Exception as e:
        logger.debug(f"Ollama model discovery unreachable: {e}")

    # Discover local LM Studio models (port 1234)
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            r = await client.get("http://127.0.0.1:1234/v1/models")
            if r.status_code == 200:
                for item in r.json().get("data", []):
                    if isinstance(item, dict) and item.get("id"):
                        models_set.add(item["id"])
    except Exception as e:
        logger.debug(f"LM Studio model discovery unreachable: {e}")

    return {
        "active_model": current_model,
        "has_api_key": bool(config.llm_key and config.llm_key not in ("no-key", "no_key")),
        "models": sorted(list(models_set)),
    }


@app.get("/api/local_models")
async def get_local_models():
    """Return ONLY locally hosted models (Ollama :11434, LM Studio :1234),
    each flagged with whether it's the model Charlie's LLM_MODEL config
    currently points at, plus per-endpoint reachability/latency and
    per-model specs (size, quantization, context length, VRAM-loaded
    state where the server exposes it) -- real telemetry, not just a
    static "here's what's installed" list."""
    import time

    import httpx

    active_name = config.llm_model.strip().lower()
    local_models = []
    endpoints = []

    # /api/ps reports what's actually loaded into VRAM right now, distinct from /api/tags' full catalog.
    ollama_loaded: dict[str, int] = {}
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            tags_resp = await client.get("http://127.0.0.1:11434/api/tags")
            latency_ms = round((time.monotonic() - t0) * 1000)
            if tags_resp.status_code == 200:
                try:
                    ps_resp = await client.get("http://127.0.0.1:11434/api/ps")
                    if ps_resp.status_code == 200:
                        for m in ps_resp.json().get("models", []):
                            if m.get("name"):
                                ollama_loaded[m["name"]] = m.get("size_vram", 0)
                except Exception as e:
                    logger.debug(f"Ollama /api/ps unavailable: {e}")
                endpoints.append({"name": "Ollama", "url": ":11434", "reachable": True, "latency_ms": latency_ms})
                for m in tags_resp.json().get("models", []):
                    if not m.get("name"):
                        continue
                    details = m.get("details", {})
                    local_models.append({
                        "name": m["name"],
                        "source": "Ollama (:11434)",
                        "active": m["name"].strip().lower() == active_name,
                        "size_bytes": m.get("size"),
                        "parameter_size": details.get("parameter_size"),
                        "quantization": details.get("quantization_level"),
                        "context_length": None,
                        "loaded_in_vram": m["name"] in ollama_loaded,
                        "vram_bytes": ollama_loaded.get(m["name"], 0),
                    })
            else:
                endpoints.append({"name": "Ollama", "url": ":11434", "reachable": False, "latency_ms": None})
    except Exception:
        endpoints.append({"name": "Ollama", "url": ":11434", "reachable": False, "latency_ms": None})

    # Try LM Studio's richer /api/v0/models first, fall back to plain OpenAI-shaped /v1/models for older versions.
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            r = await client.get("http://127.0.0.1:1234/api/v0/models")
            latency_ms = round((time.monotonic() - t0) * 1000)
            if r.status_code == 200:
                endpoints.append({"name": "LM Studio", "url": ":1234", "reachable": True, "latency_ms": latency_ms})
                for item in r.json().get("data", []):
                    if not (isinstance(item, dict) and item.get("id")):
                        continue
                    local_models.append({
                        "name": item["id"],
                        "source": "LM Studio (:1234)",
                        "active": item["id"].strip().lower() == active_name,
                        "size_bytes": item.get("size_bytes") or item.get("size"),
                        "parameter_size": None,
                        "quantization": item.get("quantization"),
                        "context_length": item.get("max_context_length") or item.get("loaded_context_length"),
                        "loaded_in_vram": item.get("state") == "loaded",
                        "vram_bytes": None,
                    })
            else:
                raise httpx.HTTPStatusError("v0 unavailable", request=r.request, response=r)
    except Exception:
        try:
            t0 = time.monotonic()
            async with httpx.AsyncClient(timeout=1.5) as client:
                r = await client.get("http://127.0.0.1:1234/v1/models")
                latency_ms = round((time.monotonic() - t0) * 1000)
                if r.status_code == 200:
                    endpoints.append({"name": "LM Studio", "url": ":1234", "reachable": True, "latency_ms": latency_ms})
                    for item in r.json().get("data", []):
                        if isinstance(item, dict) and item.get("id"):
                            local_models.append({
                                "name": item["id"],
                                "source": "LM Studio (:1234)",
                                "active": item["id"].strip().lower() == active_name,
                                "size_bytes": None,
                                "parameter_size": None,
                                "quantization": None,
                                "context_length": None,
                                "loaded_in_vram": None,
                                "vram_bytes": None,
                            })
                else:
                    endpoints.append({"name": "LM Studio", "url": ":1234", "reachable": False, "latency_ms": None})
        except Exception:
            endpoints.append({"name": "LM Studio", "url": ":1234", "reachable": False, "latency_ms": None})

    return {
        "count": len(local_models),
        "models": local_models,
        "active_model": config.llm_model,
        "active_is_local": any(m["active"] for m in local_models),
        "endpoints": endpoints,
    }


_OLLAMA_PULL_TIMEOUT = 600.0  # model downloads can take minutes


@app.post("/api/local_models/pull")
async def pull_local_model(data: dict):
    """Pull a model into local Ollama (POST :11434/api/pull, non-streaming)."""
    from fastapi import HTTPException
    import httpx

    name = data.get("name", "")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    try:
        async with httpx.AsyncClient(timeout=_OLLAMA_PULL_TIMEOUT) as client:
            r = await client.post(
                "http://127.0.0.1:11434/api/pull", json={"name": name, "stream": False}
            )
            r.raise_for_status()
            return {"status": "ok", "name": name}
    except Exception as e:
        logger.warning("Ollama pull failed for %s: %s", name, e)
        raise HTTPException(status_code=502, detail=f"Ollama pull failed: {e}")


@app.delete("/api/local_models/{name}")
async def delete_local_model(name: str):
    """Delete a model from local Ollama (DELETE :11434/api/delete)."""
    from fastapi import HTTPException
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.request(
                "DELETE", "http://127.0.0.1:11434/api/delete", json={"name": name}
            )
            r.raise_for_status()
            return {"status": "ok", "name": name}
    except Exception as e:
        logger.warning("Ollama delete failed for %s: %s", name, e)
        raise HTTPException(status_code=502, detail=f"Ollama delete failed: {e}")


_VOICE_PROCESS_STALE_SECONDS = 5.0  # main.py emits system_status ~once/sec


@app.get("/api/services/status")
async def get_services_status():
    """Return status of real Charlie system processes and services -- each
    entry below is a real check, not a hardcoded literal."""
    voice_alive = (time.time() - _system_status_received_at) < _VOICE_PROCESS_STALE_SECONDS
    voice_status = "online" if voice_alive else "offline"

    try:
        _get_store().get_sessions()
        session_store_status = "online"
    except Exception:
        session_store_status = "offline"

    # ponytail: path-exists check, not a live query -- instantiating
    # MemoryStore loads an embedding model, too expensive for a status poll.
    # Upgrade to a real connectivity probe if this ever needs to catch a
    # corrupt/locked store, not just a missing one.
    memory_store_status = "online" if os.path.exists(config.memory_db_path) else "offline"

    return {
        "services": [
            {
                "name": "Voice Pipeline Engine",
                "status": voice_status,
                "details": "sounddevice mic capture + Kokoro TTS synthesis"
                if voice_alive else "No recent system_status from the voice process",
                "type": "audio",
            },
            {
                "name": "Whisper ASR Worker",
                "status": voice_status,
                "details": "distil-large-v3 CUDA subprocess (shares the voice "
                "process's liveness signal with Voice Pipeline Engine above)",
                "type": "speech_to_text",
            },
            {
                "name": "FastAPI Web Server",
                "status": "online",
                "details": f"Uvicorn PID {os.getpid()} on port {config.charlie_port}",
                "type": "http_api",
            },
            {
                "name": "ZeroMQ EventBus Bridge",
                "status": "online" if event_bus is not None else "offline",
                "details": f"PUB/SUB IPC ports {DEFAULT_EVENT_PORT}/{DEFAULT_COMMAND_PORT}",
                "type": "ipc",
            },
            {
                "name": "SQLite SessionStore",
                "status": session_store_status,
                "details": "FTS5 isolated session history database",
                "type": "database",
            },
            {
                "name": "ChromaDB MemoryStore",
                "status": memory_store_status,
                "details": "Vector memory embedding store",
                "type": "vector_db",
            },
        ]
    }


# Serve frontend static files if they exist (checking both 'out' for NextJS and 'dist' for Vite)
_FRONTEND_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "frontend", "out"
)
if not os.path.exists(_FRONTEND_DIR):
    _FRONTEND_DIR = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "frontend", "dist"
    )
if os.path.exists(_FRONTEND_DIR):
    assets_dir = os.path.join(_FRONTEND_DIR, "assets")
    if os.path.exists(assets_dir):
        app.mount(
            "/assets",
            StaticFiles(directory=assets_dir),
            name="assets",
        )
    next_dir = os.path.join(_FRONTEND_DIR, "_next")
    if os.path.exists(next_dir):
        app.mount(
            "/_next",
            StaticFiles(directory=next_dir),
            name="_next",
        )

    @app.get("/{rest_of_path:path}")
    async def serve_frontend(request: Request, rest_of_path: str):
        if rest_of_path.startswith("api/") or rest_of_path == "ws":
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Not Found")

        # Path traversal containment: resolve the candidate and verify its
        # realpath stays inside the frontend directory before serving it.
        # Next.js static export writes nested routes as <path>.html or
        # <path>/index.html, so a hard refresh on e.g. /settings needs both
        # tried before falling back to the SPA shell.
        real_frontend_dir = os.path.realpath(_FRONTEND_DIR)
        rel_candidates = [rest_of_path]
        if rest_of_path and not rest_of_path.endswith(".html"):
            rel_candidates += [f"{rest_of_path}.html", f"{rest_of_path}/index.html"]
        for rel in rel_candidates:
            candidate = os.path.realpath(os.path.join(real_frontend_dir, rel))
            contained = os.path.isfile(candidate) and (
                candidate == real_frontend_dir
                or candidate.startswith(real_frontend_dir + os.sep)
            )
            if contained:
                return FileResponse(candidate)

        return FileResponse(
            os.path.join(_FRONTEND_DIR, "index.html"),
            headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}
        )
else:
    logger.warning("Frontend dist directory not found. Web UI will not be served.")


def start_server(
    pub_port: int = DEFAULT_EVENT_PORT, pull_port: int = DEFAULT_COMMAND_PORT
):
    """Entry point for the web server subprocess."""
    _configure_platform()
    import uvicorn

    host = config.charlie_host
    if host == "0.0.0.0":
        logger.warning(
            "Binding to 0.0.0.0 exposes Charlie to the local network. "
            "Use a reverse proxy with TLS for remote access."
        )
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
