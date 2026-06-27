"""FastAPI + WebSocket backend for Charlie web dashboard.

Runs in a separate subprocess spawned by main.py.
Communicates with the voice process via ZeroMQ (EventBus).
"""

import asyncio
import os
import sys

# Windows: pyzmq needs Selector event loop, not Proactor (must be before any zmq import)
import warnings as _warnings

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    _warnings.filterwarnings(
        "ignore", message=".*add_reader.*", category=RuntimeWarning
    )

import json
import logging
from pathlib import Path
from typing import Set

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from charlie.config import config
from charlie.ipc import DEFAULT_COMMAND_PORT, DEFAULT_EVENT_PORT, EventBus

logger = logging.getLogger("charlie.web_server")

# Module-level state
active_connections: Set[WebSocket] = set()
event_bus: EventBus | None = None
LAUNCH_ID: str = os.environ.get("CHARLIE_LAUNCH_ID", "")
pipeline_state: str = "idle"

app = FastAPI(title="Charlie Dashboard")


async def broadcast(data: dict):
    """Send a message to all connected WebSocket clients."""
    message = json.dumps(data)
    disconnected: list[WebSocket] = []
    for ws in active_connections:
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        active_connections.discard(ws)


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
        await event_bus.__aexit__(None, None, None)
        event_bus = None


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    active_connections.add(ws)
    logger.info(f"WebSocket connected: {len(active_connections)} active")
    try:
        while True:
            data = await ws.receive_text()
            logger.debug(f"WS received: {data}")
            try:
                msg = json.loads(data)
                if event_bus:
                    await event_bus.send_command(msg)
                    logger.debug(f"WS forwarded command: {msg}")
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON from client: {data}")
    except WebSocketDisconnect:
        active_connections.discard(ws)
        logger.info(f"WebSocket disconnected: {len(active_connections)} active")
    except Exception as e:
        active_connections.discard(ws)
        logger.error(f"WebSocket error: {e}")


@app.get("/api/history")
async def history(limit: int = 50):
    from charlie.session_store import SessionStore

    store = SessionStore(config.session_db_path)
    try:
        messages = store.get_recent(limit=limit)
        return {"messages": [{"role": r, "content": c} for r, c in messages]}
    finally:
        store.close()


@app.get("/api/status")
async def status():
    return {"state": pipeline_state, "launch_id": LAUNCH_ID}


@app.get("/api/sessions")
async def list_sessions(request: Request):
    """List sessions, optionally filtered by launch_id or source."""
    from charlie.session_store import SessionStore

    store = SessionStore(config.session_db_path)
    try:
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
    finally:
        store.close()


@app.post("/api/sessions")
async def create_session(data: dict):
    """Create a new session."""
    import uuid as _uuid

    from charlie.session_store import SessionStore

    session_id = data.get("session_id", str(_uuid.uuid4()))
    title = data.get("title", "New Chat")
    source = data.get("source", "web")
    launch_id = data.get("launch_id")
    store = SessionStore(config.session_db_path)
    try:
        store.create_session(session_id, title, source=source, launch_id=launch_id)
        return {
            "session_id": session_id,
            "title": title,
            "source": source,
            "launch_id": launch_id,
        }
    finally:
        store.close()


@app.get("/api/sessions/{session_id}/messages")
async def session_messages(session_id: str, limit: int = 50):
    """Get messages for a specific session."""
    from charlie.session_store import SessionStore

    store = SessionStore(config.session_db_path)
    try:
        messages = store.get_session_messages(session_id, limit=limit)
        return {"messages": [{"role": r, "content": c} for r, c in messages]}
    finally:
        store.close()


@app.put("/api/sessions/{session_id}")
async def update_session(session_id: str, data: dict):
    """Update session title."""
    from charlie.session_store import SessionStore

    title = data.get("title", "New Chat")
    store = SessionStore(config.session_db_path)
    try:
        store.update_session_title(session_id, title)
        # Broadcast title update to all connected WebSocket clients
        await broadcast(
            {
                "type": "session_update",
                "payload": {"session_id": session_id, "title": title},
            }
        )
        return {"session_id": session_id, "title": title}
    finally:
        store.close()


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a session and all its messages."""
    from charlie.session_store import SessionStore

    store = SessionStore(config.session_db_path)
    try:
        store.delete_session(session_id)
        await broadcast(
            {
                "type": "session_update",
                "payload": {"session_id": session_id, "deleted": True},
            }
        )
        return {"session_id": session_id, "deleted": True}
    finally:
        store.close()


# Static file serving for the React frontend
DIST_DIR = Path(__file__).parent.parent / "frontend" / "dist"
if DIST_DIR.exists():
    ASSETS_DIR = DIST_DIR / "assets"
    if ASSETS_DIR.exists():
        app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="static")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        file_path = DIST_DIR / full_path
        if file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(DIST_DIR / "index.html"))
else:

    @app.get("/")
    async def root():
        return {
            "message": "Charlie Web Dashboard",
            "status": "running",
            "note": "Frontend not built yet. Run: cd frontend && npm run build",
        }


def start_server(
    pub_port: int = DEFAULT_EVENT_PORT, pull_port: int = DEFAULT_COMMAND_PORT
):
    """Entry point for the web server subprocess."""
    import uvicorn

    logger.info("Starting web server on 127.0.0.1:8000")
    server_config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=8000,
        log_level="info",
        loop="asyncio",
    )
    server = uvicorn.Server(server_config)
    server.run()
