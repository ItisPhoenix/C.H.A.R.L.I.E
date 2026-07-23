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

# Same mirroring as mcp_client above: register_plugin_tools() previously ran
# only in main.py's voice process, so the web process's registry never had
# plugin_* tools even with PLUGINS_ENABLED=true -- /api/extensions needs a
# live manager here to enable/disable individual plugins at runtime.
plugin_manager = None
if config.plugins_enabled:
    try:
        from charlie.tools import register_plugin_tools

        plugin_manager = register_plugin_tools(config)
    except Exception as e:
        logger.warning("Web plugin subsystem failed to initialize: %s", e)
        plugin_manager = None
if plugin_manager is None:
    # Always give /api/extensions a manager to enable individual plugins
    # into, even when the blanket PLUGINS_ENABLED flag is off -- Phase 5's
    # per-plugin control is meant to work independently of that flag.
    from charlie.plugins import PluginManager

    plugin_manager = PluginManager()

# In-process registry of installed extensions (Phase 5) -- see
# charlie/extensions/__init__.py's ExtensionManager docstring for the
# propose()/confirm() gate this drives and the no-cross-restart-persistence
# caveat.
from charlie.extensions import ExtensionManager, InstalledExtension  # noqa: E402

_extension_manager = ExtensionManager()
_BUILTIN_PLUGIN_NAMES = ("filesystem", "browser", "calendar", "code_exec")


def _builtin_plugin(name: str):
    from charlie.plugins import BrowserPlugin, CalendarPlugin, CodeExecPlugin, FilesystemPlugin

    factories = {
        "filesystem": lambda: FilesystemPlugin(allowed_dirs=config.plugin_allow_dirs),
        "browser": BrowserPlugin,
        "calendar": CalendarPlugin,
        "code_exec": CodeExecPlugin,
    }
    if name not in factories:
        raise ValueError(
            f"Unknown built-in plugin '{name}'. Valid: {', '.join(_BUILTIN_PLUGIN_NAMES)}"
        )
    return factories[name]()


def _parsed_mcp_config(name: str, source: str, raw_text: str):
    """Parse an MCP server spec and require its name to match the
    extension's declared name, so `name` stays the single source of truth
    across propose/confirm/enable/disable for every extension kind."""
    from charlie.mcp_client import parse_server_spec

    cfg = parse_server_spec(raw_text or source)
    if cfg.name != name:
        raise ValueError(f"MCP spec name '{cfg.name}' does not match extension name '{name}'")
    return cfg


def _declared_tools_for(kind: str, name: str, source: str, raw_text: str) -> List[str]:
    """Parse (without registering) so propose() can show real declared
    tools in the SkillCard before anything activates."""
    if kind == "mcp":
        _parsed_mcp_config(name, source, raw_text)
        return []  # MCP tools aren't known until the server is actually started
    if kind == "skill":
        from charlie.extensions.skills import parse_skill_md

        return parse_skill_md(raw_text).scripts
    if kind == "openapi":
        from charlie.extensions.openapi_import import parse_openapi_spec

        return [op.operation_id for op in parse_openapi_spec(raw_text, base_url=source).operations]
    if kind == "plugin":
        return [t["name"] for t in _builtin_plugin(name).get_tools()]
    raise ValueError(f"Unknown extension kind '{kind}'")


