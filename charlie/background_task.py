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
from charlie.results import ResultsStore
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

logger = logging.getLogger("charlie.background_task")

_POLL_INTERVAL_SEC = 2.0
_STEP_RE = re.compile(r"^\s*\d+[.)]\s+(.+)$")
# Mirrors charlie/recovery_cache.py's dotfile-in-cwd convention.
_STATE_FILE = ".charlie_background_task_state.json"
_TERMINAL_STATUSES = ("done", "failed", "cancelled")
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

    def to_public_event(self) -> Dict[str, Any]:
        """Return the client-safe task state without local exception detail."""
        return {
            "id": self.id,
            "title": self.text,
            "status": self.status,
            "current_step": self.current_step,
            "total_steps": len(self.steps),
        }

    def to_state_dict(self) -> Dict[str, Any]:
        """to_event() plus session_id, for on-disk persistence."""
        return {**self.to_event(), "session_id": self.session_id}


_current_task: Optional[BackgroundTask] = None
_active_event_bus: Optional[Any] = None


def _on_manager_status_change(task: "BackgroundTask") -> None:
    """TaskManager-driven transitions (queued/running/cancelled) have no emit of their own -- fire one here."""
    if _active_event_bus is not None:
        asyncio.create_task(_emit_task_event(_active_event_bus, task))


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


async def _emit_task_event(event_bus, task: BackgroundTask) -> None:
    """WS event plus on-disk persist -- single choke point, see check_interrupted_task()."""
    await event_bus.emit(
        "background_task", task.to_public_event(),
        meta=EventMeta(source=EventSource.TASK, task_id=task.id),
    )
    _save_state(task)


def check_interrupted_task() -> Optional[Dict[str, Any]]:
    """Call at startup: report+clear a non-terminal state left by a process restart mid-task."""
    if not os.path.exists(_STATE_FILE):
        return None
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            state = json_loads(f.read())
    except Exception:
        logger.warning("Failed to read background-task state file", exc_info=True)
        return None
    if not isinstance(state, dict) or state.get("status") in _TERMINAL_STATUSES:
        return None

    state["status"] = "failed"
    state["error"] = "Charlie restarted while this task was still running."
    try:
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            f.write(json_dumps(state))
    except Exception:
        logger.warning("Failed to rewrite background-task state file", exc_info=True)
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

    await _emit_task_event(event_bus, task)

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
                task.status = "running"
                await _emit_task_event(event_bus, task)
            return True
        if not paused:
            paused = True
            task.status = "paused"
            await _emit_task_event(event_bus, task)
        await asyncio.sleep(_POLL_INTERVAL_SEC)


async def _wait_for_desktop(task: BackgroundTask) -> bool:
    """Poll until this task owns the desktop capability, keyed by task.id so two concurrent
    background tasks genuinely serialize instead of racing under one shared owner id."""
    if not _DESKTOP_AVAILABLE:
        return True
    while not desktop_session.acquire_desktop(task.id):
        if task.cancel_requested:
            return False
        await asyncio.sleep(_POLL_INTERVAL_SEC)
    return True


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
                task.status = "cancelled" if task.cancel_requested else "failed"
                if task.status == "failed":
                    task.error = "Desktop control halted (panic hotkey)."
                    await _announce(event_bus, voice, "error", f"Background task failed: {task.error}")
                await _emit_task_event(event_bus, task)
                await _store_result(task, event_bus, "\n".join(step_outputs) or task.error or "")
                return

            if not await _wait_for_desktop(task):
                task.status = "cancelled"
                await _emit_task_event(event_bus, task)
                await _store_result(task, event_bus, "\n".join(step_outputs))
                return

            try:
                step_text = task.steps[task.current_step]
                step_output = ""
                async for chunk in task.brain.chat_stream(step_text, session_id=task.session_id):
                    step_output += chunk
                step_outputs.append(step_output)
            finally:
                if _DESKTOP_AVAILABLE:
                    desktop_session.release_desktop(task.id)

            task.current_step += 1
            await _emit_task_event(event_bus, task)

        task.status = "done"
        await _emit_task_event(event_bus, task)
        await _announce(event_bus, voice, "success", f"Background task complete: {task.text}")
        await _store_result(task, event_bus, "\n".join(step_outputs))
    except Exception as e:
        logger.error("Background task %s failed at step %d: %s", task.id, task.current_step, e, exc_info=True)
        task.status = "failed"
        task.error = str(e)
        await _emit_task_event(event_bus, task)
        await _announce(event_bus, voice, "error", "Background task failed. Check task details.")
        await _store_result(task, event_bus, "\n".join(step_outputs) or task.error or "")
    finally:
        await task.brain.close()
