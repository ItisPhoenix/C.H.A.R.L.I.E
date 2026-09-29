# ruff: noqa: E402, I001
import asyncio
import concurrent.futures
import dataclasses
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
from collections import OrderedDict
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

# Windows event-loop policy (must precede zmq/asyncio imports)
from charlie.runtime import configure as _configure_platform

_configure_platform()
import subprocess
import uuid

from charlie.text_utils import normalize_app_list as _normalize_app_list


from pathlib import Path

from dotenv import load_dotenv

from charlie.logging_policy import (
    NOISY_LOGGER_PREFIXES,
    ConciseConsoleFormatter,
    ConsolePolicyFilter,
    RedactingFormatter,
    env_flag,
    parse_log_level,
)

if os.getenv("CHARLIE_TEST_MODE", "").lower() != "true":
    load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)


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
file_log_level = parse_log_level(os.getenv("FILE_LOG_LEVEL"), logging.DEBUG)
console_log_level = parse_log_level(os.getenv("CONSOLE_LOG_LEVEL"), logging.INFO)
console_diagnostics = env_flag("CONSOLE_DIAGNOSTICS")
root_logger = logging.getLogger()
root_logger.setLevel(min(file_log_level, console_log_level))

file_formatter = RedactingFormatter(
    "%(asctime)s [%(name)s] [%(levelname)s] %(funcName)s:%(lineno)d - %(message)s"
)
file_handler = logging.handlers.RotatingFileHandler(
    LOG_FILE, encoding="utf-8", maxBytes=20 * 1024 * 1024, backupCount=5
)
file_handler.setLevel(file_log_level)
file_handler.setFormatter(file_formatter)

console_formatter = ConciseConsoleFormatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(console_log_level)
console_handler.setFormatter(console_formatter)

root_logger.handlers = []
from charlie.log_redaction import SensitiveDataFilter, redact_sensitive_text

redaction_filter = SensitiveDataFilter()
file_handler.addFilter(redaction_filter)
console_handler.addFilter(redaction_filter)
console_handler.addFilter(ConsolePolicyFilter(console_log_level, diagnostics=console_diagnostics))
root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)
for _logger_name in NOISY_LOGGER_PREFIXES:
    # Keep dependency records available to the DEBUG file handler. The console
    # policy, not logger-level suppression, decides what users see.
    logging.getLogger(_logger_name).setLevel(logging.DEBUG)

# 3. NOW IMPORT CHARLIE MODULES
from charlie import background_task, telemetry
from charlie.errors import ErrorClass, classify_exception
from charlie.config import Config, config
from charlie.console_ingress import ConsoleTextIngress
from charlie.core import Brain
from charlie.extensions import (
    ExtensionRuntimeRegistry,
    RuntimeExtension,
    build_skill_card,
    canonical_extension_request_fingerprint,
)
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
from charlie.session_store import (
    SessionConflictError,
    SessionNotFoundError,
    SessionStore,
)
from charlie.results import ResultsStore
from charlie.settings_service import (
    SettingsService,
)
from charlie.state import StateMachine
from charlie.subsystem_health import HealthRegistry, HealthStatus, RuntimeStatus
from charlie.task_journal import TaskOrigin, TaskStatus, get_task_journal
from charlie.turn_contracts import ExecutionPolicy, IntentDecision, ResultEnvelope, TurnRequest
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
_WATCHER_SHUTDOWN_TIMEOUT_S = 2.0
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
config.charlie_launch_id = _LAUNCH_ID

_runtime_health = HealthRegistry(
    (
        "brain",
        "llm",
        "memory",
        "plugins",
        "mcp",
        "telegram",
        "voice",
        "voice_capture",
        "asr",
        "watchers",
        "background_tasks",
        "browser",
    ),
    launch_id=_LAUNCH_ID,
)

# Bounded replay/idempotency window; evicted IDs may be treated as new requests.
_MEDIA_RESULT_CACHE_MAX = 512
_CALENDAR_RESULT_CACHE_MAX = 512
_SETTINGS_RESULT_CACHE_MAX = 512
_EXTENSION_RESULT_CACHE_MAX = 512
from charlie.runtime_identity import git_build_identity

_SOURCE_IDENTITY, _SOURCE_DIRTY = git_build_identity(Path(__file__).resolve().parent)
_state_machine = StateMachine()  # single authoritative CoreState instance for this process

# Authoritative main runtime task tracking
active_process_task: Optional[asyncio.Task] = None
background_housekeeping_tasks: set[asyncio.Task] = set()


async def _cancel_and_drain(
    tasks: Iterable[Optional[asyncio.Task | asyncio.Future]], *, label: str = "tasks", timeout: float = 2.0
) -> None:
    current = asyncio.current_task()
    pending: list[asyncio.Task | asyncio.Future] = []
    seen: set[asyncio.Task | asyncio.Future] = set()

    for item in tasks:
        if item is None or not isinstance(item, (asyncio.Task, asyncio.Future)):
            continue
        if item is current or item in seen:
            continue
        seen.add(item)
        if not item.done():
            item.cancel()
            pending.append(item)
        elif not item.cancelled():
            try:
                item.exception()
            except (asyncio.CancelledError, asyncio.InvalidStateError):
                pass

    if not pending:
        return

    done, still_pending = await asyncio.wait(pending, timeout=timeout)
    if still_pending:
        logger.error("%s cancellation did not reach quiescence before shutdown timeout", label)
        raise RuntimeError(f"{label} cancellation did not reach quiescence before shutdown timeout")
    results = await asyncio.gather(*done, return_exceptions=True)
    for task, res in zip(done, results, strict=False):
        if isinstance(res, Exception) and not isinstance(res, asyncio.CancelledError):
            name = getattr(task, "get_name", lambda: str(task))()
            logger.warning("Error during %s task drain (%s): %s", label, name, res)


def _allocate_turn_request(text: str, session_id: str, channel: str) -> TurnRequest:
    """Allocate one immutable request identity at normalized ingress."""

    return TurnRequest.allocate(text, session_id, channel)


def _should_queue_active_turn(turn_active: bool, approval_pending: bool, channel: str) -> bool:
    """Queue Telegram messages while an approval owns the active turn."""
    return turn_active and (not approval_pending or channel == "telegram")


def _telegram_approval_reason(tool_name: str, risk_class: Any, reason: str = "") -> str:
    risk = str(getattr(risk_class, "value", risk_class)).casefold()
    if tool_name == "shell_execute":
        if risk in {"destructive", "irreversible"}:
            return "This command may change or stop something on your PC. Please review it before approving."
        return "I can't confirm this command is read-only. Please review it before approving."
    if reason.strip():
        sentence = reason.strip().rstrip(".")
        return f"{sentence[0].upper()}{sentence[1:]}."
    return "This action may change something on your PC. Please review it before approving."


def _reload_plugin_tools_state(
    runtime_config: Any,
    current_manager: Any,
    *,
    registry: Any,
    register_plugin_tools: Callable[[Any], Any],
    empty_manager_factory: Callable[[], Any],
    set_health: Callable[[str, HealthStatus], None],
) -> tuple[bool, str, Any]:
    """Reload plugin tools and return the manager that owns their closures."""
    for name in [name for name in registry._tools if name.startswith("plugin_")]:
        registry.unregister_tool(name)
    if runtime_config.plugins_enabled:
        try:
            replacement_manager = register_plugin_tools(runtime_config)
            if replacement_manager is None:
                set_health("plugins", HealthStatus.DEGRADED)
                return False, "plugin reload produced no manager", current_manager
            set_health("plugins", HealthStatus.RUNNING)
            return True, "", replacement_manager
        except Exception:
            logger.warning("Error registering plugins on reload", exc_info=True)
            set_health("plugins", HealthStatus.DEGRADED)
            return False, "plugin reload failed", current_manager

    set_health("plugins", HealthStatus.DISABLED)
    return True, "", empty_manager_factory()


async def _reconcile_mcp_extension_runtime(
    extension_registry: ExtensionRuntimeRegistry,
    mcp_client: Any,
    tool_registry: Any,
    plugin_manager: Any,
    runtime_config: Any,
) -> tuple[Any, list[str]]:
    """Reapply main-owned MCP extensions after canonical client replacement."""
    from charlie.extensions.install import install_extension

    failed: list[str] = []
    for extension in extension_registry.list():
        if extension.kind != "mcp":
            continue
        if mcp_client is not None and extension.name in getattr(mcp_client, "_servers", {}):
            mcp_client.remove_server(tool_registry, extension.name)
        if not extension.enabled:
            extension.tool_names = []
            extension.runtime_warning = None
            continue
        if mcp_client is None:
            extension.enabled = False
            extension.tool_names = []
            extension.runtime_warning = "Runtime extension degraded"
            failed.append(extension.name)
            continue
        try:
            tool_names, mcp_client = await asyncio.to_thread(
                install_extension,
                "mcp",
                extension.name,
                extension.source,
                extension.raw_text,
                tool_registry,
                plugin_manager,
                mcp_client,
                list(getattr(runtime_config, "plugin_allow_dirs", []) or []),
            )
            extension.enabled = True
            extension.tool_names = list(tool_names)
            extension.runtime_warning = None
        except Exception:
            extension.enabled = False
            extension.tool_names = []
            extension.runtime_warning = "Runtime extension degraded"
            failed.append(extension.name)
    return mcp_client, failed


async def _publish_settings_snapshot(
    bus: Optional[EventBus],
    settings_service: Any,
    *,
    rationale: str = "main settings projection",
) -> None:
    if bus is None or settings_service is None:
        return
    await bus.emit(
        "settings_snapshot",
        settings_service.snapshot(),
        meta=EventMeta(source=EventSource.BRAIN, rationale=rationale),
    )


async def _run_self_extension_request(
    self_extension_orchestrator: Any,
    request_payload: dict[str, Any],
    request_id: str,
    *,
    event_bus: Any,
) -> None:
    """Run one self-extension request through the main-owned orchestrator."""
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
        {"request_id": request_id, **result},
        meta=EventMeta(
            source=EventSource.BRAIN,
            rationale="authoritative self-extension transaction result",
        ),
    )


def _reload_callback_outcome(outcome: Any) -> tuple[bool, str]:
    if isinstance(outcome, tuple) and outcome:
        return bool(outcome[0]), str(outcome[1]) if len(outcome) > 1 else ""
    if isinstance(outcome, dict):
        return bool(outcome.get("success", outcome.get("ok", False))), str(outcome.get("reason", ""))
    return bool(outcome), ""


def _sync_brain_skill_block(brain: Any, extension_orchestrator: Any, name: str) -> None:
    blocks = extension_orchestrator.get_active_skill_blocks()
    if name in blocks:
        brain.add_installed_skill_block(name, blocks[name])
    else:
        brain.remove_installed_skill_block(name)


def _load_rehydrated_skill_blocks(brain: Any, extension_orchestrator: Any) -> None:
    for name, block in extension_orchestrator.get_active_skill_blocks().items():
        brain.add_installed_skill_block(name, block)


async def _resolve_skill_candidate_review(
    extension_orchestrator: Any, brain: Any, review_token: str, action: str
) -> bool:
    """Resolve persistent owner skill review separately from foreground tool approval."""
    if action not in {"approve", "reject", "disable"}:
        return False
    expected_status = "approved" if action == "disable" else "pending"
    candidate = await asyncio.to_thread(
        extension_orchestrator.resolve_skill_candidate_review_token,
        review_token,
        expected_status,
    )
    if candidate is None:
        return False
    name, content_hash = candidate
    operation = {
        "approve": extension_orchestrator.approve_skill_candidate,
        "reject": extension_orchestrator.reject_skill_candidate,
        "disable": extension_orchestrator.disable_skill_candidate,
    }[action]
    result = await asyncio.to_thread(operation, name, content_hash)
    if not getattr(result, "success", False):
        return False
    if action in {"approve", "disable"}:
        _sync_brain_skill_block(brain, extension_orchestrator, name)
    return True


async def _send_pending_skill_candidate_reviews(
    telegram_bot: Any, extension_orchestrator: Any, chat_id: int
) -> int:
    """Submit pending reviews once per successful Telegram API send."""
    sent = 0
    try:
        candidates = extension_orchestrator.list_skill_candidates()
    except Exception:
        logger.warning("Could not read pending skill candidate reviews", exc_info=True)
        return 0
    for candidate in candidates:
        if (
            candidate.get("status") != "pending"
            or candidate.get("review_submitted")
            or not candidate.get("review_token")
            or not candidate.get("matches_hash")
        ):
            continue
        try:
            await telegram_bot.send_skill_candidate_review(
                chat_id,
                candidate["name"],
                candidate["content_hash"],
                candidate["review_token"],
                candidate.get("content") or "",
            )
            marked = await asyncio.to_thread(
                extension_orchestrator.mark_skill_candidate_review_submitted,
                candidate["name"],
                candidate["content_hash"],
            )
            if marked:
                sent += 1
        except Exception:
            logger.warning("Could not submit skill candidate review for %s", candidate.get("name"), exc_info=True)
    return sent


def _is_sustained_research_request(text: str, runtime_config: Any) -> bool:
    """Classify explicit long research as a task without backgrounding every lookup."""
    if not getattr(runtime_config, "research_enabled", True):
        return False
    from charlie.research.router import is_sustained_research_query, route

    decision = route(text, getattr(runtime_config, "research_default_mode", "auto"))
    return decision.should_research and is_sustained_research_query(text, decision)


async def _start_sustained_research_task(
    request: TurnRequest,
    *,
    runtime_config: Config,
    event_bus: Any,
    voice: Any,
    store: Any,
    memory_store: Any,
    memory_graph: Any,
    memory_service: Any,
    on_result_stored: Optional[Callable] = None,
    on_research_result: Optional[Callable] = None,
    on_tool_call: Optional[Callable] = None,
    on_tool_result: Optional[Callable] = None,
    on_operation_result: Optional[Callable] = None,
    on_thinking_update: Optional[Callable] = None,
) -> Any:
    """Start research on the existing background manager and acknowledge immediately."""
    try:
        if store is not None:
            try:
                store.append("user", request.input, session_id=request.session_id, turn_id=request.turn_id)
                store.touch_session(request.session_id)
            except SessionNotFoundError:
                logger.warning(
                    "session_persistence_dropped | phase=sustained_research | session_id=%s "
                    "| turn_id=%s | reason=session_deleted",
                    request.session_id,
                    request.turn_id,
                )
                raise
            except Exception:
                logger.warning("sustained_research_user_message_archive_failed", exc_info=True)
        if event_bus is not None and request.channel == "voice":
            await event_bus.emit(
                "transcript",
                {"text": request.input, "source": request.channel, "session_id": request.session_id},
                meta=EventMeta(source=EventSource.VOICE, session_id=request.session_id, turn_id=request.turn_id),
            )

        task = await background_task.start(
            runtime_config,
            event_bus,
            request.input,
            session_store=store,
            memory_store=memory_store,
            memory_graph=memory_graph,
            memory_service=memory_service,
            session_id=request.session_id,
            turn_id=request.turn_id,
            origin=TaskOrigin.RESEARCH,
            capability_requirements=("research",),
            research_query=request.input,
            on_result_stored=on_result_stored,
            on_research_result=on_research_result,
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
            on_operation_result=on_operation_result,
            on_thinking_update=on_thinking_update,
            announce=False,
        )
    except Exception:
        logger.error("sustained_research_start_failed | turn_id=%s", request.turn_id, exc_info=True)
        message = "I couldn't start that research task."
        if event_bus is not None:
            await event_bus.emit(
                "alert",
                {"severity": "warning", "message": message},
                meta=EventMeta(source=EventSource.TASK, session_id=request.session_id, turn_id=request.turn_id),
            )
            await event_bus.emit(
                "token",
                {"text": message, "session_id": request.session_id},
                meta=EventMeta(source=EventSource.TASK, session_id=request.session_id, turn_id=request.turn_id),
            )
            await event_bus.emit(
                "response_done",
                {"session_id": request.session_id},
                meta=EventMeta(
                    source=EventSource.TASK,
                    session_id=request.session_id,
                    turn_id=request.turn_id,
                    rationale="sustained research task failed to start",
                ),
            )
        return None

    message = "Started. I'll keep working on that research and let you know when it is ready."
    logger.info(
        "sustained_research_started | task_id=%s | turn_id=%s | session_id=%s | status=%s",
        task.id,
        request.turn_id,
        request.session_id,
        task.status,
    )
    if event_bus is not None:
        await event_bus.emit(
            "token",
            {"text": message, "session_id": request.session_id},
            meta=EventMeta(
                source=EventSource.TASK,
                task_id=task.id,
                session_id=request.session_id,
                turn_id=request.turn_id,
                rationale="sustained research task acknowledged",
            ),
        )
        await event_bus.emit(
            "response_done",
            {"session_id": request.session_id},
            meta=EventMeta(
                source=EventSource.TASK,
                task_id=task.id,
                session_id=request.session_id,
                turn_id=request.turn_id,
                rationale="sustained research task runs independently",
            ),
        )
    if voice is not None:
        voice.speak(message, "neutral")
    return task


