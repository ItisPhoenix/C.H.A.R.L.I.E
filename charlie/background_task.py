"""Background-task runner: plan -> gate-scan -> execute, scheduled through charlie.tasks.TaskManager.

Queue/priority/dependencies/bounded-parallelism all live in
charlie.tasks -- this module supplies the domain logic (planning via a Brain,
gated-step scanning, pause-on-user-activity) that TaskManager schedules as
one run_fn per BackgroundTask. Each task runs on its own Brain instance (see
charlie.core.Brain's register_panic_hotkey/approval_timeout params) so it
never touches the foreground chat's history, cancel generation, or panic
hotkey registration.

Pause-on-user-activity: charlie.desktop.actions.last_action_tick_ms() records
the automation's own last click/keypress; charlie.desktop.session.
external_input_since() compares a fresh GetLastInputInfo read against it to
tell real user input apart from pyautogui's own synthetic input, which also
bumps that timestamp. A fresh task (no recorded action yet, tick 0) falls
back to session.user_idle_seconds() for its first idle check.
"""

import asyncio
import dataclasses
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional

from charlie.attention import decide as _attention_decide
from charlie.config import Config
from charlie.core import Brain
from charlie.events import EventMeta, EventSource, EventType
from charlie.resource_locks import CapabilityLease, CapabilityLeaseManager
from charlie.results import ResultsStore
from charlie.task_journal import (
    TaskOrigin,
    TaskPriority,
    TaskRecord,
    TaskTransitionError,
    get_task_journal,
    normalize_task_status,
)
from charlie.task_journal import (
    TaskStatus as CanonicalTaskStatus,
)
from charlie.tasks import TaskManager
from charlie.tools import get_path_gate_reason, is_shell_command_gated
from charlie.utils import json_dumps, json_loads, make_id

try:
    from charlie.desktop import actions as desktop_actions
    from charlie.desktop import session as desktop_session
    _DESKTOP_AVAILABLE = True
except ImportError:  # pragma: no cover - guard mirrors charlie/desktop/__init__.py
    desktop_actions = None
    desktop_session = None
    _DESKTOP_AVAILABLE = False

_REAL_DESKTOP_SESSION = desktop_session


def _on_manual_takeover(owner_id: str, resources: tuple[str, ...]) -> None:
    task = _manager.get(owner_id) if "_manager" in globals() else None
    if task is not None:
        if task.status not in _TERMINAL_STATUSES:
            _request_task_cancellation(task)
        logger.info("Manual takeover requested cancellation of task %s for %s", owner_id, resources)


_capability_leases = CapabilityLeaseManager(on_takeover=_on_manual_takeover)

logger = logging.getLogger("charlie.background_task")

_POLL_INTERVAL_SEC = 2.0
_STEP_RE = re.compile(r"^\s*\d+[.)]\s+(.+)$")
# Mirrors charlie/recovery_cache.py's dotfile-in-cwd convention.
_STATE_FILE = ".charlie_background_task_state.json"
_JOURNAL_FILE = ".charlie_task_journal.json"
_TERMINAL_STATUSES = ("done", "failed", "cancelled")
_CANONICAL_TERMINAL_STATUSES = frozenset({
    CanonicalTaskStatus.COMPLETED,
    CanonicalTaskStatus.FAILED,
    CanonicalTaskStatus.CANCELLED,
})
_RESTART_ERROR = "Charlie restarted while this task was still running."
# Heuristic pre-scan over free-text plan steps, not a guarantee -- the real gate runs during execution.
_DESKTOP_KEYWORD_RE = re.compile(
    r"\b(click|type|open|close|desktop|screen|window)\b", re.IGNORECASE
)

TaskStatus = Literal[
    "planning", "queued", "awaiting_approval", "running", "paused", "done", "failed", "cancelled"
]
# Statuses counted as "a task is actively running" by charlie.state and charlie.context.
ACTIVE_STATUSES = frozenset({"planning", "queued", "running", "paused"})


