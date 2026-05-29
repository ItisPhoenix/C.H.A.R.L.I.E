"""Task data models for the Async Task Manager."""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine


class TaskState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED)


class TaskPriority(int, Enum):
    CRITICAL = 0
    HIGH = 10
    NORMAL = 20
    BACKGROUND = 30


class TaskEvent(str, Enum):
    STARTED = "started"
    PROGRESS = "progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskSpec:
    """Specification for a task to be executed."""
    id: str
    description: str
    priority: TaskPriority
    handler: Callable[..., Coroutine]  # async def handler(task_ctx, *args, **kwargs)
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


@dataclass
class TaskStatus:
    """Current status of a task."""
    id: str
    state: TaskState
    progress_pct: float = 0.0
    current_step: str = ""
    result: Any = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state.is_terminal

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "state": self.state.value,
            "progress_pct": self.progress_pct,
            "current_step": self.current_step,
            "result": str(self.result) if self.result is not None else None,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


def make_task_id() -> str:
    """Generate a short unique task ID."""
    return uuid.uuid4().hex[:8]