def _configure_runtime_health(runtime_config: Any) -> None:
    """Configure main-owned policy without turning static availability into readiness."""
    voice_enabled = bool(getattr(runtime_config, "voice_enabled", True))
    try:
        from charlie.browser import BROWSER_AVAILABLE

        browser_static_available: Optional[bool] = bool(BROWSER_AVAILABLE)
    except Exception:
        browser_static_available = None

    policies = {
        "brain": (True, True, True, "main.brain"),
        "llm": (True, True, bool(getattr(runtime_config, "llm_url", "")), "main.brain"),
        "memory": (True, False, True, "main.memory"),
        "plugins": (bool(getattr(runtime_config, "plugins_enabled", False)), False, True, "main.plugins"),
        "mcp": (bool(getattr(runtime_config, "mcp_enabled", False)), False, True, "main.mcp"),
        "telegram": (bool(getattr(runtime_config, "telegram_enabled", False)), False, None, "main.telegram"),
        "voice": (voice_enabled, False, None, "main.voice"),
        "voice_capture": (voice_enabled, False, None, "main.voice"),
        "asr": (voice_enabled, False, None, "main.voice"),
        "watchers": (True, False, True, "main.watchers"),
        "background_tasks": (True, True, True, "main.background_tasks"),
        "browser": (
            bool(getattr(runtime_config, "browser_enabled", False)),
            False,
            browser_static_available,
            "browser.controller",
        ),
    }
    for name, (enabled, required, static_available, authority) in policies.items():
        try:
            _runtime_health.configure_subsystem(
                name,
                enabled=enabled,
                required=required,
                static_available=static_available,
                evidence_authority=authority,
            )
        except ValueError:
            # Tests and partial startup fixtures may intentionally use smaller registries.
            logger.debug("Runtime health registry does not expose subsystem %s", name)


def _set_subsystem_health_if_known(
    name: str,
    status: HealthStatus,
    public_detail: Optional[str] = None,
) -> None:
    """Project optional lifecycle truth without breaking reduced test registries."""
    try:
        _set_subsystem_health(name, status, public_detail)
    except ValueError:
        logger.debug("Runtime health registry does not expose subsystem %s", name)


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


_main_event_bus: Optional[Any] = None


@dataclasses.dataclass(frozen=True)
class _TrackedThreadsafeSubmission:
    future: concurrent.futures.Future
    started: concurrent.futures.Future
    completion: concurrent.futures.Future


class _EventBusSubmissionRegistry:
    """Gate and drain main-owned fire-and-forget EventBus submissions."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._accepting = True
        self._tasks: set[asyncio.Task] = set()
        self._futures: set[_TrackedThreadsafeSubmission] = set()

    def close(self) -> None:
        with self._lock:
            self._accepting = False

    def submit_task(self, coroutine: Any, loop: asyncio.AbstractEventLoop) -> Optional[asyncio.Task]:
        with self._lock:
            if not self._accepting:
                coroutine.close()
                return None
            try:
                task = loop.create_task(coroutine)
            except Exception:
                coroutine.close()
                raise
            self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def submit_threadsafe(
        self, coroutine: Any, loop: asyncio.AbstractEventLoop
    ) -> Optional[concurrent.futures.Future]:
        started = concurrent.futures.Future()
        completion = concurrent.futures.Future()

        async def _tracked_submission() -> Any:
            if not started.done():
                started.set_result(None)
            try:
                return await coroutine
            finally:
                if not completion.done():
                    completion.set_result(None)

        wrapped_coroutine = _tracked_submission()
        with self._lock:
            if not self._accepting:
                wrapped_coroutine.close()
                coroutine.close()
                return None
            try:
                future = asyncio.run_coroutine_threadsafe(wrapped_coroutine, loop)
            except Exception:
                wrapped_coroutine.close()
                coroutine.close()
                raise
            tracked = _TrackedThreadsafeSubmission(future=future, started=started, completion=completion)
            self._futures.add(tracked)
        future.add_done_callback(lambda _future: self._future_done(tracked))
        completion.add_done_callback(lambda _future: self._future_done(tracked))
        return future

    def snapshot(self) -> tuple[tuple[asyncio.Task, ...], tuple[_TrackedThreadsafeSubmission, ...]]:
        with self._lock:
            return tuple(self._tasks), tuple(self._futures)

    def prune_done(self) -> None:
        with self._lock:
            done_tasks = [task for task in self._tasks if task.done()]
            done_futures = [entry for entry in self._futures if entry.future.done() and entry.completion.done()]
            self._tasks.difference_update(done_tasks)
            self._futures.difference_update(done_futures)
        for task in done_tasks:
            self._log_task_error(task)
        for entry in done_futures:
            self._log_future_error(entry.future)

    def is_empty(self) -> bool:
        with self._lock:
            return not self._tasks and not self._futures

    def _task_done(self, task: asyncio.Task) -> None:
        with self._lock:
            self._tasks.discard(task)
        self._log_task_error(task)

    @staticmethod
    def _log_task_error(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        try:
            error = task.exception()
        except (asyncio.CancelledError, asyncio.InvalidStateError):
            return
        if error is not None:
            logger.warning("EventBus submission task failed: %s", error)

    def _future_done(self, entry: _TrackedThreadsafeSubmission) -> None:
        if entry.future.cancelled() and not entry.started.done():
            entry.started.set_result(None)
            if not entry.completion.done():
                entry.completion.set_result(None)
        with self._lock:
            if not entry.future.done() or not entry.completion.done() or entry not in self._futures:
                return
            self._futures.discard(entry)
        self._log_future_error(entry.future)

    @staticmethod
    def _log_future_error(future: concurrent.futures.Future) -> None:
        if future.cancelled():
            return
        try:
            error = future.exception()
        except (concurrent.futures.CancelledError, concurrent.futures.InvalidStateError):
            return
        if error is not None:
            logger.warning("Thread-safe EventBus submission failed: %s", error)


_main_event_bus_registry: Optional[_EventBusSubmissionRegistry] = None


def _stop_watcher_thread(
    stop_event: Optional[threading.Event],
    watcher_thread: Optional[threading.Thread],
    *,
    timeout: float = _WATCHER_SHUTDOWN_TIMEOUT_S,
) -> bool:
    """Signal and bounded-join one main-owned watcher thread."""
    if stop_event is not None:
        stop_event.set()
    if watcher_thread is None:
        return True
    if watcher_thread is threading.current_thread():
        logger.error("Watcher shutdown attempted from watcher thread")
        return False
    if watcher_thread.is_alive():
        watcher_thread.join(timeout=timeout)
    return not watcher_thread.is_alive()


def _submit_event_task(coroutine: Any, loop: Optional[asyncio.AbstractEventLoop] = None) -> Optional[asyncio.Task]:
    registry = _main_event_bus_registry
    if registry is None:
        coroutine.close()
        return None
    try:
        target_loop = loop or asyncio.get_running_loop()
        return registry.submit_task(coroutine, target_loop)
    except RuntimeError:
        coroutine.close()
        return None


def _submit_event_threadsafe(
    coroutine: Any, loop: asyncio.AbstractEventLoop
) -> Optional[concurrent.futures.Future]:
    registry = _main_event_bus_registry
    if registry is None:
        coroutine.close()
        return None
    try:
        return registry.submit_threadsafe(coroutine, loop)
    except RuntimeError:
        coroutine.close()
        return None


async def _drain_event_bus_submissions(
    registry: _EventBusSubmissionRegistry,
    *,
    loop: Optional[asyncio.AbstractEventLoop] = None,
    timeout: float = 2.0,
) -> None:
    """Cancel and resolve registered EventBus work within a bounded window."""
    target_loop = loop or asyncio.get_running_loop()

    async def _wait_until(deadline: float) -> bool:
        while True:
            registry.prune_done()
            if registry.is_empty():
                return True
            tasks, futures = registry.snapshot()
            waitables = [task for task in tasks if not task.done()]
            waitables.extend(
                asyncio.wrap_future(entry.completion, loop=target_loop)
                for entry in futures
                if not entry.completion.done()
            )
            remaining = deadline - target_loop.time()
            if not waitables or remaining <= 0:
                return registry.is_empty()
            await asyncio.wait(waitables, timeout=remaining)

    if await _wait_until(target_loop.time() + timeout):
        return

    tasks, futures = registry.snapshot()
    for task in tasks:
        if not task.done():
            task.cancel()
    for entry in futures:
        if not entry.future.done():
            entry.future.cancel()

    cancellation_deadline = target_loop.time() + min(2.0, max(timeout, 0.5))
    if await _wait_until(cancellation_deadline):
        return
    message = "EventBus submission cancellation did not reach quiescence before shutdown timeout"
    logger.error(message)
    raise RuntimeError(message)


def _close_runtime_stores(audit_store: Any, store: Any, *, quiescent: bool) -> None:
    """Close SQLite authorities only after main has proved worker quiescence."""
    if not quiescent:
        logger.error("Skipping SessionStore/AuditStore close because main worker quiescence is unverified")
        return
    if audit_store is not None:
        try:
            audit_store.close()
            logger.info("AuditStore closed")
        except Exception as exc:
            logger.warning("AuditStore close error: %s", exc)
    if store is not None:
        try:
            store.close()
            logger.info("SessionStore closed")
        except Exception as exc:
            logger.warning("SessionStore close error: %s", exc)


async def _publish_subsystem_health(bus: Optional[EventBus] = None) -> None:
    """Publish legacy subsystem projection plus canonical runtime truth."""
    if bus is None:
        return
    event = _runtime_health.event()
    await bus.emit(event["type"], event["payload"], meta=EventMeta(source=EventSource.VOICE))
    await _publish_runtime_truth(bus)


async def _publish_runtime_truth(bus: Optional[EventBus] = None) -> None:
    """Publish main-owned revisioned aggregate health for read-only consumers."""
    if bus is None:
        return
    event = _runtime_health.runtime_event()
    await bus.emit(event["type"], event["payload"], meta=EventMeta(source=EventSource.RUNTIME))


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


async def _publish_runtime_telemetry(bus: Optional[EventBus] = None) -> None:
    """Publish current main-owned runtime telemetry snapshot over IPC."""
    target_bus = bus or _main_event_bus
    if target_bus is None:
        return
    from charlie import telemetry

    await target_bus.emit(
        EventType.RUNTIME_TELEMETRY.value,
        telemetry.snapshot(),
        meta=EventMeta(
            source=EventSource.RUNTIME,
            rationale="authoritative main-runtime telemetry snapshot",
        ),
    )


def _on_telemetry_updated() -> None:
    """Invoked when LLM or tool metrics change in main process."""
    bus = _main_event_bus
    if bus is None:
        return
    try:
        loop = asyncio.get_running_loop()
        _submit_event_task(_publish_runtime_telemetry(bus), loop)
    except RuntimeError:
        pass


from charlie import telemetry as _main_telemetry

_main_telemetry.set_telemetry_listener(_on_telemetry_updated)


async def _publish_runtime_state(
    bus: Optional[EventBus] = None,
    mcp_client: Any = None,
    settings_service: Any = None,
    extension_registry: Optional[ExtensionRuntimeRegistry] = None,
) -> None:
    """Replay public operational state owned by this main process."""
    await _publish_subsystem_health(bus)
    await _publish_task_snapshot(bus)
    await _publish_tool_snapshot(bus)
    await _publish_mcp_snapshot(bus, mcp_client)
    await _publish_runtime_telemetry(bus)
    await _publish_settings_snapshot(bus, settings_service, rationale="runtime settings projection replay")
    await _publish_extension_snapshot(bus, extension_registry, rationale="runtime extension projection replay")


def _build_extension_snapshot(extension_registry: ExtensionRuntimeRegistry) -> dict[str, Any]:
    return {
        "authority": "main_runtime",
        "status": "available",
        "extensions": extension_registry.snapshot(),
    }


async def _publish_extension_snapshot(
    bus: Optional[EventBus],
    extension_registry: Optional[ExtensionRuntimeRegistry],
    *,
    rationale: str = "main extension runtime projection",
) -> None:
    if bus is None or extension_registry is None:
        return
    await bus.emit(
        "extension_snapshot",
        _build_extension_snapshot(extension_registry),
        meta=EventMeta(source=EventSource.BRAIN, rationale=rationale),
    )


def _extension_operation_result(
    payload: dict[str, Any],
    *,
    success: bool,
    tool_names: list[str],
    error: Optional[str] = None,
    kind: Optional[str] = None,
    name: Optional[str] = None,
    request_fingerprint: Optional[str] = None,
) -> dict[str, Any]:
    result = {
        "request_id": str(payload.get("request_id", "")),
        "operation": str(payload.get("operation", "")),
        "kind": str(kind if kind is not None else payload.get("kind", "")),
        "name": str(name if name is not None else payload.get("name", "")),
        "success": success,
        "tool_names": list(tool_names),
    }
    if request_fingerprint is not None:
        result["request_fingerprint"] = request_fingerprint
    if error:
        result["error"] = _safe_extension_error(payload, error)
    return result


def _safe_extension_error(payload: dict[str, Any], error: object) -> str:
    safe_error = redact_sensitive_text(str(error))
    for sensitive_value in (payload.get("raw_text"), payload.get("source")):
        if isinstance(sensitive_value, str) and sensitive_value:
            safe_error = safe_error.replace(sensitive_value, "[REDACTED_EXTENSION_MATERIAL]")
    return safe_error[:500]


def _capture_tool_registry_state(tool_registry: Any) -> dict[str, dict[str, Any]]:
    return {
        name: dict(metadata)
        for name, metadata in getattr(tool_registry, "_tools", {}).items()
        if isinstance(metadata, dict)
    }


def _restore_tool_registry_state(tool_registry: Any, before: dict[str, dict[str, Any]]) -> None:
    current = getattr(tool_registry, "_tools", {})
    for name in list(current):
        if name not in before or current[name] != before[name]:
            tool_registry.unregister_tool(name)
    current = getattr(tool_registry, "_tools", {})
    for name, metadata in before.items():
        if name in current:
            continue
        func = metadata.get("func")
        if not callable(func):
            raise RuntimeError(f"Cannot restore extension tool '{name}' without callable state.")
        tool_registry.register_tool(
            name=name,
            description=metadata.get("description", ""),
            schema=metadata.get("schema", {}),
            is_interactive=bool(metadata.get("is_interactive", False)),
            owner=metadata.get("owner", ""),
            risk_class=metadata.get("risk_class"),
        )(func)


def apply_extension_operation(
    payload: dict[str, Any],
    *,
    brain: Any,
    plugin_manager: Any,
    mcp_client: Any,
    runtime_config: Any,
    tool_registry: Any = None,
    extension_registry: Optional[ExtensionRuntimeRegistry] = None,
) -> tuple[dict[str, Any], Any]:
    """Apply one extension transition against main's live runtime owners."""
    if not isinstance(payload, dict):
        payload = {}
    operation = payload.get("operation")
    name = payload.get("name")
    allowed_operations = {"install", "enable", "disable", "uninstall"}
    allowed_kinds = {"mcp", "skill", "openapi", "plugin", "generated"}
    fingerprint = canonical_extension_request_fingerprint(str(operation or "invalid"), payload)
    if (
        not isinstance(payload.get("request_id"), str)
        or not payload["request_id"]
        or not isinstance(operation, str)
        or operation not in allowed_operations
        or not isinstance(name, str)
        or not name
        or (
            operation == "install"
            and (not isinstance(payload.get("kind"), str) or payload["kind"] not in allowed_kinds)
        )
    ):
        return (
            _extension_operation_result(
                payload,
                success=False,
                tool_names=[],
                error="Invalid extension operation request.",
                request_fingerprint=fingerprint,
            ),
            mcp_client,
        )

    entry = extension_registry.get(name) if extension_registry is not None else None
    if operation != "install" and extension_registry is not None and entry is None:
        return (
            _extension_operation_result(
                payload,
                success=False,
                tool_names=[],
                error=f"Unknown extension '{name}' in main runtime.",
                request_fingerprint=fingerprint,
            ),
            mcp_client,
        )

    if operation == "install" and extension_registry is not None and extension_registry.get(name) is not None:
        return (
            _extension_operation_result(
                payload,
                success=False,
                tool_names=[],
                error=f"Extension '{name}' is already installed in main runtime.",
                request_fingerprint=fingerprint,
            ),
            mcp_client,
        )

    kind = entry.kind if entry is not None else payload.get("kind")
    source = entry.source if entry is not None else str(payload.get("source", ""))
    raw_text = entry.raw_text if entry is not None else str(payload.get("raw_text", ""))
    known_tool_names = list(entry.tool_names) if entry is not None else payload.get("tool_names", [])
    if not isinstance(kind, str) or kind not in allowed_kinds:
        return (
            _extension_operation_result(
                payload,
                success=False,
                tool_names=[],
                error="Invalid extension kind.",
                request_fingerprint=fingerprint,
            ),
            mcp_client,
        )
    if not isinstance(known_tool_names, list) or any(not isinstance(item, str) for item in known_tool_names):
        return (
            _extension_operation_result(
                payload,
                success=False,
                tool_names=[],
                error="Invalid extension tool names.",
                kind=kind,
                name=name,
                request_fingerprint=fingerprint,
            ),
            mcp_client,
        )

    if tool_registry is None:
        from charlie.tools import registry as tool_registry

    before_tool_state = _capture_tool_registry_state(tool_registry)
    before_entry_state = (
        (entry.enabled, list(entry.tool_names), entry.runtime_warning)
        if entry is not None
        else None
    )
    before_skill_block = getattr(brain, "_installed_skill_blocks", {}).get(name)
    plugin_was_registered = bool(
        plugin_manager is not None
        and getattr(plugin_manager, "get_plugin", lambda _name: None)(name) is not None
    )
    try:
        if operation == "install":
            from charlie.extensions.install import install_extension

            tool_names, mcp_client = install_extension(
                kind,
                name,
                source,
                raw_text,
                registry=tool_registry,
                plugin_manager=plugin_manager,
                mcp_client=mcp_client,
                plugin_allow_dirs=list(getattr(runtime_config, "plugin_allow_dirs", []) or []),
            )
            if kind == "skill":
                from charlie.extensions.skills import format_skill_block, parse_skill_md

                manifest = parse_skill_md(raw_text)
                brain.add_installed_skill_block(name, format_skill_block(manifest))
            if extension_registry is not None:
                extension_registry.record(
                    RuntimeExtension(
                        name=name,
                        kind=kind,
                        source=source,
                        card=build_skill_card(name, source or kind, tool_names, raw_text or name),
                        raw_text=raw_text,
                        enabled=True,
                        tool_names=list(tool_names),
                        runtime_warning=None,
                    )
                )
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
                from charlie.extensions.install import install_extension

                tool_names, mcp_client = install_extension(
                    kind,
                    name,
                    source,
                    raw_text,
                    registry=tool_registry,
                    plugin_manager=plugin_manager,
                    mcp_client=mcp_client,
                    plugin_allow_dirs=list(getattr(runtime_config, "plugin_allow_dirs", []) or []),
                )
                if kind == "skill":
                    from charlie.extensions.skills import format_skill_block, parse_skill_md

                    brain.add_installed_skill_block(name, format_skill_block(parse_skill_md(raw_text)))
            if entry is not None:
                entry.enabled = True
                entry.tool_names = list(tool_names)
                entry.runtime_warning = None
        elif operation == "disable":
            if kind == "mcp":
                if mcp_client is None or not mcp_client.disable_server(tool_registry, name):
                    raise KeyError(f"MCP server '{name}' is not registered in main runtime.")
            elif kind == "plugin":
                from charlie.tools import disable_plugin

                if entry is None or entry.enabled:
                    if plugin_manager.get_plugin(name) is None:
                        raise KeyError(f"Plugin '{name}' is not registered in main runtime.")
                    disable_plugin(tool_registry, plugin_manager, name)
            else:
                for tool_name in known_tool_names:
                    tool_registry.unregister_tool(tool_name)
                if kind == "skill":
                    brain.remove_installed_skill_block(name)
            tool_names = []
            if entry is not None:
                entry.enabled = False
                entry.tool_names = []
                entry.runtime_warning = None
        else:  # uninstall
            if kind == "mcp":
                if mcp_client is None or not mcp_client.remove_server(tool_registry, name):
                    raise KeyError(f"MCP server '{name}' is not registered in main runtime.")
            elif kind == "plugin":
                from charlie.tools import disable_plugin

                if entry is None or entry.enabled:
                    if plugin_manager.get_plugin(name) is None:
                        raise KeyError(f"Plugin '{name}' is not registered in main runtime.")
                    disable_plugin(tool_registry, plugin_manager, name)
            else:
                if entry is not None and entry.enabled:
                    for tool_name in known_tool_names:
                        tool_registry.unregister_tool(tool_name)
                if kind == "skill":
                    brain.remove_installed_skill_block(name)
            tool_names = []
            if extension_registry is not None:
                extension_registry.remove(name)

        if not isinstance(tool_names, list) or any(not isinstance(item, str) for item in tool_names):
            raise ValueError("Extension owner returned invalid tool names.")
        brain.rebuild_stable_tier()
        return (
            _extension_operation_result(
                payload,
                success=True,
                tool_names=list(tool_names),
                kind=kind,
                name=name,
                request_fingerprint=fingerprint,
            ),
            mcp_client,
        )
    except Exception as exc:
        reason = str(exc).strip() or type(exc).__name__
        rollback_ok = True
        try:
            if operation in {"install", "enable"}:
                if kind == "mcp" and mcp_client is not None:
                    if operation == "install":
                        mcp_client.remove_server(tool_registry, name)
                    elif operation == "enable" and before_entry_state and not before_entry_state[0]:
                        mcp_client.disable_server(tool_registry, name)
                elif kind == "plugin" and plugin_manager is not None:
                    from charlie.tools import disable_plugin

                    if plugin_manager.get_plugin(name) is not None and (
                        not plugin_was_registered
                        or (operation == "enable" and before_entry_state and not before_entry_state[0])
                    ):
                        disable_plugin(tool_registry, plugin_manager, name)
                if kind == "skill" and before_skill_block is None:
                    brain.remove_installed_skill_block(name)
            elif operation in {"disable", "uninstall"} and before_entry_state and before_entry_state[0]:
                if kind == "plugin":
                    from charlie.extensions.install import builtin_plugin
                    from charlie.tools import enable_plugin

                    enable_plugin(
                        tool_registry,
                        plugin_manager,
                        builtin_plugin(name, list(getattr(runtime_config, "plugin_allow_dirs", []) or [])),
                    )
                else:
                    from charlie.extensions.install import install_extension

                    install_extension(
                        kind,
                        name,
                        source,
                        raw_text,
                        registry=tool_registry,
                        plugin_manager=plugin_manager,
                        mcp_client=mcp_client,
                        plugin_allow_dirs=list(getattr(runtime_config, "plugin_allow_dirs", []) or []),
                    )
                if kind == "skill" and before_skill_block is not None:
                    brain.add_installed_skill_block(name, before_skill_block)

            _restore_tool_registry_state(tool_registry, before_tool_state)
            if extension_registry is not None:
                if operation == "install":
                    extension_registry.remove(name)
                elif entry is not None and before_entry_state is not None:
                    if extension_registry.get(name) is None:
                        extension_registry.record(entry)
                    entry.enabled, entry.tool_names, entry.runtime_warning = before_entry_state
            if kind == "skill":
                if before_skill_block is None:
                    brain.remove_installed_skill_block(name)
                else:
                    brain.add_installed_skill_block(name, before_skill_block)
            brain.rebuild_stable_tier()
        except Exception:
            rollback_ok = False
        if not rollback_ok and extension_registry is not None:
            current_tools = getattr(tool_registry, "_tools", {})
            before_entry_tools = set(before_entry_state[1]) if before_entry_state else set()
            newly_present_tools = set(current_tools) - set(before_tool_state)
            actual_tool_names = [
                tool_name
                for tool_name in current_tools
                if tool_name in before_entry_tools or tool_name in newly_present_tools
            ]
            if entry is not None:
                if extension_registry.get(name) is None:
                    extension_registry.record(entry)
                entry.enabled = bool(actual_tool_names)
                entry.tool_names = actual_tool_names
                entry.runtime_warning = "Runtime extension degraded"
            elif operation == "install" and actual_tool_names:
                extension_registry.record(
                    RuntimeExtension(
                        name=name,
                        kind=kind,
                        source=source,
                        card=build_skill_card(name, source or kind, actual_tool_names, raw_text or name),
                        raw_text=raw_text,
                        enabled=True,
                        tool_names=actual_tool_names,
                        runtime_warning="Runtime extension degraded",
                    )
                )
            try:
                brain.rebuild_stable_tier()
            except Exception:
                logger.warning("Failed to rebuild stable tier after degraded extension rollback", exc_info=True)
        safe_reason = _safe_extension_error({"raw_text": raw_text, "source": source}, reason)
        if not rollback_ok:
            safe_reason = f"{safe_reason}; runtime extension state is degraded"
        logger.warning("Main extension operation failed: %s", safe_reason)
        return (
            _extension_operation_result(
                payload,
                success=False,
                tool_names=[],
                error=safe_reason,
                kind=kind,
                name=name,
                request_fingerprint=fingerprint,
            ),
            mcp_client,
        )