@dataclass
class BackgroundTask:
    id: str
    text: str
    steps: List[str] = field(default_factory=list)
    current_step: int = 0
    status: TaskStatus = "planning"
    flagged_steps: List[int] = field(default_factory=list)
    error: Optional[str] = None
    brain: Optional[Brain] = None
    session_id: str = ""
    cancel_requested: bool = field(default=False, repr=False)
    priority: int = 0
    depends_on: List[str] = field(default_factory=list)
    # Read by charlie.surfaces._categorize to route "workspace" hints to a sustained-interaction surface.
    visibility_hint: str = ""
    desktop_lease: Optional[CapabilityLease] = field(default=None, repr=False, compare=False)

    def to_event(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "steps": self.steps,
            "current_step": self.current_step,
            "status": self.status,
            "flagged_steps": self.flagged_steps,
            "error": self.error,
        }

    def to_public_event(self, *, include_metadata: bool = False) -> Dict[str, Any]:
        """Return the client-safe task state without local exception detail."""
        public = {
            "id": self.id,
            "title": self.text,
            "status": normalize_task_status(self.status).value,
            "current_step": self.current_step,
            "total_steps": len(self.steps),
        }
        if include_metadata:
            public.update({
                "origin": TaskOrigin.BACKGROUND.value,
                "priority": _priority_name(self.priority).value,
                "session_id": self.session_id or None,
                "progress": (self.current_step / len(self.steps)) if self.steps else None,
                "current_action": self.steps[self.current_step] if self.current_step < len(self.steps) else None,
                "capability_requirements": ["desktop"],
            })
        return public

    def to_state_dict(self) -> Dict[str, Any]:
        """to_event() plus session_id, for on-disk persistence."""
        return {**self.to_event(), "session_id": self.session_id}


_current_task: Optional[BackgroundTask] = None
_active_event_bus: Optional[Any] = None
_journal = get_task_journal()


def _priority_name(priority: int) -> TaskPriority:
    if priority > 0:
        return TaskPriority.HIGH
    if priority < 0:
        return TaskPriority.LOW
    return TaskPriority.NORMAL


def _legacy_status_for(status: CanonicalTaskStatus) -> str:
    """Map canonical lifecycle values back to the scheduler's legacy vocabulary."""
    if status is CanonicalTaskStatus.COMPLETED:
        return "done"
    if status is CanonicalTaskStatus.APPROVAL_REQUIRED:
        return "awaiting_approval"
    return status.value


def _record_task_lifecycle(
    task: BackgroundTask,
    *,
    status: str | CanonicalTaskStatus | None = None,
) -> TaskRecord:
    """Commit one background-task lifecycle snapshot to the canonical journal.

    This is the only live background-task lifecycle adapter.  Callers provide
    the status that the scheduler/domain path just selected; the journal
    commits it first, then the legacy task is mirrored from the resulting
    canonical record.  Event emission happens separately from the returned
    immutable snapshot.
    """
    requested_status = normalize_task_status(task.status if status is None else status)
    try:
        current = _journal.get(task.id)
    except KeyError:
        current = _journal.create_task(
            task.text,
            task_id=task.id,
            origin=TaskOrigin.BACKGROUND,
            priority=_priority_name(task.priority),
            status=requested_status,
            session_id=task.session_id or None,
            capability_requirements=("desktop",),
            current_step=task.current_step,
            total_steps=len(task.steps),
        )
    else:
        if current.status is not requested_status:
            try:
                if requested_status is CanonicalTaskStatus.COMPLETED and current.status not in (
                    CanonicalTaskStatus.VERIFYING,
                    CanonicalTaskStatus.COMPLETED,
                ):
                    current = _journal.transition(task.id, CanonicalTaskStatus.VERIFYING)
                current = _journal.transition(task.id, requested_status)
            except TaskTransitionError:
                # Restore the compatibility mirror to canonical truth before
                # propagating/rejecting the invalid legacy mutation.
                try:
                    task.status = _legacy_status_for(_journal.get(task.id).status)
                except KeyError:  # pragma: no cover - journal cannot disappear in-process
                    pass
                logger.error(
                    "Rejected background task transition %s -> %s for %s",
                    current.status,
                    requested_status,
                    task.id,
                )
                raise
        elif current.status in (
            CanonicalTaskStatus.COMPLETED,
            CanonicalTaskStatus.FAILED,
            CanonicalTaskStatus.CANCELLED,
        ):
            # Terminal records are immutable against later stale legacy payloads.
            task.status = _legacy_status_for(current.status)
            return current

    progress = (task.current_step / len(task.steps)) if task.steps else None
    current_action = task.steps[task.current_step] if task.current_step < len(task.steps) else None
    current = _journal.update_progress(
        task.id,
        progress=progress,
        current_action=current_action,
        current_step=task.current_step,
        total_steps=len(task.steps),
        waiting_reason="user_input" if requested_status is CanonicalTaskStatus.PAUSED else None,
    )
    task.status = _legacy_status_for(current.status)
    return current


