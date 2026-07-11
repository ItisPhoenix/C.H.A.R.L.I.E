"""FastAPI + WebSocket backend for Charlie web dashboard.

Runs in a separate subprocess spawned by main.py.
Communicates with the voice process via ZeroMQ (EventBus).
"""

import asyncio

# Windows event-loop policy (must precede zmq/asyncio imports)
from charlie.runtime import configure as _configure_platform

_configure_platform()

import json
import logging
import os
import time
import uuid
from typing import Set

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, WebSocketException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from charlie.config import config
from charlie.ipc import DEFAULT_COMMAND_PORT, DEFAULT_EVENT_PORT, EventBus
from charlie.memory_graph import MemoryGraph
from charlie.session_store import SessionStore

logger = logging.getLogger("charlie.web_server")

_START_TIME = time.time()

# Module-level state
active_connections: Set[WebSocket] = set()
# Maps each WS connection to the session_id it is currently viewing. Lets us
# scope per-session streams (token/transcript) instead of leaking them to all
# connected browsers.
ws_sessions: dict[WebSocket, str] = {}

# MCP tools are discovered once here at web-server startup (mirroring main.py)
# and registered into the shared registry so /api/mcp/* reflects reality.
mcp_client = None
if config.mcp_enabled:
    try:
        from charlie.mcp_client import start_mcp

        mcp_client = start_mcp(config)
        if mcp_client is None:
            logger.info("Web MCP subsystem not started (no servers configured)")
    except Exception as e:
        logger.warning("Web MCP subsystem failed to initialize: %s", e)
        mcp_client = None
# Events that carry a session_id and must only reach clients subscribed to it.
_SESSION_SCOPED_EVENTS = ("token", "transcript")
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

app = FastAPI(title="Charlie Dashboard")

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
    for ws in active_connections:
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
        if etype == "blackboard_update":
            global _blackboard_state
            _blackboard_state = event.get("payload", {})
        elif etype == "system_status":
            global _system_status
            _system_status = event.get("payload", {})
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

        await broadcast(event)

    try:
        await event_bus.consume_events(on_event)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Event bridge error: {e}", exc_info=True)


@app.on_event("startup")
async def startup():
    global event_bus
    event_bus = EventBus(
        pub_port=DEFAULT_EVENT_PORT,
        pull_port=DEFAULT_COMMAND_PORT,
        is_producer=False,
    )
    await event_bus.__aenter__()
    asyncio.create_task(_event_bridge())
    logger.info("Web server started, event bridge active")


@app.on_event("shutdown")
async def shutdown():
    global event_bus
    if event_bus:
        # Fire-and-forget: close sockets immediately (linger=0 handles this),
        # then terminate context. If this hangs, run.py's force-exit timer kills us.
        try:
            await asyncio.wait_for(
                event_bus.__aexit__(None, None, None),
                timeout=2.0,
            )
        except (asyncio.TimeoutError, Exception) as exc:
            logger.debug("EventBus shutdown cleanup issue (non-fatal): %s", exc)
        event_bus = None


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
    except Exception as e:
        active_connections.discard(ws)
        ws_sessions.pop(ws, None)
        logger.error("WebSocket error: %s", e)


@app.get("/api/history")
async def history(limit: int = 50):
    store = _get_store()
    messages = store.get_recent(limit=limit)
    return {"messages": [{"role": r, "content": c} for r, c in messages]}


@app.get("/api/status")
async def status():
    return {
        "state": pipeline_state,
        "launch_id": LAUNCH_ID,
        "uptime_seconds": int(time.time() - _START_TIME),
        "pid": os.getpid(),
    }


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
    launch_id = data.get("launch_id")
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
    """Get messages for a specific session."""
    store = _get_store()
    messages = store.get_session_messages(session_id, limit=limit)
    return {"messages": [{"role": r, "content": c} for r, c in messages]}


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
# Blackboard API (for Tauri dashboard)
# ---------------------------------------------------------------------------
# In-memory blackboard state (synced from main process via ZMQ)
_blackboard_state: dict = {
    "tasks": [],
    "agents": {},
}
_system_status: dict = {
    "cpu": 0.0,
    "ram": 0.0,
    "gpu": 0.0,
    "active_agents": [],
}
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


@app.get("/api/memory/facts")
async def get_memory_facts():
    """Retrieve all known facts from the SQLite knowledge graph.

    The graph stores nodes (node_type/label/properties) rather than
    subject/predicate/object triples, so each node is mapped to a
    fact-shaped record the frontend already renders.
    """
    graph = _get_memory_graph()
    if graph:
        try:
            nodes = graph.get_all_nodes()
            facts = [
                {
                    "subject": n.get("node_type", ""),
                    "predicate": "is",
                    "object": n.get("content", ""),
                }
                for n in nodes
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
        defs = [
            d for d in registry.get_tool_definitions()
            if d.get("name", "").startswith("mcp_")
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
        connected = enabled and any(
            d.get("name", "").startswith("mcp_")
            for d in registry.get_tool_definitions()
        )
        return {"enabled": enabled, "connected": connected}
    except Exception as e:
        logger.error(f"Error fetching MCP status: {e}")
    return {"enabled": False, "connected": False}


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


@app.get("/api/session/active")
async def get_active_session():
    """Get the currently active frontend session."""
    return {"active_session": _active_frontend_session}

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
        real_frontend_dir = os.path.realpath(_FRONTEND_DIR)
        candidate = os.path.realpath(os.path.join(real_frontend_dir, rest_of_path))
        contained = (
            rest_of_path
            and os.path.isfile(candidate)
            and (
                candidate == real_frontend_dir
                or candidate.startswith(real_frontend_dir + os.sep)
            )
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
        loop="asyncio",
    )
    server = uvicorn.Server(server_config)
    server.run()