def _cache_extension_result(
    result_cache: OrderedDict[str, dict[str, Any]],
    request_id: str,
    payload: dict[str, Any],
    fingerprint_cache: dict[str, str],
    fingerprint: str,
) -> None:
    result_cache[request_id] = payload
    result_cache.move_to_end(request_id)
    fingerprint_cache[request_id] = fingerprint
    while len(result_cache) > _EXTENSION_RESULT_CACHE_MAX:
        evicted_id, _ = result_cache.popitem(last=False)
        fingerprint_cache.pop(evicted_id, None)


async def _handle_extension_operation_request(
    payload: Any,
    *,
    brain: Any,
    plugin_manager: Any,
    mcp_client: Any,
    runtime_config: Any,
    tool_registry: Any,
    extension_registry: ExtensionRuntimeRegistry,
    event_bus: Any,
    result_cache: OrderedDict[str, dict[str, Any]],
    in_flight: dict[str, Any],
    fingerprint_cache: dict[str, str],
    operation_lock: Optional[asyncio.Lock] = None,
) -> tuple[dict[str, Any], Any]:
    payload = payload if isinstance(payload, dict) else {}
    request_id = str(payload.get("request_id") or uuid.uuid4().hex)
    payload = dict(payload)
    payload["request_id"] = request_id
    operation = str(payload.get("operation") or "invalid")
    supplied_fingerprint = payload.get("request_fingerprint")
    fingerprint = canonical_extension_request_fingerprint(operation, payload)
    payload["request_fingerprint"] = fingerprint

    async def publish(result: dict[str, Any], *, cache: bool = True) -> dict[str, Any]:
        if cache:
            _cache_extension_result(result_cache, request_id, result, fingerprint_cache, fingerprint)
        await event_bus.emit(
            "extension_operation_result",
            result,
            meta=EventMeta(source=EventSource.BRAIN, task_id=request_id, rationale="main extension authority result"),
        )
        return result

    if supplied_fingerprint is not None and supplied_fingerprint != fingerprint:
        conflict = _extension_operation_result(
            payload,
            success=False,
            tool_names=[],
            error="request_id is already bound to a different extension operation",
            request_fingerprint=fingerprint,
        )
        conflict["runtime_status"] = "request_id_conflict"
        return await publish(conflict, cache=False), mcp_client

    cached = result_cache.get(request_id)
    if cached is not None:
        if fingerprint_cache.get(request_id) != fingerprint:
            conflict = _extension_operation_result(
                payload,
                success=False,
                tool_names=[],
                error="request_id is already bound to a different extension operation",
                request_fingerprint=fingerprint,
            )
            conflict["runtime_status"] = "request_id_conflict"
            return await publish(conflict, cache=False), mcp_client
        return await publish(cached), mcp_client

    existing_entry = in_flight.get(request_id)
    existing = existing_entry[0] if isinstance(existing_entry, tuple) else existing_entry
    existing_fingerprint = (
        existing_entry[1] if isinstance(existing_entry, tuple) and len(existing_entry) == 2 else None
    )
    current = asyncio.current_task()
    if existing is not None and existing is not current and not existing.done():
        if existing_fingerprint != fingerprint:
            conflict = _extension_operation_result(
                payload,
                success=False,
                tool_names=[],
                error="request_id is already bound to a different extension operation",
                request_fingerprint=fingerprint,
            )
            conflict["runtime_status"] = "request_id_conflict"
            return await publish(conflict, cache=False), mcp_client
        await asyncio.shield(existing)
        cached = result_cache.get(request_id)
        if cached is not None:
            return await publish(cached), mcp_client
        return (
            _extension_operation_result(
                payload, success=False, tool_names=[], error="Extension operation was cancelled"
            ),
            mcp_client,
        )

    if current is not None:
        in_flight[request_id] = (current, fingerprint)
    lock_acquired = False
    if operation_lock is not None:
        await operation_lock.acquire()
        lock_acquired = True
    try:
        result, updated_mcp_client = apply_extension_operation(
            payload,
            brain=brain,
            plugin_manager=plugin_manager,
            mcp_client=mcp_client,
            runtime_config=runtime_config,
            tool_registry=tool_registry,
            extension_registry=extension_registry,
        )
        result["request_fingerprint"] = fingerprint
        await _publish_tool_snapshot(event_bus, tool_registry)
        await _publish_mcp_snapshot(event_bus, updated_mcp_client)
        await _publish_extension_snapshot(
            event_bus,
            extension_registry,
            rationale="extension runtime state changed or operation completed",
        )
        await event_bus.emit(
            "extension_operation_result",
            result,
            meta=EventMeta(source=EventSource.BRAIN, task_id=request_id, rationale="main extension authority result"),
        )
        _cache_extension_result(result_cache, request_id, result, fingerprint_cache, fingerprint)
        return result, updated_mcp_client
    finally:
        entry = in_flight.get(request_id)
        if current is not None and (
            entry is current or (isinstance(entry, tuple) and len(entry) == 2 and entry[0] is current)
        ):
            in_flight.pop(request_id, None)
        if lock_acquired:
            operation_lock.release()


