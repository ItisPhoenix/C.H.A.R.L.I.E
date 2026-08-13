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

ManagedTaskStatus = Literal["queued", "running", "done", "failed", "cancelled"]

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

    def __init__(self, max_parallel: int = 1, on_status_change: Optional[Callable[[Any], None]] = None):
        self.max_parallel = max_parallel
        self._on_status_change = on_status_change
        self._tasks: Dict[str, ManagedTask] = {}
        self._run_fns: Dict[str, Callable[[], Awaitable[None]]] = {}
        self._running_ids: Set[str] = set()

    def _set_status(self, task: ManagedTask, status: ManagedTaskStatus) -> None:
        task.status = status
        if self._on_status_change is not None:
            self._on_status_change(task)

    def submit(self, task: ManagedTask, run_fn: Callable[[], Awaitable[None]]) -> None:
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
            asyncio.create_task(self._run(task))

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
            if task.status == "running":
                self._set_status(task, "done")
            self._schedule()