def _request_task_cancellation(task: BackgroundTask) -> None:
    """Record cancellation intent canonically before mirroring the legacy flag."""
    try:
        _journal.get(task.id)
    except KeyError:
        _record_task_lifecycle(task, status=task.status)
    _journal.request_cancel(task.id)
    task.cancel_requested = True


def _public_event_from_record(record: TaskRecord) -> Dict[str, Any]:
    """Project a canonical snapshot into the existing client-safe event shape."""
    current_action = record.current_action if record.current_step < record.total_steps else None
    return {
        "id": record.id,
        "title": record.title,
        "status": record.status.value,
        "current_step": record.current_step,
        "total_steps": record.total_steps,
        "origin": record.origin.value,
        "priority": record.priority.value,
        "session_id": record.session_id,
        "progress": record.progress,
        "current_action": current_action,
        "capability_requirements": list(record.capability_requirements),
    }


def _on_manager_status_change(task: "BackgroundTask") -> None:
    """Commit manager status synchronously, then emit its captured snapshot."""
    captured_status = task.status
    try:
        record = _record_task_lifecycle(task, status=captured_status)
    except TaskTransitionError:
        # The adapter already restored the compatibility mirror and logged the
        # rejected transition. Never emit mutable legacy state as canonical.
        return
    if _active_event_bus is not None:
        asyncio.create_task(_emit_task_event(_active_event_bus, record, task=task))


_manager = TaskManager(max_parallel=1, on_status_change=_on_manager_status_change)


def get_current_task() -> Optional[BackgroundTask]:
    """Most recently created task -- may be queued, running, or already terminal."""
    return _current_task


def count_active_tasks() -> int:
    return _manager.active_count()


def list_tasks() -> List[BackgroundTask]:
    return _manager.list()


def _save_state(task: BackgroundTask) -> None:
    try:
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            f.write(json_dumps(task.to_state_dict()))
    except Exception:
        logger.warning("Failed to persist background-task state", exc_info=True)


async def _emit_task_event(
    event_bus,
    record: TaskRecord,
    *,
    task: Optional[BackgroundTask] = None,
) -> None:
    """Emit a captured canonical task snapshot and preserve legacy state on disk."""
    await event_bus.emit(
        "background_task", _public_event_from_record(record),
        meta=EventMeta(source=EventSource.TASK, task_id=record.id),
    )
    if task is not None:
        _save_state(task)


def _write_legacy_state(state: Dict[str, Any]) -> None:
    try:
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            f.write(json_dumps(state))
    except Exception:
        logger.warning("Failed to rewrite background-task state file", exc_info=True)


def _reconcile_persisted_background_tasks() -> tuple[set[str], set[str]]:
    """Fail every non-terminal background record left by the prior process."""
    reconciled_ids: set[str] = set()
    failed_transition_ids: set[str] = set()
    for record in _journal.list():
        if record.origin is not TaskOrigin.BACKGROUND or record.status in _CANONICAL_TERMINAL_STATUSES:
            continue
        try:
            _journal.transition(
                record.id,
                CanonicalTaskStatus.FAILED,
                error_summary=_RESTART_ERROR,
            )
            reconciled_ids.add(record.id)
        except TaskTransitionError:
            try:
                current = _journal.get(record.id)
            except KeyError:
                current = None
            if current is not None and current.status in _CANONICAL_TERMINAL_STATUSES:
                logger.info(
                    "Persisted background task %s became terminal during restart reconciliation",
                    record.id,
                )
                continue
            failed_transition_ids.add(record.id)
            logger.error(
                "Failed to reconcile persisted background task %s from %s to failed",
                record.id,
                record.status.value,
                exc_info=True,
            )
    return reconciled_ids, failed_transition_ids


def _legacy_task_id(state: Dict[str, Any]) -> Optional[str]:
    task_id = state.get("id")
    if task_id is None:
        return None
    task_id = str(task_id)
    return task_id or None


def _legacy_title(state: Dict[str, Any]) -> str:
    title = state.get("text")
    if not isinstance(title, str):
        title = state.get("title", "")
    return title if isinstance(title, str) else str(title)


def _legacy_steps(state: Dict[str, Any]) -> List[str]:
    steps = state.get("steps", [])
    if not isinstance(steps, (list, tuple)):
        return []
    return [step if isinstance(step, str) else str(step) for step in steps]


def _legacy_current_step(state: Dict[str, Any]) -> int:
    try:
        return max(0, int(state.get("current_step", 0)))
    except (TypeError, ValueError):
        return 0