def _try_build_mcp_snapshot(mcp_client: Any) -> Optional[dict[str, Any]]:
    """Build a snapshot for operation accounting without inventing state."""
    try:
        return _build_mcp_snapshot(mcp_client)
    except Exception as exc:
        logger.error("Could not build authoritative MCP snapshot: %s", exc, exc_info=True)
        return None


def _set_subsystem_health(name: str, status: HealthStatus, public_detail: Optional[str] = None) -> None:
    """Record one safe public subsystem transition."""
    _runtime_health.set(name, status, public_detail=public_detail)


def _on_brain_llm_health(status: HealthStatus, detail: Optional[str] = None) -> None:
    """Callback when Brain's primary conversational LLM health transitions."""
    _set_subsystem_health("llm", status, public_detail=detail)
    if status == HealthStatus.RUNNING:
        _set_subsystem_health("brain", HealthStatus.RUNNING, "Ready")
    elif status == HealthStatus.DEGRADED:
        _set_subsystem_health("brain", HealthStatus.DEGRADED, "Primary LLM unavailable")
    elif status == HealthStatus.STARTING:
        _set_subsystem_health("brain", HealthStatus.STARTING, "Starting")

    bus = _main_event_bus
    if bus is not None:
        try:
            loop = asyncio.get_running_loop()
            _submit_event_task(_publish_subsystem_health(bus), loop)
        except RuntimeError:
            pass


def _log_port_release(host: str, port: int) -> None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            released = probe.connect_ex((host, port)) != 0
        logger.info("port_release | port=%s | released=%s", port, released)
    except Exception as exc:
        logger.warning(
            "port_release | port=%s | released=unknown | error=%s",
            port,
            type(exc).__name__,
        )


class _UnavailableVoiceEngine:
    """No-op voice replacement that keeps non-voice Charlie features available."""

    is_available = False
    is_ready = False
    asr_ready = False
    asr_readiness_status = "failed"

    def __init__(self, detail: str = "Microphone unavailable", *, disabled: bool = False) -> None:
        self.is_speaking = threading.Event()
        self._muted = True
        self._volume = 0.0
        self._readiness_detail = detail
        if disabled:
            self.asr_readiness_status = "disabled"

    def readiness_detail(self) -> str:
        return self._readiness_detail

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
        if self.asr_readiness_status == "disabled":
            return self._readiness_detail
        return "ASR unavailable: microphone engine unavailable"


def _start_voice_or_degrade(
    voice_config: Config,
    on_speech: Callable[[str], None],
    on_tts_start: Callable[[], None],
    on_tts_stop: Callable[[], None],
    on_speech_onset: Optional[Callable[..., None]] = None,
) -> VoiceEngine | _UnavailableVoiceEngine:
    """Start voice while retaining non-audio runtime capabilities on failure."""
    if not getattr(voice_config, "voice_enabled", True):
        detail = "Voice input and output disabled by VOICE_ENABLED=false"
        for name in ("voice", "voice_capture", "asr"):
            _set_subsystem_health(name, HealthStatus.DISABLED, detail)
        return _UnavailableVoiceEngine(detail, disabled=True)

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


def _safe_speak(
    voice, text: str, emotion: str, label: str = "", *, channel: Optional[str] = None
) -> None:
    """Speak text, logging (not swallowing) any TTS failure.

    A mid-stream TTS error must never abort the answer generation loop --
    the UI token stream and message persistence downstream must still run.
    """
    if channel == "console":
        return
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


def _print_console_reply(text: str) -> None:
    print(f"\nCharlie: {text}", flush=True)


def _schedule_process(coro, loop):
    fut = _submit_event_threadsafe(coro, loop)
    if fut is None:
        return None
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
    including the foreground voice loop, for that long.
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


async def _deliver_background_result(task_id, summary, *, db_path, telegram_bot, telegram_user_id, voice):
    store = ResultsStore(db_path=db_path)
    try:
        record = store.get(task_id)
    finally:
        store.close()
    full_result = record.full_result.strip() if record and record.full_result else ""
    message = f"{summary}\n\n{full_result}" if full_result else summary
    if len(message) > 4000:
        message = message[:3960].rstrip() + "\n[Full result retained locally.]"

    delivery = {"telegram": "not_configured", "voice": "not_configured"}
    if telegram_bot is not None and isinstance(telegram_user_id, int) and telegram_user_id > 0:
        try:
            await telegram_bot.send_message(telegram_user_id, message)
            delivery["telegram"] = "accepted"
        except Exception:
            delivery["telegram"] = "failed"
            logger.warning("Telegram background-result delivery failed for %s", task_id, exc_info=True)
    if bool(getattr(voice, "is_ready", False)):
        try:
            delivery["voice"] = "queued" if voice.speak(summary, "neutral") is None else "not_queued"
        except Exception:
            delivery["voice"] = "failed"
            logger.warning("Voice background-result delivery failed for %s", task_id, exc_info=True)
    elif voice is not None:
        delivery["voice"] = "not_ready"
    return delivery


