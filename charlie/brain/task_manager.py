"""Async Task Manager — true concurrent task execution."""

import asyncio
import time

from charlie.brain.task_models import (
    TaskEvent,
    TaskSpec,
    TaskState,
    TaskStatus,
)
from charlie.utils.logger import get_logger

logger = get_logger(__name__)


class TaskContext:
    """Context object passed to task handlers for progress reporting and cancellation."""

    def __init__(self, task_id: str, manager: "AsyncTaskManager"):
        self.task_id = task_id
        self._manager = manager
        self._cancelled = False

    def report_progress(self, pct: float, step: str = "") -> None:
        """Report task progress (0.0 to 1.0)."""
        status = self._manager._statuses.get(self.task_id)
        if status and not status.is_terminal:
            status.progress_pct = min(1.0, max(0.0, pct))
            status.current_step = step
            self._manager._emit(TaskEvent.PROGRESS, self.task_id, status)

    def is_cancelled(self) -> bool:
        """Check if this task has been requested to cancel."""
        return self._cancelled

    async def check_cancelled(self) -> None:
        """Raise CancelledError if task should stop. Call in long-running loops."""
        if self._cancelled:
            raise asyncio.CancelledError()


class AsyncTaskManager:
    """Central task orchestrator with priority queue and concurrency limits."""

    def __init__(self, max_concurrent: int = 3):
        self._max_concurrent = max_concurrent
        self._statuses: dict[str, TaskStatus] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._contexts: dict[str, TaskContext] = {}
        self._pending: list[TaskSpec] = []
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._running = 0
        self._listeners: list = []
        self._started = False
        self._dispatch_task: asyncio.Task | None = None

    def _ensure_dispatch(self) -> None:
        """Start the dispatch loop if not already running."""
        if not self._started:
            self._started = True
            try:
                self._dispatch_task = asyncio.ensure_future(self._dispatch_loop())
            except RuntimeError:
                pass  # No event loop yet

    def on_event(self, listener) -> None:
        """Register an event listener: fn(event: TaskEvent, task_id: str, status: TaskStatus)."""
        self._listeners.append(listener)

    def _emit(self, event: TaskEvent, task_id: str, status: TaskStatus) -> None:
        for listener in self._listeners:
            try:
                listener(event, task_id, status)
            except Exception:
                pass

    def submit(self, spec: TaskSpec) -> str:
        """Submit a task for execution. Returns task ID."""
        self._ensure_dispatch()

        status = TaskStatus(id=spec.id, state=TaskState.PENDING)
        self._statuses[spec.id] = status
        self._pending.append(spec)
        self._pending.sort(key=lambda s: s.priority.value)

        logger.info("task_submitted | id=%s | desc=%s | priority=%s",
                     spec.id, spec.description, spec.priority.name)
        return spec.id

    def cancel(self, task_id: str) -> bool:
        """Cancel a task. Returns True if task was found and cancelled."""
        status = self._statuses.get(task_id)
        if not status:
            return False

        if status.is_terminal:
            return False

        # Remove from pending queue
        for i, spec in enumerate(self._pending):
            if spec.id == task_id:
                self._pending.pop(i)
                status.state = TaskState.CANCELLED
                status.finished_at = time.time()
                self._emit(TaskEvent.CANCELLED, task_id, status)
                logger.info("task_cancelled | id=%s | state=pending", task_id)
                return True

        # Cancel running task
        ctx = self._contexts.get(task_id)
        if ctx:
            ctx._cancelled = True

        aio_task = self._tasks.get(task_id)
        if aio_task and not aio_task.done():
            aio_task.cancel()
            status.state = TaskState.CANCELLED
            status.finished_at = time.time()
            self._emit(TaskEvent.CANCELLED, task_id, status)
            logger.info("task_cancelled | id=%s | state=running", task_id)
            return True

        return False

    def status(self, task_id: str) -> TaskStatus | None:
        """Get the current status of a task."""
        return self._statuses.get(task_id)

    def list_active(self) -> list[TaskStatus]:
        """List all non-terminal tasks."""
        return [s for s in self._statuses.values() if not s.is_terminal]

    def list_all(self) -> list[TaskStatus]:
        """List all tasks."""
        return list(self._statuses.values())

    async def shutdown(self) -> None:
        """Cancel all tasks and stop the dispatch loop."""
        for task_id in list(self._tasks.keys()):
            self.cancel(task_id)

        for spec in self._pending:
            status = self._statuses.get(spec.id)
            if status and not status.is_terminal:
                status.state = TaskState.CANCELLED
                status.finished_at = time.time()
        self._pending.clear()

        if self._dispatch_task and not self._dispatch_task.done():
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass

        self._started = False
        logger.info("task_manager_shutdown")

    async def _dispatch_loop(self) -> None:
        """Background loop that dispatches pending tasks when slots are available."""
        while True:
            try:
                while not self._pending:
                    await asyncio.sleep(0.05)

                await self._semaphore.acquire()

                spec = self._pending.pop(0)
                status = self._statuses[spec.id]

                status.state = TaskState.RUNNING
                status.started_at = time.time()
                ctx = TaskContext(spec.id, self)
                self._contexts[spec.id] = ctx

                aio_task = asyncio.ensure_future(self._run_task(spec, ctx, status))
                self._tasks[spec.id] = aio_task

                self._emit(TaskEvent.STARTED, spec.id, status)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("dispatch_loop_error | %s", e)
                await asyncio.sleep(0.1)

    async def _run_task(self, spec: TaskSpec, ctx: TaskContext, status: TaskStatus) -> None:
        """Execute a single task."""
        try:
            result = await spec.handler(ctx, *spec.args, **spec.kwargs)
            if not status.is_terminal:
                status.state = TaskState.COMPLETED
                status.result = result
                status.progress_pct = 1.0
                status.finished_at = time.time()
                self._emit(TaskEvent.COMPLETED, spec.id, status)
                logger.info("task_completed | id=%s | desc=%s", spec.id, spec.description)

        except asyncio.CancelledError:
            if not status.is_terminal:
                status.state = TaskState.CANCELLED
                status.finished_at = time.time()
                self._emit(TaskEvent.CANCELLED, spec.id, status)

        except Exception as e:
            if not status.is_terminal:
                status.state = TaskState.FAILED
                status.error = str(e)
                status.finished_at = time.time()
                self._emit(TaskEvent.FAILED, spec.id, status)
                logger.error("task_failed | id=%s | error=%s", spec.id, e)

        finally:
            self._semaphore.release()
            self._tasks.pop(spec.id, None)
            self._contexts.pop(spec.id, None)