def _legacy_initial_status(state: Dict[str, Any]) -> CanonicalTaskStatus:
    raw_status = state.get("status")
    try:
        return normalize_task_status(raw_status) if isinstance(raw_status, str) else CanonicalTaskStatus.QUEUED
    except ValueError:
        logger.warning("Unknown legacy background-task status %r; reconstructing as queued", raw_status)
        return CanonicalTaskStatus.QUEUED


def _legacy_state_is_terminal(state: Dict[str, Any]) -> bool:
    if state.get("status") in _TERMINAL_STATUSES:
        return True
    try:
        return _legacy_initial_status(state) in _CANONICAL_TERMINAL_STATUSES
    except (TypeError, ValueError):
        return False


def _mirror_legacy_terminal_state(state: Dict[str, Any], record: TaskRecord) -> None:
    state["status"] = _legacy_status_for(record.status)
    if record.error_summary:
        state["error"] = record.error_summary
    _write_legacy_state(state)


def _reconstruct_legacy_task(state: Dict[str, Any]) -> TaskRecord:
    """Create the smallest canonical background record for legacy-only state."""
    task_id = _legacy_task_id(state) or make_id()

    steps = _legacy_steps(state)
    current_step = _legacy_current_step(state)
    session_id = state.get("session_id")
    if not isinstance(session_id, str):
        session_id = None
    _journal.create_task(
        _legacy_title(state),
        task_id=task_id,
        origin=TaskOrigin.BACKGROUND,
        status=_legacy_initial_status(state),
        session_id=session_id,
        current_step=current_step,
        total_steps=len(steps),
    )
    current_action = steps[current_step] if current_step < len(steps) else None
    progress = (current_step / len(steps)) if steps else None
    _journal.update_progress(
        task_id,
        progress=progress,
        current_action=current_action,
        current_step=current_step,
        total_steps=len(steps),
    )
    return _journal.transition(task_id, CanonicalTaskStatus.FAILED, error_summary=_RESTART_ERROR)


def check_interrupted_task() -> Optional[Dict[str, Any]]:
    """Reconcile canonical background history, then preserve the legacy warning contract."""
    reconciled_ids, failed_transition_ids = _reconcile_persisted_background_tasks()
    if not os.path.exists(_STATE_FILE):
        return None
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            state = json_loads(f.read())
    except Exception:
        logger.warning("Failed to read background-task state file", exc_info=True)
        return None
    if not isinstance(state, dict) or _legacy_state_is_terminal(state):
        return None

    task_id = _legacy_task_id(state)
    canonical = None
    if task_id is not None:
        try:
            canonical = _journal.get(task_id)
        except KeyError:
            pass

    if canonical is not None:
        if canonical.status in _CANONICAL_TERMINAL_STATUSES:
            if task_id in reconciled_ids:
                state["status"] = "failed"
                state["error"] = _RESTART_ERROR
                _write_legacy_state(state)
                return state
            _mirror_legacy_terminal_state(state, canonical)
            return None
        if canonical.origin is not TaskOrigin.BACKGROUND:
            logger.error(
                "Legacy background task %s conflicts with non-background canonical origin %s",
                task_id,
                canonical.origin.value,
            )
            return None
        if task_id in failed_transition_ids:
            return None
        logger.error(
            "Persisted background task %s remained non-terminal after restart reconciliation",
            task_id,
        )
        return None

    try:
        reconstructed = _reconstruct_legacy_task(state)
    except TaskTransitionError:
        logger.error("Failed to reconcile reconstructed legacy background task %s", task_id, exc_info=True)
        return None
    except (KeyError, ValueError, TypeError):
        logger.error("Failed to reconstruct legacy background task %s", task_id, exc_info=True)
        return None

    if task_id is None:
        state["id"] = reconstructed.id
    state["status"] = "failed"
    state["error"] = _RESTART_ERROR
    _write_legacy_state(state)
    return state


def _parse_steps(plan_text: str) -> List[str]:
    steps = []
    for line in plan_text.splitlines():
        m = _STEP_RE.match(line)
        if m:
            steps.append(m.group(1).strip())
    return steps


def _scan_gated_steps(steps: List[str]) -> List[int]:
    flagged = []
    for i, step in enumerate(steps):
        if is_shell_command_gated(step) or get_path_gate_reason(step) or _DESKTOP_KEYWORD_RE.search(step):
            flagged.append(i)
    return flagged