async def main() -> int:
    global _main_event_bus, _main_event_bus_registry
    loop = asyncio.get_running_loop()
    settings_service = SettingsService(config_instance=config)
    _configure_runtime_health(config)
    if background_task.is_admission_open():
        _set_subsystem_health_if_known("background_tasks", HealthStatus.RUNNING, "Admission open")
    _orig_handler = loop.call_exception_handler

    def _guarded_handler(ctx):
        if not isinstance(ctx.get("exception"), asyncio.CancelledError):
            _orig_handler(ctx)

    loop.call_exception_handler = _guarded_handler

    logger.info("Charlie is waking up...")
    voice = None
    console_ingress = None
    store = None
    audit_store = None
    memory_graph = None
    brain = None
    calendar_runtime = None
    zmq_handler = None
    speech_echo_cooldown = 0.0
    last_emotion = "neutral"
    # VAD-fragmented duplicate text within this window is suppressed (see on_speech).
    recent_turn_texts: Dict[str, float] = {}
    _DEDUPE_WINDOW_SEC = 20.0
    telegram_bot = None
    mcp_client = None
    mcp_start_task = None
    exit_code = 0
    shutdown_quiescent = True
    # True while a chat turn's LLM/tool loop runs -- see _dispatch_or_queue.
    turn_active = False
    pending_turns: list[TurnRequest] = []
    pending_turn_times: Dict[str, float] = {}
    voice_diagnostic_traces: Dict[str, Any] = {}
    turn_channels_by_id: Dict[str, str] = {}
    repeated_success_patterns_by_turn: Dict[str, list[dict[str, str]]] = {}
    telegram_status_messages_by_turn: Dict[str, int] = {}
    telegram_status_text_by_turn: Dict[str, str] = {}
    telegram_status_tasks_by_turn: Dict[str, asyncio.Task] = {}
    telegram_approval_turn_by_request: Dict[str, str] = {}
    telegram_approval_expiry_tasks: Dict[str, asyncio.Task] = {}
    telegram_origin_turn_ids: set[str] = set()
    telegram_background_tasks_by_turn: Dict[str, set[str]] = {}
    telegram_background_task_ids: set[str] = set()
    # ponytail: one-owner Telegram bridge; serialize edits globally, split per-chat if multi-owner support arrives.
    telegram_status_lock = asyncio.Lock()
    active_turn_id: Optional[str] = None
    active_turn_session_id: Optional[str] = None
    active_task_id: Optional[str] = None
    active_operation_name: Optional[str] = None
    active_operation_task_id: Optional[str] = None
    active_operation_cancellable = True
    runtime_shutting_down = False
    watcher_callback_lock = threading.Lock()
    watcher_stop_event = threading.Event()
    watcher_thread: Optional[threading.Thread] = None
    settings_operation_lock = asyncio.Lock()
    session_lifecycle_gate = asyncio.Lock()
    extension_runtime_registry = ExtensionRuntimeRegistry()
    extension_operation_results: OrderedDict[str, dict[str, Any]] = OrderedDict()
    extension_operation_in_flight: dict[str, Any] = {}
    extension_operation_fingerprints: dict[str, str] = {}
    event_bus_registry = _EventBusSubmissionRegistry()
    _main_event_bus_registry = event_bus_registry

    def _begin_shutdown() -> None:
        nonlocal runtime_shutting_down
        with watcher_callback_lock:
            if runtime_shutting_down:
                return
            runtime_shutting_down = True
        if console_ingress is not None and not console_ingress.stop():
            logger.debug("Console input reader remains blocked on stdin during shutdown")
        _runtime_health.set_runtime_lifecycle(RuntimeStatus.SHUTTING_DOWN)
        _set_subsystem_health_if_known("background_tasks", HealthStatus.SHUTTING_DOWN, "Admission closing")
        background_task.close_admission()
        event_bus_registry.close()
        bus = _main_event_bus
        if bus is not None:
            try:
                _submit_event_task(_publish_subsystem_health(bus), loop)
            except RuntimeError:
                logger.debug("Unable to publish runtime shutdown health", exc_info=True)

    def _stop_voice(*, final: bool) -> None:
        nonlocal voice, shutdown_quiescent, exit_code
        if voice is None:
            return

        def _set_optional_voice_health(name: str, status: HealthStatus, detail: str) -> None:
            try:
                _set_subsystem_health(name, status, detail)
            except ValueError:
                logger.debug("Voice health projection does not expose subsystem %s", name)

        try:
            result = voice.stop()
            if result is None:
                voice = None
                return
            if bool(getattr(result, "quiescent", False)):
                _set_subsystem_health("voice", HealthStatus.STOPPED, "Stopped")
                _set_optional_voice_health("voice_capture", HealthStatus.STOPPED, "Stopped")
                _set_optional_voice_health("asr", HealthStatus.STOPPED, "Stopped")
                voice = None
                return

            _set_subsystem_health("voice", HealthStatus.DEGRADED, "Voice shutdown incomplete")
            _set_optional_voice_health("voice_capture", HealthStatus.DEGRADED, "Voice shutdown incomplete")
            _set_optional_voice_health("asr", HealthStatus.DEGRADED, "Voice shutdown incomplete")
            if final:
                shutdown_quiescent = False
                exit_code = 1
            logger.error(
                "main_voice_shutdown_incomplete | final=%s | alive_threads=%s "
                "| asr_process_alive=%s | diagnostics_worker_alive=%s | errors=%s",
                final,
                getattr(result, "alive_threads", ()),
                getattr(result, "asr_process_alive", None),
                getattr(result, "diagnostics_worker_alive", None),
                getattr(result, "errors", ()),
            )
        except Exception:
            _set_subsystem_health("voice", HealthStatus.DEGRADED, "Voice shutdown failed")
            if final:
                shutdown_quiescent = False
                exit_code = 1
            logger.error("Voice subsystem shutdown failed", exc_info=True)

    def _stop_watcher() -> None:
        nonlocal watcher_thread, exit_code
        if watcher_thread is None:
            watcher_stop_event.set()
            _set_subsystem_health_if_known("watchers", HealthStatus.STOPPED, "Stopped")
            return
        stopped = _stop_watcher_thread(watcher_stop_event, watcher_thread)
        if stopped:
            watcher_thread = None
            _set_subsystem_health("watchers", HealthStatus.STOPPED)
            return
        exit_code = 1
        _set_subsystem_health("watchers", HealthStatus.DEGRADED, "Watcher shutdown timed out")
        logger.error("Watcher thread did not stop within %.1fs", _WATCHER_SHUTDOWN_TIMEOUT_S)

    async def _drain_runtime_submissions() -> None:
        nonlocal shutdown_quiescent, exit_code
        try:
            await _drain_event_bus_submissions(event_bus_registry, loop=loop)
        except (RuntimeError, asyncio.CancelledError) as exc:
            shutdown_quiescent = False
            exit_code = 1
            logger.error("Main runtime submission drain incomplete; dependent stores remain open: %s", exc)

    global background_housekeeping_tasks
    active_process_task: Optional[asyncio.Task] = None
    # Keep the module-level mirror for existing launch-boundary diagnostics.
    globals()["active_process_task"] = None
    background_housekeeping_tasks.clear()

    def _cancel_housekeeping() -> list[asyncio.Task]:
        cancelled: list[asyncio.Task] = []
        for task in tuple(background_housekeeping_tasks):
            if not task.done():
                task.cancel()
                cancelled.append(task)
        if brain is not None:
            try:
                brain_tasks = brain.cancel_background_tasks()
                if brain_tasks:
                    cancelled.extend([t for t in brain_tasks if isinstance(t, asyncio.Task)])
            except Exception:
                pass
        return cancelled

    try:
        try:
            store = SessionStore(config.session_db_path)
            orphan_counter = getattr(store, "count_legacy_orphans", None)
            if callable(orphan_counter):
                orphan_counts = orphan_counter()
                if any(orphan_counts.values()):
                    logger.warning("legacy_session_orphans | counts=%s", orphan_counts)
        except Exception as e:
            logger.error(f"Failed to initialize SessionStore: {e}")
            raise
        from charlie.audit_store import AuditStore

        audit_store = AuditStore(config.session_db_path)
        memory_graph, memory_store, memory_service = _compose_memory_dependencies(config)
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

        def on_tool_call(name, args, *, turn_id=None, task_id=None, session_id=None):
            nonlocal active_operation_name, active_operation_task_id, active_operation_cancellable
            if task_id == active_task_id:
                active_operation_name = name
                active_operation_task_id = task_id
                active_operation_cancellable = name not in _NON_CANCELLABLE_FOREGROUND_TOOLS
            event_session_id = session_id or current_session_id
            try:
                active_audit_store = audit_store
            except NameError:
                active_audit_store = None
            if active_audit_store is not None:
                active_audit_store.record(name, args, "requested")
            if event_bus:
                _submit_event_threadsafe(
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
            event_session_id = session_id or current_session_id
            if event_bus:
                _submit_event_threadsafe(
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
                if envelope.data.get("persistence_status") == "failed":
                    status = f"{status}:persistence_failed"
                turn_id = envelope.turn_id
                from charlie.self_extension.orchestrator import repeated_success_audit_metadata

                audit_arguments = repeated_success_audit_metadata(
                    envelope,
                    turn_channels_by_id.get(turn_id or "", ""),
                    name,
                )
                active_audit_store.record(name, audit_arguments, str(status))
                signature = audit_arguments.get("repeated_success_signature")
                risk_class = audit_arguments.get("risk_class")
                if signature and turn_id:
                    patterns = repeated_success_patterns_by_turn.setdefault(turn_id, [])
                    if not any(item["signature"] == signature for item in patterns):
                        patterns.append(
                            {
                                "request": envelope.request,
                                "capability": envelope.capability or "",
                                "operation": envelope.operation or name,
                                "signature": signature,
                                "risk_class": risk_class,
                            }
                        )

        def on_intent_decision(decision: IntentDecision):
            """Observe the one primary route selected for an interactive turn."""

            logger.info(
                "Intent decision: turn=%s session=%s intent=%s policy=%s fresh=%s action=%s durable=%s "
                "source=%s capabilities=%s",
                decision.turn_id,
                decision.session_id,
                decision.intent,
                decision.execution_policy,
                decision.freshness_requirement,
                decision.external_action_required,
                decision.durable_work_required,
                decision.routing_source,
                decision.capabilities,
            )

        async def _set_telegram_turn_status(
            turn_id: str, text: str, *, create: bool = False
        ) -> None:
            if telegram_bot is None or config.telegram_user_id <= 0:
                return
            async with telegram_status_lock:
                message_id = telegram_status_messages_by_turn.get(turn_id)
                previous_text = telegram_status_text_by_turn.get(turn_id)
                if previous_text == text and (message_id is not None or not create):
                    return
                telegram_status_text_by_turn[turn_id] = text
                if message_id is None:
                    if create:
                        message_id = await telegram_bot.send_status_message(
                            config.telegram_user_id, text
                        )
                        if message_id is not None:
                            telegram_status_messages_by_turn[turn_id] = message_id
                else:
                    await telegram_bot.edit_status_message(
                        config.telegram_user_id, message_id, text
                    )

        async def _show_telegram_status_after_delay(turn_id: str) -> None:
            await asyncio.sleep(2.0)
            if telegram_status_tasks_by_turn.get(turn_id) is not asyncio.current_task():
                return
            await _set_telegram_turn_status(
                turn_id,
                telegram_status_text_by_turn.get(turn_id, "Working on it…"),
                create=True,
            )

        async def _start_telegram_turn_feedback(turn_id: str) -> None:
            if telegram_bot is None or config.telegram_user_id <= 0:
                return
            await telegram_bot.send_typing(config.telegram_user_id)
            if turn_id in telegram_status_messages_by_turn:
                await _set_telegram_turn_status(turn_id, "Working on it…")
                return
            telegram_status_text_by_turn[turn_id] = "Working on it…"
            old_task = telegram_status_tasks_by_turn.get(turn_id)
            if old_task is not None and not old_task.done():
                old_task.cancel()
            telegram_status_tasks_by_turn[turn_id] = asyncio.create_task(
                _show_telegram_status_after_delay(turn_id),
                name=f"telegram-status-{turn_id}",
            )

        async def _finish_telegram_turn_feedback(turn_id: str) -> None:
            task = telegram_status_tasks_by_turn.pop(turn_id, None)
            if task is not None and task is not asyncio.current_task() and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            message_id = telegram_status_messages_by_turn.pop(turn_id, None)
            telegram_status_text_by_turn.pop(turn_id, None)
            for request_id, approval_turn_id in list(telegram_approval_turn_by_request.items()):
                if approval_turn_id == turn_id:
                    telegram_approval_turn_by_request.pop(request_id, None)
            if not telegram_background_tasks_by_turn.get(turn_id):
                telegram_origin_turn_ids.discard(turn_id)
            if message_id is not None and telegram_bot is not None:
                await telegram_bot.delete_status_message(config.telegram_user_id, message_id)
            for task_id in telegram_background_tasks_by_turn.get(turn_id, set()):
                if task_id in telegram_status_messages_by_turn:
                    continue
                task = telegram_status_tasks_by_turn.get(task_id)
                if task is None or task.done():
                    telegram_status_tasks_by_turn[task_id] = asyncio.create_task(
                        _show_telegram_status_after_delay(task_id),
                        name=f"telegram-status-{task_id}",
                    )

        def _schedule_telegram_status_update(turn_id: str, text: str) -> None:
            def schedule() -> None:
                if not runtime_shutting_down and turn_id in telegram_status_text_by_turn:
                    asyncio.create_task(_set_telegram_turn_status(turn_id, text))

            try:
                loop.call_soon_threadsafe(schedule)
            except RuntimeError:
                logger.debug("Telegram status update dropped after loop shutdown")

        def _schedule_telegram_task_status(
            task_id: str, text: str = "", *, finished: bool = False, parent_turn_id: Optional[str] = None
        ) -> None:
            def schedule() -> None:
                if runtime_shutting_down:
                    return
                if finished:
                    asyncio.create_task(_finish_telegram_turn_feedback(task_id))
                    telegram_background_task_ids.discard(task_id)
                    if parent_turn_id:
                        task_ids = telegram_background_tasks_by_turn.get(parent_turn_id)
                        if task_ids is not None:
                            task_ids.discard(task_id)
                            if not task_ids:
                                telegram_background_tasks_by_turn.pop(parent_turn_id, None)
                                telegram_origin_turn_ids.discard(parent_turn_id)
                    return
                telegram_status_text_by_turn[task_id] = text
                if parent_turn_id and active_turn_id == parent_turn_id:
                    return
                if task_id in telegram_status_messages_by_turn:
                    asyncio.create_task(_set_telegram_turn_status(task_id, text))
                elif task_id not in telegram_status_tasks_by_turn or telegram_status_tasks_by_turn[task_id].done():
                    telegram_status_tasks_by_turn[task_id] = asyncio.create_task(
                        _show_telegram_status_after_delay(task_id),
                        name=f"telegram-status-{task_id}",
                    )

            try:
                loop.call_soon_threadsafe(schedule)
            except RuntimeError:
                logger.debug("Telegram task status dropped after loop shutdown")

        def _on_task_journal_change(record) -> None:
            origin = getattr(record.origin, "value", record.origin)
            if origin not in {TaskOrigin.BACKGROUND.value, TaskOrigin.RESEARCH.value}:
                return
            parent_turn_id = record.turn_id
            if parent_turn_id and (
                parent_turn_id in telegram_origin_turn_ids
                or turn_channels_by_id.get(parent_turn_id) == "telegram"
            ):
                telegram_origin_turn_ids.add(parent_turn_id)
                telegram_background_tasks_by_turn.setdefault(parent_turn_id, set()).add(record.id)
                telegram_background_task_ids.add(record.id)
            elif record.id not in telegram_background_task_ids:
                return
            if record.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
                _schedule_telegram_task_status(
                    record.id,
                    finished=True,
                    parent_turn_id=parent_turn_id,
                )
                return
            status_text = {
                TaskStatus.QUEUED: "Queued. I’ll get to this next.",
                TaskStatus.APPROVAL_REQUIRED: "Waiting for your approval…",
            }.get(record.status, "Working on it…")
            _schedule_telegram_task_status(
                record.id,
                status_text,
                parent_turn_id=parent_turn_id,
            )

        get_task_journal().set_on_change(_on_task_journal_change)

        def on_thinking_update(name, args, *, turn_id=None, task_id=None, session_id=None):
            event_session_id = session_id or current_session_id
            if turn_id and turn_channels_by_id.get(turn_id) == "telegram":
                background_record = None
                if task_id:
                    try:
                        background_record = get_task_journal().get(task_id)
                    except KeyError:
                        pass
                if background_record is not None and background_record.origin in {
                    TaskOrigin.BACKGROUND,
                    TaskOrigin.RESEARCH,
                }:
                    parent_turn_id = background_record.turn_id or turn_id
                    telegram_origin_turn_ids.add(parent_turn_id)
                    telegram_background_tasks_by_turn.setdefault(parent_turn_id, set()).add(task_id)
                    telegram_background_task_ids.add(task_id)
                    _schedule_telegram_task_status(task_id, "Working on it…", parent_turn_id=parent_turn_id)
                else:
                    _schedule_telegram_status_update(turn_id, "Checking that…")
            if event_bus:
                desc = f"I'll use the {name} tool"
                if args:
                    summary = str(args)[:80]
                    desc += f" with {summary}"
                _submit_event_threadsafe(
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

        def _resolve_tool_approval_and_notify(
            request_id: str,
            approved: bool,
            *,
            expected_platform: Optional[str] = None,
        ) -> bool:
            """Resolve one pending approval future and report whether it was current."""
            from charlie.core import get_active_tool_approval, resolve_tool_approval

            if not isinstance(request_id, str) or not request_id:
                logger.warning("Rejected malformed tool approval request id")
                return False

            if expected_platform is not None and get_active_tool_approval() != (
                request_id,
                expected_platform,
            ):
                logger.warning("Rejected approval for a stale or wrong-channel request: %s", request_id)
                return False

            if expected_platform == "telegram" and approved:
                journal = get_task_journal()
                background_record = next(
                    (
                        record
                        for record in journal.list(include_terminal=False)
                        if record.origin is TaskOrigin.BACKGROUND
                        and record.status is TaskStatus.APPROVAL_REQUIRED
                        and record.approval_reference == request_id
                    ),
                    None,
                )
                if background_record is not None:
                    try:
                        journal.transition(
                            background_record.id,
                            TaskStatus.RUNNING,
                            waiting_reason="",
                            approval_reference="",
                        )
                    except Exception:
                        logger.error(
                            "Could not resume background task after approval %s",
                            request_id,
                            exc_info=True,
                        )
                        return resolve_tool_approval(
                            request_id,
                            False,
                            expected_platform=expected_platform,
                        )

            if not resolve_tool_approval(
                request_id,
                approved,
                expected_platform=expected_platform,
            ):
                logger.warning("Ignored stale or unknown tool approval: %s", request_id)
                return False
            expiry_task = telegram_approval_expiry_tasks.pop(request_id, None)
            if expiry_task is not None and not expiry_task.done():
                expiry_task.cancel()
            if event_bus is not None:
                _submit_event_threadsafe(
                    event_bus.emit(
                        "tool_approval_resolved",
                        {"request_id": request_id},
                        meta=EventMeta(source=EventSource.BRAIN),
                    ),
                    loop,
                )
            approval_turn_id = telegram_approval_turn_by_request.pop(request_id, None)
            if approved and approval_turn_id:
                _schedule_telegram_status_update(approval_turn_id, "Working on it…")
            return True

        async def _delete_telegram_approval_after_timeout(request_id: str) -> None:
            """Remove a foreground approval prompt after its core wait expires."""
            try:
                from charlie.core import _TELEGRAM_TOOL_APPROVAL_TIMEOUT_SEC

                await asyncio.sleep(_TELEGRAM_TOOL_APPROVAL_TIMEOUT_SEC + 0.5)
                if telegram_bot is not None:
                    await telegram_bot.delete_approval_message(request_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "Could not clear expired Telegram approval %s",
                    request_id,
                    exc_info=True,
                )
            finally:
                current = telegram_approval_expiry_tasks.get(request_id)
                if current is asyncio.current_task():
                    telegram_approval_expiry_tasks.pop(request_id, None)

        async def _handle_voice_control(request: TurnRequest, control: Any) -> None:
            nonlocal speech_echo_cooldown
            from charlie.core import get_active_tool_approval

            action = getattr(control, "action", control)
            target = getattr(control, "target", None)
            trace = voice_diagnostic_traces.get(request.turn_id)
            pending_approval = get_active_tool_approval()
            if pending_approval and pending_approval[1] == "voice" and action != "stop_tts":
                voice.stop_tts()
                _resolve_tool_approval_and_notify(
                    pending_approval[0],
                    False,
                    expected_platform="voice",
                )
                handled = True
                logger.info("voice_control_applied | control=%s | action=decline_approval", action)
            elif action == "stop_tts":
                tts_active = bool(getattr(voice, "is_speaking", None) and voice.is_speaking.is_set())
                if tts_active:
                    voice.stop_tts()
                    handled = True
                    logger.info("voice_control_applied | control=%s | action=stop_tts", action)
                else:
                    handled = False
                    logger.info("voice_control_ignored | control=%s | reason=no_active_tts", action)
            elif action == "cancel_task":
                task = background_task.find_task(target)
                if task is None:
                    handled = False
                    logger.info(
                        "voice_control_ignored | control=%s | target=%s | reason=task_not_found",
                        action,
                        target,
                    )
                else:
                    voice.stop_tts()
                    handled = background_task.cancel(task.id)
                    logger.info(
                        "voice_control_applied | control=%s | target=%s | task_id=%s | action=request_cancel",
                        action,
                        target,
                        task.id,
                    )
            elif action == "cancel_all_tasks":
                cancelled_ids = background_task.cancel_all()
                queued_ids = [item.turn_id for item in pending_turns]
                queued_telegram_ids = [
                    item.turn_id for item in pending_turns if item.channel == "telegram"
                ]
                pending_turns.clear()
                for queued_id in queued_ids:
                    pending_turn_times.pop(queued_id, None)
                    voice_diagnostic_traces.pop(queued_id, None)
                for queued_id in queued_telegram_ids:
                    await _finish_telegram_turn_feedback(queued_id)
                foreground_handled = await _apply_voice_control(
                    "cancel",
                    voice=voice,
                    brain=brain,
                    active_turn=active_turn_id is not None,
                    active_operation_cancellable=active_operation_cancellable,
                    active_process_task=active_process_task,
                    cancel_housekeeping=_cancel_housekeeping,
                )
                handled = bool(cancelled_ids or queued_ids) or foreground_handled
                logger.info(
                    "voice_control_applied | control=%s | action=cancel_all | "
                    "task_count=%s | queued_count=%s | foreground=%s",
                    action,
                    len(cancelled_ids),
                    len(queued_ids),
                    foreground_handled,
                )
            else:
                handled = await _apply_voice_control(
                    "cancel" if action in {"cancel_foreground", "abandon_foreground"} else action,
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
                        "control": action,
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

        async def on_tool_approval_request(
            request_id,
            tool_name,
            reason,
            platform,
            risk_class,
            *,
            operation_preview=None,
            turn_id=None,
            task_id=None,
            session_id=None,
        ):
            if platform == "telegram":
                if not telegram_bot:
                    return False

                from charlie.telegram_bot import should_relay_approval

                if not should_relay_approval(True, config.telegram_user_id):
                    return False

                journal = get_task_journal()
                background_task_id = None
                if task_id:
                    try:
                        record = journal.get(task_id)
                    except KeyError:
                        record = None
                    if record is not None and record.origin is TaskOrigin.BACKGROUND:
                        try:
                            journal.require_approval(task_id, approval_reference=request_id)
                        except Exception:
                            logger.warning(
                                "Could not record background approval request %s for task %s",
                                request_id,
                                task_id,
                                exc_info=True,
                            )
                            return False
                        background_task_id = task_id

                sent = False
                try:
                    status_key = task_id if background_task_id is not None else turn_id
                    if status_key:
                        telegram_approval_turn_by_request[request_id] = status_key
                        await _set_telegram_turn_status(status_key, "Waiting for your approval…")
                    await asyncio.wait_for(
                        telegram_bot.send_approval_request(
                            config.telegram_user_id,
                            request_id,
                            tool_name,
                            _telegram_approval_reason(tool_name, risk_class, reason),
                            operation_preview=operation_preview,
                        ),
                        timeout=15.0,
                    )
                    sent = True
                    if background_task_id is None:
                        telegram_approval_expiry_tasks[request_id] = asyncio.create_task(
                            _delete_telegram_approval_after_timeout(request_id),
                            name=f"telegram-approval-expiry-{request_id}",
                        )
                except asyncio.TimeoutError:
                    logger.warning("Telegram approval request %s timed out before send confirmation", request_id)
                except Exception:
                    logger.warning("Telegram approval request %s could not be sent", request_id, exc_info=True)
                finally:
                    if not sent and background_task_id is not None:
                        try:
                            record = journal.get(background_task_id)
                            if (
                                record.status is TaskStatus.APPROVAL_REQUIRED
                                and record.approval_reference == request_id
                            ):
                                journal.transition(
                                    background_task_id,
                                    TaskStatus.RUNNING,
                                    waiting_reason="",
                                    approval_reference="",
                                )
                        except Exception:
                            logger.warning(
                                "Could not clear failed background approval request %s for task %s",
                                request_id,
                                background_task_id,
                                exc_info=True,
                            )
                return sent
            return None

        async def on_result_stored(task_id, summary, attention_level):
            try:
                return await _deliver_background_result(
                    task_id,
                    summary,
                    db_path=config.session_db_path,
                    telegram_bot=telegram_bot,
                    telegram_user_id=config.telegram_user_id,
                    voice=voice,
                )
            finally:
                if task_id in telegram_background_task_ids:
                    await _finish_telegram_turn_feedback(task_id)
                    telegram_background_task_ids.discard(task_id)
                    for parent_turn_id, task_ids in list(telegram_background_tasks_by_turn.items()):
                        task_ids.discard(task_id)
                        if not task_ids:
                            telegram_background_tasks_by_turn.pop(parent_turn_id, None)
                            telegram_origin_turn_ids.discard(parent_turn_id)

        def on_research_result(report, *, session_id, task_id=None, turn_id=None):
            if event_bus is None:
                return
            payload = {"query": report.query, "text": report.legacy_text()}
            _submit_event_threadsafe(
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

        _set_subsystem_health("llm", HealthStatus.STARTING, "Starting")
        _set_subsystem_health("brain", HealthStatus.STARTING, "Starting")
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
                on_llm_health=_on_brain_llm_health,
            )
        except Exception as e:
            logger.error(f"Failed to initialize Brain: {e}")
            _set_subsystem_health("llm", HealthStatus.DEGRADED, "Brain initialization failed")
            _set_subsystem_health("brain", HealthStatus.DEGRADED, "Brain initialization failed")
            raise RuntimeError("Charlie Brain initialization failed") from e

        # Bounded startup probe for primary conversational LLM.
        # Construction alone does NOT cause brain = RUNNING.
        try:
            llm_ok = await brain.probe_primary_llm(timeout=5.0)
            if not llm_ok:
                logger.warning("Primary LLM unavailable at startup; runtime entering degraded mode")
        except Exception as probe_err:
            logger.warning("Primary LLM startup probe encountered error: %s", probe_err)
            _set_subsystem_health("llm", HealthStatus.DEGRADED, "Probe error")
            _set_subsystem_health("brain", HealthStatus.DEGRADED, "Primary LLM unavailable")

        # Canonical memory facade wiring stays owned by main's composition root.
        _wire_memory_service(memory_service)
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
                        _set_subsystem_health("mcp", HealthStatus.DISABLED)
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
        current_session_id = f"voice_{_LAUNCH_ID}"

        def ensure_session_ready(session_id: str):
            if not session_id:
                return
            try:
                row = store.get_session_record(session_id)
                if row is None:
                    store.create_session(session_id, title="New Chat", source="voice", launch_id=_LAUNCH_ID)
                elif row.get("launch_id") not in (None, _LAUNCH_ID):
                    raise SessionConflictError(f"Session '{session_id}' belongs to another launch")
            except Exception as exc:
                logger.debug(f"ensure_session_ready skipped: {exc}")

        def update_session_title_from_text(session_id: str, user_text: str) -> None:
            if not session_id or not user_text:
                return
            try:
                candidate = " ".join(user_text.strip().split()[:6]).strip()
                if not candidate:
                    return
                if not store.auto_title_session(session_id, candidate):
                    return
                if event_bus:
                    _submit_event_threadsafe(
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

            session_id = current_session_id
            request = _allocate_turn_request(text, session_id, "voice")
            trace = diagnostic_metadata.get("trace") if isinstance(diagnostic_metadata, dict) else None
            if trace is not None:
                trace.bind(turn_id=request.turn_id, session_id=session_id)
                voice_diagnostic_traces[request.turn_id] = trace
            from charlie.personality import parse_voice_control_intent as _parse_voice_control_intent

            control = _parse_voice_control_intent(text)
            if control is not None:
                _schedule_process(_handle_voice_control(request, control), loop)
            else:
                _schedule_process(_dispatch_or_queue(request), loop)

        async def _dispatch_or_queue_impl(
            request: TurnRequest,
            release_lifecycle_gate: Callable[[], None],
        ):
            """Run the turn now, or queue it if one is already running tool calls.

            Only ever called via _schedule_process (run_coroutine_threadsafe), so
            this always executes on the loop thread -- the turn_active check and
            pending_turns mutation below are a single synchronous span with no
            await in between, making them atomic with respect to any other
            coroutine on this loop without needing a lock.
            """
            nonlocal turn_active
            nonlocal active_process_task
            nonlocal active_turn_id, active_turn_session_id, active_task_id
            from charlie.core import get_active_tool_approval

            ensure_session_ready(request.session_id)
            for pending_index, pending_request in enumerate(pending_turns):
                if pending_request.turn_id == request.turn_id:
                    pending_turns.pop(pending_index)
                    pending_turn_times.pop(request.turn_id, None)
                    break
            trace = voice_diagnostic_traces.get(request.turn_id)
            sustained_checker = globals().get("_is_sustained_research_request")
            runtime_config = globals().get("config")
            if (
                callable(sustained_checker)
                and runtime_config is not None
                and request.channel in {"voice", "telegram"}
                and get_active_tool_approval() is None
                and sustained_checker(request.input, runtime_config)
            ):
                if request.channel == "telegram":
                    telegram_origin_turn_ids.add(request.turn_id)
                brain.record_intent_decision(
                    request,
                    intent="research",
                    capabilities=("research", "task"),
                    routing_source="research_router",
                    confidence=1.0,
                    rationale="sustained research request admitted to the durable research lane",
                    execution_policy=ExecutionPolicy.RESEARCH,
                    durable_work_required=True,
                )
                await _start_sustained_research_task(
                    request,
                    runtime_config=runtime_config,
                    event_bus=event_bus,
                    voice=voice,
                    store=store,
                    memory_store=memory_store,
                    memory_graph=memory_graph,
                    memory_service=memory_service,
                    on_result_stored=on_result_stored,
                    on_research_result=on_research_result,
                    on_tool_call=on_tool_call,
                    on_tool_result=on_tool_result,
                    on_operation_result=on_operation_result,
                    on_thinking_update=on_thinking_update,
                )
                if request.channel == "telegram" and not telegram_background_tasks_by_turn.get(request.turn_id):
                    telegram_origin_turn_ids.discard(request.turn_id)
                return

            blocked_by_active_turn = _should_queue_active_turn(
                turn_active,
                get_active_tool_approval() is not None,
                request.channel,
            )

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
                    old_turn_id = active_turn_id
                    logger.info(
                        "supersession_requested | old_turn_id=%s | new_turn_id=%s | reason=new_voice_request",
                        old_turn_id,
                        request.turn_id,
                    )
                    brain.cancel_chat()
                    old_task.cancel()
                    try:
                        await old_task
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        logger.debug("Superseded foreground turn exited with an error", exc_info=True)
                    logger.info(
                        "old_generation_cancellation_completed | old_turn_id=%s | new_turn_id=%s",
                        old_turn_id,
                        request.turn_id,
                    )
                    blocked_by_active_turn = False

            # Approval replies must reach _process() immediately, never queue
            # behind the turn that is waiting for them.
            if blocked_by_active_turn:
                pending_turns.append(request)
                pending_turn_times[request.turn_id] = time.monotonic()
                if request.channel == "telegram":
                    await _set_telegram_turn_status(
                        request.turn_id,
                        "Queued. I’ll get to this next.",
                        create=True,
                    )
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
            globals()["active_process_task"] = active_process_task
            active_turn_id = request.turn_id
            active_turn_session_id = request.session_id
            active_task_id = None
            turn_active = True
            release_lifecycle_gate()
            try:
                if request.channel == "telegram":
                    telegram_origin_turn_ids.add(request.turn_id)
                    await _start_telegram_turn_feedback(request.turn_id)
                await _process(request, brain, voice)
            finally:
                if request.channel == "telegram":
                    try:
                        await _finish_telegram_turn_feedback(request.turn_id)
                    except Exception:
                        logger.warning(
                            "Could not clear Telegram turn status for %s",
                            request.turn_id,
                            exc_info=True,
                        )
                if active_process_task is asyncio.current_task():
                    active_process_task = None
                if globals().get("active_process_task") is asyncio.current_task():
                    globals()["active_process_task"] = None
                if active_turn_id == request.turn_id:
                    turn_active = False
                    active_turn_id = None
                    active_turn_session_id = None
                    active_task_id = None

        async def _dispatch_or_queue(request: TurnRequest):
            await session_lifecycle_gate.acquire()
            gate_released = False

            def release_lifecycle_gate() -> None:
                nonlocal gate_released
                if not gate_released:
                    gate_released = True
                    session_lifecycle_gate.release()

            try:
                return await _dispatch_or_queue_impl(request, release_lifecycle_gate)
            finally:
                release_lifecycle_gate()

        def on_console_text(text: str):
            request = _allocate_turn_request(text, current_session_id, "console")
            _schedule_process(_dispatch_or_queue(request), loop)

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
            nonlocal speech_echo_cooldown, last_emotion, turn_active
            nonlocal active_turn_id, active_turn_session_id, active_task_id
            nonlocal active_operation_name, active_operation_task_id, active_operation_cancellable
            text = request.input
            session_id = request.session_id
            platform = request.channel
            trace = voice_diagnostic_traces.get(request.turn_id)
            queued_at = pending_turn_times.pop(request.turn_id, None)
            dispatch_timestamp = time.monotonic()
            active_turn_id = request.turn_id
            active_turn_session_id = session_id
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

            async def _deliver_immediate_reply(message: str) -> None:
                if platform == "telegram" and telegram_bot is not None:
                    try:
                        await telegram_bot.send_message(config.telegram_user_id, message)
                    except Exception:
                        logger.warning("Failed to send Telegram reply", exc_info=True)
                elif platform == "console":
                    _print_console_reply(message)
                else:
                    _safe_speak(voice, message, last_emotion, "fast-reply", channel=platform)

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

            # Route an approval response to its waiting request instead of
            # starting an unrelated chat turn.
            from charlie.core import get_active_tool_approval

            pending_approval = get_active_tool_approval()
            if pending_approval:
                pending_approval_id, approval_channel = pending_approval
                record_primary_decision(
                    intent="control",
                    routing_source="control",
                    rationale="pending tool approval response handled by the control path",
                )
                if platform == "voice" and approval_channel == "voice":
                    answer = parse_yes_no(text)
                else:
                    from charlie.telegram_bot import parse_text_approval_response

                    answer = parse_text_approval_response(text)
                if answer is None:
                    guidance = "Use the matching Approve or Decline button in Telegram. Typed yes does not approve."
                    if platform == "voice":
                        voice.speak(guidance, last_emotion)
                    elif platform == "console":
                        await _deliver_immediate_reply(guidance)
                    elif telegram_bot:
                        try:
                            await telegram_bot.send_message(config.telegram_user_id, guidance)
                        except Exception:
                            logger.warning("Failed to send Telegram approval guidance", exc_info=True)
                    mark_response_complete()
                    return
                resolved = _resolve_tool_approval_and_notify(
                    pending_approval_id,
                    answer,
                    expected_platform=platform,
                )
                if platform == "voice":
                    if answer and resolved:
                        voice.speak("Okay, running it.", last_emotion)
                    elif resolved:
                        voice.speak("Cancelled.", last_emotion)
                    else:
                        voice.speak("That approval expired.", last_emotion)
                elif platform == "console" and not resolved:
                    await _deliver_immediate_reply("That approval expired.")
                elif not resolved and telegram_bot:
                    try:
                        await telegram_bot.send_message(
                            config.telegram_user_id, "That approval has expired. Send the request again."
                        )
                    except Exception:
                        logger.warning("Failed to send expired-approval notice", exc_info=True)
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
                if platform != "console":
                    print(f"\n{response_str}", flush=True)
                await _deliver_immediate_reply(response_str)
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
                if platform != "console":
                    print(f"\n{response_str}", flush=True)
                await _deliver_immediate_reply(response_str)
                mark_response_complete()
                return
            task_id = request.task_id
            active_task_id = task_id

            # Emit the recognized voice transcript once for runtime observers.
            if event_bus and platform == "voice":
                _submit_event_task(
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
            except SessionNotFoundError:
                logger.warning(
                    "session_persistence_dropped | phase=user | session_id=%s | turn_id=%s | reason=session_deleted",
                    session_id,
                    request.turn_id,
                )
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
                if platform in {"telegram", "console"}:
                    await _deliver_immediate_reply(ack)
                else:
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
                _submit_event_task(
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
            full_reply_buffer = ""
            is_first_chunk = True
            is_first_flush = True
            turn_active = True
            turn_channels_by_id[request.turn_id] = platform
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

                    # Progressive flush: sentence boundary > clause boundary > force-flush.
                    flushed = False

                    # Early first-flush: wait for first sentence boundary, or force at 150 chars
                    if is_first_flush:
                        sentence_buffer, flushed = _flush_complete_sentences(
                            sentence_buffer,
                            lambda part: _safe_speak(
                                voice, part, detected_emotion, "first-flush", channel=platform
                            ),
                        )
                        if flushed:
                            is_first_flush = False
                        elif len(sentence_buffer) >= 150:
                            idx = sentence_buffer.rfind(" ", 0, 150)
                            if idx > 0:
                                _safe_speak(
                                    voice, sentence_buffer[:idx], detected_emotion, "first-force", channel=platform
                                )
                                sentence_buffer = sentence_buffer[idx:].lstrip()
                            is_first_flush = False
                            flushed = True

                    if not flushed:
                        sentence_buffer, flushed = _flush_complete_sentences(
                            sentence_buffer,
                            lambda part: _safe_speak(
                                voice, part, detected_emotion, "sentence", channel=platform
                            ),
                        )

                    if not flushed and len(sentence_buffer) >= _MAX_FLUSH_CHARS:
                        # Force-flush: prefer clause (comma/semicolon) boundary,
                        # fall back to word boundary to avoid mid-word splits.
                        clause_idx = _CLAUSE_BOUNDARY.search(sentence_buffer[:_MAX_FLUSH_CHARS])
                        if clause_idx:
                            flush_end = clause_idx.end()
                            _safe_speak(
                                voice, sentence_buffer[:flush_end], detected_emotion, "clause", channel=platform
                            )
                            sentence_buffer = sentence_buffer[flush_end:].lstrip()
                        else:
                            word_idx = sentence_buffer.rfind(" ", 0, _MAX_FLUSH_CHARS)
                            if word_idx > 0:
                                _safe_speak(
                                    voice, sentence_buffer[:word_idx], detected_emotion, "word", channel=platform
                                )
                                sentence_buffer = sentence_buffer[word_idx:].lstrip()
                            elif sentence_buffer.strip():
                                _safe_speak(
                                    voice,
                                    sentence_buffer[:_MAX_FLUSH_CHARS],
                                    detected_emotion,
                                    "force",
                                    channel=platform,
                                )
                                sentence_buffer = sentence_buffer[_MAX_FLUSH_CHARS:]

                # Final TTS for any text that did not reach a progressive boundary.
                if sentence_buffer.strip():
                    _safe_speak(
                        voice, sparkle + sentence_buffer, detected_emotion, "final", channel=platform
                    )

                # Persist the generated reply.
                final_reply = full_reply_buffer.strip()
                if final_reply:
                    candidate_staged = False
                    if platform == "telegram" and self_extension_orchestrator is not None:
                        try:
                            correction_candidate = await asyncio.to_thread(
                                self_extension_orchestrator.stage_reusable_correction_candidate,
                                text,
                            )
                            if correction_candidate is not None and correction_candidate.success:
                                final_reply += "\n\nI staged an inactive instruction candidate for your review."
                                candidate_staged = True
                                logger.info(
                                    "reusable_skill_candidate_staged_from_owner_correction | status=%s",
                                    correction_candidate.status.value,
                                )
                        except Exception:
                            logger.warning("Could not stage a reusable correction candidate", exc_info=True)
                    if (
                        platform == "telegram"
                        and self_extension_orchestrator is not None
                        and not candidate_staged
                    ):
                        patterns = repeated_success_patterns_by_turn.pop(request.turn_id, [])
                        for pattern in patterns:
                            try:
                                # ponytail: scan 500 audit rows; add an index if volume outgrows this window.
                                candidate = await asyncio.to_thread(
                                    self_extension_orchestrator.stage_repeated_success_candidate,
                                    pattern["request"],
                                    pattern["capability"],
                                    pattern["operation"],
                                    audit_store.list(limit=500),
                                    current_turn_id=request.turn_id,
                                    expected_signature=pattern["signature"],
                                    risk_class=pattern["risk_class"],
                                    requires_approval=False,
                                )
                                if candidate is not None and candidate.success:
                                    final_reply += "\n\nI staged an inactive instruction candidate for your review."
                                    logger.info(
                                        "reusable_skill_candidate_staged_from_repeated_success | status=%s",
                                        candidate.status.value,
                                    )
                                    candidate_staged = True
                                    break
                            except Exception:
                                logger.warning(
                                    "Could not stage a repeated-success skill candidate", exc_info=True
                                )
                    try:
                        store.append("assistant", final_reply, session_id=session_id, turn_id=request.turn_id)
                        store.touch_session(session_id)
                    except SessionNotFoundError:
                        logger.warning(
                            "session_persistence_dropped | phase=assistant | session_id=%s "
                            "| turn_id=%s | reason=session_deleted",
                            session_id,
                            request.turn_id,
                        )
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
                        meta=EventMeta(
                            source=EventSource.BRAIN,
                            task_id=task_id,
                            session_id=session_id,
                            turn_id=request.turn_id,
                        ),
                    )
                mark_response_complete()
            except asyncio.CancelledError:
                logger.info(
                    "stale_foreground_output_suppressed | old_turn_id=%s | reason=foreground_cancelled",
                    request.turn_id,
                )
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
                error_class, message = classify_exception(exc)
                await _deliver_immediate_reply(message)
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
                turn_channels_by_id.pop(request.turn_id, None)
                repeated_success_patterns_by_turn.pop(request.turn_id, None)
                if platform == "telegram" and telegram_bot is not None and self_extension_orchestrator is not None:
                    await _send_pending_skill_candidate_reviews(
                        telegram_bot,
                        self_extension_orchestrator,
                        config.telegram_user_id,
                    )
                turn_active = False
                if active_operation_task_id == task_id:
                    active_operation_name = None
                    active_operation_task_id = None
                    active_operation_cancellable = True
                if active_turn_id == request.turn_id:
                    active_turn_id = None
                    active_turn_session_id = None
                    active_task_id = None
                voice_diagnostic_traces.pop(request.turn_id, None)
                clear_diagnostic_context = getattr(voice, "set_diagnostic_context", None)
                if callable(clear_diagnostic_context):
                    clear_diagnostic_context(None)
                if pending_turns:
                    # Keep the handoff visible as queued until its gated
                    # admission starts; a concurrent full transcript purge
                    # must not observe a false idle window here.
                    next_request = pending_turns[0]
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

            if platform == "voice" and full_reply_buffer.strip() and not screen_content_query:
                _schedule_housekeeping(brain._extract_thread_update(text, full_reply_buffer, session_id))
            if platform == "voice":
                brain.schedule_deferred_background_work()

        async def _reload_voice_engine() -> tuple[bool, str]:
            """Stop and respawn VoiceEngine so mic/VAD/ASR/TTS-model/wake-word settings take effect.

            These are all baked into VoiceEngine.__init__ or the ASR worker subprocess it
            spawns (see charlie/config.py's "voice" restart tier), so a live attribute
            change alone never reaches them -- only recreating the engine does.
            """
            nonlocal voice
            if not config.voice_enabled:
                detail = "Voice input and output disabled by VOICE_ENABLED=false"
                voice = _UnavailableVoiceEngine(detail, disabled=True)
                for name in ("voice", "voice_capture", "asr"):
                    _set_subsystem_health(name, HealthStatus.DISABLED, detail)
                return True, ""
            try:
                voice.stop()
            except Exception as ex:
                logger.warning(f"Error stopping voice engine on reload: {ex}")
                _set_subsystem_health("voice", HealthStatus.DEGRADED)
                _set_subsystem_health("voice_capture", HealthStatus.DEGRADED)
                return False, "voice reload could not stop the current engine"
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
                return True, ""
            except Exception as ex:
                logger.error(f"Error reloading VoiceEngine: {ex}", exc_info=True)
                _set_subsystem_health("voice", HealthStatus.DEGRADED)
                _set_subsystem_health("voice_capture", HealthStatus.DEGRADED)
                return False, "voice reload failed"

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

        async def _reload_mcp_client() -> tuple[bool, str]:
            """Stop the MCP subprocess client and restart it if still enabled."""
            nonlocal mcp_client
            from charlie.tools import registry

            for k in [k for k in registry._tools if k.startswith("mcp_")]:
                registry.unregister_tool(k)
            try:
                mcp_client = await _restart_mcp_client(mcp_client, config)
                base_client_missing = bool(config.mcp_enabled and config.mcp_servers and mcp_client is None)
                mcp_client, failed_extensions = await _reconcile_mcp_extension_runtime(
                    extension_runtime_registry,
                    mcp_client,
                    registry,
                    plugin_manager,
                    config,
                )
                _set_subsystem_health(
                    "mcp",
                    HealthStatus.DEGRADED
                    if failed_extensions or base_client_missing
                    else HealthStatus.RUNNING
                    if mcp_client is not None
                    else HealthStatus.DISABLED,
                )
                if base_client_missing:
                    return False, "mcp reload produced no client"
                return (not failed_extensions), "mcp extension reapply failed" if failed_extensions else ""
            except Exception as ex:
                logger.warning(f"Error reloading MCP client: {ex}")
                mcp_client = None
                try:
                    mcp_client, _failed_extensions = await _reconcile_mcp_extension_runtime(
                        extension_runtime_registry,
                        None,
                        registry,
                        plugin_manager,
                        config,
                    )
                except Exception:
                    logger.warning("MCP extension reconciliation failed after client reload error", exc_info=True)
                _set_subsystem_health("mcp", HealthStatus.DEGRADED)
                return False, "mcp reload failed"

        def _reload_plugin_tools() -> tuple[bool, str]:
            """Re-register plugin tools to match the current enabled flag / allow-dirs."""
            nonlocal plugin_manager
            from charlie.plugins import PluginManager
            from charlie.tools import register_plugin_tools, registry

            success, reason, replacement_manager = _reload_plugin_tools_state(
                config,
                plugin_manager,
                registry=registry,
                register_plugin_tools=register_plugin_tools,
                empty_manager_factory=PluginManager,
                set_health=_set_subsystem_health,
            )
            if success:
                plugin_manager = replacement_manager
                for entry in extension_runtime_registry.list():
                    if entry.kind != "plugin":
                        continue
                    plugin = plugin_manager.get_plugin(entry.name)
                    if plugin is None:
                        entry.enabled = False
                        entry.tool_names = []
                    else:
                        entry.enabled = True
                        entry.tool_names = [f"plugin_{tool['name']}" for tool in plugin.get_tools()]
            return success, reason

        async def _publish_settings_reload_state() -> None:
            """Refresh projections affected by targeted settings reloads."""
            brain.rebuild_stable_tier()
            await _publish_subsystem_health(event_bus)
            from charlie.tools import registry as _reloaded_registry

            await _publish_tool_snapshot(event_bus, _reloaded_registry)
            await _publish_mcp_snapshot(event_bus, mcp_client)
            await _publish_extension_snapshot(
                event_bus,
                extension_runtime_registry,
                rationale="settings reload refreshed extension runtime projection",
            )

        async def _apply_extension_runtime_request(payload: dict[str, Any]) -> dict[str, Any]:
            nonlocal mcp_client
            from charlie.tools import registry as _extension_registry

            result, mcp_client = await _handle_extension_operation_request(
                payload,
                brain=brain,
                plugin_manager=plugin_manager,
                mcp_client=mcp_client,
                runtime_config=config,
                tool_registry=_extension_registry,
                extension_registry=extension_runtime_registry,
                event_bus=event_bus,
                result_cache=extension_operation_results,
                in_flight=extension_operation_in_flight,
                fingerprint_cache=extension_operation_fingerprints,
                operation_lock=settings_operation_lock,
            )
            return result

        def _self_extension_runtime_operation(
            operation: str,
            name: str,
            command: str,
            args: list[str],
            env: dict[str, str],
        ) -> dict[str, Any]:
            raw_text = json.dumps(
                {
                    "mcpServers": {
                        name: {"command": command, "args": list(args), "env": dict(env)},
                    }
                },
                sort_keys=True,
            )
            payload = {
                "request_id": f"self-extension-{uuid.uuid4().hex}",
                "operation": operation,
                "kind": "mcp",
                "name": name,
                "source": "self_extension",
                "raw_text": raw_text,
            }
            future = _submit_event_threadsafe(_apply_extension_runtime_request(payload), loop)
            if future is None:
                return {
                    "success": False,
                    "error": "Main extension authority is shutting down or unavailable.",
                }
            try:
                return future.result(timeout=90.0)
            except Exception as exc:
                return {"success": False, "error": f"Canonical MCP extension operation failed: {exc}"}

        # Telegram runs in-process so it shares the canonical dispatch and approval owners.
        if config.telegram_enabled:
            try:
                from charlie.telegram_bot import TelegramBot

                async def on_telegram_message(text, chat_id):
                    from charlie.core import get_active_tool_approval
                    from charlie.telegram_bot import (
                        is_text_approval_attempt,
                        parse_text_approval_response,
                    )

                    pending_approval = get_active_tool_approval()
                    if pending_approval:
                        answer = parse_text_approval_response(text)
                        if answer is None and is_text_approval_attempt(text):
                            await telegram_bot.send_message(
                                chat_id,
                                "Use the matching Approve or Decline button. Typed yes does not approve.",
                            )
                            return
                        if answer is not None and not _resolve_tool_approval_and_notify(
                            pending_approval[0],
                            answer,
                            expected_platform="telegram",
                        ):
                            await telegram_bot.send_message(
                                chat_id, "That approval has expired. Send the request again."
                            )
                        if answer is not None:
                            return
                    if not turn_active:
                        await telegram_bot.send_typing(chat_id)
                    request = _allocate_turn_request(text, current_session_id, "telegram")
                    await _dispatch_or_queue(request)

                def on_telegram_approval(request_id, approved):
                    return _resolve_tool_approval_and_notify(
                        request_id,
                        approved,
                        expected_platform="telegram",
                    )

                async def on_telegram_skill_candidate_review(review_token, approved):
                    if self_extension_orchestrator is None or brain is None:
                        return False
                    return await _resolve_skill_candidate_review(
                        self_extension_orchestrator,
                        brain,
                        review_token,
                        approved,
                    )

                telegram_bot = TelegramBot(
                    config.telegram_bot_token,
                    config.telegram_user_id,
                    on_telegram_message,
                    on_telegram_approval,
                    on_skill_candidate_review=on_telegram_skill_candidate_review,
                )
                await telegram_bot.start()
                logger.info("Telegram bot started")
                _set_subsystem_health("telegram", HealthStatus.RUNNING)
            except Exception as e:
                logger.warning(f"Failed to start Telegram bot: {e}")
                failed_telegram_bot = telegram_bot
                telegram_bot = None
                if failed_telegram_bot is not None:
                    try:
                        await failed_telegram_bot.stop()
                    except Exception:
                        logger.debug("Telegram cleanup after failed startup was incomplete", exc_info=True)
                _set_subsystem_health("telegram", HealthStatus.DEGRADED)

        logger.info("Loading AI models (Whisper, VAD, Kokoro)...")
        # TTS lifecycle callbacks for IPC events
        def on_tts_start():
            if event_bus:
                _submit_event_threadsafe(
                    event_bus.emit(
                        "speaking_start",
                        {"session_id": current_session_id},
                        meta=EventMeta(source=EventSource.VOICE),
                    ),
                    loop,
                )

        def on_tts_stop():
            if event_bus:
                _submit_event_threadsafe(
                    event_bus.emit(
                        "speaking_stop",
                        {"session_id": current_session_id},
                        meta=EventMeta(source=EventSource.VOICE),
                    ),
                    loop,
                )

        def on_speech_onset(_metadata=None):
            """Preempt only the active foreground response after VAD confirms speech."""

            if not config.enable_barge_in:
                return

            def _handle_onset() -> None:
                _cancel_housekeeping()
                if active_turn_id is None:
                    logger.info("speech_onset | foreground=idle | sustained_tasks=untouched")
                    return
                task = active_process_task
                if task is None or task.done():
                    logger.info(
                        "speech_onset | foreground_turn_id=%s | process=inactive | sustained_tasks=untouched",
                        active_turn_id,
                    )
                    return
                if active_operation_name is not None and not active_operation_cancellable:
                    logger.info(
                        "Speech onset deferred foreground cancellation until safe operation completes: %s",
                        active_operation_name,
                    )
                    return
                brain.cancel_chat()
                logger.info(
                    "speech_onset_supersession_requested | old_turn_id=%s | sustained_tasks=untouched",
                    active_turn_id,
                )
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
                _submit_event_threadsafe(
                    event_bus.emit("wake_word", {}, meta=EventMeta(source=EventSource.VOICE)), loop
                )
            if config.browser_enabled and config.browser_warm_on_wake:
                from charlie.browser import controller as browser_controller

                browser_controller.warm()

        voice.set_wake_word_callback(on_wake_word)

        # Connection test & Dynamic Welcome
        ensure_session_ready(current_session_id)
        if config.voice_enabled:
            logger.debug("Requesting dynamic welcome message from LLM...")
            welcome_msg = ""
            try:
                async with asyncio.timeout(25.0):
                    async for chunk in brain.chat_stream(
                        "Give me a very brief, one-sentence startup welcome. Be warm, natural, "
                        "and speak like a human colleague (not an AI assistant). "
                        "Do NOT say 'How can I help you' or 'How can I assist'. Speak only in English.",
                        session_id=current_session_id,
                        skip_tools=True,
                    ):
                        welcome_msg += chunk
            except asyncio.TimeoutError:
                logger.warning("Dynamic welcome timed out after 25s. Using fallback.")
                welcome_msg = "Hey there. I'm online and listening."
            except Exception as e:
                logger.warning(f"Dynamic welcome failed: {type(e).__name__}: {e}. Using fallback.")
                welcome_msg = "Hey there. I'm online and listening."
        else:
            welcome_msg = "Voice is disabled. Charlie is online for text and Telegram."

        if not config.voice_enabled:
            online_status = "   Charlie is online; voice disabled"
        elif voice.is_ready:
            welcome_msg = welcome_msg or "Hey there. I'm online and listening."
            online_status = "   Charlie is online and listening"
        else:
            welcome_msg = "Hey there. I'm online. Microphone input is unavailable."
            online_status = "   Charlie is online; microphone unavailable"
        print("=" * 40, flush=True)
        print(online_status, flush=True)
        print("=" * 40, flush=True)
        print(f"\rCharlie: {welcome_msg}", flush=True)
        if config.voice_enabled:
            voice.speak(welcome_msg, "neutral")

        # Real GPU utilization, re-read every tick so runtime observers reflect
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
                        was_idle = is_idle
                    await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Metric emitter error: {e}")

        # Run the canonical voice/runtime loop with the event publisher.
        async with EventBus(pub_port=5555) as bus:
            event_bus = bus
            _main_event_bus = bus
            await _publish_subsystem_health(bus)
            bus.set_state_listener(_on_event_for_state)
            voice.set_event_bus(bus)
            import charlie.recovery

            charlie.recovery._event_bus = bus
            brain.event_bus = bus
            import charlie.mcp_client

            charlie.mcp_client.set_event_bus(bus, asyncio.get_running_loop())

            # Build one authoritative self-extension service only after the
            # real EventBus loop and MCP subsystem are available. Chat tools
            # delegate to this instance; they never construct an orchestrator.
            await mcp_start_task
            # Publish an initial authoritative runtime snapshot after startup.
            await _publish_runtime_state(bus, mcp_client, settings_service, extension_runtime_registry)
            from charlie.capabilities import get_capability_index
            from charlie.code_index import CodeIndex
            from charlie.doctor import CharlieDoctor
            from charlie.self_extension import SelfExtensionOrchestrator
            from charlie.self_knowledge import SelfKnowledgeService

            def _schedule_settings_snapshot(rationale: str) -> None:
                _submit_event_threadsafe(
                    _publish_settings_snapshot(event_bus, settings_service, rationale=rationale),
                    loop,
                )

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
                settings_service=settings_service,
                settings_snapshot_callback=_schedule_settings_snapshot,
                config=config,
                capability_index=shared_capability_index,
                event_bus=bus,
                event_loop=asyncio.get_running_loop(),
                mcp_client=mcp_client,
                tool_registry=tool_registry,
                runtime_extension_operation=_self_extension_runtime_operation,
                doctor=doctor,
                code_index=code_index,
                self_knowledge=self_knowledge,
                introspector=runtime_introspector,
            )
            await asyncio.to_thread(self_extension_orchestrator.rehydrate_mcp_runtime)
            configure_runtime_services(
                self_extension_orchestrator=self_extension_orchestrator,
                runtime_introspector=runtime_introspector,
                self_knowledge_service=self_knowledge,
                doctor=doctor,
            )
            _load_rehydrated_skill_blocks(brain, self_extension_orchestrator)
            if telegram_bot is not None and config.telegram_user_id > 0:
                submitted_reviews = await _send_pending_skill_candidate_reviews(
                    telegram_bot,
                    self_extension_orchestrator,
                    config.telegram_user_id,
                )
                if submitted_reviews:
                    logger.info("Submitted %s pending skill candidate review(s) to owner Telegram", submitted_reviews)

            from charlie.calendar_runtime import CalendarRuntime, configure_calendar_runtime
            from charlie.calendar_scheduler import (
                deliver_due_automation_reminders,
                deliver_due_automation_tasks,
                deliver_due_reminders,
            )
            from charlie.utils import utc_now_iso

            calendar_runtime = CalendarRuntime(config.session_db_path)
            configure_calendar_runtime(calendar_runtime)
            interrupted_automations = await calendar_runtime.execute("reconcile_automation_claims")
            if interrupted_automations:
                recovery_notice = (
                    f"{interrupted_automations} scheduled automation run(s) were interrupted by restart "
                    "and need review before retry."
                )
                logger.warning(recovery_notice)
                await bus.emit(
                    "alert",
                    {"severity": "warning", "message": recovery_notice},
                    meta=EventMeta(
                        source=EventSource.TASK,
                        rationale="started automation reminders need review after process restart",
                    ),
                )
                if getattr(voice, "is_ready", False):
                    voice.speak(recovery_notice, "neutral")

            async def _calendar_reminder_loop() -> None:
                while True:
                    async def _deliver_alert(event: dict) -> None:
                        message = f"Reminder: {event['title']}"
                        await bus.emit(
                            "alert",
                            {"severity": "info", "message": message, "reminder_id": event["id"]},
                            meta=EventMeta(source=EventSource.WATCHER, rationale="local reminder became due"),
                        )

                    async def _deliver_voice(event: dict) -> None:
                        voice.speak(f"Reminder: {event['title']}", "neutral")

                    async def _deliver_automation_alert(schedule: dict) -> None:
                        message = f"Reminder: {schedule['text']}"
                        await bus.emit(
                            "alert",
                            {"severity": "info", "message": message, "reminder_id": schedule["id"]},
                            meta=EventMeta(source=EventSource.WATCHER, rationale="automation reminder became due"),
                        )

                    def _queue_automation_voice(schedule: dict) -> None:
                        voice.speak(f"Reminder: {schedule['text']}", "neutral")

                    async def _send_automation_telegram(schedule: dict) -> None:
                        await telegram_bot.send_message(
                            config.telegram_user_id, f"Reminder: {schedule['text']}"
                        )

                    async def _dispatch_scheduled_task(schedule: dict) -> None:
                        result = await brain._handle_start_background_task(
                            {"text": schedule["text"]},
                            platform="telegram",
                            task_id=schedule["active_task_id"],
                            require_successful_operation=True,
                        )
                        if result.startswith("Error:"):
                            raise RuntimeError(result)

                    try:
                        await deliver_due_reminders(
                            calendar_runtime,
                            utc_now_iso(),
                            alert_callback=_deliver_alert,
                            voice_callback=(
                                _deliver_voice if config.voice_enabled and getattr(voice, "is_ready", False) else None
                            ),
                        )
                        await deliver_due_automation_reminders(
                            calendar_runtime,
                            utc_now_iso(),
                            alert_callback=_deliver_automation_alert,
                            voice_callback=(
                                _queue_automation_voice if getattr(voice, "is_ready", False) else None
                            ),
                            telegram_callback=(
                                _send_automation_telegram if telegram_bot is not None else None
                            ),
                        )
                        await deliver_due_automation_tasks(
                            calendar_runtime,
                            utc_now_iso(),
                            dispatch_callback=_dispatch_scheduled_task,
                            task_journal=get_task_journal(),
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.warning("Calendar reminder iteration failed; continuing", exc_info=True)
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

            def _on_watcher_signal(event: dict, level: AttentionLevel, reason: str) -> None:
                # Re-emit through the normal alert path so runtime observers see it.
                with watcher_callback_lock:
                    if runtime_shutting_down:
                        logger.debug("Ignoring watcher signal during runtime shutdown")
                        return
                    payload = event.get("payload") or {}
                    message = payload.get("message", reason)
                    logger.warning(f"Watcher signal: {message}")
                    try:
                        _submit_event_threadsafe(
                            bus.emit(
                                event.get("type", "alert"),
                                payload,
                                meta=EventMeta(source=EventSource.WATCHER, rationale=reason),
                            ),
                            _watcher_loop,
                        )
                    except Exception:
                        logger.warning("Failed to emit watcher alert event", exc_info=True)
                    signal = payload.get("signal") or {}
                    if (
                        signal.get("kind") in {"path_change", "stalled_task"}
                        and level >= AttentionLevel.INFORM
                        and telegram_bot is not None
                        and config.telegram_user_id > 0
                    ):
                        async def _deliver_watcher_telegram() -> None:
                            try:
                                await telegram_bot.send_message(
                                    config.telegram_user_id, f"Watcher alert: {message}"
                                )
                            except Exception:
                                logger.warning("Watcher Telegram API send failed", exc_info=True)
                            else:
                                logger.info("Watcher Telegram API send accepted")

                        try:
                            submitted = _submit_event_threadsafe(
                                _deliver_watcher_telegram(), _watcher_loop
                            )
                            if submitted is None:
                                logger.warning("Watcher Telegram delivery failed: main loop unavailable")
                        except Exception:
                            logger.warning("Failed to schedule watcher Telegram delivery", exc_info=True)
                    if level >= AttentionLevel.ATTENTION and bool(getattr(voice, "is_ready", False)):
                        try:
                            voice.speak(message, "neutral")
                        except Exception:
                            logger.warning("Failed to speak watcher alert", exc_info=True)

            _watcher_registry = WatcherRegistry()
            _watcher_registry.register(
                cpu_ram_watcher(_read_cpu_ram_percent, config.alert_cpu_pct, config.alert_ram_pct)
            )
            _watcher_registry.register(mcp_health_watcher(_get_mcp_status))
            _watcher_registry.register(
                stalled_task_watcher(lambda: get_task_journal().list(include_terminal=False))
            )
            _watcher_registry.register(repeated_tool_failure_watcher(telemetry.unreliable_tools))
            if config.watch_paths:
                _watcher_registry.register(path_change_watcher(config.watch_paths))

            try:
                watcher_thread = start_watcher_thread(
                    _watcher_registry,
                    _on_watcher_signal,
                    stop_event=watcher_stop_event,
                )
                if not watcher_thread.is_alive():
                    raise RuntimeError("Watcher thread exited during startup")
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
                            _submit_event_task(
                                bus.emit("log", {"line": log_entry}, meta=EventMeta(source=EventSource.VOICE)), loop
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

            console_ingress = ConsoleTextIngress(loop, on_console_text)
            console_ingress.start()

            system_status_task = asyncio.create_task(_emit_system_status(bus), name="emit_system_status")
            calendar_task = asyncio.create_task(_calendar_reminder_loop(), name="calendar_reminder_loop")
            steady_state_tasks = [
                system_status_task,
                calendar_task,
            ]
            if config.voice_enabled:
                steady_state_tasks.extend(
                    [
                        asyncio.create_task(_voice_loop_idle(voice), name="voice_loop_idle"),
                        asyncio.create_task(_monitor_voice_health(bus), name="monitor_voice_health"),
                    ]
                )

            try:
                await asyncio.gather(*steady_state_tasks)
            except Exception as e:
                exit_code = 1
                logger.error("Steady-state runtime task failed: %s", e, exc_info=True)
                raise
            finally:
                _begin_shutdown()
                _stop_watcher()
                logger.info("main_shutdown_begin | stage=before_event_bus_close")
                if zmq_handler is not None:
                    try:
                        logging.getLogger().removeHandler(zmq_handler)
                    except Exception as e:
                        logger.warning("Failed to remove ZMQ log handler: %s", e)
                    zmq_handler = None

                if brain is not None:
                    try:
                        brain.cancel_chat()
                    except Exception:
                        pass

                _stop_voice(final=False)

                housekeeping_to_drain = _cancel_housekeeping()
                active_process_for_shutdown = active_process_task or globals().get("active_process_task")
                tasks_to_drain = [
                    *steady_state_tasks,
                    active_process_for_shutdown,
                    *housekeeping_to_drain,
                    *tuple(background_housekeeping_tasks),
                    mcp_start_task,
                ]
                await _cancel_and_drain(tasks_to_drain, label="event_bus_tasks")
                try:
                    await background_task.shutdown()
                    _set_subsystem_health_if_known("background_tasks", HealthStatus.STOPPED, "Stopped")
                except Exception:
                    _set_subsystem_health_if_known("background_tasks", HealthStatus.DEGRADED, "Shutdown incomplete")
                    raise
                await _drain_runtime_submissions()
                if calendar_runtime is not None:
                    try:
                        calendar_runtime.close()
                    except Exception as e:
                        logger.warning("Calendar runtime close error: %s", e)
                    from charlie.calendar_runtime import configure_calendar_runtime

                    configure_calendar_runtime(None)
                    calendar_runtime = None
    except KeyboardInterrupt:
        logger.info("Interrupt received, shutting down...")
    except asyncio.CancelledError:
        pass
    except Exception as e:
        exit_code = 1
        logger.error("Charlie runtime startup/execution failed: %s", e, exc_info=True)
    finally:
        _begin_shutdown()
        _stop_watcher()
        logger.info("main_shutdown_begin | exit_code=%s", exit_code)
        if zmq_handler is not None:
            try:
                logging.getLogger().removeHandler(zmq_handler)
            except Exception as e:
                logger.warning("Failed to remove ZMQ log handler: %s", e)
            zmq_handler = None

        active_process_for_shutdown = active_process_task or globals().get("active_process_task")
        outer_tasks = [
            mcp_start_task,
            active_process_for_shutdown,
            *tuple(background_housekeeping_tasks),
        ]
        await _cancel_and_drain(outer_tasks, label="outer_tasks")
        try:
            await background_task.shutdown()
            _set_subsystem_health_if_known("background_tasks", HealthStatus.STOPPED, "Stopped")
        except Exception:
            _set_subsystem_health_if_known("background_tasks", HealthStatus.DEGRADED, "Shutdown incomplete")
            exit_code = 1
        await _drain_runtime_submissions()

        try:
            from charlie.desktop import shutdown_uia_executor

            shutdown_uia_executor()
            logger.info("Desktop UIA executor shut down")
        except Exception as e:
            logger.warning("Desktop UIA executor shutdown error: %s", e)

        try:
            from charlie.media_runtime import shutdown_media_executor

            shutdown_media_executor()
        except Exception as e:
            logger.warning("Media executor shutdown error: %s", e)

        _stop_voice(final=True)

        get_task_journal().set_on_change(None)
        if telegram_bot is not None:
            expiry_tasks = list(telegram_approval_expiry_tasks.values())
            telegram_approval_expiry_tasks.clear()
            for task in expiry_tasks:
                if not task.done():
                    task.cancel()
            if expiry_tasks:
                await asyncio.gather(*expiry_tasks, return_exceptions=True)
            for turn_id in set(telegram_status_text_by_turn) | set(telegram_status_messages_by_turn):
                try:
                    await _finish_telegram_turn_feedback(turn_id)
                except Exception:
                    logger.warning(
                        "Could not clear Telegram status during shutdown for %s",
                        turn_id,
                        exc_info=True,
                    )
            try:
                await telegram_bot.stop()
                logger.info("Telegram stopped")
            except Exception as e:
                logger.warning("Telegram bot stop error: %s", e)

        if calendar_runtime is not None:
            try:
                calendar_runtime.close()
            except Exception as e:
                logger.warning("Calendar runtime close error: %s", e)
            from charlie.calendar_runtime import configure_calendar_runtime

            configure_calendar_runtime(None)
            calendar_runtime = None

        if mcp_client is not None:
            try:
                await asyncio.to_thread(mcp_client.stop)
                logger.info("MCP subsystem stopped")
            except Exception as e:
                logger.warning("MCP subsystem stop error: %s", e)

        if brain is not None:
            try:
                await brain.close()
                _set_subsystem_health_if_known("brain", HealthStatus.STOPPED, "Stopped")
                _set_subsystem_health_if_known("llm", HealthStatus.STOPPED, "Stopped")
                logger.info("Brain closed")
            except Exception as e:
                _set_subsystem_health_if_known("brain", HealthStatus.DEGRADED, "Shutdown incomplete")
                _set_subsystem_health_if_known("llm", HealthStatus.DEGRADED, "Shutdown incomplete")
                logger.warning("Brain close error: %s", e)

        if memory_graph is not None and hasattr(memory_graph, "close"):
            try:
                memory_graph.close()
                logger.info("Memory graph closed")
            except Exception as e:
                logger.warning("Memory graph close error: %s", e)

        _close_runtime_stores(audit_store, store, quiescent=shutdown_quiescent)

        _main_event_bus = None
        loop.call_exception_handler = _orig_handler

        _log_port_release("127.0.0.1", 5555)

        if shutdown_quiescent and exit_code == 0:
            _runtime_health.set_runtime_lifecycle(RuntimeStatus.STOPPED)
            logging.shutdown()
        else:
            _runtime_health.mark_shutdown_failed()

    return exit_code

async def _voice_loop_idle(voice):
    """Keep the main coroutine alive while voice threads run."""
    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    print("Direct main.py execution is unsupported. Use: python run.py", file=sys.stderr)
    sys.exit(1)
