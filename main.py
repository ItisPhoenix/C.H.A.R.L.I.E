# ruff: noqa: E402, I001
import asyncio
import dataclasses
import errno
import http.client
import io
import json
import logging
import logging.handlers
import os
import re
import socket
import sys
import time
import threading
from typing import Any, Callable, Dict, Optional, Tuple

# Windows event-loop policy (must precede zmq/asyncio imports)
from charlie.runtime import configure as _configure_platform

_configure_platform()
import subprocess
import tempfile
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


if "pytest" not in sys.modules:
    if sys.platform == "win32":
        if hasattr(sys.stdout, "buffer"):
            sys.stdout = SafeStreamWrapper(
                io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True, write_through=True)
            )
        if hasattr(sys.stderr, "buffer"):
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
for _logger_name in ("httpcore", "httpx", "asyncio", "comtypes", "trafilatura"):
    logging.getLogger(_logger_name).setLevel(logging.WARNING)

# 3. NOW IMPORT CHARLIE MODULES
from charlie import background_task, telemetry
from charlie.errors import ErrorClass, classify_exception
from charlie.config import Config, config
from charlie.core import Brain
from charlie.presentation_control import PresentationRequest, get_presentation_controller
from charlie.surface_intent import match_surface_request
from charlie.events import EventMeta, EventSource, EventType
from charlie.ipc import EventBus
from charlie.memory_graph import MemoryGraph
from charlie.memory_service import MemoryService
from charlie.memory_store import MemoryStore
from charlie.personality import (
    get_emotion_for_context,
    parse_voice_command,
    parse_yes_no,
)
from charlie.session_store import SessionStore
from charlie.state import StateMachine
from charlie.subsystem_health import HealthRegistry, HealthStatus
from charlie.task_journal import TaskOrigin, TaskPriority, TaskStatus, get_task_journal
from charlie.turn_contracts import IntentDecision, ResultEnvelope, TurnRequest
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
_NON_CANCELLABLE_FOREGROUND_TOOLS = frozenset(
    {
        "file_write",
        "shell_execute",
        "memory",
        "vector_memory",
        "graph_add_fact",
        "graph_consolidate",
        "desktop_click",
        "desktop_click_at",
        "desktop_type",
        "desktop_invoke",
        "desktop_key",
        "desktop_move",
        "desktop_drag",
        "desktop_scroll",
        "desktop_focus",
        "desktop_window",
        "desktop_move_window",
        "start_background_task",
        "propose_new_tool",
    }
)
_LAUNCH_ID: str = str(uuid.uuid4())  # sidebar filters "this launch" vs "all history" by this
_state_machine = StateMachine()  # single authoritative CoreState instance for this process


def _allocate_turn_request(text: str, session_id: str, channel: str) -> TurnRequest:
    """Allocate one immutable request identity at normalized ingress."""

    return TurnRequest.allocate(text, session_id, channel)


def _watcher_surface_kind(level: AttentionLevel) -> tuple[PresentationKind, DismissPolicy, int | None, PreferredZone]:
    """Map authoritative watcher urgency to the matching non-tool HUD surface."""
    if level >= AttentionLevel.ATTENTION:
        return PresentationKind.ATTENTION, DismissPolicy.MANUAL, None, PreferredZone.CENTER
    return PresentationKind.NOTIFICATION, DismissPolicy.TIMED, 8000, PreferredZone.TOP_RIGHT


def _task_workspace_intent(task: Any) -> PresentationIntent:
    """Build canonical task workspace intent from runtime Task Journal record."""
    return PresentationIntent(
        id=f"task-workspace:{task.id}",
        kind=PresentationKind.WORKSPACE,
        task_id=task.id,
        title=f"TASK // {task.title}",
        summary=f"{task.status.value.upper()} // {task.id}",
        content={"task_id": task.id, "task": task.to_dict()},
        priority=70,
        dismiss_policy=DismissPolicy.MANUAL,
        workspace_type="tasks",
        preferred_zone=PreferredZone.CONTEXTUAL,
        anchor=AnchorTarget.CORE,
        replayable=True,
        replace_key=f"task-focus:{task.id}",
    )


def _task_workspace_admitted(task: Any) -> bool:
    """Return whether task has execution substance for a full workspace.

    Lifecycle status alone is not enough: fast-path and placeholder records can
    be active with zero steps and no meaningful execution detail.
    """
    total_steps = int(getattr(task, "total_steps", 0) or 0)
    if total_steps > 0:
        return True
    if str(getattr(task, "current_action", "") or "").strip():
        return True
    if str(getattr(task, "waiting_reason", "") or "").strip():
        return True
    if str(getattr(task, "approval_reference", "") or "").strip():
        return True
    if getattr(task, "capability_requirements", ()):
        return True
    return False


_runtime_health = HealthRegistry(
    (
        "brain",
        "memory",
        "plugins",
        "mcp",
        "web",
        "companion",
        "telegram",
        "voice",
        "voice_capture",
        "asr",
        "watchers",
    )
)


def _build_runtime_introspector(
    *,
    config: Any,
    capability_index: Any,
    mcp_client: Any,
    memory_service: Any = None,
) -> Any:
    """Compose introspection around this process's canonical runtime owners."""
    from charlie.resource_locks import get_capability_lease_manager
    from charlie.runtime_introspector import RuntimeIntrospector

    return RuntimeIntrospector(
        config=config,
        capability_index=capability_index,
        health_registry=_runtime_health,
        task_journal=get_task_journal(),
        lease_manager=get_capability_lease_manager(),
        mcp_client=mcp_client,
        memory_service=memory_service,
    )


hud_visible: bool = True
hud_client_count: int = 0
_main_event_bus: Optional[Any] = None


async def _summon_hud(toggle: bool = False, event_bus: Optional[Any] = None) -> None:
    """Show the React HUD without opening a workspace."""
    global hud_visible, hud_client_count
    from charlie.utils import open_url_in_browser

    if toggle:
        if hud_client_count == 0:
            # No actual HUD client exists.
            # Treat this as SUMMON, not hide.
            hud_visible = True
        else:
            hud_visible = not hud_visible
    elif not hud_visible:
        hud_visible = True

    host = "127.0.0.1" if config.charlie_host == "0.0.0.0" else config.charlie_host
    # Open HUD only if no browser client is already connected.
    # If already visible + connected, do NOT open another tab.
    if hud_visible:
        if hud_client_count == 0:
            open_url_in_browser(f"http://{host}:{config.charlie_port}/")
        # else: hud_client_count > 0 -> browser already connected, do not open another tab

    bus = event_bus or _main_event_bus
    if bus:
        await bus.emit(
            "hud_visibility",
            {"visible": hud_visible},
            meta=EventMeta(source=EventSource.SURFACE, rationale="pet or hotkey summoned React HUD"),
        )


async def _open_conversation_workspace(event_bus: Optional[Any] = None) -> None:
    """Ensure HUD visibility, then open the canonical conversation workspace."""
    await _summon_hud(event_bus=event_bus)
    bus = event_bus or _main_event_bus
    if bus:
        await bus.emit(
            "presentation_intent",
            PresentationIntent(
                id="conversation-workspace",
                kind=PresentationKind.WORKSPACE,
                title="CONVERSATION",
                summary="Chat Session",
                priority=80,
                dismiss_policy=DismissPolicy.PERSISTENT,
                workspace_type="conversation",
                preferred_zone=PreferredZone.CENTER,
                anchor=AnchorTarget.SCREEN,
                replace_key="workspace:conversation",
                replayable=True,
            ).to_dict(),
            meta=EventMeta(source=EventSource.SURFACE, rationale="operator opened conversation workspace"),
        )


async def _publish_subsystem_health(bus: Optional[EventBus] = None) -> None:
    """Publish current public health snapshot when the IPC producer exists."""
    if bus is None:
        return
    event = _runtime_health.event()
    await bus.emit(event["type"], event["payload"], meta=EventMeta(source=EventSource.VOICE))


async def _publish_task_snapshot(bus: Optional[EventBus] = None) -> None:
    """Publish the canonical main-process task journal as a safe public snapshot."""
    if bus is None:
        return
    tasks = [background_task._public_event_from_record(record) for record in get_task_journal().list()]
    await bus.emit(
        EventType.TASK_SNAPSHOT.value,
        {"tasks": tasks},
        meta=EventMeta(source=EventSource.TASK),
    )


def _build_tool_snapshot(tool_registry: Any = None) -> dict[str, Any]:
    """Build a safe public roster from the registry that Brain actually executes."""
    if tool_registry is None:
        from charlie.tools import registry as tool_registry

    metadata = tool_registry.list_metadata()
    if not isinstance(metadata, list):
        raise ValueError("Main tool registry returned an invalid metadata list.")

    tools: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for item in metadata:
        if not isinstance(item, dict):
            raise ValueError("Main tool registry returned invalid tool metadata.")
        name = item.get("name")
        if not isinstance(name, str) or not name.strip() or name in seen_names:
            raise ValueError("Main tool registry returned invalid or duplicate tool names.")
        seen_names.add(name)

        safe_item: dict[str, Any] = {"name": name}
        for key in ("description", "owner"):
            if key in item:
                value = item[key]
                if not isinstance(value, str):
                    raise ValueError(f"Main tool registry returned invalid {key} metadata.")
                safe_item[key] = value
        if "risk_class" in item:
            risk_class = item["risk_class"]
            if risk_class is not None and not isinstance(risk_class, str):
                raise ValueError("Main tool registry returned invalid risk metadata.")
            safe_item["risk_class"] = risk_class
        tools.append(safe_item)

    return {"authority": "main_runtime", "tools": tools}


async def _publish_tool_snapshot(
    bus: Optional[EventBus] = None,
    tool_registry: Any = None,
) -> None:
    """Publish the current main-owned executable-tool projection over IPC."""
    if bus is None:
        return
    await bus.emit(
        EventType.TOOL_SNAPSHOT.value,
        _build_tool_snapshot(tool_registry),
        meta=EventMeta(
            source=EventSource.RUNTIME,
            rationale="authoritative main-runtime tool registry snapshot",
        ),
    )


_MCP_SENSITIVE_ARGUMENT_MARKERS = frozenset(
    {
        "api-key",
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "credential",
        "env",
        "header",
        "headers",
        "password",
        "private-key",
        "private_key",
        "secret",
        "token",
    }
)