async def _store_result(task: BackgroundTask, event_bus, full_result: str) -> None:
    """Persist one row per terminal task (charlie/results.py) -- attention_level
    reuses charlie.attention's own BACKGROUND_TASK status table, same source of truth
    the live event stream already scores this status against. Emits RESULT_STORED so
    the Surface Engine can react (e.g. route an ARCHIVED-persistence surface)."""
    level, _ = _attention_decide({"type": EventType.BACKGROUND_TASK, "payload": {"status": task.status}})
    summary = f"Background task '{task.text}' {task.status}."
    store = ResultsStore(db_path=task.brain.config.session_db_path)
    try:
        store.store(task.id, summary, full_result, int(level))
    except Exception:
        logger.warning("Failed to persist background-task result", exc_info=True)
    finally:
        store.close()
    try:
        await event_bus.emit(
            EventType.RESULT_STORED, {"task_id": task.id, "summary": summary, "attention_level": int(level)},
            meta=EventMeta(source=EventSource.TASK, task_id=task.id),
        )
    except Exception:
        logger.warning("Failed to emit result_stored event", exc_info=True)
    if task.brain.on_result_stored:
        try:
            task.brain.on_result_stored(task.id, summary, int(level))
        except Exception:
            logger.warning("on_result_stored callback failed", exc_info=True)


async def _announce(event_bus, voice, severity: str, message: str) -> None:
    """Mirror main.py's resource-alert pattern: an "alert" event plus spoken TTS."""
    try:
        await event_bus.emit(
            "alert", {"severity": severity, "message": message},
            meta=EventMeta(source=EventSource.TASK),
        )
    except Exception:
        logger.warning("Failed to emit background-task alert event", exc_info=True)
    if voice is not None:
        try:
            voice.speak(message, "neutral")
        except Exception:
            logger.warning("Failed to speak background-task alert", exc_info=True)


async def start(
    config: Config, event_bus, text: str, session_store=None, memory_store=None, voice=None,
    priority: int = 0, depends_on: Optional[List[str]] = None, visibility_hint: str = "",
    on_result_stored: Optional[Callable] = None,
) -> BackgroundTask:
    """Plan a background task and hand it to the TaskManager queue -- no
    upfront approval gate. Runs immediately if a slot is free (the common
    case at the default max_parallel=1), otherwise queues behind whatever is
    already running/queued, per priority then submission order. Returns once
    the plan is generated and the task is submitted to the queue; does not
    await task completion. _run_loop reports progress asynchronously via
    "background_task" events."""
    global _current_task, _active_event_bus
    _active_event_bus = event_bus
    _manager.max_parallel = config.background_max_parallel_tasks

    task = BackgroundTask(
        id=make_id(8), text=text, session_id=f"bg:{make_id(6)}",
        priority=priority, depends_on=list(depends_on or []), visibility_hint=visibility_hint,
    )
    _current_task = task

    bg_config = dataclasses.replace(
        config,
        iteration_budget_max=config.background_iteration_budget_max,
        desktop_max_actions=config.background_max_actions,
    )
    task.brain = Brain(
        bg_config,
        session_store=session_store,
        memory_store=memory_store,
        register_panic_hotkey=False,
        approval_timeout=None,
        is_background=True,
        on_result_stored=on_result_stored,
    )

    record = _record_task_lifecycle(task, status=CanonicalTaskStatus.PLANNING)
    await _emit_task_event(event_bus, record, task=task)

    plan_prompt = (
        "Break the following task into a short numbered list of concrete steps. "
        "Reply with ONLY the numbered list, one step per line, no preamble.\n\n"
        f"Task: {text}"
    )
    plan_text = ""
    async for chunk in task.brain.chat_stream(plan_prompt, session_id=task.session_id, skip_tools=True):
        plan_text += chunk
    task.steps = _parse_steps(plan_text) or [text]
    task.flagged_steps = _scan_gated_steps(task.steps)

    _manager.submit(task, lambda: _run_loop(task, event_bus, voice))
    await _announce(event_bus, voice, "info", f"Starting background task: {text}")
    return task


def cancel(task_id: str) -> bool:
    task = _manager.get(task_id)
    if task is not None and task.status not in _TERMINAL_STATUSES:
        _request_task_cancellation(task)
    cancelled = _manager.cancel(task_id)
    task = _manager.get(task_id)
    if cancelled and task is not None and task.status == "running" and task.brain is not None:
        task.brain.cancel_chat()
    return cancelled


