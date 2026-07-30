"""Lean background-task runner: plan -> gate-scan -> approve once -> execute.

Single active task at a time, not a queue/kanban board -- matches the "lean
rebuild" scope in plans/here-is-a-draft-witty-frost.md. Runs on its own
Brain instance (see charlie.core.Brain's register_panic_hotkey/
approval_timeout params) so it never touches the foreground chat's history,
cancel generation, or panic hotkey registration.

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
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from charlie.config import Config
from charlie.core import Brain
from charlie.tools import get_path_gate_reason, is_shell_command_gated
from charlie.utils import make_id

try:
    from charlie.desktop import actions as desktop_actions
    from charlie.desktop import session as desktop_session
    _DESKTOP_AVAILABLE = True
except ImportError:  # pragma: no cover - guard mirrors charlie/desktop/__init__.py
    desktop_actions = None
    desktop_session = None
    _DESKTOP_AVAILABLE = False

logger = logging.getLogger("charlie.background_task")

_OWNER_ID = "background_task"
_POLL_INTERVAL_SEC = 2.0
_STEP_RE = re.compile(r"^\s*\d+[.)]\s+(.+)$")
# Heuristic pre-scan over free-text plan steps, not a guarantee -- the real gate runs during execution.
_DESKTOP_KEYWORD_RE = re.compile(
    r"\b(click|type|open|close|desktop|screen|window)\b", re.IGNORECASE
)

TaskStatus = Literal[
    "planning", "awaiting_approval", "running", "paused", "done", "failed", "cancelled"
]


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


_current_task: Optional[BackgroundTask] = None


def get_current_task() -> Optional[BackgroundTask]:
    return _current_task


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


async def start(config: Config, event_bus, text: str, session_store=None, memory_store=None) -> BackgroundTask:
    """Plan a background task and start running it immediately -- no upfront
    approval gate. Returns once the plan is generated and the run loop is
    scheduled; does not await task completion. _run_loop reports progress
    asynchronously via "background_task" events."""
    global _current_task
    if _current_task is not None and _current_task.status not in ("done", "failed", "cancelled"):
        raise RuntimeError(f"A background task is already {_current_task.status}: {_current_task.text}")

    task = BackgroundTask(id=make_id(8), text=text, session_id=f"bg:{make_id(6)}")
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
    )

    await event_bus.emit("background_task", task.to_event())

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

    task.status = "running"
    await event_bus.emit("background_task", task.to_event())
    asyncio.create_task(_run_loop(task, event_bus))
    return task


def cancel(task_id: str) -> bool:
    if _current_task is None or _current_task.id != task_id:
        return False
    _current_task.cancel_requested = True
    return True


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
                await event_bus.emit("background_task", task.to_event())
            return True
        if not paused:
            paused = True
            task.status = "paused"
            await event_bus.emit("background_task", task.to_event())
        await asyncio.sleep(_POLL_INTERVAL_SEC)


async def _run_loop(task: BackgroundTask, event_bus) -> None:
    config = task.brain.config
    try:
        while task.current_step < len(task.steps):
            if not await _wait_until_clear(task, config, event_bus):
                task.status = "cancelled" if task.cancel_requested else "failed"
                if task.status == "failed":
                    task.error = "Desktop control halted (panic hotkey)."
                await event_bus.emit("background_task", task.to_event())
                return

            if _DESKTOP_AVAILABLE:
                desktop_session.acquire_desktop(_OWNER_ID)
            try:
                step_text = task.steps[task.current_step]
                async for _ in task.brain.chat_stream(step_text, session_id=task.session_id):
                    pass
            finally:
                if _DESKTOP_AVAILABLE:
                    desktop_session.release_desktop(_OWNER_ID)

            task.current_step += 1
            await event_bus.emit("background_task", task.to_event())

        task.status = "done"
        await event_bus.emit("background_task", task.to_event())
    except Exception as e:
        logger.error("Background task %s failed at step %d: %s", task.id, task.current_step, e, exc_info=True)
        task.status = "failed"
        task.error = str(e)
        await event_bus.emit("background_task", task.to_event())
    finally:
        await task.brain.close()