def _sanitize_mcp_args(args: Any) -> list[str]:
    """Keep public MCP command arguments useful without exposing secrets."""
    if args is None:
        return []
    if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
        raise ValueError("MCP server arguments must be a list of strings.")

    safe_args: list[str] = []
    redact_next = False
    for arg in args:
        lowered = arg.strip().lower()
        if redact_next:
            safe_args.append("<redacted>")
            redact_next = False
            continue
        if "=" in arg:
            option, _value = arg.split("=", 1)
            marker = option.lstrip("-").strip().lower()
            if marker in _MCP_SENSITIVE_ARGUMENT_MARKERS:
                safe_args.append(f"{option}=<redacted>")
                continue
        marker = lowered.lstrip("-")
        if marker in _MCP_SENSITIVE_ARGUMENT_MARKERS:
            safe_args.append(arg)
            redact_next = True
            continue
        if "authorization:" in lowered or lowered.startswith("bearer "):
            safe_args.append("<redacted>")
            continue
        safe_args.append(arg)
    return safe_args


def _build_mcp_snapshot(mcp_client: Any = None, *, enabled: Optional[bool] = None) -> dict[str, Any]:
    """Build a safe MCP projection from main's canonical MCP client only."""
    detailed = [] if mcp_client is None else mcp_client.list_servers_detailed()
    if not isinstance(detailed, list):
        raise ValueError("Main MCP client returned an invalid server list.")

    servers: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for item in detailed:
        if not isinstance(item, dict):
            raise ValueError("Main MCP client returned invalid server metadata.")
        name = item.get("name")
        if not isinstance(name, str) or not name.strip() or name in seen_names:
            raise ValueError("Main MCP client returned invalid or duplicate server names.")
        running = item.get("running")
        if type(running) is not bool:
            raise ValueError("Main MCP client returned invalid server running state.")
        status = item.get("status")
        if not isinstance(status, str) or not status.strip():
            raise ValueError("Main MCP client returned invalid server status.")

        raw_tools = item.get("tools", [])
        if not isinstance(raw_tools, list):
            raise ValueError("Main MCP client returned invalid server tools.")
        tools: list[dict[str, str]] = []
        seen_tool_names: set[str] = set()
        for tool in raw_tools:
            if not isinstance(tool, dict):
                raise ValueError("Main MCP client returned invalid tool metadata.")
            tool_name = tool.get("name")
            description = tool.get("description", "")
            if (
                not isinstance(tool_name, str)
                or not tool_name.strip()
                or tool_name in seen_tool_names
                or not isinstance(description, str)
            ):
                raise ValueError("Main MCP client returned invalid or duplicate tool metadata.")
            seen_tool_names.add(tool_name)
            tools.append({"name": tool_name, "description": description})

        command = item.get("command", "")
        if not isinstance(command, str):
            raise ValueError("Main MCP client returned invalid server command.")
        servers.append(
            {
                "name": name,
                "command": command,
                "args": _sanitize_mcp_args(item.get("args", [])),
                "running": running,
                "status": status,
                "tools_count": len(tools),
                "tools": tools,
            }
        )
        seen_names.add(name)

    if enabled is None:
        enabled = bool(getattr(config, "mcp_enabled", False))
    return {"authority": "main_runtime", "enabled": bool(enabled), "servers": servers}


async def _publish_mcp_snapshot(
    bus: Optional[EventBus] = None,
    mcp_client: Any = None,
    *,
    enabled: Optional[bool] = None,
) -> None:
    """Publish current main-owned MCP state over IPC."""
    if bus is None:
        return
    await bus.emit(
        EventType.MCP_SNAPSHOT.value,
        _build_mcp_snapshot(mcp_client, enabled=enabled),
        meta=EventMeta(
            source=EventSource.RUNTIME,
            rationale="authoritative main-runtime MCP snapshot",
        ),
    )


async def _publish_runtime_state(bus: Optional[EventBus] = None, mcp_client: Any = None) -> None:
    """Replay public operational state owned by this main process."""
    await _publish_subsystem_health(bus)
    await _publish_task_snapshot(bus)
    await _publish_tool_snapshot(bus)
    await _publish_mcp_snapshot(bus, mcp_client)


async def _dispatch_web_command(
    cmd: dict,
    bus: Optional[EventBus] = None,
    mcp_client: Any = None,
) -> bool:
    """Dispatch the runtime-state command from the live web command consumer."""
    if not isinstance(cmd, dict) or cmd.get("type") != "runtime_state_request":
        return False
    await _publish_runtime_state(bus, mcp_client)
    return True


async def _handle_runtime_state_request(
    cmd_type: str,
    bus: Optional[EventBus] = None,
    mcp_client: Any = None,
) -> bool:
    """Handle the command-loop runtime replay branch and report whether it matched."""
    return await _dispatch_web_command({"type": cmd_type}, bus, mcp_client)


def _extension_operation_result(
    payload: dict[str, Any],
    *,
    success: bool,
    tool_names: list[str],
    error: Optional[str] = None,
) -> dict[str, Any]:
    result = {
        "request_id": str(payload.get("request_id", "")),
        "operation": str(payload.get("operation", "")),
        "kind": str(payload.get("kind", "")),
        "name": str(payload.get("name", "")),
        "success": success,
        "tool_names": list(tool_names),
    }
    if error:
        result["error"] = error[:500]
    return result


def apply_extension_operation(
    payload: dict[str, Any],
    *,
    brain: Any,
    plugin_manager: Any,
    mcp_client: Any,
    runtime_config: Any,
    tool_registry: Any = None,
) -> tuple[dict[str, Any], Any]:
    """Apply one extension transition against main's live runtime owners."""
    if not isinstance(payload, dict):
        payload = {}
    operation = payload.get("operation")
    kind = payload.get("kind")
    name = payload.get("name")
    allowed_operations = {"install", "enable", "disable", "uninstall"}
    allowed_kinds = {"mcp", "skill", "openapi", "plugin", "generated"}
    if (
        not isinstance(payload.get("request_id"), str)
        or not payload["request_id"]
        or not isinstance(operation, str)
        or operation not in allowed_operations
        or not isinstance(kind, str)
        or kind not in allowed_kinds
        or not isinstance(name, str)
        or not name
    ):
        return (
            _extension_operation_result(
                payload,
                success=False,
                tool_names=[],
                error="Invalid extension operation request.",
            ),
            mcp_client,
        )

    known_tool_names = payload.get("tool_names", [])
    if not isinstance(known_tool_names, list) or any(not isinstance(item, str) for item in known_tool_names):
        return (
            _extension_operation_result(
                payload,
                success=False,
                tool_names=[],
                error="Invalid extension tool names.",
            ),
            mcp_client,
        )

    if tool_registry is None:
        from charlie.tools import registry as tool_registry

    try:
        if operation == "install":
            from charlie.extensions.install import install_extension

            tool_names, mcp_client = install_extension(
                kind,
                name,
                str(payload.get("source", "")),
                str(payload.get("raw_text", "")),
                registry=tool_registry,
                plugin_manager=plugin_manager,
                mcp_client=mcp_client,
                plugin_allow_dirs=list(getattr(runtime_config, "plugin_allow_dirs", []) or []),
            )
            if kind == "skill":
                from charlie.extensions.skills import format_skill_block, parse_skill_md

                manifest = parse_skill_md(str(payload.get("raw_text", "")))
                brain.add_installed_skill_block(name, format_skill_block(manifest))
        elif operation == "enable":
            if kind == "mcp":
                if mcp_client is None:
                    raise RuntimeError("Main MCP client is unavailable.")
                tool_names = mcp_client.enable_server(tool_registry, name)
            elif kind == "plugin":
                from charlie.extensions.install import builtin_plugin
                from charlie.tools import enable_plugin

                tool_names = enable_plugin(
                    tool_registry,
                    plugin_manager,
                    builtin_plugin(name, list(getattr(runtime_config, "plugin_allow_dirs", []) or [])),
                )
            else:
                # Skill/OpenAPI/generated adapters retain their registered
                # tools while disabled; main still owns the lifecycle ACK and
                # stable-tier rebuild for these transitions.
                tool_names = list(known_tool_names)
        elif operation == "disable":
            if kind == "mcp":
                if mcp_client is None or not mcp_client.disable_server(tool_registry, name):
                    raise KeyError(f"MCP server '{name}' is not registered in main runtime.")
            elif kind == "plugin":
                from charlie.tools import disable_plugin

                if plugin_manager.get_plugin(name) is None:
                    raise KeyError(f"Plugin '{name}' is not registered in main runtime.")
                disable_plugin(tool_registry, plugin_manager, name)
            # Skill/OpenAPI/generated tools remain registered by design.
            tool_names = list(known_tool_names)
        else:  # uninstall
            if kind == "mcp":
                if mcp_client is None or not mcp_client.remove_server(tool_registry, name):
                    raise KeyError(f"MCP server '{name}' is not registered in main runtime.")
            elif kind == "plugin":
                from charlie.tools import disable_plugin

                if plugin_manager.get_plugin(name) is None:
                    raise KeyError(f"Plugin '{name}' is not registered in main runtime.")
                disable_plugin(tool_registry, plugin_manager, name)
            else:
                for tool_name in known_tool_names:
                    tool_registry.unregister_tool(tool_name)
                if kind == "skill":
                    brain.remove_installed_skill_block(name)
            tool_names = []

        if not isinstance(tool_names, list) or any(not isinstance(item, str) for item in tool_names):
            raise ValueError("Extension owner returned invalid tool names.")
        brain.rebuild_stable_tier()
        return (
            _extension_operation_result(
                payload,
                success=True,
                tool_names=list(tool_names),
            ),
            mcp_client,
        )
    except Exception as exc:
        reason = str(exc).strip() or type(exc).__name__
        logger.warning("Main extension operation failed: %s", reason)
        return (
            _extension_operation_result(
                payload,
                success=False,
                tool_names=[],
                error=reason,
            ),
            mcp_client,
        )


def _mcp_operation_result(
    payload: dict[str, Any],
    *,
    success: bool,
    error: Optional[str] = None,
) -> dict[str, Any]:
    """Build the safe correlated acknowledgement for one MCP command."""
    result = {
        "request_id": str(payload.get("request_id", "")),
        "operation": str(payload.get("operation", "")),
        "success": success,
        "server_name": str(payload.get("server_name", "")),
    }
    if error:
        result["error"] = error[:500]
    return result