async def _wait_until_clear(task: BackgroundTask, config: Config, event_bus) -> bool:
    """Block until no real external input has landed since the task's own
    last action (or, before any action, until the user has been idle for
    config.desktop_idle_threshold_s). Returns False if cancelled or
    panic-halted while waiting."""
    if not _DESKTOP_AVAILABLE:
        return not task.cancel_requested

    paused = False
    while True:
        if task.cancel_requested or desktop_actions.is_halted():
            return False
        tick = desktop_actions.last_action_tick_ms()
        clear = (
            desktop_session.user_idle_seconds() >= config.desktop_idle_threshold_s
            if tick == 0
            else not desktop_session.external_input_since(tick)
        )
        if clear:
            if paused:
                record = _record_task_lifecycle(task, status=CanonicalTaskStatus.RUNNING)
                await _emit_task_event(event_bus, record, task=task)
            return True
        if not paused:
            paused = True
            record = _record_task_lifecycle(task, status=CanonicalTaskStatus.PAUSED)
            await _emit_task_event(event_bus, record, task=task)
        await asyncio.sleep(_POLL_INTERVAL_SEC)


async def _wait_for_desktop(task: BackgroundTask) -> bool:
    """Acquire the canonical desktop lease, retaining the old fake-session seam for tests."""
    if not _DESKTOP_AVAILABLE:
        return True
    if desktop_session is not _REAL_DESKTOP_SESSION:
        while not desktop_session.acquire_desktop(task.id):
            if task.cancel_requested:
                return False
            await asyncio.sleep(_POLL_INTERVAL_SEC)
        return True
    while not task.cancel_requested:
        try:
            task.desktop_lease = await _capability_leases.acquire(
                "desktop", task.id, timeout=_POLL_INTERVAL_SEC
            )
            return True
        except asyncio.TimeoutError:
            continue
    return False


async def _run_loop(task: BackgroundTask, event_bus, voice=None) -> None:
    config = task.brain.config
    step_outputs: List[str] = []
    try:
        while task.current_step < len(task.steps):
            if task.current_step in task.flagged_steps:
                await _announce(
                    event_bus, voice, "warning",
                    f"Background task flagged step {task.current_step + 1}: {task.steps[task.current_step]}",
                )

            if not await _wait_until_clear(task, config, event_bus):
                status = CanonicalTaskStatus.CANCELLED if task.cancel_requested else CanonicalTaskStatus.FAILED
                if status is CanonicalTaskStatus.FAILED:
                    task.error = "Desktop control halted (panic hotkey)."
                    await _announce(event_bus, voice, "error", f"Background task failed: {task.error}")
                record = _record_task_lifecycle(task, status=status)
                await _emit_task_event(event_bus, record, task=task)
                await _store_result(task, event_bus, "\n".join(step_outputs) or task.error or "")
                return

            if not await _wait_for_desktop(task):
                record = _record_task_lifecycle(task, status=CanonicalTaskStatus.CANCELLED)
                await _emit_task_event(event_bus, record, task=task)
                await _store_result(task, event_bus, "\n".join(step_outputs))
                return

            try:
                step_text = task.steps[task.current_step]
                step_output = ""
                async for chunk in task.brain.chat_stream(step_text, session_id=task.session_id):
                    step_output += chunk
                step_outputs.append(step_output)
            finally:
                if task.desktop_lease is not None:
                    await task.desktop_lease.release()
                    task.desktop_lease = None
                elif _DESKTOP_AVAILABLE and desktop_session is not _REAL_DESKTOP_SESSION:
                    desktop_session.release_desktop(task.id)

            task.current_step += 1
            record = _record_task_lifecycle(task)
            await _emit_task_event(event_bus, record, task=task)

        record = _record_task_lifecycle(task, status=CanonicalTaskStatus.COMPLETED)
        await _emit_task_event(event_bus, record, task=task)
        await _announce(event_bus, voice, "success", f"Background task complete: {task.text}")
        await _store_result(task, event_bus, "\n".join(step_outputs))
    except Exception as e:
        logger.error("Background task %s failed at step %d: %s", task.id, task.current_step, e, exc_info=True)
        task.error = str(e)
        record = _record_task_lifecycle(task, status=CanonicalTaskStatus.FAILED)
        await _emit_task_event(event_bus, record, task=task)
        await _announce(event_bus, voice, "error", "Background task failed. Check task details.")
        await _store_result(task, event_bus, "\n".join(step_outputs) or task.error or "")
    finally:
        await task.brain.close()
