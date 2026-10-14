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
from datetime import datetime, timezone
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
# The rotating file handler below is attached to the root logger at *import*
# time, so merely importing this module -- which a dozen test modules do -- used
# to open and rotate the production log. A single pytest run therefore shifted
# up to 20MB of real history into charlie.log.1 and destroyed the diagnostic
# record the file exists to preserve. Under test mode we resolve the log inside
# the isolated test state dir that tests/conftest.py already redirects every
# other runtime resource into, so importing main stays non-destructive.
# CHARLIE_LOG_FILE remains an explicit override so this location is a seam
# rather than a second hardcoded copy of conftest's directory name.
_TEST_MODE = os.getenv("CHARLIE_TEST_MODE", "").lower() == "true"
if _TEST_MODE:
    _log_dir = os.path.join(".codex-pytest-tmp", "logs")
    os.makedirs(_log_dir, exist_ok=True)
    LOG_FILE = os.path.join(_log_dir, "charlie.log")
else:
    LOG_FILE = "logs/charlie.log"
LOG_FILE = os.getenv("CHARLIE_LOG_FILE", "").strip() or LOG_FILE
os.makedirs(os.path.dirname(os.path.abspath(LOG_FILE)), exist_ok=True)

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
from charlie.self_extension.approval_bridge import (
    ApprovalChannelRegistry,
    sanitize_approval_reason,
)
from charlie.settings_service import (
    SettingsService,
)
from charlie.single_instance import SingleInstanceError, SingleInstanceLock
from charlie.state import StateMachine
from charlie.subsystem_health import HealthRegistry, HealthStatus, RuntimeStatus
from charlie.task_journal import TaskOrigin, TaskStatus, get_task_journal
from charlie.turn_contracts import ExecutionPolicy, IntentDecision, ResultEnvelope, TurnRequest
from charlie.voice import VoiceEngine
from charlie.web_gateway import RuntimeWebGateway
from charlie.attention import AttentionLevel
from charlie.watchers import (
    WatcherRegistry,
    cpu_ram_watcher,
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
_WEB_TEXT_COMMAND_CACHE_MAX = 512
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


def _preempt_foreground_for_speech(brain: Any, task: Any) -> bool:
    """An approval answer must not cancel the action waiting for that answer."""
    from charlie.core import get_active_tool_approval
    if get_active_tool_approval() is not None:
        return False
    brain.cancel_chat()
    task.cancel()
    return True


def _parse_owner_text_approval(text: str, *, platform: str, approval_channel: str) -> Optional[bool]:
    """Parse typed approval only on the owner channel that issued the prompt."""
    if platform == approval_channel and platform in {"voice", "console"}:
        return parse_yes_no(text)
    from charlie.telegram_bot import parse_text_approval_response

    return parse_text_approval_response(text)


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
        request = await asyncio.to_thread(
            self_extension_orchestrator.plan_request,
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
            approval_platform=request.channel,
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
                {"text": message, "session_id": request.session_id, "channel": request.channel},
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

    message = "Research started."
    if store is not None:
        try:
            store.append("assistant", message, session_id=request.session_id, turn_id=request.turn_id)
            store.touch_session(request.session_id)
        except Exception:
            logger.warning("sustained_research_ack_archive_failed", exc_info=True)
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
            {"text": message, "session_id": request.session_id, "channel": request.channel},
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
    if voice is not None and request.channel == "voice":
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
        memory_service=memory_service,
    )


_main_event_bus: Optional[Any] = None

# The constructed Brain, exposed at module scope so main-owned health publication can
# read the degradations Brain records. Brain is otherwise a local of the startup
# function, which made those degradations unobservable to any real health surface.
_main_brain: Optional[Any] = None


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


# Which subsystem each Brain degradation belongs to. A degradation is a property of
# the subsystem that lost truth, not of the brain as a whole.
_BRAIN_DEGRADATION_SUBSYSTEM = {
    "llm_health_delivery": "llm",
    "session_history": "brain",
    "context_tier": "memory",
    "correction_persistence": "memory",
}


def _project_brain_degradations() -> None:
    """Surface Brain-owned degradations on the main-owned health registry.

    Brain records what it could not read, deliver, or persist, but cannot write to
    the runtime HealthRegistry -- it only publishes LLM transitions through a sink.
    Without this projection the health surface kept showing the last *successfully
    delivered* state, which is a silent false-healthy signal.

    Degradations for a subsystem the registry does not know are skipped rather than
    inventing a subsystem, and a failure here must never take health publication down.
    """
    brain = _main_brain
    if brain is None:
        return
    try:
        degradations = brain.health_degradations()
    except Exception:
        logger.warning("Could not read Brain health degradations", exc_info=True)
        return
    for key in degradations or {}:
        subsystem = _BRAIN_DEGRADATION_SUBSYSTEM.get(key)
        if subsystem is None:
            continue
        try:
            _set_subsystem_health(
                subsystem,
                HealthStatus.DEGRADED,
                public_detail=f"degraded: {key}",
            )
        except Exception:
            logger.debug("Degradation not projectable: %s", subsystem, exc_info=True)


async def _publish_subsystem_health(bus: Optional[EventBus] = None) -> None:
    """Publish legacy subsystem projection plus canonical runtime truth."""
    _project_brain_degradations()
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
        {"tasks": tasks, "task_id": None},
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
    settings_service: Any = None,
    extension_registry: Optional[ExtensionRuntimeRegistry] = None,
) -> None:
    """Replay public operational state owned by this main process."""
    await _publish_subsystem_health(bus)
    await _publish_task_snapshot(bus)
    await _publish_tool_snapshot(bus)
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
    tool_registry: Any = None,
    extension_registry: Optional[ExtensionRuntimeRegistry] = None,
) -> dict[str, Any]:
    """Apply one extension transition against main's live runtime owners."""
    if not isinstance(payload, dict):
        payload = {}
    operation = payload.get("operation")
    name = payload.get("name")
    allowed_operations = {"install", "enable", "disable", "uninstall"}
    allowed_kinds = {"skill", "openapi", "generated"}
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
        return _extension_operation_result(
                payload,
                success=False,
                tool_names=[],
                error="Invalid extension operation request.",
                request_fingerprint=fingerprint,
        )

    entry = extension_registry.get(name) if extension_registry is not None else None
    if operation != "install" and extension_registry is not None and entry is None:
        return _extension_operation_result(
                payload,
                success=False,
                tool_names=[],
                error=f"Unknown extension '{name}' in main runtime.",
                request_fingerprint=fingerprint,
        )

    if operation == "install" and extension_registry is not None and extension_registry.get(name) is not None:
        return _extension_operation_result(
                payload,
                success=False,
                tool_names=[],
                error=f"Extension '{name}' is already installed in main runtime.",
                request_fingerprint=fingerprint,
        )

    kind = entry.kind if entry is not None else payload.get("kind")
    source = entry.source if entry is not None else str(payload.get("source", ""))
    raw_text = entry.raw_text if entry is not None else str(payload.get("raw_text", ""))
    known_tool_names = list(entry.tool_names) if entry is not None else payload.get("tool_names", [])
    if not isinstance(kind, str) or kind not in allowed_kinds:
        return _extension_operation_result(
                payload,
                success=False,
                tool_names=[],
                error="Invalid extension kind.",
                request_fingerprint=fingerprint,
        )
    if not isinstance(known_tool_names, list) or any(not isinstance(item, str) for item in known_tool_names):
        return _extension_operation_result(
                payload,
                success=False,
                tool_names=[],
                error="Invalid extension tool names.",
                kind=kind,
                name=name,
                request_fingerprint=fingerprint,
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
    try:
        if operation == "install":
            from charlie.extensions.install import install_extension

            tool_names = install_extension(kind, name, source, raw_text, registry=tool_registry)
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
            from charlie.extensions.install import install_extension

            tool_names = install_extension(kind, name, source, raw_text, registry=tool_registry)
            if kind == "skill":
                from charlie.extensions.skills import format_skill_block, parse_skill_md

                brain.add_installed_skill_block(name, format_skill_block(parse_skill_md(raw_text)))
            if entry is not None:
                entry.enabled = True
                entry.tool_names = list(tool_names)
                entry.runtime_warning = None
        elif operation == "disable":
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
        return _extension_operation_result(
                payload,
                success=True,
                tool_names=list(tool_names),
                kind=kind,
                name=name,
                request_fingerprint=fingerprint,
        )
    except Exception as exc:
        reason = str(exc).strip() or type(exc).__name__
        rollback_ok = True
        try:
            if operation in {"install", "enable"}:
                if kind == "skill" and before_skill_block is None:
                    brain.remove_installed_skill_block(name)
            elif operation in {"disable", "uninstall"} and before_entry_state and before_entry_state[0]:
                from charlie.extensions.install import install_extension

                install_extension(kind, name, source, raw_text, registry=tool_registry)
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
        return _extension_operation_result(
                payload,
                success=False,
                tool_names=[],
                error=safe_reason,
                kind=kind,
                name=name,
                request_fingerprint=fingerprint,
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
    tool_registry: Any,
    extension_registry: ExtensionRuntimeRegistry,
    event_bus: Any,
    result_cache: OrderedDict[str, dict[str, Any]],
    in_flight: dict[str, Any],
    fingerprint_cache: dict[str, str],
    operation_lock: Optional[asyncio.Lock] = None,
) -> dict[str, Any]:
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
        return await publish(conflict, cache=False)

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
            return await publish(conflict, cache=False)
        return await publish(cached)

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
            return await publish(conflict, cache=False)
        await asyncio.shield(existing)
        cached = result_cache.get(request_id)
        if cached is not None:
            return await publish(cached)
        return _extension_operation_result(
                payload, success=False, tool_names=[], error="Extension operation was cancelled"
        )

    if current is not None:
        in_flight[request_id] = (current, fingerprint)
    lock_acquired = False
    if operation_lock is not None:
        await operation_lock.acquire()
        lock_acquired = True
    try:
        result = apply_extension_operation(
            payload,
            brain=brain,
            tool_registry=tool_registry,
            extension_registry=extension_registry,
        )
        result["request_fingerprint"] = fingerprint
        await _publish_tool_snapshot(event_bus, tool_registry)
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
        return result
    finally:
        entry = in_flight.get(request_id)
        if current is not None and (
            entry is current or (isinstance(entry, tuple) and len(entry) == 2 and entry[0] is current)
        ):
            in_flight.pop(request_id, None)
        if lock_acquired:
            operation_lock.release()


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
    voice,
    text: str,
    emotion: str,
    label: str = "",
    *,
    channel: Optional[str] = None,
    speech_owner: Optional[Any] = None,
) -> None:
    """Speak text, logging (not swallowing) any TTS failure.

    A mid-stream TTS error must never abort the answer generation loop --
    the UI token stream and message persistence downstream must still run.
    """
    if channel is not None and channel != "voice":
        return
    text = re.sub(r"\[S\d+\]", "", text or "").replace("  ", " ").strip()
    if not text:
        return
    try:
        if speech_owner is None:
            voice.speak(text.strip(), emotion)
        else:
            voice.speak(text.strip(), emotion, speech_owner=speech_owner)
    except Exception:
        logger.warning(
            "TTS speak failed%s: dropping audio only, answer continues",
            f" ({label})" if label else "",
            exc_info=True,
        )