def apply_mcp_operation(
    payload: dict[str, Any],
    *,
    mcp_client: Any,
    brain: Any = None,
    tool_registry: Any = None,
) -> tuple[dict[str, Any], Any]:
    """Apply one MCP transition against main's canonical client and registry."""
    if not isinstance(payload, dict):
        payload = {}
    operation = payload.get("operation")
    server_name = payload.get("server_name")
    allowed_operations = {"add", "connect", "disconnect", "restart", "delete"}
    if (
        not isinstance(payload.get("request_id"), str)
        or not payload["request_id"]
        or not isinstance(operation, str)
        or operation not in allowed_operations
        or not isinstance(server_name, str)
        or not server_name.strip()
    ):
        return (
            _mcp_operation_result(
                payload,
                success=False,
                error="Invalid MCP operation request.",
            ),
            mcp_client,
        )

    command = payload.get("command")
    args = payload.get("args", [])
    if operation == "add" and (
        not isinstance(command, str)
        or not command.strip()
        or not isinstance(args, list)
        or any(not isinstance(arg, str) for arg in args)
    ):
        return (
            _mcp_operation_result(
                payload,
                success=False,
                error="MCP add requires a command and a list of string arguments.",
            ),
            mcp_client,
        )

    try:
        if operation == "add":
            from charlie.mcp_client import MCPClient, MCPServerConfig

            if mcp_client is None:
                mcp_client = MCPClient()
            existing = {
                item.get("name")
                for item in mcp_client.list_servers_detailed()
                if isinstance(item, dict)
            }
            if server_name in existing:
                raise ValueError(f"MCP server '{server_name}' is already registered.")
            mcp_client.add_server(
                MCPServerConfig(
                    name=server_name,
                    command=command.strip(),
                    args=list(args),
                )
            )
        else:
            if mcp_client is None:
                raise RuntimeError("Main MCP client is unavailable.")
            if tool_registry is None:
                from charlie.tools import registry as tool_registry
            if operation == "connect":
                mcp_client.enable_server(tool_registry, server_name)
            elif operation == "disconnect":
                if not mcp_client.disable_server(tool_registry, server_name):
                    raise KeyError(f"MCP server '{server_name}' is not registered in main runtime.")
            elif operation == "restart":
                if not mcp_client.restart_server(tool_registry, server_name):
                    raise KeyError(f"MCP server '{server_name}' is not registered in main runtime.")
            else:  # delete
                if not mcp_client.remove_server(tool_registry, server_name):
                    raise KeyError(f"MCP server '{server_name}' is not registered in main runtime.")
            if brain is not None:
                brain.rebuild_stable_tier()
        return _mcp_operation_result(payload, success=True), mcp_client
    except Exception as exc:
        reason = str(exc).strip() or type(exc).__name__
        logger.warning("Main MCP operation failed: %s", reason)
        return (
            _mcp_operation_result(payload, success=False, error=reason),
            mcp_client,
        )


def _try_build_mcp_snapshot(mcp_client: Any) -> Optional[dict[str, Any]]:
    """Build a snapshot for operation accounting without inventing state."""
    try:
        return _build_mcp_snapshot(mcp_client)
    except Exception as exc:
        logger.error("Could not build authoritative MCP snapshot: %s", exc, exc_info=True)
        return None


async def _dispatch_mcp_operation(
    payload: dict[str, Any],
    bus: Optional[EventBus],
    *,
    mcp_client: Any,
    brain: Any,
    tool_registry: Any = None,
) -> tuple[dict[str, Any], Any]:
    """Run one MCP command, publish truthful projections, then emit its ACK."""
    if tool_registry is None:
        from charlie.tools import registry as tool_registry

    before_tools = _build_tool_snapshot(tool_registry)
    before_mcp = _try_build_mcp_snapshot(mcp_client)
    result, updated_client = apply_mcp_operation(
        payload,
        mcp_client=mcp_client,
        brain=brain,
        tool_registry=tool_registry,
    )
    after_tools = _build_tool_snapshot(tool_registry)
    after_mcp = _try_build_mcp_snapshot(updated_client)
    operation = result.get("operation")
    tools_changed = before_tools is None or after_tools != before_tools
    mcp_changed = before_mcp is None or after_mcp != before_mcp

    if result.get("success") is True and after_mcp is None:
        result = _mcp_operation_result(
            payload,
            success=False,
            error="MCP operation changed runtime but its authoritative snapshot could not be built.",
        )
        result["partial"] = True

    if result.get("success") is True:
        if operation in {"connect", "disconnect", "restart"}:
            await _publish_tool_snapshot(bus, tool_registry)
        elif operation == "delete" and tools_changed:
            await _publish_tool_snapshot(bus, tool_registry)
        await _publish_mcp_snapshot(bus, updated_client)
    else:
        # Failed operations normally leave both projections untouched. If a
        # client partially mutated before raising, publish the truthful state
        # before the correlated failure acknowledgement.
        if tools_changed:
            await _publish_tool_snapshot(bus, tool_registry)
        if mcp_changed and after_mcp is not None:
            await _publish_mcp_snapshot(bus, updated_client)
        if tools_changed or mcp_changed:
            result["partial"] = True

    if bus is not None:
        await bus.emit(
            EventType.MCP_OPERATION_RESULT.value,
            result,
            meta=EventMeta(
                source=EventSource.BRAIN,
                rationale="authoritative main-runtime MCP operation result",
            ),
        )
    return result, updated_client


def _set_subsystem_health(name: str, status: HealthStatus, public_detail: Optional[str] = None) -> None:
    """Record one safe public subsystem transition."""
    _runtime_health.set(name, status, public_detail=public_detail)


def _companion_dependency_status() -> tuple[bool, Optional[str]]:
    """Check optional Qt dependency before spawning the companion process."""
    try:
        from charlie.pet_window import QT_AVAILABLE, QT_IMPORT_ERROR
    except Exception as exc:
        return False, f"Companion dependency initialization failed: {type(exc).__name__}"
    if QT_AVAILABLE:
        return True, None
    reason = str(QT_IMPORT_ERROR) if QT_IMPORT_ERROR else "Qt binding unavailable"
    return False, f"Optional companion dependency unavailable: {reason}"


async def _monitor_companion_readiness(process: subprocess.Popen, ready_file: Path, event_bus: Any) -> None:
    """Publish companion readiness only after child initialization signals success."""
    deadline = time.monotonic() + 10.0
    try:
        while time.monotonic() < deadline:
            if ready_file.is_file():
                _set_subsystem_health("companion", HealthStatus.RUNNING, "Ready")
                await _publish_subsystem_health(event_bus)
                return
            exit_code = process.poll()
            if exit_code is not None:
                _set_subsystem_health(
                    "companion",
                    HealthStatus.DEGRADED,
                    f"Companion exited before readiness (exit code {exit_code})",
                )
                await _publish_subsystem_health(event_bus)
                return
            await asyncio.sleep(0.1)
        _set_subsystem_health("companion", HealthStatus.DEGRADED, "Companion readiness timed out")
        await _publish_subsystem_health(event_bus)
    finally:
        try:
            ready_file.unlink(missing_ok=True)
        except OSError:
            logger.debug("Unable to remove companion readiness marker", exc_info=True)