def _install_extension(kind: str, name: str, source: str, raw_text: str) -> List[str]:
    """Parse and register an approved extension into the shared registry.
    Returns the registered tool names."""
    from charlie.tools import registry

    if kind == "mcp":
        global mcp_client
        from charlie.mcp_client import MCPClient

        cfg = _parsed_mcp_config(name, source, raw_text)
        if mcp_client is None:
            mcp_client = MCPClient()
        mcp_client.add_server(cfg)
        return mcp_client.enable_server(registry, name)
    if kind == "skill":
        from charlie.extensions.skills import parse_skill_md, register_skill_scripts

        manifest = parse_skill_md(raw_text)

        def _runner(script_path: str, args: List[str]) -> str:
            return f"Script execution not yet implemented: {script_path} {args}"

        return register_skill_scripts(registry, manifest, _runner)
    if kind == "openapi":
        from charlie.extensions.openapi_import import parse_openapi_spec, register_openapi_operations

        spec = parse_openapi_spec(raw_text, base_url=source)
        return register_openapi_operations(registry, spec)
    if kind == "plugin":
        from charlie.tools import enable_plugin

        return enable_plugin(registry, plugin_manager, _builtin_plugin(name))
    raise ValueError(f"Unknown extension kind '{kind}'")
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
    """Startup: init EventBus + ZMQ guard. Shutdown: tear down EventBus."""
    # --- startup ---
    global event_bus
    event_bus = EventBus(
        pub_port=DEFAULT_EVENT_PORT,
        pull_port=DEFAULT_COMMAND_PORT,
        is_producer=False,
    )
    await event_bus.__aenter__()
    asyncio.create_task(_event_bridge())
    logger.info("Web server started, event bridge active")

    # ZMQ guard — suppress CancelledError traceback on Windows shutdown
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
        await ws.send_text(json.dumps({"type": "blackboard_update", "payload": _blackboard_state}))
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
    return {
        "state": pipeline_state,
        "launch_id": LAUNCH_ID,
        "uptime_seconds": int(time.time() - _START_TIME),
        "pid": os.getpid(),
        "desktop_control_enabled": config.desktop_control_enabled,
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


@app.get("/api/extensions")
async def list_extensions():
    """List installed extensions across all four Phase 5 adapters."""
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
    return {"status": "ok", "installed": True, "tool_names": tool_names}


@app.post("/api/extensions/{name}/enable")
async def enable_extension(name: str):
    """Re-activate a disabled extension's tools without reinstalling it."""
    from charlie.tools import registry

    ext = _extension_manager.get(name)
    if ext is None:
        return {"status": "error", "message": f"Unknown extension '{name}'"}

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
    """Expose standard dashboard configurations."""
    return {
        "GPU_DEVICE": config.gpu_device,
        "KOKORO_LANG": config.kokoro_lang,
        "KOKORO_VOICE": config.kokoro_voice,
        "WHISPER_MODEL": config.whisper_model,
        "WAKE_WORD_ENABLED": config.wake_word_enabled,
        "BLACKBOARD_ENABLED": config.blackboard_enabled,
        "MCP_ENABLED": config.mcp_enabled,
        "PLUGINS_ENABLED": config.plugins_enabled,
        "MCP_SERVERS": config.mcp_servers,
        "PLUGIN_ALLOW_DIRS": config.plugin_allow_dirs,
    }


@app.post("/api/config")
async def update_dashboard_config(data: dict):
    """Update configurations both in-memory and in .env on disk."""
    try:
        env_updates = {}
        if "GPU_DEVICE" in data:
            config.gpu_device = str(data["GPU_DEVICE"])
            env_updates["GPU_DEVICE"] = config.gpu_device
        if "KOKORO_LANG" in data:
            config.kokoro_lang = str(data["KOKORO_LANG"])
            env_updates["KOKORO_LANG"] = config.kokoro_lang
        if "KOKORO_VOICE" in data:
            config.kokoro_voice = str(data["KOKORO_VOICE"])
            env_updates["KOKORO_VOICE"] = config.kokoro_voice
        if "WHISPER_MODEL" in data:
            config.whisper_model = str(data["WHISPER_MODEL"])
            env_updates["WHISPER_MODEL"] = config.whisper_model
        if "WAKE_WORD_ENABLED" in data:
            config.wake_word_enabled = bool(data["WAKE_WORD_ENABLED"])
            env_updates["WAKE_WORD_ENABLED"] = config.wake_word_enabled
        if "BLACKBOARD_ENABLED" in data:
            config.blackboard_enabled = bool(data["BLACKBOARD_ENABLED"])
            env_updates["BLACKBOARD_ENABLED"] = config.blackboard_enabled
        if "MCP_ENABLED" in data:
            config.mcp_enabled = bool(data["MCP_ENABLED"])
            env_updates["MCP_ENABLED"] = config.mcp_enabled
        if "PLUGINS_ENABLED" in data:
            config.plugins_enabled = bool(data["PLUGINS_ENABLED"])
            env_updates["PLUGINS_ENABLED"] = config.plugins_enabled
        if "MCP_SERVERS" in data:
            config.mcp_servers = list(data["MCP_SERVERS"])
            env_updates["MCP_SERVERS"] = config.mcp_servers
        if "PLUGIN_ALLOW_DIRS" in data:
            config.plugin_allow_dirs = list(data["PLUGIN_ALLOW_DIRS"])
            env_updates["PLUGIN_ALLOW_DIRS"] = config.plugin_allow_dirs

        if env_updates:
            _update_env_file(env_updates)

        return {"status": "ok", "config": await get_dashboard_config()}
    except Exception as e:
        logger.error(f"Error updating config: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


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
    return {"status": "error", "message": "Memory graph not available"}

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