async def _speak_turn_work_progress(
    brain,
    voice,
    request,
    get_state,
    is_current,
    *,
    speech_owner: Any,
    initial_delay: float = 5.0,
    interval: float = 20.0,
) -> None:
    """Speak question-specific runtime updates only while a voice turn is waiting."""
    if request.channel != "voice":
        return
    logger.info("spoken_work_progress_started | turn_id=%s", request.turn_id)
    previous = None
    update_attempts = 0
    await asyncio.sleep(initial_delay)
    while is_current():
        state = dict(get_state())
        signature = json.dumps(state, sort_keys=True)
        stage = state.get("stage")
        is_research_work = stage in {"planning", "searching", "found", "reading"} and bool(
            state.get("query") or state.get("source_count") is not None
        )
        is_synthesis = stage == "answer_generation" and bool(
            state.get("source_acquisition_finished") or state.get("source_count") is not None
        )
        is_action = stage == "action_in_progress" and bool(state.get("operation"))
        if signature != previous and (is_research_work or is_synthesis or is_action) and update_attempts < 2:
            previous = signature
            try:
                update_attempts += 1
                logger.info("spoken_work_progress_attempt | turn_id=%s | stage=%s", request.turn_id, state.get("stage"))
                update = str(state.get("message") or "").strip()
                if not update:
                    update = await brain.spoken_work_update(request.input, state)
                current_state = dict(get_state())
                if (
                    update and is_current()
                    and current_state.get("stage") == stage
                    and not voice.is_speaking.is_set()
                ):
                    _safe_speak(
                        voice,
                        update,
                        "neutral",
                        "work-progress",
                        channel=request.channel,
                        speech_owner=speech_owner,
                    )
                    logger.info(
                        "spoken_work_progress | turn_id=%s | session_id=%s | stage=%s | text=%r",
                        request.turn_id, request.session_id, state.get("stage"), update,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("Spoken work progress skipped", exc_info=True)
        await asyncio.sleep(interval)


def _spoken_research_summary(report) -> str:
    """Extract a short spoken summary from the validated report answer."""
    answer = str(getattr(report, "answer", "") or "")
    section = (
        r"(?:coverage(?:\s+(?:notes|summary|gaps|and limitations))?|research coverage|"
        r"sources?(?:\s+(?:and\s+)?citations?)?|bibliography|citations?|"
        r"evidence(?:\s+coverage)?|diagnostics|research details)"
    )
    kept = []
    for line in answer.splitlines():
        if re.match(rf"(?i)^\s*(?:#{{1,6}}\s*)?{section}\b(?:\s*:\s*.*|\s+-\s+.*|\s*)$", line):
            break
        if re.match(r"^\s*\[S\d+\]\s+https?://", line, re.I):
            continue
        kept.append(line)
    summary = " ".join(kept)
    summary = re.sub(r"\[S\d+\]", "", summary)
    summary = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", summary)
    summary = re.sub(r"https?://\S+", "", summary)
    summary = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", summary)
    summary = re.sub(r"\s+([,.!?;:])", r"\1", summary)
    summary = re.sub(r"\s+", " ", summary).strip(" \t\r\n-*#")
    sentences = re.split(r"(?<=[.!?])\s+", summary)
    spoken = []
    word_count = 0
    for sentence in sentences[:2]:
        words = sentence.split()
        if word_count + len(words) <= 30:
            spoken.append(sentence)
            word_count += len(words)
        elif not spoken:
            spoken.append(" ".join(words[:30]).rstrip(" ,;:.!?") + ".")
            break
        else:
            break
    return " ".join(spoken).strip()


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


def _clean_background_result(full_result: str, fallback_summary: str = "") -> str:
    """Render only useful result content for the owner channel."""
    text = " ".join((full_result or fallback_summary or "").split()).strip()
    if not text:
        return ""

    python_match = re.search(r"\bPython\s+version\s*:\s*(\d+(?:\.\d+)+)", text, re.IGNORECASE)
    taskkill_match = re.search(r"`(TASKKILL\s+[^`]+)`", text, re.IGNORECASE)
    if taskkill_match is None:
        taskkill_match = re.search(r"\b(TASKKILL\s+\[/S\s+[^\n]+)", text, re.IGNORECASE)
    if python_match and taskkill_match:
        heading = (taskkill_match.group(1) or taskkill_match.group(0)).strip("`*_ ")
        return f"Python version: {python_match.group(1)}\nTaskkill help heading: {heading}"

    text = re.sub(r"^Background task\s+['\"].*?['\"]\s+(?:done|failed)\.\s*(?:Result:\s*)?", "", text, flags=re.I)
    text = re.sub(r"\b(?:Done\.?|Here(?:'s| is) what I found:\s*)", "", text, flags=re.I)
    parts = [part.strip() for part in re.split(r"\s{2,}|\n+", text) if part.strip()]
    deduped = list(dict.fromkeys(parts))
    return "\n".join(deduped) or text


def _compact_catchup_speech(catchup_message: str) -> str:
    """Keep idle-return voice updates short; the dashboard retains full details."""
    text = " ".join((catchup_message or "").split())
    if re.search(r"(?i)research", text) and re.search(
        r"(?i)(?:failed|couldn['’]t verify|insufficient|without enough)", text
    ):
        return "Research finished without enough verified evidence. Check the dashboard."
    count = re.search(r"(?i)\b(\d+)\s+things finished\b", text)
    if count:
        return f"{count.group(1)} background tasks finished. Check the dashboard."
    if re.search(r"(?i)\bfailed\b", text):
        return "A background task failed. Check the dashboard."
    return "A background task finished. Check the dashboard."


async def _deliver_background_result(
    task_id, summary, *, db_path, telegram_bot, telegram_user_id, voice, channel=None, spoken_summary=None,
):
    store = ResultsStore(db_path=db_path)
    try:
        record = store.get(task_id)
    finally:
        store.close()
    full_result = record.full_result.strip() if record and record.full_result else ""
    message = _clean_background_result(full_result, summary)
    if len(message) > 4000:
        message = message[:4000].rstrip()

    delivery = {"telegram": "not_configured", "voice": "not_configured"}
    def cancellation_requested() -> bool:
        try:
            task = get_task_journal().get(task_id)
        except KeyError:
            return True
        return task.cancel_requested or task.status.value == "cancelled"

    if (
        channel == "telegram"
        and telegram_bot is not None
        and isinstance(telegram_user_id, int)
        and telegram_user_id > 0
    ):
        if cancellation_requested():
            delivery["telegram"] = "not_delivered"
            return delivery
        try:
            await telegram_bot.send_message(telegram_user_id, message)
            delivery["telegram"] = "accepted"
        except Exception:
            delivery["telegram"] = "failed"
            logger.warning("Telegram background-result delivery failed for %s", task_id, exc_info=True)
    if channel == "voice" and bool(getattr(voice, "is_ready", False)):
        if cancellation_requested():
            delivery["voice"] = "not_delivered"
            return delivery
        try:
            spoken = spoken_summary or message
            delivery["voice"] = "queued" if voice.speak(spoken, "neutral") is None else "not_queued"
        except Exception:
            delivery["voice"] = "failed"
            logger.warning("Voice background-result delivery failed for %s", task_id, exc_info=True)
    elif channel == "voice" and voice is not None:
        delivery["voice"] = "not_ready"
    return delivery


_DASHBOARD_RESEARCH_HISTORY_LIMIT = 10
_RESEARCH_TELEGRAM_HANDOFF = re.compile(
    r"\b(?:send|share|forward)\b(?s:.{0,120}?)\b(?:research|findings|results|report|that|this|it)\b"
    r"(?s:.{0,80}?)\b(?:to|over|via|on)\s+(?:my\s+)?telegram\b",
    re.IGNORECASE,
)
_DASHBOARD_ACTIVITY_HISTORY_LIMIT = 40
_DASHBOARD_ACTIVITY_EVENT_TYPES = frozenset({
    "background_task",
    "task_snapshot",
    "tool_call",
    "tool_result",
    "research_progress",
    "research_result",
    "result_stored",
    "tool_approval_request",
    "tool_approval_resolved",
    "browser_task_started",
    "browser_task_done",
    "alert",
})


def _is_research_telegram_handoff(text: str) -> bool:
    return bool(_RESEARCH_TELEGRAM_HANDOFF.search(text.strip()))


def _latest_completed_research_result(
    history_by_session: dict[str, list[dict[str, Any]]], session_id: str
) -> Optional[dict[str, Any]]:
    for item in reversed(history_by_session.get(session_id, [])):
        result_text = item.get("text") or item.get("answer") if isinstance(item, dict) else None
        if (
            isinstance(item, dict)
            and item.get("session_id") == session_id
            and item.get("partial") is False
            and item.get("completeness") == "complete"
            and item.get("termination_reason") == "completed"
            and isinstance(result_text, str)
            and result_text.strip()
        ):
            return item
    return None


async def _send_recent_research_to_telegram(
    history_by_session: dict[str, list[dict[str, Any]]],
    session_id: str,
    *,
    telegram_enabled: bool,
    telegram_token_configured: bool,
    telegram_user_id: int,
    telegram_bot: Any,
) -> str:
    if not telegram_enabled or not telegram_token_configured or telegram_user_id <= 0 or telegram_bot is None:
        return "unavailable"
    result = _latest_completed_research_result(history_by_session, session_id)
    if result is None:
        return "missing"
    try:
        message_id = await telegram_bot.send_message(
            telegram_user_id, result.get("text") or result["answer"]
        )
    except Exception:
        logger.warning("Telegram research handoff delivery failed", exc_info=True)
        return "failed"
    return "sent" if message_id is not None else "unconfirmed"


def _append_dashboard_research_result(
    history_by_session: dict[str, list[dict[str, Any]]],
    session_id: Optional[str],
    payload: dict[str, Any],
) -> None:
    history = history_by_session.setdefault(session_id or "default", [])
    result_id = payload.get("result_id")
    if isinstance(result_id, str) and result_id:
        for index, existing in enumerate(history):
            if existing.get("result_id") == result_id:
                history[index] = payload
                return
    history.append(payload)
    del history[:-_DASHBOARD_RESEARCH_HISTORY_LIMIT]


def _dashboard_history_record(event: Any) -> Optional[dict[str, Any]]:
    """Project only session-owned, user-visible event data into durable history."""
    if not isinstance(event, dict):
        return None
    event_type = event.get("type")
    event_id = event.get("id")
    session_id = event.get("session_id")
    payload = event.get("payload")
    if (
        not isinstance(event_type, str)
        or event_type not in _DASHBOARD_ACTIVITY_EVENT_TYPES
        or not isinstance(event_id, str)
        or not event_id
        or not isinstance(session_id, str)
        or not session_id
        or not isinstance(payload, dict)
    ):
        return None
    channel = payload.get("channel")
    if channel == "telegram":
        return None
    if event_type in {"tool_approval_request", "tool_approval_resolved"} and channel != "web":
        return None
    if event_type == "research_result":
        stored_payload = payload
    else:
        summary = next(
            (
                value.strip()
                for key in ("title", "current_action", "action", "operation_preview", "message", "name", "text")
                if isinstance((value := payload.get(key)), str) and value.strip()
            ),
            "",
        )
        if not summary:
            return None
        stored_payload = {"message": summary[:320]}
        if isinstance(channel, str):
            stored_payload["channel"] = channel
    return {
        "session_id": session_id,
        "event_id": event_id,
        "event_type": event_type,
        "timestamp": event.get("timestamp") if isinstance(event.get("timestamp"), str) else None,
        "payload": stored_payload,
    }


def _dashboard_activity_event(event: dict[str, Any]) -> dict[str, Any]:
    """Keep research details in the result view; activity gets a short label."""
    if event.get("type") != "research_result":
        return event
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    query = payload.get("query")
    message = f"Research completed: {query}" if isinstance(query, str) and query else "Research completed"
    activity_payload = {"message": message}
    if isinstance(payload.get("channel"), str):
        activity_payload["channel"] = payload["channel"]
    return {**event, "payload": activity_payload}


def _research_resource_records(items: Any) -> list[dict[str, str]]:
    from urllib.parse import urlparse

    records = []
    for item in items or []:
        raw_url = str(getattr(item, "url", "") or "").strip()
        url = raw_url
        canonical_url = str(getattr(item, "canonical_url", "") or "").strip()
        parsed = urlparse(url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            url = canonical_url
            parsed = urlparse(url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            continue
        domain = str(getattr(item, "domain", "") or "").strip() or parsed.hostname or parsed.netloc
        records.append({
            "title": str(getattr(item, "title", "") or "").strip()[:300] or domain,
            "url": url,
            "canonical_url": canonical_url if url == raw_url else url,
            "domain": domain[:255],
        })
    return records


async def main() -> int:
    global _main_event_bus, _main_event_bus_registry
    # Claim the runtime before doing any real startup work. The expensive part
    # of boot (models, brain, subscriptions) is worth skipping entirely for an
    # instance that is not allowed to own the Telegram poller anyway. Releasing
    # lives in the shutdown `finally` below; the few lines between here and that
    # `try` are covered by the OS, which drops the lock if this process dies
    # before reaching it -- the same guarantee that makes a crashed predecessor
    # non-blocking for the next run.
    instance_lock = SingleInstanceLock()
    try:
        instance_lock.acquire()
    except SingleInstanceError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    logger.info("Single-instance lock acquired: %s (pid=%s)", instance_lock.path, os.getpid())
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
    exit_code = 0
    shutdown_quiescent = True
    # True while a chat turn's LLM/tool loop runs -- see _dispatch_or_queue.
    turn_active = False
    pending_turns: list[TurnRequest] = []
    pending_turn_times: Dict[str, float] = {}
    voice_diagnostic_traces: Dict[str, Any] = {}
    turn_channels_by_id: Dict[str, str] = {}
    turn_work_states: Dict[str, dict] = {}
    turn_research_turns = set()
    turn_spoken_research_summaries: Dict[str, str] = {}
    repeated_success_patterns_by_turn: Dict[str, list[dict[str, str]]] = {}
    telegram_status_messages_by_turn: Dict[str, int] = {}
    telegram_status_text_by_turn: Dict[str, str] = {}
    telegram_status_tasks_by_turn: Dict[str, asyncio.Task] = {}
    telegram_approval_turn_by_request: Dict[str, str] = {}
    telegram_approval_expiry_tasks: Dict[str, asyncio.Task] = {}
    telegram_origin_turn_ids: set[str] = set()
    telegram_background_tasks_by_turn: Dict[str, set[str]] = {}
    telegram_background_task_ids: set[str] = set()
    telegram_background_start_messages: Dict[str, set[int]] = {}
    telegram_background_finished_tasks: set[str] = set()
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
    web_gateway: Optional[RuntimeWebGateway] = None
    settings_operation_lock = asyncio.Lock()
    session_lifecycle_gate = asyncio.Lock()
    extension_runtime_registry = ExtensionRuntimeRegistry()
    extension_operation_results: OrderedDict[str, dict[str, Any]] = OrderedDict()
    # shortcut: process-local acknowledgments, so refresh/restart or eviction can admit a retry again; persist only if that becomes an acceptance requirement.
    web_text_command_results: OrderedDict[str, tuple[str, str, dict[str, Any]]] = OrderedDict()
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
            channel = turn_channels_by_id.get(active_turn_id or "")
            if channel != "voice" or voice is None:
                _print_console_reply(text)
            elif voice:
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

        desktop_focus_overlay_tasks = set()

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

        def on_tool_execution_start(name, args, *, turn_id=None, task_id=None, session_id=None):
            from charlie.computer.focus_overlay import is_desktop_focus_tool, show_desktop_focus

            if is_desktop_focus_tool(name) and show_desktop_focus(name, args):
                desktop_focus_overlay_tasks.add(task_id or turn_id)

        def on_tool_result(name, result, *, turn_id=None, task_id=None, session_id=None):
            nonlocal active_operation_name, active_operation_task_id, active_operation_cancellable
            from charlie.computer.focus_overlay import hide_desktop_focus, is_desktop_focus_tool

            overlay_task = task_id or turn_id
            if is_desktop_focus_tool(name) and (overlay_task in desktop_focus_overlay_tasks or None in desktop_focus_overlay_tasks):
                desktop_focus_overlay_tasks.discard(overlay_task)
                if None in desktop_focus_overlay_tasks:
                    desktop_focus_overlay_tasks.discard(None)
                hide_desktop_focus()
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
            event_session_id = envelope.session_id or current_session_id
            structured = envelope.data.get("structured_data")
            if not isinstance(structured, dict):
                structured = next((item for item in envelope.artifacts if isinstance(item, dict)), None)
            if not isinstance(structured, dict) and isinstance(envelope.data, dict) and envelope.data.get("verified") is True:
                structured = envelope.data
            if name in {"weather_get", "stopwatch_control", "timer_create", "timer_cancel", "dashboard_widget_control"} and (
                event_session_id != current_session_id or not isinstance(structured, dict)
            ):
                logger.debug(
                    "Dashboard widget result projection skipped: tool=%s session_match=%s structured=%s",
                    name,
                    event_session_id == current_session_id,
                    isinstance(structured, dict),
                )
            if event_session_id == current_session_id and isinstance(structured, dict) and structured.get("verified") is True:
                changed = False
                if name == "weather_get":
                    location = structured.get("location")
                    current = structured.get("current")
                    daily = structured.get("daily")
                    units = structured.get("units")
                    if isinstance(location, dict) and isinstance(current, dict):
                        sunset_values = daily.get("sunset") if isinstance(daily, dict) else None
                        dashboard_widget_state["weather"] = {
                            "location": str(location.get("name") or location.get("display_name") or ""),
                            "temperature": current.get("temperature_2m"),
                            "temperature_unit": units.get("temperature_2m", "°C") if isinstance(units, dict) else "°C",
                            "feels_like": current.get("apparent_temperature"),
                            "humidity": current.get("relative_humidity_2m"),
                            "wind_speed": current.get("wind_speed_10m"),
                            "wind_speed_unit": units.get("wind_speed_10m", "km/h") if isinstance(units, dict) else "km/h",
                            "weather_code": current.get("weather_code"),
                            "observed_at": current.get("time"),
                            "sunset": sunset_values[0] if isinstance(sunset_values, list) and sunset_values else None,
                        }
                        changed = True
                elif name == "stopwatch_control":
                    state = structured.get("state")
                    elapsed = structured.get("elapsed_seconds")
                    if state in {"running", "stopped"} and isinstance(elapsed, (int, float)):
                        dashboard_widget_state["stopwatch"] = {
                            "state": state,
                            "elapsed_seconds": max(0.0, float(elapsed)),
                            "sampled_at": datetime.now(timezone.utc).isoformat(),
                        }
                        changed = True
                elif name in {"timer_create", "timer_cancel"}:
                    changed = True
                elif name == "dashboard_widget_control" and isinstance(structured.get("visible"), bool):
                    changed = structured.get("widget", "clock") in {"clock", "weather", "timer", "stopwatch", "music", "system_status"}
                if changed:
                    _publish_dashboard_widgets()

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

        async def _delete_telegram_background_ack(task_id: str) -> None:
            """Delete transient background acknowledgement messages, preserving final results."""
            message_ids = telegram_background_start_messages.pop(task_id, set())
            if telegram_bot is None:
                return
            for message_id in message_ids:
                try:
                    await telegram_bot.delete_status_message(config.telegram_user_id, message_id)
                except Exception:
                    logger.warning(
                        "Could not clear Telegram background acknowledgement %s for task %s",
                        message_id,
                        task_id,
                        exc_info=True,
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
            if turn_id and turn_channels_by_id.get(turn_id) == "voice":
                state = {"stage": "action_in_progress", "operation": name}
                if name == "research" and isinstance(args, dict):
                    turn_research_turns.add(turn_id)
                    state = {
                        key: args[key]
                        for key in (
                            "stage", "message", "current", "total", "query",
                            "source_count", "search_result_count", "evidence_count",
                            "passage_count", "finding_count", "resolved_count", "unresolved_count",
                        )
                        if key in args
                    }
                    if state.get("stage") in {"done", "synthesizing"}:
                        state["stage"] = "answer_generation"
                        state["source_acquisition_finished"] = True
                turn_work_states[turn_id] = state
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

            background_record = None
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
                        # Do not leave an approved future capable of running an
                        # operation whose journal transition failed.
                        resolve_tool_approval(
                            request_id,
                            False,
                            expected_platform=expected_platform,
                        )
                        return False

            resolved = resolve_tool_approval(
                request_id,
                approved,
                expected_platform=expected_platform,
            )
            if not resolved:
                logger.warning("Ignored stale or unknown tool approval: %s", request_id)
                if background_record is not None:
                    try:
                        get_task_journal().fail(
                            background_record.id,
                            error_summary="Approval callback became stale before execution; action was not run.",
                        )
                    except Exception:
                        logger.error(
                            "Could not fail stale background task %s after approval race",
                            background_record.id,
                            exc_info=True,
                        )
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

        web_approval_payload: dict[str, Any] = {}

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
            if tool_name == "desktop_close_app":
                reason = "Closing an app may lose unsaved changes."
            elif reason.startswith("operation '") and reason.endswith("requires approval"):
                reason = "Charlie needs your approval before continuing with this action."
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
            if platform == "web":
                if event_bus is None:
                    return False
                web_approval_payload.clear()
                web_approval_payload.update({
                    "request_id": request_id, "message": reason, "channel": platform,
                    "operation_preview": operation_preview or "",
                })
                await event_bus.emit(
                    "tool_approval_request", dict(web_approval_payload),
                    meta=EventMeta(source=EventSource.BRAIN, turn_id=turn_id, task_id=task_id, session_id=session_id),
                )
                return True
            if platform == "voice":
                if voice is None or not voice.is_ready:
                    return False
                _safe_speak(voice, f"Need your OK: {reason}. Yes or no?", last_emotion, channel="voice")
                return True
            if platform == "console":
                _print_console_reply(f"Need your OK: {reason}. Yes or no?")
                return True
            return False

        async def on_result_stored(task_id, summary, attention_level, *, channel=None, spoken_summary=None):
            try:
                return await _deliver_background_result(
                    task_id,
                    summary,
                    db_path=config.session_db_path,
                    telegram_bot=telegram_bot,
                    telegram_user_id=config.telegram_user_id,
                    voice=voice,
                    channel=channel,
                    spoken_summary=spoken_summary,
                )
            finally:
                # Result persistence can race the journal terminal callback;
                # cleanup of the transient acknowledgement must not depend on
                # telegram_background_task_ids still containing the task.
                telegram_background_finished_tasks.add(task_id)
                await _delete_telegram_background_ack(task_id)
                if task_id in telegram_background_task_ids:
                    await _finish_telegram_turn_feedback(task_id)
                    telegram_background_task_ids.discard(task_id)
                    for parent_turn_id, task_ids in list(telegram_background_tasks_by_turn.items()):
                        task_ids.discard(task_id)
                        if not task_ids:
                            telegram_background_tasks_by_turn.pop(parent_turn_id, None)
                            telegram_origin_turn_ids.discard(parent_turn_id)

        recent_research_results = {}

        def on_research_result(report, *, session_id, task_id=None, turn_id=None, channel=None):
            if turn_id and turn_id in turn_work_states:
                coverage = list(getattr(report, "coverage", []) or [])
                turn_work_states[turn_id] = {
                    "stage": "answer_generation",
                    "source_count": len(getattr(report, "sources", []) or []),
                    "evidence_count": len(getattr(report, "evidence", []) or []),
                    "passage_count": len(getattr(report, "passages", []) or []),
                    "finding_count": len(getattr(report, "facts", []) or []),
                    "resolved_count": sum(getattr(item, "status", "") in {"supported", "contradicted"} for item in coverage),
                    "unresolved_count": sum(getattr(item, "status", "") == "unresolved" for item in coverage),
                    "partial": bool(getattr(report, "partial", False)),
                }
                if channel == "voice" or turn_channels_by_id.get(turn_id) == "voice":
                    turn_research_turns.add(turn_id)
                    turn_spoken_research_summaries[turn_id] = _spoken_research_summary(report)
            if event_bus is None:
                return False
            from charlie.research.search import clean_query
            from charlie.research.citations import referenced_ids
            from charlie.research.sources import citation_source_class
            from datetime import datetime, timezone

            used_sources = referenced_ids(report.answer) if getattr(report, "answer", "") else set()
            included_passages = [
                item
                for item in getattr(report, "passages", [])
                if not used_sources or item.source_id in used_sources
            ]
            included_passage_ids = {item.passage_id for item in included_passages}
            raw_search_results = list(getattr(report, "search_results", []) or [])
            raw_read_pages = list(getattr(report, "sources", []) or [])
            search_results = _research_resource_records(raw_search_results)
            read_pages = _research_resource_records(raw_read_pages)
            sources_list = []
            for citation in report.citations:
                if citation.source_id in used_sources:
                    s_class = None
                    if hasattr(report, "sources"):
                        for s in report.sources:
                            if s.source_id == citation.source_id:
                                s_class = getattr(s, "source_class", None)
                                break
                    s_class = citation_source_class(
                        s_class, citation.url, brief=getattr(report, "brief", None)
                    )
                    sources_list.append({
                        "id": citation.source_id,
                        "title": citation.title,
                        "url": citation.url,
                        "class": s_class,
                    })

            logger.info(
                "research_result_resources | turn_id=%s | raw_search=%d | displayed_links=%d "
                "| raw_pages=%d | displayed_pages=%d | cited_sources=%d",
                turn_id,
                len(raw_search_results),
                len(search_results),
                len(raw_read_pages),
                len(read_pages),
                len(sources_list),
            )

            sources_text = "\n".join(
                f"[{item['id']}] {item['title']}: {item['url']}"
                for item in sources_list
            )
            answer = report.answer or "Research finished without a verified answer."
            if store is not None and session_id:
                try:
                    store.append("assistant", answer, session_id=session_id, turn_id=turn_id)
                    store.touch_session(session_id)
                except SessionNotFoundError:
                    logger.warning(
                        "session_persistence_dropped | phase=sustained_research_answer "
                        "| session_id=%s | turn_id=%s | reason=session_deleted",
                        session_id,
                        turn_id,
                    )
                except Exception:
                    logger.warning("sustained_research_answer_archive_failed", exc_info=True)
            legacy_text = f"{answer}\n\nSources:\n{sources_text}" if sources_text else answer
            topic = (
                report.brief.topic
                if getattr(report, "brief", None) and report.brief.topic
                else clean_query(report.query)
            )
            payload = {
                "result_id": task_id or turn_id,
                "channel": channel or turn_channels_by_id.get(turn_id or ""),
                "turn_id": turn_id,
                "session_id": session_id,
                "query": topic,
                "mode": getattr(getattr(report, "mode", None), "value", None),
                "text": legacy_text,
                "answer": answer,
                "partial": getattr(report, "partial", False),
                "completeness": getattr(getattr(report, "completeness", None), "value", None),
                "termination_reason": getattr(getattr(report, "termination_reason", None), "value", None),
                "stop_reason": getattr(report, "stop_reason", ""),
                "search_result_count": len(search_results),
                "document_count": len(read_pages),
                "evidence_count": len(getattr(report, "evidence", [])),
                "passage_count": len(getattr(report, "passages", [])),
                "fact_count": len(getattr(report, "facts", [])),
                "search_results": search_results[:80],
                "read_pages": read_pages[:80],
                "source_urls": list(dict.fromkeys(item["url"] for item in read_pages)),
                "evidence": [
                    {
                        "source_id": item.source_id,
                        "passage_id": item.passage_id,
                        "statement": item.statement,
                        "document_hash": item.document_hash,
                        "start_offset": item.start_offset,
                        "end_offset": item.end_offset,
                    }
                    for item in getattr(report, "evidence", [])
                    if not used_sources or item.source_id in used_sources
                ],
                "verified_facts": [
                    {
                        "candidate": fact.candidate,
                        "aspect": fact.aspect,
                        "value": fact.value,
                        "quote": fact.quote,
                        "source_id": fact.source_id,
                        "url": fact.url,
                    }
                    for fact in getattr(report, "facts", [])
                    if not used_sources or fact.source_id in used_sources
                ],
                "passages": [
                    {
                        "id": item.passage_id,
                        "source_id": item.source_id,
                        "source_url": item.source_url,
                        "canonical_url": item.canonical_url,
                        "document_hash": item.document_hash,
                        "text": item.text,
                        "start_offset": item.start_offset,
                        "end_offset": item.end_offset,
                        "retrieved_at": item.retrieved_at,
                        "published_at": item.published_at,
                        "source_class": item.source_class,
                    }
                    for item in included_passages
                ],
                "claims": [
                    {
                        "id": claim.claim_id,
                        "text": claim.text,
                        "subject": claim.subject,
                        "predicate": claim.predicate,
                        "value": claim.value,
                        "units": claim.units,
                        "time_scope": claim.time_scope,
                        "evidence_passage_ids": [
                            passage_id
                            for passage_id in claim.evidence_passage_ids
                            if passage_id in included_passage_ids
                        ],
                        "relationship": claim.relationship,
                        "verification_status": claim.verification_status,
                    }
                    for claim in getattr(report, "claims", [])
                    if set(claim.evidence_passage_ids).intersection(included_passage_ids)
                ],
                "duration_ms": float(getattr(report, "duration_ms", 0.0) or 0.0),
                "provider_outcomes": list(getattr(report, "provider_outcomes", [])),
                "synthesis": getattr(report, "synthesis_kind", "model"),
                "gaps": getattr(report, "gaps", []),
                "coverage": [
                    {
                        "id": item.id,
                        "question": item.question,
                        "required_fields": list(getattr(item, "required_fields", [])),
                        "supporting_claim_ids": list(getattr(item, "supporting_claim_ids", [])),
                        "refuting_claim_ids": list(getattr(item, "refuting_claim_ids", [])),
                        "status": item.status,
                        "conflict": bool(getattr(item, "conflict", False)),
                        "missing_evidence": list(item.missing_evidence),
                    }
                    for item in getattr(report, "coverage", [])
                ],
                "sources": sources_list,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
            _append_dashboard_research_result(recent_research_results, session_id, payload)
            receipt = _submit_event_threadsafe(
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
            return receipt if receipt is not None else False

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
                on_tool_execution_start=on_tool_execution_start,
                on_operation_result=on_operation_result,
                get_current_weather_location=lambda: (
                    memory_service.get_preference("current_location")
                    or (dashboard_widget_state.get("weather") or {}).get("location")
                ),
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

        # Self-extension owner approval.
        # The seam in charlie.tools is synchronous (it is reached from executor
        # threads), while the canonical owner above is async. The bridge submits
        # the owner's decision to THIS loop and waits for the answer; if it is ever
        # called from the loop thread it refuses rather than deadlocking the loop
        # that has to deliver the answer.
        #
        # The self-extension seam carries no turn identity, so the channel that can
        # answer is resolved from the turns main is currently serving. Raising the
        # prompt on a channel nobody can answer would park the single global
        # approval slot for the whole timeout and decline every other gated tool
        # call meanwhile, so an unknown or ambiguous channel refuses without
        # prompting at all.
        approval_channels = ApprovalChannelRegistry()
        try:
            from charlie.core import ApprovalDecision as _ApprovalDecision
            from charlie.self_extension.approval_bridge import (
                build_self_extension_approval_callback,
            )
            from charlie.tools import set_self_extension_approval_callback

            async def _self_extension_owner_decision(
                payload: dict[str, Any]
            ) -> Optional[str]:
                binding = str(payload.get("approval_binding") or "")
                argv = [str(item) for item in (payload.get("argv") or [])]
                if not binding or not argv:
                    return None
                channel = approval_channels.resolve()
                if channel is None:
                    # Fail closed FAST: no prompt is parked, so the single approval
                    # slot stays free for other gated calls.
                    logger.debug(
                        "Self-extension approval channel is not knowable; refusing "
                        "without prompting."
                    )
                    return None
                # The reason is built from an LLM-planned request and reaches the
                # owner verbatim: self_extension_proposal has no hardened preview
                # channel, so it is sanitized here before it can structure or
                # answer the owner's question.
                reason = sanitize_approval_reason(
                    payload.get("reason") or "Approve this self-extension change?"
                )
                if not reason:
                    return None
                decision = await brain._request_tool_approval_decision(
                    "self_extension_proposal",
                    {"command": argv},
                    reason,
                    platform=channel,
                    risk_class="security_sensitive",
                )
                if getattr(decision, "value", decision) != _ApprovalDecision.APPROVED.value:
                    return None
                return binding

            set_self_extension_approval_callback(
                build_self_extension_approval_callback(
                    _self_extension_owner_decision,
                    loop=loop,
                    # The bridge must always give up before the owner's own wait so
                    # the owner's finally clears the global approval slot.
                    owner_timeout=getattr(brain, "_approval_timeout", None),
                )
            )
        except Exception:
            logger.warning(
                "Self-extension approval bridge unavailable; proposals will refuse",
                exc_info=True,
            )

        # Canonical memory facade wiring stays owned by main's composition root.
        _wire_memory_service(memory_service)
        self_extension_orchestrator = None

        # Placeholder for event_bus (set later in async context)
        event_bus = None
        current_session_id = f"voice_{_LAUNCH_ID}"
        _default_hidden_widgets = {"weather", "stopwatch", "music", "timer", "system_status"}
        _saved_widget_visibility = None
        if memory_service:
            try:
                _saved_widget_visibility = json.loads(
                    memory_service.get_preference("dashboard_widget_visibility") or "null"
                )
            except Exception:
                logger.warning("Dashboard widget visibility preference could not be read", exc_info=True)
        dashboard_widget_state = {
            "weather": None,
            "stopwatch": None,
            "timers": [],
            "media": None,
            "system_status": None,
            "clock_visible": (
                _saved_widget_visibility.get("clock_visible") is True
                if isinstance(_saved_widget_visibility, dict) else False
            ),
            "hidden_widgets": _default_hidden_widgets,
        }
        if isinstance(_saved_widget_visibility, dict):
            saved_hidden = _saved_widget_visibility.get("hidden_widgets")
            if isinstance(saved_hidden, list) and len(saved_hidden) <= 512 and all(
                isinstance(item, str) and len(item) <= 128
                and (item in {"weather", "stopwatch", "music", "timer", "system_status"} or item.startswith("timer:"))
                for item in saved_hidden
            ):
                saved_hidden_set = set(saved_hidden)
                if _saved_widget_visibility.get("schema_version", 1) < 2:
                    saved_hidden_set.add("system_status")
                dashboard_widget_state["hidden_widgets"] = saved_hidden_set

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
            from charlie.core import get_active_tool_approval
            pending_approval = get_active_tool_approval()
            approval_answer = bool(pending_approval and pending_approval[1] == "voice"
                and _parse_owner_text_approval(text, platform="voice", approval_channel="voice") is not None)
            if not approval_answer and last_dispatch is not None and now - last_dispatch < _DEDUPE_WINDOW_SEC:
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
                and request.channel in {"voice", "telegram", "web"}
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

        async def _web_command(command: dict[str, Any]) -> dict[str, Any]:
            """Route browser commands through canonical runtime owners."""
            command_type = str(command.get("type", ""))
            if command_type in {"set_dashboard_widget_visibility", "set_clock_widget_visibility"}:
                widget = "clock" if command_type == "set_clock_widget_visibility" else command.get("widget")
                result = _set_dashboard_widget_visibility(widget, command.get("visible"), command.get("widget_id"))
                if result.get("accepted"):
                    _publish_dashboard_widgets()
                return result
            if command_type == "update_timer":
                import math
                from datetime import timedelta

                schedule_id = command.get("schedule_id")
                text = command.get("text", "Timer finished")
                delay_seconds = command.get("delay_seconds")
                delivery_channel = command.get("delivery_channel", "web")
                if not isinstance(schedule_id, str) or not schedule_id.strip():
                    raise ValueError("schedule_id must be a non-empty string")
                if not isinstance(delivery_channel, str) or delivery_channel not in {"web", "voice"}:
                    raise ValueError("delivery_channel must match a dashboard-visible timer")
                if not isinstance(text, str) or len(text) > 120:
                    raise ValueError("text must be a string of at most 120 characters")
                if isinstance(delay_seconds, bool) or not isinstance(delay_seconds, (int, float)) or not math.isfinite(delay_seconds) or delay_seconds <= 0:
                    raise ValueError("delay_seconds must be a positive finite number")
                if calendar_runtime is None:
                    return {"accepted": False, "error": "The timer service is unavailable."}
                schedule = await calendar_runtime.execute("get_automation", schedule_id)
                if (schedule.get("kind") != "reminder" or schedule.get("recurrence") != "once"
                    or schedule.get("status") != "active" or schedule.get("delivery_channel") != delivery_channel
                    or schedule.get("active_run_status") == "running"):
                    return {"accepted": False, "error": "This timer is no longer editable."}
                due_at = (datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)).isoformat()
                updated = await calendar_runtime.execute("update_automation", schedule_id, {
                    "text": text.strip() or "Timer finished",
                    "first_run_at": due_at,
                })
                if (updated.get("status") != "active" or updated.get("text") != (text.strip() or "Timer finished")
                    or updated.get("delivery_channel") != delivery_channel):
                    return {"accepted": False, "error": "Charlie could not verify the timer change."}
                _publish_dashboard_widgets()
                return {"accepted": True, "schedule_id": schedule_id, "due_at": updated.get("next_run_at")}
            if command_type == "stopwatch_control":
                action = command.get("action")
                if action not in {"start", "stop", "reset"}:
                    raise ValueError("action must be start, stop, or reset")
                from charlie.tools import stopwatch_control

                result = await asyncio.to_thread(stopwatch_control, action)
                structured = result.structured_data if isinstance(result.structured_data, dict) else {}
                if structured.get("verified") is not True:
                    return {"accepted": False, "error": "Charlie could not verify the stopwatch change."}
                dashboard_widget_state["stopwatch"] = {
                    "state": structured.get("state"),
                    "elapsed_seconds": structured.get("elapsed_seconds"),
                    "sampled_at": datetime.now(timezone.utc).isoformat(),
                }
                _publish_dashboard_widgets()
                return {"accepted": True, **structured}
            if command_type == "media_control":
                action = command.get("action")
                if action not in {"play_pause", "next_track", "prev_track"}:
                    raise ValueError("action must be play_pause, next_track, or prev_track")
                from charlie.tools import media_control

                result = await asyncio.to_thread(media_control, action)
                structured = result.structured_data if isinstance(result.structured_data, dict) else {}
                if structured.get("ok") is not True:
                    return {"accepted": False, "error": str(structured.get("reason") or "Media command was unavailable.")}
                _publish_dashboard_widgets()
                return {"accepted": True, "action": action, "verified": structured.get("verified") is True}
            if command_type == "update_settings":
                updates = command.get("updates")
                if not isinstance(updates, dict) or not updates:
                    return {"accepted": False, "error": "Choose at least one setting to save."}
                allowed_keys = {field["key"] for field in settings_service.get_field_specs()}
                if any(key not in allowed_keys for key in updates):
                    return {"accepted": False, "error": "The request contains an unknown setting."}
                from charlie.settings_service import SettingValidationError

                try:
                    result = await asyncio.to_thread(settings_service.apply_updates, updates)
                except SettingValidationError as exc:
                    return {"accepted": False, "error": str(exc)}
                await _publish_settings_snapshot(event_bus, settings_service, rationale="dashboard settings saved")
                return {"accepted": True, "saved": result["saved"]}
            if command_type in {"set_mic_state", "set_audio_state"}:
                if not config.voice_enabled:
                    raise ValueError("native voice is unavailable")
                field = "mic_muted" if command_type == "set_mic_state" else "muted"
                value = command.get(field)
                if not isinstance(value, bool):
                    raise ValueError(f"{field} must be a boolean")
                state = voice.set_mic_state(value) if field == "mic_muted" else voice.set_audio_state(muted=value)
                await event_bus.emit(
                    "mic_state" if field == "mic_muted" else "audio_state",
                    state,
                    meta=EventMeta(source=EventSource.VOICE),
                )
                return {"accepted": True, **state}
            if command_type == "submit_text":
                text = command.get("text")
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("text must be a non-empty string")
                request_id = command.get("request_id")
                session_id = command.get("session_id")
                if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
                    raise ValueError("request_id must be a non-empty string of at most 128 characters")
                if not isinstance(session_id, str) or not session_id.strip():
                    raise ValueError("session_id must be a non-empty string")
                clean_text = text.strip()
                cached = web_text_command_results.get(request_id)
                if cached is not None:
                    cached_session_id, cached_text, acknowledgement = cached
                    if (cached_session_id, cached_text) != (session_id, clean_text):
                        return {"accepted": False, "error": "request_id is already bound to a different text or session."}
                    web_text_command_results.move_to_end(request_id)
                    return dict(acknowledgement)
                if session_id != current_session_id:
                    return {"accepted": False, "error": "The dashboard session changed; refresh before sending this message."}
                request = _allocate_turn_request(clean_text, session_id, "web")
                acknowledgement = {"accepted": True, "turn_id": request.turn_id, "session_id": request.session_id}
                web_text_command_results[request_id] = (session_id, clean_text, acknowledgement)
                web_text_command_results.move_to_end(request_id)
                while len(web_text_command_results) > _WEB_TEXT_COMMAND_CACHE_MAX:
                    web_text_command_results.popitem(last=False)
                process_coro = _dispatch_or_queue(request)
                try:
                    scheduled = _schedule_process(process_coro, loop)
                except Exception:
                    web_text_command_results.pop(request_id, None)
                    process_coro.close()
                    raise
                if scheduled is None:
                    web_text_command_results.pop(request_id, None)
                    return {"accepted": False, "error": "The runtime could not schedule this message."}
                return dict(acknowledgement)
            if command_type == "cancel_task":
                task_id = command.get("task_id")
                if not isinstance(task_id, str) or not task_id.strip():
                    raise ValueError("task_id must be a non-empty string")
                accepted = background_task.cancel(task_id)
                record = get_task_journal().get(task_id)
                await _publish_task_snapshot(event_bus)
                return {
                    "accepted": accepted,
                    "task_id": record.id,
                    "status": record.status.value,
                    "cancel_requested": record.cancel_requested,
                }
            if command_type in {"approve", "reject"}:
                request_id = command.get("request_id")
                if not isinstance(request_id, str) or not request_id.strip():
                    raise ValueError("request_id must be a non-empty string")
                from charlie.core import get_active_tool_approval

                pending = get_active_tool_approval()
                if pending is None or pending[0] != request_id:
                    raise ValueError("approval is stale or unavailable")
                resolved = _resolve_tool_approval_and_notify(
                    request_id,
                    command_type == "approve",
                    expected_platform="web",
                )
                if not resolved:
                    raise ValueError("approval was not accepted")
                return {"accepted": True, "request_id": request_id, "approved": command_type == "approve"}
            raise ValueError(f"unsupported web command: {command_type}")

        def _dashboard_widgets_snapshot() -> dict[str, Any]:
            media = dashboard_widget_state.get("media")
            return {
                "timers": list(dashboard_widget_state.get("timers", [])),
                "weather": dashboard_widget_state.get("weather"),
                "stopwatch": dashboard_widget_state.get("stopwatch"),
                **({"media": media} if isinstance(media, dict) else {}),
                "clock_visible": dashboard_widget_state.get("clock_visible", False),
                "hidden_widgets": sorted(dashboard_widget_state.get("hidden_widgets", set())),
                "system_status": dashboard_widget_state.get("system_status"),
            }

        def _refresh_dashboard_widgets_snapshot() -> dict[str, Any]:
            timers = list(dashboard_widget_state.get("timers", []))
            media = dashboard_widget_state.get("media")
            try:
                from charlie.tools import media_snapshot

                snapshot_result = media_snapshot().structured_data
                if isinstance(snapshot_result, dict):
                    media = snapshot_result
            except Exception:
                logger.warning("dashboard media snapshot could not be loaded", exc_info=True)
            if calendar_runtime is not None:
                try:
                    schedules = calendar_runtime.execute_sync("list_automations")
                    timers = [
                        {
                            "id": schedule.get("id"),
                            "text": schedule.get("text", "Timer"),
                            "due_at": schedule.get("next_run_at") or schedule.get("first_run_at"),
                            "timezone": schedule.get("timezone"),
                            "delivery_channel": schedule.get("delivery_channel"),
                        }
                        for schedule in schedules
                        if schedule.get("kind") == "reminder"
                        and schedule.get("recurrence") == "once"
                        and schedule.get("status") == "active"
                        and schedule.get("delivery_channel") in {"web", "voice"}
                        and isinstance(schedule.get("next_run_at") or schedule.get("first_run_at"), str)
                    ]
                except Exception:
                    logger.warning("dashboard timer widget snapshot could not be loaded", exc_info=True)
            dashboard_widget_state["timers"] = timers
            dashboard_widget_state["media"] = media
            return _dashboard_widgets_snapshot()

        dashboard_media_signature = None

        def _dashboard_media_signature(widgets: dict[str, Any]) -> tuple[Any, ...] | None:
            media = widgets.get("media")
            if not isinstance(media, dict):
                return None
            return tuple(media.get(key) for key in (
                "available", "adapter_available", "title", "artist", "album", "app", "status", "duration_seconds",
            ))

        def _set_dashboard_widget_visibility(widget: Any, visible: Any, widget_id: Any = None) -> dict[str, Any]:
            if not isinstance(widget, str) or widget not in {"clock", "weather", "timer", "stopwatch", "music", "system_status"}:
                return {"accepted": False, "error": "unknown dashboard widget"}
            if not isinstance(visible, bool):
                return {"accepted": False, "error": "visible must be a boolean"}
            if widget_id is not None and (
                widget != "timer"
                or not isinstance(widget_id, str)
                or not widget_id.strip()
                or widget_id != widget_id.strip()
                or len(widget_id) > 128
            ):
                return {"accepted": False, "error": "widget_id is valid only for a timer"}
            previous_clock_visibility = dashboard_widget_state.get("clock_visible", False)
            previous_hidden_widgets = set(dashboard_widget_state.get("hidden_widgets", set()))
            if widget == "clock":
                dashboard_widget_state["clock_visible"] = visible
            else:
                hidden = dashboard_widget_state.setdefault("hidden_widgets", set())
                if widget == "timer" and visible and widget_id is None:
                    hidden.difference_update([item for item in hidden if item == "timer" or item.startswith("timer:")])
                elif widget == "timer" and visible:
                    hidden.discard("timer")
                    hidden.discard(f"timer:{widget_id}")
                elif widget == "timer" and widget_id is not None:
                    hidden.add(f"timer:{widget_id}")
                elif visible:
                    hidden.discard(widget)
                else:
                    hidden.add(widget)
            try:
                stored = memory_service.set_preference(
                    "dashboard_widget_visibility",
                    json.dumps({
                        "schema_version": 2,
                        "clock_visible": dashboard_widget_state["clock_visible"],
                        "hidden_widgets": sorted(dashboard_widget_state["hidden_widgets"]),
                    }, separators=(",", ":")),
                    provenance="dashboard_widget_control",
                ) if memory_service else False
            except Exception:
                logger.warning("Dashboard widget visibility preference could not be saved", exc_info=True)
                stored = False
            if not stored:
                dashboard_widget_state["clock_visible"] = previous_clock_visibility
                dashboard_widget_state["hidden_widgets"] = previous_hidden_widgets
                return {"accepted": False, "error": "Widget preference could not be saved."}
            return {"accepted": True, "widget": widget, "visible": visible, **({"widget_id": widget_id} if widget_id else {})}

        def _publish_dashboard_widgets(widgets: dict[str, Any] | None = None) -> None:
            nonlocal dashboard_media_signature
            if event_bus is None:
                return
            try:
                widgets = widgets if widgets is not None else _dashboard_widgets_snapshot()
                dashboard_media_signature = _dashboard_media_signature(widgets)
                _submit_event_threadsafe(
                    event_bus.emit(
                        "dashboard_widgets",
                        {
                            "widgets": widgets,
                            "channel": "web",
                            "session_id": current_session_id,
                        },
                        meta=EventMeta(
                            source=EventSource.BRAIN,
                            session_id=current_session_id,
                            rationale="dashboard visibility or timer, stopwatch, weather, and media state changed",
                        ),
                    ),
                    loop,
                )
            except RuntimeError:
                logger.debug("dashboard widgets update skipped during shutdown", exc_info=True)

        async def _media_widget_poll_loop() -> None:
            while True:
                await asyncio.sleep(4)
                gateway = web_gateway
                if gateway is None or gateway._client_count() == 0:
                    continue
                widgets = await asyncio.to_thread(_refresh_dashboard_widgets_snapshot)
                if _dashboard_media_signature(widgets) != dashboard_media_signature:
                    _publish_dashboard_widgets(widgets)

        def _web_snapshot() -> dict[str, Any]:
            from charlie.core import get_active_tool_approval
            tasks = [
                task
                for task in get_task_journal().snapshot()
                if task.get("session_id") == current_session_id
            ]
            try:
                dashboard_events = store.get_dashboard_events(
                    current_session_id,
                    limit=_DASHBOARD_RESEARCH_HISTORY_LIMIT + _DASHBOARD_ACTIVITY_HISTORY_LIMIT,
                ) if store else []
                if store:
                    get_history_with_turns = getattr(store, "get_session_messages_with_turn_ids", None)
                    conversation_rows = (
                        get_history_with_turns(current_session_id, limit=50)
                        if callable(get_history_with_turns)
                        else [(*row, None) for row in store.get_session_messages(current_session_id, limit=50)]
                    )
                else:
                    conversation_rows = []
            except Exception:
                logger.warning("dashboard session history could not be loaded", exc_info=True)
                dashboard_events = []
                conversation_rows = []
            research_history_by_session: dict[str, list[dict[str, Any]]] = {}
            for event in dashboard_events:
                payload = event.get("payload")
                if (
                    event.get("type") == "research_result"
                    and isinstance(payload, dict)
                    and payload.get("channel") != "telegram"
                ):
                    _append_dashboard_research_result(research_history_by_session, current_session_id, payload)
            for item in recent_research_results.get(current_session_id, []):
                if item.get("channel") != "telegram":
                    _append_dashboard_research_result(research_history_by_session, current_session_id, item)
            research_results = research_history_by_session.get(current_session_id, [])
            research_result = research_results[-1] if research_results else None
            research_texts = {
                text
                for item in research_results
                for text in (item.get("answer"), item.get("text"))
                if isinstance(text, str) and text
            }
            conversation_history = [
                {
                    "id": f"{current_session_id}:{index}",
                    "role": "you" if role == "user" else "charlie",
                    "text": content,
                    **({"turn_id": turn_id} if isinstance(turn_id, str) and turn_id else {}),
                }
                for index, (role, content, turn_id) in enumerate(conversation_rows)
                if role in {"user", "assistant"} and not (role == "assistant" and content in research_texts)
            ]
            activity_history = [
                _dashboard_activity_event(event)
                for event in dashboard_events
                if event.get("type") in _DASHBOARD_ACTIVITY_EVENT_TYPES
                and not (
                    isinstance(event.get("payload"), dict)
                    and event["payload"].get("channel") == "telegram"
                )
            ]
            return {
                "version": 1,
                "revision": int(time.time()),
                "session_id": current_session_id,
                "connection": "connected",
                "title": "Charlie is working" if active_turn_id else "Charlie is ready",
                "summary": active_operation_name
                or ("Working on your request." if active_turn_id else "Ready when you are."),
                "details": [
                    {
                        "label": str(task.get("title", "Task")),
                        "value": str(task.get("current_action") or task.get("status", "running")),
                    }
                    for task in tasks[:6]
                ],
                "conversation": {
                    "state": "working" if active_turn_id else "ready",
                    "label": "Working" if active_turn_id else "Ready when you are",
                    "caption": active_operation_name or "",
                },
                "conversation_history": conversation_history,
                "activity": activity_history,
                "tasks": tasks,
                "active_turn_id": active_turn_id,
                "active_task_id": active_task_id,
                "voice": {
                    "enabled": bool(config.voice_enabled),
                    **(voice.get_mic_state() if config.voice_enabled else {"mic_muted": True}),
                    **(voice.get_audio_state() if config.voice_enabled else {"muted": True, "volume": 0.0}),
                },
                "research_result": research_result,
                "research_results": research_results,
                "widgets": _dashboard_widgets_snapshot(),
                "settings": settings_service.snapshot(),
                "approval": dict(web_approval_payload) if get_active_tool_approval() == (
                    web_approval_payload.get("request_id"), "web",
                ) else None,
            }

        def _persist_dashboard_event(event: dict[str, Any]) -> None:
            record = _dashboard_history_record(event)
            if record is None or store is None:
                return
            try:
                store.append_dashboard_event(
                    record["session_id"],
                    record["event_id"],
                    record["event_type"],
                    record["payload"],
                    timestamp=record["timestamp"],
                )
            except Exception:
                logger.warning("dashboard session event could not be persisted", exc_info=True)

        def on_console_text(text: str):
            request = _allocate_turn_request(text, current_session_id, "console")
            _schedule_process(_dispatch_or_queue(request), loop)

        def _cleanup_intent_decision(processor):
            """Release interactive route metadata after every processing outcome.

            Also publishes the turn's channel for the duration of the turn. The
            self-extension approval seam is synchronous and carries no turn id, so
            this is the only place that can tell the bridge which channel is able
            to answer an approval raised from inside the turn.
            """

            async def wrapped(request: TurnRequest, process_brain, process_voice):
                approval_channels.begin(request.turn_id, request.channel)
                try:
                    return await processor(request, process_brain, process_voice)
                finally:
                    approval_channels.end(request.turn_id)
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
                elif platform == "web" and event_bus is not None:
                    await event_bus.emit(
                        "token", {"text": message, "session_id": session_id, "channel": "web"},
                        meta=EventMeta(source=EventSource.BRAIN, session_id=session_id, turn_id=request.turn_id),
                    )
                else:
                    _safe_speak(voice, message, last_emotion, "fast-reply", channel=platform)

            def record_primary_decision(
                *,
                intent: str,
                capabilities: tuple[str, ...] = (),
                routing_source: str = "control",
                confidence: Optional[float] = 1.0,
                rationale: str = "",
                execution_policy: ExecutionPolicy = ExecutionPolicy.CONVERSATION,
                external_action_required: bool = False,
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
                        execution_policy=execution_policy,
                        external_action_required=external_action_required,
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

            # Route an approval response to its waiting request instead of
            # starting an unrelated chat turn.
            from charlie.core import get_active_tool_approval

            pending_approval = get_active_tool_approval()
            # Telegram ingress handles explicit text responses before dispatch;
            # every other owner message remains a normal turn and is queued
            # behind a foreground approval by _should_queue_active_turn.
            if pending_approval and platform != "telegram":
                pending_approval_id, approval_channel = pending_approval
                record_primary_decision(
                    intent="control",
                    routing_source="control",
                    rationale="pending tool approval response handled by the control path",
                )
                answer = _parse_owner_text_approval(
                    text,
                    platform=platform,
                    approval_channel=approval_channel,
                )
                if answer is None:
                    if approval_channel == "voice":
                        guidance = "Please say yes or no."
                    elif approval_channel == "console":
                        guidance = "Please type yes or no."
                    else:
                        destination = "Charlie" if approval_channel == "web" else "Telegram"
                        guidance = f"Use the matching Approve or Decline buttons in {destination}."
                    await _deliver_immediate_reply(guidance)
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
                elif platform == "web" and not resolved:
                    await _deliver_immediate_reply("That approval expired.")
                elif platform == "telegram" and not resolved and telegram_bot:
                    try:
                        await telegram_bot.send_message(
                            config.telegram_user_id, "That approval has expired. Send the request again."
                        )
                    except Exception:
                        logger.warning("Failed to send expired-approval notice", exc_info=True)
                mark_response_complete()
                return

            if time.time() < speech_echo_cooldown:
                record_primary_decision(
                    intent="control",
                    routing_source="control",
                    rationale="speech echo cooldown suppressed the incoming utterance",
                )
                logger.info(f"Echo suppressed: {text}")
                mark_response_complete("suppressed_echo")
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

            if _is_research_telegram_handoff(text):
                record_primary_decision(
                    intent="action",
                    capabilities=("telegram",),
                    routing_source="deterministic",
                    rationale="explicit request to send this session's completed research to Telegram",
                    execution_policy=ExecutionPolicy.ACTION,
                    external_action_required=True,
                )
                outcome = await _send_recent_research_to_telegram(
                    recent_research_results,
                    session_id,
                    telegram_enabled=bool(config.telegram_enabled),
                    telegram_token_configured=bool(config.telegram_bot_token),
                    telegram_user_id=config.telegram_user_id,
                    telegram_bot=telegram_bot,
                )
                response = {
                    "sent": "I sent the completed research to your Telegram.",
                    "unavailable": "Telegram isn't configured, so I couldn't send the research.",
                    "missing": "I don't have a completed research result in this session to send.",
                    "failed": "I couldn't send the research to Telegram.",
                    "unconfirmed": "Telegram didn't confirm delivery, so I can't say the research was sent.",
                }[outcome]
                await _deliver_immediate_reply(response)
                mark_response_complete("completed" if outcome == "sent" else outcome)
                return

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
            turn_work_states[request.turn_id] = {"stage": "response_generation"}
            progress_task = None
            progress_owner = None
            if platform == "voice" and config.voice_enabled:
                progress_owner = voice.create_speech_owner(request.turn_id)
                progress_task = asyncio.create_task(
                    _speak_turn_work_progress(
                        brain, voice, request,
                        lambda: turn_work_states.get(request.turn_id, {}),
                        lambda: active_turn_id == request.turn_id and is_first_chunk,
                        speech_owner=progress_owner,
                    ),
                    name=f"spoken-progress-{request.turn_id}",
                )
            try:
                def speak_reply(part, label):
                    if request.turn_id not in turn_research_turns:
                        _safe_speak(voice, part, detected_emotion, label, channel=platform)

                async for chunk in brain.chat_stream(
                    text,
                    platform=platform,
                    session_id=session_id,
                    task_id=task_id,
                    turn_id=request.turn_id,
                    turn_request=request,
                    diagnostic_trace=trace,
                ):
                    if is_first_chunk and not chunk.strip():
                        continue
                    if is_first_chunk:
                        if progress_task is not None:
                            voice.cancel_speech_owner(progress_owner)
                            progress_task.cancel()
                            await asyncio.gather(progress_task, return_exceptions=True)
                        print("\r" + " " * 30 + "\r", end="", flush=True)
                        is_first_chunk = False
                    print(chunk, end="", flush=True)
                    sentence_buffer += chunk
                    full_reply_buffer += chunk
                    if event_bus:
                        _submit_event_task(
                            event_bus.emit(
                                "token",
                                {"text": chunk, "session_id": session_id, "channel": platform},
                                meta=EventMeta(
                                    source=EventSource.BRAIN,
                                    task_id=task_id,
                                    session_id=session_id,
                                    turn_id=request.turn_id,
                                ),
                            )
                        )

                    # Progressive flush: sentence boundary > clause boundary > force-flush.
                    flushed = False

                    # Early first-flush: wait for first sentence boundary, or force at 150 chars
                    if is_first_flush:
                        sentence_buffer, flushed = _flush_complete_sentences(
                            sentence_buffer,
                            lambda part: speak_reply(part, "first-flush"),
                        )
                        if flushed:
                            is_first_flush = False
                        elif len(sentence_buffer) >= 150:
                            idx = sentence_buffer.rfind(" ", 0, 150)
                            if idx > 0:
                                speak_reply(sentence_buffer[:idx], "first-force")
                                sentence_buffer = sentence_buffer[idx:].lstrip()
                            is_first_flush = False
                            flushed = True

                    if not flushed:
                        sentence_buffer, flushed = _flush_complete_sentences(
                            sentence_buffer,
                            lambda part: speak_reply(part, "sentence"),
                        )

                    if not flushed and len(sentence_buffer) >= _MAX_FLUSH_CHARS:
                        # Force-flush: prefer clause (comma/semicolon) boundary,
                        # fall back to word boundary to avoid mid-word splits.
                        clause_idx = _CLAUSE_BOUNDARY.search(sentence_buffer[:_MAX_FLUSH_CHARS])
                        if clause_idx:
                            flush_end = clause_idx.end()
                            speak_reply(sentence_buffer[:flush_end], "clause")
                            sentence_buffer = sentence_buffer[flush_end:].lstrip()
                        else:
                            word_idx = sentence_buffer.rfind(" ", 0, _MAX_FLUSH_CHARS)
                            if word_idx > 0:
                                speak_reply(sentence_buffer[:word_idx], "word")
                                sentence_buffer = sentence_buffer[word_idx:].lstrip()
                            elif sentence_buffer.strip():
                                speak_reply(sentence_buffer[:_MAX_FLUSH_CHARS], "force")
                                sentence_buffer = sentence_buffer[_MAX_FLUSH_CHARS:]

                # Final TTS for any text that did not reach a progressive boundary.
                if sentence_buffer.strip():
                    speak_reply(sentence_buffer, "final")
                if request.turn_id in turn_research_turns:
                    _safe_speak(
                        voice,
                        turn_spoken_research_summaries.get(request.turn_id, ""),
                        "neutral",
                        "research-summary",
                        channel=platform,
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
                            message_id = await telegram_bot.send_message(config.telegram_user_id, final_reply)
                            match = re.search(
                                r"Background task started \(id=([A-Za-z0-9_-]+),",
                                final_reply,
                            )
                            if message_id is not None and match is not None:
                                background_id = match.group(1)
                                telegram_background_start_messages.setdefault(background_id, set()).add(message_id)
                                if background_id in telegram_background_finished_tasks:
                                    telegram_background_finished_tasks.discard(background_id)
                                    await _delete_telegram_background_ack(background_id)
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
                if progress_task is not None:
                    voice.cancel_speech_owner(progress_owner)
                    progress_task.cancel()
                    await asyncio.gather(progress_task, return_exceptions=True)
                turn_work_states.pop(request.turn_id, None)
                turn_research_turns.discard(request.turn_id)
                turn_spoken_research_summaries.pop(request.turn_id, None)
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

        async def _publish_settings_reload_state() -> None:
            """Refresh projections affected by targeted settings reloads."""
            brain.rebuild_stable_tier()
            await _publish_subsystem_health(event_bus)
            from charlie.tools import registry as _reloaded_registry

            await _publish_tool_snapshot(event_bus, _reloaded_registry)
            await _publish_extension_snapshot(
                event_bus,
                extension_runtime_registry,
                rationale="settings reload refreshed extension runtime projection",
            )

        async def _apply_extension_runtime_request(payload: dict[str, Any]) -> dict[str, Any]:
            from charlie.tools import registry as _extension_registry

            result = await _handle_extension_operation_request(
                payload,
                brain=brain,
                tool_registry=_extension_registry,
                extension_registry=extension_runtime_registry,
                event_bus=event_bus,
                result_cache=extension_operation_results,
                in_flight=extension_operation_in_flight,
                fingerprint_cache=extension_operation_fingerprints,
                operation_lock=settings_operation_lock,
            )
            return result

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
                        {"session_id": current_session_id, "text": getattr(voice, "current_speech_text", "")},
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
                from charlie.core import get_active_tool_approval
                pending_approval = get_active_tool_approval()
                if pending_approval is not None and pending_approval[1] == "voice":
                    logger.info("speech_onset | foreground=awaiting_voice_approval | cancellation=deferred")
                    return
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
                if not _preempt_foreground_for_speech(brain, task):
                    logger.info("speech_onset | foreground=awaiting_approval | cancellation=deferred")
                    return
                logger.info(
                    "speech_onset_supersession_requested | old_turn_id=%s | sustained_tasks=untouched",
                    active_turn_id,
                )

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
                    system_status = {
                            "cpu": cpu_percent,
                            "ram": ram_percent,
                            "gpu": await asyncio.to_thread(_read_gpu_percent),
                            "net_kbps": max(0.0, net_kbps),
                            "uptime_seconds": time.time() - boot_time,
                            "battery_percent": battery_percent,
                            "disk_percent": disk_percent,
                            "sampled_at_ms": round(time.time() * 1000),
                        }
                    dashboard_widget_state["system_status"] = system_status
                    await bus.emit(
                        "system_status",
                        system_status,
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
                                voice.speak(_compact_catchup_speech(catchup_msg), "neutral")
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
            # Expose Brain so main-owned health publication can project the
            # degradations it records (unreadable context files, lost session
            # history, undeliverable LLM transitions). Assigned at module scope
            # deliberately: without it those losses are unobservable.
            global _main_brain
            _main_brain = brain
            # The web gateway is a presentation surface, not a runtime
            # dependency. An optional HTTP listener must never be able to end
            # the voice/Telegram runtime: Docker Desktop reserves port ranges
            # and commonly holds the configured web port, and an unhandled bind error here used to
            # propagate out of main() and kill the whole process. Degrade to
            # "gateway unavailable" and keep the assistant running.
            web_gateway = None
            try:
                web_gateway = RuntimeWebGateway(
                    loop=loop,
                    command_handler=_web_command,
                    snapshot_getter=_web_snapshot,
                    static_dir=Path(__file__).resolve().parent / "frontend" / "dist",
                    port=config.web_port,
                    require_auth=False,
                )
                web_gateway.start()
                if (
                    web_gateway.bootstrap_url
                    and os.getenv("CHARLIE_AUTO_OPEN_BROWSER", "true").lower() == "true"
                    and os.getenv("CHARLIE_TEST_MODE", "").lower() != "true"
                ):
                    try:
                        import webbrowser

                        webbrowser.open(web_gateway.bootstrap_url)
                    except Exception:
                        pass
            except OSError as exc:
                logger.warning(
                    "Charlie web gateway unavailable on port %s (%s); continuing without it. "
                    "Set CHARLIE_WEB_PORT to a free port to re-enable the web surface.",
                    os.getenv("CHARLIE_WEB_PORT", "8000"),
                    exc,
                )
                web_gateway = None
            if web_gateway is not None:
                # Subscribe only once the listener exists, so the bus never
                # holds a publish method belonging to a gateway that never
                # started. The gateway is a presentation surface and is not a
                # registered runtime subsystem, so it reports through the log
                # rather than the subsystem health registry.
                logger.info("Charlie web gateway listening on http://127.0.0.1:%s", web_gateway.port)
            bus.subscribe(_persist_dashboard_event)
            if web_gateway is not None:
                bus.subscribe(web_gateway.publish)
            await _publish_subsystem_health(bus)
            bus.set_state_listener(_on_event_for_state)
            voice.set_event_bus(bus)
            import charlie.recovery

            charlie.recovery._event_bus = bus
            brain.event_bus = bus
            # Publish an initial authoritative runtime snapshot after startup.
            await _publish_runtime_state(bus, settings_service, extension_runtime_registry)
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
                dashboard_widget_visibility_callback=_set_dashboard_widget_visibility,
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
            _publish_dashboard_widgets()
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

                    async def _deliver_voice(event: dict) -> bool:
                        return voice.speak(f"Reminder: {event['title']}", "neutral") is None

                    async def _send_calendar_telegram(event: dict) -> None:
                        await telegram_bot.send_message(
                            config.telegram_user_id, f"Reminder: {event['title']}"
                        )

                    async def _deliver_console_reminder(event: dict) -> None:
                        print(f"\nReminder: {event['title']}", flush=True)

                    async def _deliver_automation_alert(schedule: dict) -> None:
                        message = f"Reminder: {schedule['text']}"
                        await bus.emit(
                            "alert",
                            {"severity": "info", "message": message, "reminder_id": schedule["id"], "channel": schedule.get("delivery_channel")},
                            meta=EventMeta(source=EventSource.WATCHER, rationale="automation reminder became due"),
                        )

                    def _queue_automation_voice(schedule: dict) -> bool:
                        return voice.speak(f"Reminder: {schedule['text']}", "neutral") is None

                    async def _deliver_console_automation(schedule: dict) -> None:
                        print(f"\nReminder: {schedule['text']}", flush=True)

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
                            telegram_callback=(
                                _send_calendar_telegram if telegram_bot is not None else None
                            ),
                            console_callback=_deliver_console_reminder,
                        )
                        delivered_automation_reminders = await deliver_due_automation_reminders(
                            calendar_runtime,
                            utc_now_iso(),
                            alert_callback=_deliver_automation_alert,
                            voice_callback=(
                                _queue_automation_voice if getattr(voice, "is_ready", False) else None
                            ),
                            telegram_callback=(
                                _send_automation_telegram if telegram_bot is not None else None
                            ),
                            console_callback=_deliver_console_automation,
                        )
                        if delivered_automation_reminders:
                            _publish_dashboard_widgets()
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
                    await asyncio.sleep(1)

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
            if web_gateway is not None:
                steady_state_tasks.append(asyncio.create_task(_media_widget_poll_loop(), name="media_widget_poll"))
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
        if web_gateway is not None:
            try:
                web_gateway.close()
            except Exception:
                logger.warning("Web gateway shutdown error", exc_info=True)
            web_gateway = None
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
            from charlie.computer.backend import reset_backend

            stopped = await asyncio.wait_for(asyncio.to_thread(reset_backend), timeout=22.0)
            if not stopped:
                raise RuntimeError("Cua desktop runtime did not confirm shutdown")
            logger.info("Cua desktop backend shut down")
        except Exception:
            exit_code = 1
            logger.warning("Cua desktop backend shutdown incomplete; its worker may still be running", exc_info=True)

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
        try:
            from charlie.browser import controller as browser_controller
            stopped = await asyncio.wait_for(asyncio.to_thread(browser_controller.shutdown), timeout=12.0)
            if not stopped:
                raise RuntimeError("Playwright controller did not confirm shutdown")
        except Exception:
            exit_code = 1
            logger.warning("Browser controller shutdown incomplete; its worker may still be running", exc_info=True)

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

        # Released before logging.shutdown() so the line below still reaches the
        # file. Releasing last means no other instance can start while this one
        # is still tearing down.
        instance_lock.release()
        logger.info("Single-instance lock released: %s", instance_lock.path)

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