def _start_subsystem_process(
    name: str,
    command: Tuple[str, ...],
    env: Optional[Dict[str, str]] = None,
    readiness_file: Optional[Path] = None,
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
    _set_subsystem_health(name, HealthStatus.STARTING if readiness_file else HealthStatus.RUNNING)
    return process


_WEB_STARTUP_TIMEOUT_SECONDS = 10.0
_WEB_PROBE_TIMEOUT_SECONDS = 0.5


def _web_probe_host(host: str) -> str:
    """Use a loopback probe address for the configured local web host."""
    normalized = host.strip()
    return "127.0.0.1" if normalized.lower() == "localhost" else normalized


def _web_port_is_listening(host: str, port: int) -> bool:
    """Return whether a TCP listener currently owns the configured web port."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((_web_probe_host(host), port))
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE or getattr(exc, "winerror", None) == 10048:
            return True
        raise RuntimeError(
            f"Unable to determine whether Charlie web port {port} is available."
        ) from exc
    finally:
        probe.close()
    return False


def _fetch_web_status(host: str, port: int) -> Optional[dict[str, Any]]:
    """Read the local runtime identity endpoint without using proxy settings."""
    connection: Optional[http.client.HTTPConnection] = None
    try:
        connection = http.client.HTTPConnection(
            _web_probe_host(host),
            port,
            timeout=_WEB_PROBE_TIMEOUT_SECONDS,
        )
        connection.request("GET", "/api/status", headers={"Accept": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            return None
        payload = json.loads(response.read(64 * 1024).decode("utf-8"))
        return payload if isinstance(payload, dict) else None
    except (http.client.HTTPException, OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    finally:
        if connection is not None:
            connection.close()


def _expected_frontend_build_identity() -> Optional[dict[str, Any]]:
    """Read the build identity that the child web process must serve."""
    configured_dist = os.environ.get("CHARLIE_FRONTEND_DIST")
    dist = Path(configured_dist) if configured_dist else Path(__file__).parent / "frontend" / "dist"
    try:
        manifest = json.loads((dist / "charlie-build.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return manifest if isinstance(manifest, dict) else None


def _web_process_owns_pid(process: subprocess.Popen, reported_pid: Any) -> bool:
    """Accept the launcher PID or the real interpreter child PID on Windows."""
    try:
        reported_pid = int(reported_pid)
    except (TypeError, ValueError):
        return False
    if reported_pid == process.pid:
        return True

    try:
        import psutil

        return any(child.pid == reported_pid for child in psutil.Process(process.pid).children(recursive=True))
    except (psutil.Error, OSError, ValueError):
        return False


def _web_identity_error(
    status: Optional[dict[str, Any]],
    process: subprocess.Popen,
    launch_id: str,
    expected_build: Optional[dict[str, Any]],
) -> Optional[str]:
    """Return a safe explanation when a ready response is not this launch."""
    if not isinstance(status, dict):
        return "Charlie web runtime returned no valid identity response."
    if status.get("launch_id") != launch_id:
        return "Charlie web runtime launch identity mismatch; refusing to attach to an unknown or stale HUD."
    if not _web_process_owns_pid(process, status.get("pid")):
        return "Charlie web runtime process identity mismatch; refusing to attach to an unexpected HUD process."
    frontend_build = status.get("frontend_build")
    if not isinstance(frontend_build, dict):
        return "Charlie web runtime did not report a frontend build identity."
    if status.get("source_identity") != frontend_build.get("git_sha"):
        return "Charlie web runtime source identity is inconsistent with its frontend build."
    if expected_build is not None:
        for key in ("build_id", "input_fingerprint", "git_sha", "dirty"):
            if key in expected_build and frontend_build.get(key) != expected_build[key]:
                return f"Charlie web runtime frontend build identity mismatch ({key})."
    return None


def _terminate_subsystem_process(process: subprocess.Popen) -> None:
    """Stop one child process without affecting any unrelated process."""
    try:
        process.terminate()
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    except (OSError, AttributeError):
        logger.debug("Unable to terminate failed subsystem child", exc_info=True)


def _log_port_release(host: str, port: int) -> None:
    try:
        released = not _web_port_is_listening(host, port)
        logger.info("port_release | port=%s | released=%s", port, released)
    except Exception as exc:
        logger.warning(
            "port_release | port=%s | released=unknown | error=%s",
            port,
            type(exc).__name__,
        )


def _start_web_subprocess(
    command: Tuple[str, ...],
    env: Dict[str, str],
    *,
    host: str,
    port: int,
    launch_id: str,
    startup_timeout: float = _WEB_STARTUP_TIMEOUT_SECONDS,
) -> subprocess.Popen:
    """Start only this launch's HUD and require its identity before continuing."""
    if _web_port_is_listening(host, port):
        existing_status = _fetch_web_status(host, port)
        if isinstance(existing_status, dict) and existing_status.get("launch_id"):
            message = (
                f"Port {port} is occupied by another Charlie runtime. "
                "Stop the existing runtime before starting a new one."
            )
        else:
            message = f"Port {port} is occupied by another process. Stop it before starting Charlie."
        _set_subsystem_health("web", HealthStatus.DEGRADED, message)
        raise RuntimeError(message)

    try:
        process = subprocess.Popen(command, cwd=os.path.dirname(__file__), env=env)
    except Exception as exc:
        message = f"Charlie web subprocess could not start: {type(exc).__name__}."
        _set_subsystem_health("web", HealthStatus.DEGRADED, message)
        raise RuntimeError(message) from exc

    _set_subsystem_health("web", HealthStatus.STARTING, "Waiting for owned web runtime")
    expected_build = _expected_frontend_build_identity()
    deadline = time.monotonic() + startup_timeout
    try:
        while time.monotonic() < deadline:
            exit_code = process.poll()
            if exit_code is not None:
                message = f"Charlie web subprocess exited before readiness (exit code {exit_code})."
                _set_subsystem_health("web", HealthStatus.DEGRADED, message)
                raise RuntimeError(message)

            status = _fetch_web_status(host, port)
            if status is not None:
                identity_error = _web_identity_error(status, process, launch_id, expected_build)
                if identity_error is not None:
                    _set_subsystem_health("web", HealthStatus.DEGRADED, identity_error)
                    raise RuntimeError(identity_error)
                _set_subsystem_health("web", HealthStatus.RUNNING, "Ready")
                logger.info("Owned web runtime ready (PID: %s, launch_id=%s)", process.pid, launch_id)
                return process
            time.sleep(0.1)
    except Exception:
        _terminate_subsystem_process(process)
        raise

    message = f"Charlie web subprocess did not become ready within {startup_timeout:.1f}s."
    _set_subsystem_health("web", HealthStatus.DEGRADED, message)
    _terminate_subsystem_process(process)
    raise RuntimeError(message)


class _UnavailableVoiceEngine:
    """No-op voice replacement that keeps non-voice Charlie features available."""

    is_available = False
    is_ready = False
    asr_ready = False
    asr_readiness_status = "failed"

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

    def asr_readiness_detail(self) -> str:
        return "ASR unavailable: microphone engine unavailable"


def _start_voice_or_degrade(
    voice_config: Config,
    on_speech: Callable[[str], None],
    on_tts_start: Callable[[], None],
    on_tts_stop: Callable[[], None],
    on_speech_onset: Optional[Callable[..., None]] = None,
) -> VoiceEngine | _UnavailableVoiceEngine:
    """Start voice or retain text and web operation after a voice failure."""
    try:
        voice = VoiceEngine(
            voice_config,
            on_speech=on_speech,
            on_tts_start=on_tts_start,
            on_tts_stop=on_tts_stop,
            on_speech_onset=on_speech_onset,
        )
        voice.start()
    except Exception:
        logger.warning("Failed to start voice", exc_info=True)
        _set_subsystem_health("voice", HealthStatus.DEGRADED)
        _set_subsystem_health("voice_capture", HealthStatus.DEGRADED)
        _set_subsystem_health("asr", HealthStatus.DEGRADED, "ASR unavailable: microphone engine unavailable")
        return _UnavailableVoiceEngine()
    if voice.is_ready:
        _set_subsystem_health("voice", HealthStatus.RUNNING, voice.readiness_detail())
        _set_subsystem_health("voice_capture", HealthStatus.RUNNING, voice.readiness_detail())
        _set_subsystem_health("asr", HealthStatus.STARTING, voice.asr_readiness_detail())
    else:
        _set_subsystem_health("voice", HealthStatus.DEGRADED, voice.readiness_detail())
        _set_subsystem_health("voice_capture", HealthStatus.DEGRADED, voice.readiness_detail())
        _set_subsystem_health("asr", HealthStatus.DEGRADED, "ASR unavailable: microphone capture unavailable")
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
    text = re.sub(r"\[S\d+\]", "", text or "").replace("  ", " ").strip()
    if not text:
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


async def _apply_voice_control(
    control: str,
    *,
    voice: Any,
    brain: Any,
    active_turn: bool,
    active_operation_cancellable: bool,
    active_process_task: Optional[asyncio.Task],
    cancel_housekeeping: Callable[[], None],
) -> bool:
    """Apply one exact realtime control without entering the conversational path."""
    tts_active = bool(getattr(voice, "is_speaking", None) and voice.is_speaking.is_set())
    if not active_turn and not tts_active:
        logger.info("voice_control_ignored | control=%s | reason=no_active_work", control)
        return False

    voice.stop_tts()
    if not active_turn:
        logger.info("voice_control_applied | control=%s | action=stop_tts", control)
        return True
    if not active_operation_cancellable:
        logger.info(
            "voice_control_applied | control=%s | action=stop_tts | "
            "foreground_operation=non_cancellable",
            control,
        )
        return True

    cancel_housekeeping()
    brain.cancel_chat()
    if active_process_task is not None and not active_process_task.done():
        active_process_task.cancel()
        try:
            await active_process_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("Voice control cancellation task exited with an error", exc_info=True)
    logger.info("voice_control_applied | control=%s | action=cancel_foreground", control)
    return True


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


def _semantic_memory_expected(runtime_config: Config) -> bool:
    """Treat an explicitly configured embedding service as an expected component."""
    return bool(str(getattr(runtime_config, "memory_embedding_url", "") or "").strip())


def _set_memory_health_from_service(memory_service: MemoryService) -> None:
    """Project canonical memory health into the runtime HealthRegistry."""
    health = memory_service.get_health()
    status = health.get("status")
    if status == "available":
        _set_subsystem_health("memory", HealthStatus.RUNNING)
    elif status == "degraded":
        _set_subsystem_health("memory", HealthStatus.DEGRADED)
    elif status == "unavailable":
        _set_subsystem_health("memory", HealthStatus.STOPPED)
    else:
        _set_subsystem_health("memory", HealthStatus.DEGRADED)


def _compose_memory_dependencies(runtime_config: Config) -> tuple[MemoryGraph, Optional[MemoryStore], MemoryService]:
    """Compose process-owned long-term memory adapters and facade.

    The graph is required for the main process, matching Brain's existing
    fail-fast construction behavior. Vector memory remains optional and keeps
    its existing graceful-degradation semantics.
    """
    try:
        memory_graph = MemoryGraph(runtime_config.memory_graph_db)
    except Exception:
        logger.error("Failed to initialize knowledge graph memory", exc_info=True)
        raise

    memory_store = None
    semantic_expected = _semantic_memory_expected(runtime_config)
    try:
        memory_store = MemoryStore(runtime_config)
    except Exception as e:
        logger.warning(f"Vector memory disabled: {e}")
        # A failed construction is an attempted required adapter, even when
        # no object can be handed to the facade for later health inspection.
        semantic_expected = True

    memory_service = MemoryService(
        graph=memory_graph,
        memory_store=memory_store,
        semantic_expected=semantic_expected,
    )
    _set_memory_health_from_service(memory_service)
    return memory_graph, memory_store, memory_service


def _wire_memory_service(memory_service: MemoryService) -> None:
    """Wire the process-composed memory facade into the tool registry."""
    from charlie.tools import registry as tool_registry

    tool_registry.set_memory_service(memory_service)


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
    mcp_client = None
    mcp_start_task = None
    companion_ready_file: Optional[Path] = None
    companion_monitor_task: Optional[asyncio.Task] = None
    exit_code = 0
    # True while a chat turn's LLM/tool loop runs -- see _dispatch_or_queue.
    turn_active = False
    pending_turns: list[TurnRequest] = []
    pending_turn_times: Dict[str, float] = {}
    voice_diagnostic_traces: Dict[str, Any] = {}
    active_turn_id: Optional[str] = None
    active_task_id: Optional[str] = None
    active_process_task: Optional[asyncio.Task] = None
    active_operation_name: Optional[str] = None
    active_operation_task_id: Optional[str] = None
    active_operation_cancellable = True
    background_housekeeping_tasks: set[asyncio.Task] = set()

    try:
        store = SessionStore(config.session_db_path)
    except Exception as e:
        logger.error(f"Failed to initialize SessionStore: {e}")
        return
    from charlie.audit_store import AuditStore

    audit_store = AuditStore(config.session_db_path)
    try:
        memory_graph, memory_store, memory_service = _compose_memory_dependencies(config)
    except Exception:
        # MemoryGraph was previously required by Brain construction. Stop
        # startup explicitly rather than substituting a fabricated graph.
        audit_store.close()
        if store:
            store.close()
        return

    def speaking_callback(text):
        if voice:
            voice.speak(text, last_emotion)

    loop = asyncio.get_running_loop()

    def _schedule_housekeeping(coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        background_housekeeping_tasks.add(task)

        def _finished(completed: asyncio.Task) -> None:
            background_housekeeping_tasks.discard(completed)
            if completed.cancelled():
                return
            try:
                error = completed.exception()
            except asyncio.CancelledError:
                return
            if error is not None:
                logger.debug("Background voice housekeeping failed: %s", error)

        task.add_done_callback(_finished)
        return task

    def _cancel_housekeeping() -> None:
        for task in tuple(background_housekeeping_tasks):
            task.cancel()
        try:
            brain.cancel_background_tasks()
        except NameError:
            pass

    def on_tool_call(name, args, *, turn_id=None, task_id=None, session_id=None):
        nonlocal active_operation_name, active_operation_task_id, active_operation_cancellable
        if task_id == active_task_id:
            active_operation_name = name
            active_operation_task_id = task_id
            active_operation_cancellable = name not in _NON_CANCELLABLE_FOREGROUND_TOOLS
        event_session_id = session_id or current_web_session_id
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
                    {"name": name, "args": args, "session_id": event_session_id},
                    meta=EventMeta(
                        source=EventSource.BRAIN,
                        task_id=task_id,
                        session_id=event_session_id,
                        turn_id=turn_id,
                    ),
                ),
                loop,
            )

    def on_tool_result(name, result, *, turn_id=None, task_id=None, session_id=None):
        nonlocal active_operation_name, active_operation_task_id, active_operation_cancellable
        if task_id == active_operation_task_id:
            active_operation_name = None
            active_operation_task_id = None
            active_operation_cancellable = True
        event_session_id = session_id or current_web_session_id
        if event_bus:
            asyncio.run_coroutine_threadsafe(
                event_bus.emit(
                    "tool_result",
                    {"name": name, "text": result, "session_id": event_session_id},
                    meta=EventMeta(
                        source=EventSource.BRAIN,
                        task_id=task_id,
                        session_id=event_session_id,
                        turn_id=turn_id,
                    ),
                ),
                loop,
            )

    def on_operation_result(name: str, envelope: ResultEnvelope):
        """Record structured operation status while the legacy text callback stays unchanged."""
        try:
            active_audit_store = audit_store
        except NameError:
            active_audit_store = None
        if active_audit_store is not None:
            status = getattr(envelope.status, "value", envelope.status)
            active_audit_store.record(name, {}, str(status))

    def on_intent_decision(decision: IntentDecision):
        """Observe the one primary route selected for an interactive turn."""

        logger.info(
            "Intent decision: turn=%s session=%s intent=%s source=%s capabilities=%s",
            decision.turn_id,
            decision.session_id,
            decision.intent,
            decision.routing_source,
            decision.capabilities,
        )

    def on_thinking_update(name, args, *, turn_id=None, task_id=None, session_id=None):
        event_session_id = session_id or current_web_session_id
        if event_bus:
            desc = f"I'll use the {name} tool"
            if args:
                summary = str(args)[:80]
                desc += f" with {summary}"
            asyncio.run_coroutine_threadsafe(
                event_bus.emit(
                    "thinking_update",
                    {"text": desc, "session_id": event_session_id},
                    meta=EventMeta(
                        source=EventSource.BRAIN,
                        task_id=task_id,
                        session_id=event_session_id,
                        turn_id=turn_id,
                    ),
                ),
                loop,
            )

    _CONVERSATION_SUMMON_RE = re.compile(r"\b(?:show|open) (?:me )?(?:the )?(?:chat|conversation)\b", re.IGNORECASE)

    def _resolve_tool_approval_and_notify(request_id: str, approved: bool) -> None:
        """Resolve pending future and dismiss its canonical attention intent."""
        from charlie.core import resolve_tool_approval

        if not isinstance(request_id, str) or not request_id:
            logger.warning("Rejected malformed tool approval request id")
            return
        if not resolve_tool_approval(request_id, approved):
            logger.warning("Ignored stale or unknown tool approval: %s", request_id)
            return
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

    async def _handle_voice_control(request: TurnRequest, control: str) -> None:
        nonlocal speech_echo_cooldown
        from charlie.core import get_active_voice_approval

        trace = voice_diagnostic_traces.get(request.turn_id)
        pending_approval_id = get_active_voice_approval()
        if pending_approval_id:
            voice.stop_tts()
            _resolve_tool_approval_and_notify(pending_approval_id, False)
            handled = True
            logger.info("voice_control_applied | control=%s | action=decline_approval", control)
        else:
            handled = await _apply_voice_control(
                control,
                voice=voice,
                brain=brain,
                active_turn=active_turn_id is not None,
                active_operation_cancellable=active_operation_cancellable,
                active_process_task=active_process_task,
                cancel_housekeeping=_cancel_housekeeping,
            )

        if trace is not None:
            trace.bind(turn_id=request.turn_id, session_id=request.session_id)
            trace.mark_once(
                "intent_decision",
                fields={
                    "intent": "control",
                    "control": control,
                    "handled": handled,
                    "routing_source": "deterministic",
                },
            )
            trace.mark_once(
                "response_text_complete",
                fields={"status": "control", "completion_boundary": "control_without_answer"},
            )
        recorder = getattr(brain, "record_intent_decision", None)
        if callable(recorder):
            recorder(
                request,
                intent="control",
                capabilities=(),
                routing_source="deterministic",
                confidence=1.0,
                rationale=("active voice interruption command" if handled else "inactive voice control ignored"),
            )
        voice_diagnostic_traces.pop(request.turn_id, None)
        if handled:
            speech_echo_cooldown = time.time() + 1.5

    def on_tool_approval_request(
        request_id,
        tool_name,
        reason,
        platform,
        risk_class,
        *,
        turn_id=None,
        task_id=None,
        session_id=None,
    ):
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
            turn_id=turn_id,
            task_id=task_id,
            session_id=session_id,
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
                meta=EventMeta(
                    source=EventSource.BRAIN,
                    task_id=task_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    rationale="tool approval requires attention",
                ),
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

    def on_research_result(report, *, session_id, task_id=None, turn_id=None):
        """Forward typed research cards with identity from owning chat turn."""
        if event_bus is None:
            return
        from charlie.research.router import is_briefing_query
        from charlie.research.presentation import (
            build_briefing_workspace_payload,
            build_research_workspace_payload,
        )

        is_briefing = is_briefing_query(report.query)
        payload = build_briefing_workspace_payload(report) if is_briefing else build_research_workspace_payload(report)
        payload["session_id"] = session_id

        from charlie.presentation import default_presentation_resolver

        outcome = ResultEnvelope(
            request=report.query,
            capability="research",
            operation="news_briefing" if is_briefing else "research.web.execute",
            result=payload.get("summary", ""),
            status="completed",
            data=payload,
            session_id=session_id,
            task_id=task_id,
            turn_id=turn_id,
        )
        intent = default_presentation_resolver.resolve(outcome)
        logger.info(
            "Research result resolved presentation intent: id=%s kind=%s ws_type=%s is_briefing=%s",
            intent.id,
            intent.kind,
            intent.workspace_type,
            is_briefing,
        )

        def _emit_events():
            try:
                cur_loop = asyncio.get_running_loop()
                cur_loop.create_task(
                    event_bus.emit(
                        "research_result",
                        payload,
                        meta=EventMeta(
                            source=EventSource.TASK,
                            task_id=task_id,
                            session_id=session_id,
                            turn_id=turn_id,
                        ),
                    )
                )
                cur_loop.create_task(
                    event_bus.emit(
                        "presentation_intent",
                        intent.to_dict(),
                        meta=EventMeta(
                            source=EventSource.TASK,
                            task_id=task_id,
                            session_id=session_id,
                            turn_id=turn_id,
                            rationale="research presentation intent",
                        ),
                    )
                )
            except RuntimeError:
                asyncio.run_coroutine_threadsafe(
                    event_bus.emit(
                        "research_result",
                        payload,
                        meta=EventMeta(
                            source=EventSource.TASK,
                            task_id=task_id,
                            session_id=session_id,
                            turn_id=turn_id,
                        ),
                    ),
                    loop,
                )
                asyncio.run_coroutine_threadsafe(
                    event_bus.emit(
                        "presentation_intent",
                        intent.to_dict(),
                        meta=EventMeta(
                            source=EventSource.TASK,
                            task_id=task_id,
                            session_id=session_id,
                            turn_id=turn_id,
                            rationale="research presentation intent",
                        ),
                    ),
                    loop,
                )

        _emit_events()

    try:
        brain = Brain(
            config,
            on_thought_callback=speaking_callback,
            session_store=store,
            memory_store=memory_store,
            memory_graph=memory_graph,
            memory_service=memory_service,
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
            on_operation_result=on_operation_result,
            on_intent_decision=on_intent_decision,
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

    # Canonical memory facade wiring stays owned by main's composition root.
    _wire_memory_service(memory_service)

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
        # Always keep a manager available so the main-authoritative
        # extension_operation handler can enable/disable one built-in plugin
        # even when the blanket PLUGINS_ENABLED flag is off.
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

    def on_speech(text: str, diagnostic_metadata=None):
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
        request = _allocate_turn_request(text, session_id, "voice")
        trace = diagnostic_metadata.get("trace") if isinstance(diagnostic_metadata, dict) else None
        if trace is not None:
            trace.bind(turn_id=request.turn_id, session_id=session_id)
            voice_diagnostic_traces[request.turn_id] = trace
        from charlie.personality import parse_voice_control as _parse_voice_control

        control = _parse_voice_control(text)
        if control is not None:
            _schedule_process(_handle_voice_control(request, control), loop)
        else:
            _schedule_process(_dispatch_or_queue(request), loop)

    async def _dispatch_or_queue(request: TurnRequest):
        """Run the turn now, or queue it if one is already running tool calls.

        Only ever called via _schedule_process (run_coroutine_threadsafe), so
        this always executes on the loop thread -- the turn_active check and
        pending_turns mutation below are a single synchronous span with no
        await in between, making them atomic with respect to any other
        coroutine on this loop without needing a lock.
        """
        nonlocal turn_active, active_process_task
        from charlie.core import get_active_voice_approval

        trace = voice_diagnostic_traces.get(request.turn_id)
        blocked_by_active_turn = turn_active and not get_active_voice_approval()

        if request.channel == "voice" and blocked_by_active_turn:
            current_task = asyncio.current_task()
            old_task = active_process_task
            if old_task is not None and old_task is not current_task and not old_task.done():
                for queued in [item for item in pending_turns if item.channel == "voice"]:
                    pending_turns.remove(queued)
                    pending_turn_times.pop(queued.turn_id, None)
                    voice_diagnostic_traces.pop(queued.turn_id, None)
                if active_operation_name is not None and not active_operation_cancellable:
                    pending_turns.append(request)
                    pending_turn_times[request.turn_id] = time.monotonic()
                    logger.info(
                        "voice_turn_schedule | turn_id=%s | scheduling=queued_safe_completion | "
                        "active_operation=%s | queue_depth=%s",
                        request.turn_id,
                        active_operation_name,
                        len(pending_turns),
                    )
                    return
                brain.cancel_chat()
                old_task.cancel()
                try:
                    await old_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.debug("Superseded foreground turn exited with an error", exc_info=True)
                blocked_by_active_turn = False

        # A gated tool call inside the still-running turn is waiting on a
        # spoken yes/no -- that answer must reach _process() immediately
        # (it routes to resolve_tool_approval), never queued behind the
        # very turn it's meant to unblock.
        if blocked_by_active_turn:
            pending_turns.append(request)
            pending_turn_times[request.turn_id] = time.monotonic()
            if request.channel == "voice":
                logger.info(
                    "voice_turn_schedule | utterance_id=%s | turn_id=%s | session_id=%s "
                    "| scheduling=queued | queue_depth=%s | blocked_by_active_turn=%s "
                    "| active_turn_id=%s | active_task_id=%s",
                    getattr(trace, "utterance_id", None),
                    request.turn_id,
                    request.session_id,
                    len(pending_turns),
                    blocked_by_active_turn,
                    active_turn_id,
                    active_task_id,
                )
            logger.info(f"Queued utterance (a turn is already running tool calls): {request.input}")
            return
        if request.channel == "voice":
            logger.info(
                "voice_turn_schedule | utterance_id=%s | turn_id=%s | session_id=%s "
                "| scheduling=immediate | queue_depth=%s | blocked_by_active_turn=%s "
                "| active_turn_id=%s | active_task_id=%s",
                getattr(trace, "utterance_id", None),
                request.turn_id,
                request.session_id,
                len(pending_turns),
                blocked_by_active_turn,
                active_turn_id,
                active_task_id,
            )
        active_process_task = asyncio.current_task()
        try:
            await _process(request, brain, voice)
        finally:
            if active_process_task is asyncio.current_task():
                active_process_task = None

    def _cleanup_intent_decision(processor):
        """Release interactive route metadata after every processing outcome."""

        async def wrapped(request: TurnRequest, process_brain, process_voice):
            try:
                return await processor(request, process_brain, process_voice)
            finally:
                finalizer = getattr(process_brain, "finalize_intent_decision", None)
                if callable(finalizer):
                    finalizer(request.turn_id)

        return wrapped

    @_cleanup_intent_decision
    async def _process(request: TurnRequest, brain, voice):
        nonlocal speech_echo_cooldown, last_emotion, turn_active, active_turn_id, active_task_id
        nonlocal active_operation_name, active_operation_task_id, active_operation_cancellable
        text = request.input
        session_id = request.session_id
        platform = request.channel
        trace = voice_diagnostic_traces.get(request.turn_id)
        queued_at = pending_turn_times.pop(request.turn_id, None)
        dispatch_timestamp = time.monotonic()
        active_turn_id = request.turn_id
        active_task_id = None
        if trace is not None:
            trace.bind(turn_id=request.turn_id, session_id=session_id)
            trace.mark_once(
                "turn_dispatch",
                fields={
                    "platform": platform,
                    "queue_status": "queued" if queued_at is not None else "immediate",
                    "queue_age_ms": (
                        (dispatch_timestamp - queued_at) * 1000 if queued_at is not None else 0.0
                    ),
                    "pending_queue_depth": len(pending_turns),
                },
                timestamp=dispatch_timestamp,
            )
        set_diagnostic_context = getattr(voice, "set_diagnostic_context", None)
        if callable(set_diagnostic_context):
            set_diagnostic_context(trace)

        def mark_response_complete(status: str = "completed") -> None:
            if trace is not None:
                trace.mark_once(
                    "response_text_complete",
                    fields={
                        "status": status,
                        "platform": platform,
                        "completion_boundary": "response_done_or_early_return",
                    },
                    timestamp=time.monotonic(),
                )

        def record_primary_decision(
            *,
            intent: str,
            capabilities: tuple[str, ...] = (),
            routing_source: str = "control",
            confidence: Optional[float] = 1.0,
            rationale: str = "",
        ) -> None:
            recorder = getattr(brain, "record_intent_decision", None)
            if recorder is not None:
                recorder(
                    request,
                    intent=intent,
                    capabilities=capabilities,
                    routing_source=routing_source,
                    confidence=confidence,
                    rationale=rationale,
                )
            if trace is not None:
                trace.mark_once(
                    "intent_decision",
                    fields={
                        "intent": intent,
                        "capabilities": capabilities,
                        "routing_source": routing_source,
                        "confidence": confidence,
                    },
                )

        if time.time() < speech_echo_cooldown:
            record_primary_decision(
                intent="control",
                routing_source="control",
                rationale="speech echo cooldown suppressed the incoming utterance",
            )
            logger.info(f"Echo suppressed: {text}")
            mark_response_complete("suppressed_echo")
            return

        # A gated tool call (destructive shell command / sensitive file path)
        # is waiting on a spoken yes/no -- route this utterance to the answer
        # instead of starting a new chat turn. See
        # charlie.core.Brain.request_tool_approval / get_active_voice_approval.
        from charlie.core import get_active_voice_approval

        pending_approval_id = get_active_voice_approval()
        if pending_approval_id:
            record_primary_decision(
                intent="control",
                routing_source="control",
                rationale="pending tool approval response handled by the control path",
            )
            answer = parse_yes_no(text)
            if answer is None:
                voice.speak("Sorry, I didn't catch that. Say yes to continue or no to cancel.", last_emotion)
                mark_response_complete()
                return
            _resolve_tool_approval_and_notify(pending_approval_id, answer)
            voice.speak("Okay, running it." if answer else "Cancelled.", last_emotion)
            mark_response_complete()
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
                record_primary_decision(
                    intent="control",
                    routing_source="control",
                    rationale="barge-in lifecycle command interrupted active speech",
                )
                logger.info("Barge-in: Command word detected. Stopping TTS.")
                voice.stop_tts()
                brain.cancel_chat()
                speech_echo_cooldown = time.time() + 1.5
            else:
                # Echo detection: is this a subset of what Charlie is currently saying?
                if voice.is_echo(text):
                    logger.info(f"Echo suppressed (during TTS): {text}")
                    mark_response_complete("suppressed_echo")
                    return
                # New content during TTS -- barge in (cancel current turn)
                logger.info("Barge-in: New user input during TTS. Canceling.")
                voice.stop_tts()
                brain.cancel_chat()
                speech_echo_cooldown = time.time() + 0.8

        # Route !search command
        if text.strip().startswith("!search "):
            record_primary_decision(
                intent="memory",
                capabilities=("memory",),
                routing_source="control",
                rationale="explicit history search command",
            )
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
            mark_response_complete()
            return
        # Route /memory-review command
        if text.strip().lower() in ("/memory-review", "!memory-review"):
            record_primary_decision(
                intent="memory",
                capabilities=("memory",),
                routing_source="control",
                rationale="explicit learned-memory review command",
            )
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
            mark_response_complete()
            return
        panel_intent = match_surface_request(text)
        if panel_intent is not None:
            record_primary_decision(
                intent="control",
                routing_source="control",
                rationale=f"presentation command selected {panel_intent.action}",
            )
            result = get_presentation_controller().execute(
                PresentationRequest(
                    action=panel_intent.action,
                    surface=panel_intent.surface_id,
                    source=EventSource.VOICE,
                )
            )
            voice.speak(result.message, last_emotion)
            mark_response_complete()
            return

        # Route conversation-only phrase to the normal HUD summon path.
        if _CONVERSATION_SUMMON_RE.search(text):
            record_primary_decision(
                intent="control",
                routing_source="control",
                rationale="conversation workspace command selected presentation control",
            )
            await _open_conversation_workspace()
            voice.speak("Here you go.", last_emotion)
            mark_response_complete()
            return

        task_id = uuid.uuid4().hex
        active_task_id = task_id
        capability_requirements: tuple[str, ...] = ()
        try:
            from charlie import router as task_router
            from charlie.browser import intent as browser_intent
            from charlie.browser.session import get_session as get_browser_session

            browser_session = get_browser_session()
            parsed_browser_request = browser_intent.parse_browser_intent(
                text,
                browser_session.current_domain or "",
            )
            browser_operations = {
                "BACK",
                "FILTER",
                "SORT",
                "READ",
                "CURRENT_PAGE_FACT",
                "COMPARE",
                "PRODUCT_SELECT",
                "MEDIA",
            }
            if task_router.match_browser_task(text) or (
                browser_session.last_url and parsed_browser_request.operation in browser_operations
            ):
                capability_requirements = ("browser",)
        except Exception:
            logger.debug("Foreground capability classification failed", exc_info=True)

        foreground_journal = get_task_journal()
        turn_task = foreground_journal.create_task(
            text,
            task_id=task_id,
            origin=TaskOrigin.FOREGROUND,
            priority=TaskPriority.HIGH,
            status=TaskStatus.RUNNING,
            session_id=session_id,
            capability_requirements=capability_requirements,
        )

        async def _emit_foreground_task(record) -> None:
            if event_bus:
                await event_bus.emit(
                    "background_task",
                    record.to_dict(),
                    meta=EventMeta(
                        source=EventSource.TASK,
                        task_id=task_id,
                        session_id=session_id,
                        turn_id=request.turn_id,
                    ),
                )

        await _emit_foreground_task(turn_task)

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
                    meta=EventMeta(
                        source=EventSource.VOICE,
                        task_id=task_id,
                        session_id=session_id,
                        turn_id=request.turn_id,
                    ),
                )
            )

        # Store user message
        try:
            store.append("user", text, session_id=session_id, turn_id=request.turn_id)
            store.touch_session(session_id)
            update_session_title_from_text(session_id, text)
        except Exception as e:
            logger.warning(f"Failed to archive user message or touch session: {e}")
        # Voice command detection (before LLM call)
        cmd_emotion = parse_voice_command(text)
        if cmd_emotion is not None:
            record_primary_decision(
                intent="control",
                routing_source="control",
                rationale="voice preference command selected local voice control",
            )
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
                event_bus.emit(
                    "thinking",
                    {"session_id": session_id},
                    meta=EventMeta(
                        source=EventSource.BRAIN,
                        task_id=task_id,
                        session_id=session_id,
                        turn_id=request.turn_id,
                    ),
                )
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
            async for chunk in brain.chat_stream(
                text,
                platform=platform,
                session_id=session_id,
                task_id=task_id,
                turn_id=request.turn_id,
                turn_request=request,
                diagnostic_trace=trace,
            ):
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
                                    meta=EventMeta(
                                        source=EventSource.BRAIN,
                                        task_id=task_id,
                                        session_id=session_id,
                                        turn_id=request.turn_id,
                                    ),
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
                    meta=EventMeta(
                        source=EventSource.BRAIN,
                        task_id=task_id,
                        session_id=session_id,
                        turn_id=request.turn_id,
                    ),
                )

            # Final TTS
            if sentence_buffer.strip():
                _safe_speak(voice, sparkle + sentence_buffer, detected_emotion, "final")

            # Persist the generated reply, falling back to web_buffer if cancelled.
            final_reply = full_reply_buffer.strip() or web_buffer.strip()
            if final_reply:
                try:
                    store.append("assistant", final_reply, session_id=session_id, turn_id=request.turn_id)
                    store.touch_session(session_id)
                except Exception as e:
                    logger.warning(f"Failed to archive assistant message or touch session: {e}")
                if platform == "telegram" and telegram_bot:
                    try:
                        await telegram_bot.send_message(config.telegram_user_id, final_reply)
                    except Exception:
                        logger.warning("Failed to send Telegram reply", exc_info=True)

            # Emit response_done event so the UI can stop its typing indicator.
            turn_task = foreground_journal.transition(task_id, TaskStatus.VERIFYING)
            await _emit_foreground_task(turn_task)
            turn_task = foreground_journal.complete(
                task_id,
                result_reference=f"session:{session_id}",
            )
            await _emit_foreground_task(turn_task)
            if event_bus:
                await event_bus.emit(
                    "response_done",
                    {"session_id": session_id},
                    meta=EventMeta(
                        source=EventSource.BRAIN,
                        task_id=task_id,
                        session_id=session_id,
                        turn_id=request.turn_id,
                    ),
                )
            mark_response_complete()
        except asyncio.CancelledError:
            logger.info("Foreground voice turn superseded: %s", request.turn_id)
            try:
                turn_task = foreground_journal.cancel(task_id)
                await _emit_foreground_task(turn_task)
            except Exception:
                logger.debug("Failed to mark superseded foreground turn cancelled", exc_info=True)
            if event_bus:
                await event_bus.emit(
                    "response_done",
                    {"session_id": session_id},
                    meta=EventMeta(
                        source=EventSource.BRAIN,
                        task_id=task_id,
                        session_id=session_id,
                        turn_id=request.turn_id,
                        rationale="foreground voice turn superseded by newer speech",
                    ),
                )
            mark_response_complete("superseded")
            return
        except Exception as exc:
            logger.error("Turn failed", exc_info=True)
            try:
                turn_task = foreground_journal.fail(task_id, error_summary=str(exc)[:500])
                await _emit_foreground_task(turn_task)
            except Exception:
                logger.warning("Failed to mark foreground task failed", exc_info=True)
            error_class, message = classify_exception(exc)
            _safe_speak(voice, message, last_emotion, "turn-failed")
            if event_bus:
                severity = "error" if error_class == ErrorClass.CRITICAL else "warning"
                await event_bus.emit(
                    "alert",
                    {"severity": severity, "message": message},
                    meta=EventMeta(
                        source=EventSource.BRAIN,
                        task_id=task_id,
                        session_id=session_id,
                        turn_id=request.turn_id,
                        rationale=f"turn failed: {error_class.value}",
                    ),
                )
                await event_bus.emit(
                    "response_done",
                    {"session_id": session_id},
                    meta=EventMeta(
                        source=EventSource.BRAIN,
                        task_id=task_id,
                        session_id=session_id,
                        turn_id=request.turn_id,
                        rationale="turn failed with an unhandled exception",
                    ),
                )
            raise
        finally:
            turn_active = False
            if active_operation_task_id == task_id:
                active_operation_name = None
                active_operation_task_id = None
                active_operation_cancellable = True
            if active_turn_id == request.turn_id:
                active_turn_id = None
                active_task_id = None
            voice_diagnostic_traces.pop(request.turn_id, None)
            clear_diagnostic_context = getattr(voice, "set_diagnostic_context", None)
            if callable(clear_diagnostic_context):
                clear_diagnostic_context(None)
            if pending_turns:
                next_request = pending_turns.pop(0)
                logger.info(f"Dequeuing pending turn: {next_request.input}")
                _schedule_process(_dispatch_or_queue(next_request), loop)

        # Learning loop: deferred to background -- doesn't block next turn.
        # Skipped for screen-content queries -- the reply is a description of
        # whatever's on screen at that moment, never a genuine user preference,
        # and storing it as one pollutes memory with stale screen snapshots that
        # resurface on later "what's on my screen" queries.
        from charlie.router import SCREEN_QUERY_RE as _screen_query_re, is_direct_screen_perception_query
        from charlie.core import _VISUAL_CONTENT_QUERY_RE

        screen_content_query = bool(
            _screen_query_re.search(text)
            or is_direct_screen_perception_query(text)
            or _VISUAL_CONTENT_QUERY_RE.search(text)
        )

        if platform == "voice" and full_reply_buffer.strip() and text.strip() and not screen_content_query:

            async def _background_learn(user_text: str, reply_text: str):
                try:
                    await asyncio.sleep(0)
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

            _schedule_housekeeping(_background_learn(text, full_reply_buffer))

        if platform == "voice" and full_reply_buffer.strip() and not screen_content_query:
            _schedule_housekeeping(brain._extract_thread_update(text, full_reply_buffer, session_id))
            _schedule_housekeeping(brain._background_save_to_memory(full_reply_buffer, "assistant"))
        if platform == "voice":
            brain.schedule_deferred_background_work()

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
                on_speech_onset=on_speech_onset,
            )
            voice.start()
            voice.set_wake_word_callback(on_wake_word)
            logger.info("VoiceEngine reloaded.")
        except Exception as ex:
            logger.error(f"Error reloading VoiceEngine: {ex}", exc_info=True)

    async def _monitor_voice_health(event_bus: EventBus) -> None:
        """Publish capture and ASR readiness after asynchronous worker startup."""
        previous: tuple[object, object] | None = None
        while True:
            capture_ready = bool(getattr(voice, "is_ready", False))
            asr_status = str(getattr(voice, "asr_readiness_status", "failed"))
            state = (capture_ready, asr_status)
            if state != previous:
                if capture_ready:
                    _set_subsystem_health("voice_capture", HealthStatus.RUNNING, voice.readiness_detail())
                    _set_subsystem_health("voice", HealthStatus.RUNNING, voice.readiness_detail())
                else:
                    _set_subsystem_health("voice_capture", HealthStatus.DEGRADED, voice.readiness_detail())
                    _set_subsystem_health("voice", HealthStatus.DEGRADED, voice.readiness_detail())
                if asr_status == "ready":
                    asr_health = HealthStatus.RUNNING
                elif asr_status == "starting":
                    asr_health = HealthStatus.STARTING
                else:
                    asr_health = HealthStatus.DEGRADED
                _set_subsystem_health("asr", asr_health, voice.asr_readiness_detail())
                await _publish_subsystem_health(event_bus)
                previous = state
            await asyncio.sleep(0.1)

    async def _reload_mcp_client():
        """Stop the MCP subprocess client and restart it if still enabled."""
        nonlocal mcp_client
        from charlie.tools import registry

        for k in [k for k in registry._tools if k.startswith("mcp_")]:
            registry.unregister_tool(k)
        try:
            mcp_client = await _restart_mcp_client(mcp_client, config)
        except Exception as ex:
            logger.warning(f"Error reloading MCP client: {ex}")
            mcp_client = None

    def _reload_plugin_tools():
        """Re-register plugin tools to match the current enabled flag / allow-dirs."""
        from charlie.tools import registry

        for k in [k for k in registry._tools if k.startswith("plugin_")]:
            registry.unregister_tool(k)
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
                    request = _allocate_turn_request(chat_text, current_web_session_id, "web")
                    await _dispatch_or_queue(request)
                elif cmd_type == "session_active":
                    payload_sid = cmd.get("payload", {}).get("session_id")
                    current_web_session_id = cmd.get("session_id") or payload_sid or _voice_fallback_session_id
                    from charlie.recovery import set_active_session_id

                    set_active_session_id(current_web_session_id)
                    logger.info(f"Active session updated to: {current_web_session_id}")
                elif cmd_type == "ws_connection_count":
                    global hud_client_count
                    hud_client_count = cmd.get("count", 0)
                    from charlie.recovery import set_active_ws_count

                    set_active_ws_count(hud_client_count)
                elif cmd_type == "runtime_state_request":
                    await _dispatch_web_command(cmd, event_bus, mcp_client)
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
                elif cmd_type == "presentation_command":
                    payload = cmd.get("payload", {})
                    action = payload.get("action")
                    if action == "summon_hud":
                        await _summon_hud()
                    elif action == "open_conversation":
                        await _open_conversation_workspace()
                    elif action == "dismiss_widget" and isinstance(payload.get("id"), str):
                        await event_bus.emit(
                            "presentation_dismiss",
                            {"id": payload["id"]},
                            meta=EventMeta(source=EventSource.RUNTIME, rationale="operator dismissed widget from HUD"),
                        )
                    elif action == "focus_task" and isinstance(payload.get("task_id"), str):
                        task_id = payload["task_id"]
                        await event_bus.emit(
                            "presentation_command",
                            {"action": "focus_task", "task_id": task_id},
                            meta=EventMeta(source=EventSource.BRAIN, rationale="operator focused task from HUD rail"),
                        )
                        try:
                            task = get_task_journal().get(task_id)
                        except KeyError:
                            logger.warning("Ignoring focus request for unknown task %s", task_id)
                        else:
                            if _task_workspace_admitted(task):
                                intent = _task_workspace_intent(task)
                                await event_bus.emit(
                                    "presentation_intent",
                                    intent.to_dict(),
                                    meta=EventMeta(
                                        source=EventSource.TASK,
                                        task_id=task.id,
                                        rationale="task focus opened its runtime workspace",
                                    ),
                                )
                            else:
                                logger.info("Skipping full workspace for completed zero-step task %s", task.id)
                elif cmd_type == "hud_invoke":
                    # Pet/hotkey summon must not open a workspace.
                    await _summon_hud()
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
                elif cmd_type == "extension_operation":
                    payload = cmd.get("payload", {})
                    from charlie.tools import registry as _extension_registry

                    result, mcp_client = apply_extension_operation(
                        payload,
                        brain=brain,
                        plugin_manager=plugin_manager,
                        mcp_client=mcp_client,
                        runtime_config=config,
                        tool_registry=_extension_registry,
                    )
                    if result.get("success") is True:
                        await _publish_tool_snapshot(event_bus, _extension_registry)
                    await event_bus.emit(
                        "extension_operation_result",
                        result,
                        meta=EventMeta(
                            source=EventSource.BRAIN,
                            rationale="authoritative main-runtime extension operation result",
                        ),
                    )
                elif cmd_type == "mcp_operation":
                    payload = cmd.get("payload", {})
                    _, mcp_client = await _dispatch_mcp_operation(
                        payload,
                        event_bus,
                        mcp_client=mcp_client,
                        brain=brain,
                    )
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
                    await _publish_subsystem_health(event_bus)
                    from charlie.tools import registry as _reloaded_registry

                    await _publish_tool_snapshot(event_bus, _reloaded_registry)
                    await _publish_mcp_snapshot(event_bus, mcp_client)

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
                            memory_graph=memory_graph,
                            memory_service=memory_service,
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
    try:
        web_proc = await asyncio.to_thread(
            _start_web_subprocess,
            (sys.executable, web_entry),
            _web_env,
            host=config.charlie_host,
            port=config.charlie_port,
            launch_id=_LAUNCH_ID,
        )
    except Exception as exc:
        exit_code = 1
        logger.error("Charlie runtime startup failed before voice initialization: %s", exc, exc_info=True)
        try:
            await brain.close()
        except Exception:
            logger.warning("Brain cleanup after startup failure failed", exc_info=True)
        try:
            if store is not None:
                store.close()
        except Exception:
            logger.warning("SessionStore cleanup after startup failure failed", exc_info=True)
        try:
            audit_store.close()
        except Exception:
            logger.warning("AuditStore cleanup after startup failure failed", exc_info=True)
        logging.shutdown()
        os._exit(exit_code)

    # Start desktop companion subprocess (Windows-only, PySide6)
    if config.pet_enabled:
        _set_subsystem_health("companion", HealthStatus.STARTING)
        companion_ready, companion_detail = _companion_dependency_status()
        if not companion_ready:
            logger.warning("Companion unavailable: %s", companion_detail)
            _set_subsystem_health("companion", HealthStatus.DEGRADED, companion_detail)
        else:
            pet_entry = os.path.join(os.path.dirname(__file__), "charlie", "pet_entry.py")
            companion_ready_file = Path(tempfile.gettempdir()) / f"charlie-companion-{_LAUNCH_ID}.ready"
            companion_env = os.environ.copy()
            companion_env["CHARLIE_COMPANION_READY_FILE"] = str(companion_ready_file)
            pet_proc = _start_subsystem_process(
                "companion",
                (sys.executable, pet_entry),
                companion_env,
                readiness_file=companion_ready_file,
            )

    # Telegram runs in-process (needs direct access to _dispatch_or_queue), not a subprocess like web/pet/hud.
    if config.telegram_enabled:
        try:
            from charlie.telegram_bot import TelegramBot, should_relay_approval

            async def on_telegram_message(text, chat_id):
                request = _allocate_turn_request(text, current_web_session_id, "telegram")
                await _dispatch_or_queue(request)

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

        def on_speech_onset(_metadata=None):
            """Preempt optional work as soon as VAD confirms user speech."""

            if not config.enable_barge_in:
                return

            def _handle_onset() -> None:
                _cancel_housekeeping()
                if active_turn_id is None:
                    return
                task = active_process_task
                if task is None or task.done():
                    return
                if active_operation_name is not None and not active_operation_cancellable:
                    logger.info(
                        "Speech onset deferred foreground cancellation until safe operation completes: %s",
                        active_operation_name,
                    )
                    return
                brain.cancel_chat()
                logger.info("Speech onset superseding cancellable foreground response")
                task.cancel()

            try:
                loop.call_soon_threadsafe(_handle_onset)
            except RuntimeError:
                pass

        voice = _start_voice_or_degrade(
            config,
            on_speech,
            on_tts_start,
            on_tts_stop,
            on_speech_onset,
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

        if voice.is_ready:
            welcome_msg = welcome_msg or "Hey there. I'm online and listening."
            online_status = "   Charlie is online and listening"
        else:
            welcome_msg = "Hey there. I'm online. Microphone input is unavailable."
            online_status = "   Charlie is online; microphone unavailable"
        print("=" * 40, flush=True)
        print(online_status, flush=True)
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
                    cpu_percent = psutil.cpu_percent(interval=0.1)
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
            global _main_event_bus
            _main_event_bus = bus
            await _publish_subsystem_health(bus)
            if pet_proc is not None and companion_ready_file is not None:
                companion_monitor_task = asyncio.create_task(
                    _monitor_companion_readiness(pet_proc, companion_ready_file, bus)
                )
            bus.set_state_listener(_on_event_for_state)
            voice.set_event_bus(bus)
            import charlie.recovery

            charlie.recovery._event_bus = bus
            brain.event_bus = bus
            charlie.recovery.set_active_session_id(current_web_session_id)
            import charlie.tools

            charlie.tools.set_event_bus(bus, asyncio.get_running_loop())
            import charlie.mcp_client

            charlie.mcp_client.set_event_bus(bus, asyncio.get_running_loop())

            # Build one authoritative self-extension service only after the
            # real EventBus loop and MCP subsystem are available. Chat tools
            # delegate to this instance; they never construct an orchestrator.
            await mcp_start_task
            # Replay after web subscriber and producer command sockets have had
            # time to connect; initial PUB events can be lost during startup.
            await _publish_runtime_state(bus, mcp_client)
            from charlie.capabilities import get_capability_index
            from charlie.code_index import CodeIndex
            from charlie.doctor import CharlieDoctor
            from charlie.self_extension import SelfExtensionOrchestrator
            from charlie.self_knowledge import SelfKnowledgeService
            from charlie.settings_service import SettingsService

            shared_capability_index = get_capability_index()
            runtime_introspector = _build_runtime_introspector(
                config=config,
                capability_index=shared_capability_index,
                mcp_client=mcp_client,
                memory_service=memory_service,
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

            async def _spawn_watcher_surface(
                event: dict, message: str, reason: str, level: AttentionLevel
            ) -> None:
                from charlie.utils import make_id
                kind, dismiss_policy, auto_dismiss_ms, preferred_zone = _watcher_surface_kind(level)
                watcher_intent = PresentationIntent(
                    id=make_id(),
                    kind=kind,
                    title="Heads up",
                    summary=message,
                    priority=65,
                    attention_level=PresentationAttention.HIGH,
                    dismiss_policy=dismiss_policy,
                    auto_dismiss_ms=auto_dismiss_ms,
                    preferred_zone=preferred_zone,
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
                        asyncio.run_coroutine_threadsafe(
                            _spawn_watcher_surface(event, message, reason, level), _watcher_loop
                        )
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
                    _monitor_voice_health(bus),
                    mcp_start_task,
                    _calendar_reminder_loop(),
                )
            finally:
                logging.getLogger().removeHandler(zmq_handler)
                calendar_store.close()
                logger.info("main_shutdown_begin | stage=before_event_bus_close")
                if voice is not None:
                    voice.stop()
    except KeyboardInterrupt:
        logger.info("Interrupt received, shutting down...")
    except asyncio.CancelledError:
        pass
    except Exception as e:
        exit_code = 1
        logger.error("Charlie runtime startup/execution failed: %s", e, exc_info=True)
    finally:
        logger.info("main_shutdown_begin | exit_code=%s", exit_code)
        if mcp_start_task is not None and not mcp_start_task.done():
            mcp_start_task.cancel()
            try:
                await mcp_start_task
            except asyncio.CancelledError:
                logger.info("MCP startup task stopped")
            except Exception:
                logger.warning("MCP startup task stopped with an error", exc_info=True)
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
            web_pid = getattr(web_proc, "pid", None)
            _terminate_subsystem_process(web_proc)
            logger.info("web child exited | pid=%s | exit_code=%s", web_pid, web_proc.poll())
        if pet_proc is not None:
            pet_pid = getattr(pet_proc, "pid", None)
            _terminate_subsystem_process(pet_proc)
            logger.info("companion child exited | pid=%s | exit_code=%s", pet_pid, pet_proc.poll())
        if companion_monitor_task is not None:
            companion_monitor_task.cancel()
            try:
                await companion_monitor_task
            except asyncio.CancelledError:
                logger.info("companion readiness monitor stopped")
            except Exception:
                logger.warning("Companion readiness monitor stopped with an error", exc_info=True)
        if companion_ready_file is not None:
            try:
                companion_ready_file.unlink(missing_ok=True)
            except OSError:
                pass
        if telegram_bot is not None:
            try:
                await telegram_bot.stop()
                logger.info("Telegram stopped")
            except Exception as e:
                logger.warning(f"Telegram bot stop error: {e}")

        _log_port_release(config.charlie_host, config.charlie_port)
        _log_port_release("127.0.0.1", 5555)
        _log_port_release("127.0.0.1", 5556)

        logging.shutdown()
        # Force exit to ensure background threads don't hang the process on Windows
        os._exit(exit_code)


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
