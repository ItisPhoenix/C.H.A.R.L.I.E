"""Generic task scheduler: queue, priority, dependencies, bounded parallelism, cancel.

Domain-agnostic -- no charlie.core/Brain import -- so charlie/background_task.py
can wire its own planning/execution logic in via a run_fn closure per task. A
ManagedTask's status is the single source of truth the manager schedules
from; a run_fn is responsible for setting its own task's status to a
terminal value ("done"/"failed"/"cancelled") before returning, or the
manager defaults it to "done" on a clean return.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, Set

from charlie.task_journal import resolve_task_history_max_terminal

ManagedTaskStatus = Literal["queued", "running", "done", "failed", "cancelled"]
_TERMINAL_STATUSES = frozenset({"done", "failed", "cancelled"})

class TaskManagerAdmissionClosed(RuntimeError):
    """New work cannot be accepted after shutdown admission closes."""


logger = logging.getLogger("charlie.tasks")


@dataclass
class ManagedTask:
    id: str
    priority: int = 0
    depends_on: List[str] = field(default_factory=list)
    status: ManagedTaskStatus = "queued"
    cancel_requested: bool = False


class TaskManager:
    """Bounded-parallelism scheduler. Single asyncio event loop only, not thread-safe."""

    def __init__(
        self,
        max_parallel: int = 1,
        on_status_change: Optional[Callable[[Any], None]] = None,
        *,
        max_terminal_records: Optional[int] = None,
    ):
        self.max_parallel = max_parallel
        self._on_status_change = on_status_change
        self._max_terminal_records = resolve_task_history_max_terminal(max_terminal_records)
        self._tasks: Dict[str, ManagedTask] = {}
        self._run_fns: Dict[str, Callable[[], Awaitable[None]]] = {}
        self._running_ids: Set[str] = set()
        self._task_handles: Dict[str, asyncio.Task] = {}
        self._terminal_order: Dict[str, int] = {}
        self._terminal_sequence = 0
        self._accepting = True

    def _set_status(self, task: ManagedTask, status: ManagedTaskStatus) -> None:
        task.status = status
        if status in _TERMINAL_STATUSES:
            self._remember_terminal(task)
        else:
            self._terminal_order.pop(task.id, None)
        if self._on_status_change is not None:
            self._on_status_change(task)

    def _remember_terminal(self, task: ManagedTask) -> None:
        self._terminal_sequence += 1
        self._terminal_order[task.id] = self._terminal_sequence

    def _prune_terminal_history(self) -> None:
        terminal_tasks = [task for task in self._tasks.values() if task.status in _TERMINAL_STATUSES]
        overflow = len(terminal_tasks) - self._max_terminal_records
        if overflow <= 0:
            return
        pinned_dependencies = {
            dependency_id
            for task in self._tasks.values()
            if task.status not in _TERMINAL_STATUSES
            for dependency_id in task.depends_on
        }
        prunable = [task for task in terminal_tasks if task.id not in pinned_dependencies]
        oldest = sorted(
            prunable,
            key=lambda task: (self._terminal_order.get(task.id, -1), task.id),
        )[: min(overflow, len(prunable))]
        for task in oldest:
            self._tasks.pop(task.id, None)
            self._run_fns.pop(task.id, None)
            self._terminal_order.pop(task.id, None)

    def submit(self, task: ManagedTask, run_fn: Callable[[], Awaitable[None]]) -> None:
        if not self._accepting:
            raise TaskManagerAdmissionClosed("Task manager is shutting down")
        self._tasks[task.id] = task
        self._run_fns[task.id] = run_fn
        self._set_status(task, "queued")
        self._schedule()

    def get(self, task_id: str) -> Optional[ManagedTask]:
        return self._tasks.get(task_id)

    def list(self) -> List[ManagedTask]:
        return list(self._tasks.values())

    def active_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status in ("queued", "running"))

    def cancel(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task is None:
            return False
        if task.status == "queued":
            self._set_status(task, "cancelled")
            self._schedule()
        else:
            task.cancel_requested = True
        return True

    def _ready(self, task: ManagedTask) -> bool:
        return all(
            dep_id in self._tasks and self._tasks[dep_id].status == "done"
            for dep_id in task.depends_on
        )

    def _cancel_blocked_dependents(self) -> None:
        for task in self._tasks.values():
            if task.status != "queued":
                continue
            if any(
                dep_id not in self._tasks or self._tasks[dep_id].status in ("failed", "cancelled")
                for dep_id in task.depends_on
            ):
                self._set_status(task, "cancelled")

    def _schedule(self) -> None:
        try:
            self._cancel_blocked_dependents()
            candidates = sorted(
                (t for t in self._tasks.values() if t.status == "queued" and self._ready(t)),
                key=lambda t: (-t.priority, t.id),
            )
            for task in candidates:
                if len(self._running_ids) >= self.max_parallel:
                    break
                self._set_status(task, "running")
                self._running_ids.add(task.id)
                handle = asyncio.create_task(self._run(task), name=f"managed-task:{task.id}")
                self._task_handles[task.id] = handle
        finally:
            self._prune_terminal_history()

    @property
    def accepting(self) -> bool:
        return self._accepting

    def close_admission(self) -> None:
        """Atomically reject all submissions after shutdown starts."""
        self._accepting = False
        try:
            for task in self._tasks.values():
                if task.status == "queued":
                    self._set_status(task, "cancelled")
        finally:
            self._prune_terminal_history()

    async def _run(self, task: ManagedTask) -> None:
        try:
            await self._run_fns[task.id]()
        except asyncio.CancelledError:
            if task.status == "running":
                self._set_status(task, "cancelled")
            raise
        except Exception:
            logger.error("Task %s failed", task.id, exc_info=True)
            if task.status == "running":
                self._set_status(task, "failed")
        finally:
            self._running_ids.discard(task.id)
            self._task_handles.pop(task.id, None)
            if task.status == "running":
                self._set_status(task, "done")
            elif task.status in _TERMINAL_STATUSES and task.id not in self._terminal_order:
                # Legacy run functions may mutate status directly; seed ordering
                # only when no canonical _set_status transition recorded it.
                self._remember_terminal(task)
            self._schedule()

    async def shutdown(self, timeout: float = 5.0) -> None:
        """Cancel queued/running work and wait for task bodies to quiesce."""
        self.close_admission()
        for task in self._tasks.values():
            if task.status == "queued":
                self._set_status(task, "cancelled")
            elif task.status == "running":
                task.cancel_requested = True
        handles = list(self._task_handles.values())
        for handle in handles:
            if not handle.done():
                handle.cancel()
        if handles:
            try:
                await asyncio.wait_for(asyncio.gather(*handles, return_exceptions=True), timeout=timeout)
            except asyncio.TimeoutError:
                logger.error("Task manager shutdown timed out; tasks are not quiescent")
                raise
        self._running_ids.clear()
        self._prune_terminal_history()
