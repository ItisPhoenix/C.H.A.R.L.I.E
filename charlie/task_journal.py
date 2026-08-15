"""Canonical task identity, lifecycle, and restart-safe journal.

The existing background scheduler remains an execution adapter.  This module is
the small domain model that owns task lifecycle truth and exposes only canonical
V1 states to new callers.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Callable, Iterable, Optional

from charlie.utils import make_id, utc_now_iso


class TaskStatus(StrEnum):
    QUEUED = "queued"
    PLANNING = "planning"
    WAITING = "waiting"
    RUNNING = "running"
    PAUSED = "paused"
    APPROVAL_REQUIRED = "approval_required"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskOrigin(StrEnum):
    FOREGROUND = "foreground"
    BACKGROUND = "background"
    BROWSER = "browser"
    RESEARCH = "research"
    SYSTEM = "system"
    MAINTENANCE = "maintenance"
    CHILD = "child"


class TaskPriority(StrEnum):
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


_LEGACY_STATUS_ALIASES = {
    "done": TaskStatus.COMPLETED,
    "awaiting_approval": TaskStatus.APPROVAL_REQUIRED,
}
_TERMINAL_STATUSES = frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED})
_ALLOWED_TRANSITIONS = {
    TaskStatus.QUEUED: frozenset({
        TaskStatus.PLANNING, TaskStatus.WAITING, TaskStatus.RUNNING,
        TaskStatus.PAUSED, TaskStatus.APPROVAL_REQUIRED, TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    }),
    TaskStatus.PLANNING: frozenset({
        TaskStatus.QUEUED, TaskStatus.WAITING, TaskStatus.RUNNING,
        TaskStatus.PAUSED, TaskStatus.APPROVAL_REQUIRED, TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    }),
    TaskStatus.WAITING: frozenset({
        TaskStatus.PLANNING, TaskStatus.RUNNING, TaskStatus.PAUSED,
        TaskStatus.APPROVAL_REQUIRED, TaskStatus.FAILED, TaskStatus.CANCELLED,
    }),
    TaskStatus.RUNNING: frozenset({
        TaskStatus.WAITING, TaskStatus.PAUSED, TaskStatus.APPROVAL_REQUIRED,
        TaskStatus.VERIFYING, TaskStatus.FAILED, TaskStatus.CANCELLED,
    }),
    TaskStatus.PAUSED: frozenset({
        TaskStatus.WAITING, TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED,
    }),
    TaskStatus.APPROVAL_REQUIRED: frozenset({
        TaskStatus.WAITING, TaskStatus.PLANNING, TaskStatus.RUNNING,
        TaskStatus.FAILED, TaskStatus.CANCELLED,
    }),
    TaskStatus.VERIFYING: frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}


class TaskTransitionError(ValueError):
    """Raised when a task attempts an impossible lifecycle transition."""


def normalize_task_status(status: str | TaskStatus) -> TaskStatus:
    if isinstance(status, TaskStatus):
        return status
    legacy = _LEGACY_STATUS_ALIASES.get(status)
    if legacy is not None:
        return legacy
    try:
        return TaskStatus(status)
    except ValueError as exc:
        raise ValueError(f"Unknown task status: {status!r}") from exc


def _enum_value(value: str | StrEnum, enum_type: type[StrEnum], default: StrEnum) -> StrEnum:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except ValueError:
        return default


@dataclass
class TaskRecord:
    id: str
    title: str
    status: TaskStatus = TaskStatus.QUEUED
    origin: TaskOrigin = TaskOrigin.FOREGROUND
    priority: TaskPriority = TaskPriority.NORMAL
    session_id: Optional[str] = None
    parent_task_id: Optional[str] = None
    capability_requirements: tuple[str, ...] = ()
    current_step: int = 0
    total_steps: int = 0
    progress: Optional[float] = None
    current_action: Optional[str] = None
    waiting_reason: Optional[str] = None
    result_reference: Optional[str] = None
    error_summary: Optional[str] = None
    approval_reference: Optional[str] = None
    cancel_requested: bool = False
    created_at: str = field(default_factory=utc_now_iso)
    started_at: Optional[str] = None
    updated_at: str = field(default_factory=utc_now_iso)
    completed_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status.value,
            "origin": self.origin.value,
            "priority": self.priority.value,
            "session_id": self.session_id,
            "parent_task_id": self.parent_task_id,
            "capability_requirements": list(self.capability_requirements),
            "current_step": self.current_step,
            "total_steps": self.total_steps,
            "progress": self.progress,
            "current_action": self.current_action,
            "waiting_reason": self.waiting_reason,
            "result_reference": self.result_reference,
            "error_summary": self.error_summary,
            "approval_reference": self.approval_reference,
            "cancel_requested": self.cancel_requested,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "TaskRecord":
        status = normalize_task_status(payload.get("status", TaskStatus.QUEUED))
        return cls(
            id=str(payload.get("id", "")),
            title=str(payload.get("title", "")),
            status=status,
            origin=_enum_value(payload.get("origin", TaskOrigin.FOREGROUND), TaskOrigin, TaskOrigin.FOREGROUND),
            priority=_enum_value(payload.get("priority", TaskPriority.NORMAL), TaskPriority, TaskPriority.NORMAL),
            session_id=payload.get("session_id"),
            parent_task_id=payload.get("parent_task_id"),
            capability_requirements=tuple(str(value) for value in payload.get("capability_requirements", ())),
            current_step=int(payload.get("current_step", 0)),
            total_steps=int(payload.get("total_steps", 0)),
            progress=payload.get("progress"),
            current_action=payload.get("current_action"),
            waiting_reason=payload.get("waiting_reason"),
            result_reference=payload.get("result_reference"),
            error_summary=payload.get("error_summary"),
            approval_reference=payload.get("approval_reference"),
            cancel_requested=bool(payload.get("cancel_requested", False)),
            created_at=str(payload.get("created_at", utc_now_iso())),
            started_at=payload.get("started_at"),
            updated_at=str(payload.get("updated_at", utc_now_iso())),
            completed_at=payload.get("completed_at"),
        )


class TaskJournal:
    """Thread-safe canonical task journal with optional JSON persistence."""

    def __init__(
        self,
        state_path: str | Path | None = None,
        on_change: Optional[Callable[[TaskRecord], None]] = None,
    ) -> None:
        self._state_path = Path(state_path) if state_path is not None else None
        self._on_change = on_change
        self._lock = threading.RLock()
        self._records: dict[str, TaskRecord] = {}
        self._load()

    def create_task(
        self,
        title: str,
        *,
        task_id: Optional[str] = None,
        origin: str | TaskOrigin = TaskOrigin.FOREGROUND,
        priority: str | TaskPriority = TaskPriority.NORMAL,
        status: str | TaskStatus = TaskStatus.QUEUED,
        session_id: Optional[str] = None,
        parent_task_id: Optional[str] = None,
        capability_requirements: Iterable[str] = (),
        current_step: int = 0,
        total_steps: int = 0,
    ) -> TaskRecord:
        task = TaskRecord(
            id=task_id or make_id(12),
            title=title,
            status=normalize_task_status(status),
            origin=_enum_value(origin, TaskOrigin, TaskOrigin.FOREGROUND),
            priority=_enum_value(priority, TaskPriority, TaskPriority.NORMAL),
            session_id=session_id,
            parent_task_id=parent_task_id,
            capability_requirements=tuple(sorted({str(value) for value in capability_requirements})),
            current_step=current_step,
            total_steps=total_steps,
        )
        with self._lock:
            if task.id in self._records:
                raise ValueError(f"Task already exists: {task.id}")
            self._records[task.id] = task
            self._persist()
            self._notify(task)
            return replace(task)

    def get(self, task_id: str) -> TaskRecord:
        with self._lock:
            try:
                return replace(self._records[task_id])
            except KeyError as exc:
                raise KeyError(f"Unknown task: {task_id}") from exc

    def list(self, *, include_terminal: bool = True) -> list[TaskRecord]:
        with self._lock:
            records = self._records.values()
            if not include_terminal:
                records = (record for record in records if record.status not in _TERMINAL_STATUSES)
            return [replace(record) for record in records]

    def snapshot(self, *, include_terminal: bool = True) -> list[dict]:
        return [task.to_dict() for task in self.list(include_terminal=include_terminal)]

    def transition(
        self,
        task_id: str,
        status: str | TaskStatus,
        *,
        waiting_reason: Optional[str] = None,
        error_summary: Optional[str] = None,
        result_reference: Optional[str] = None,
        approval_reference: Optional[str] = None,
    ) -> TaskRecord:
        next_status = normalize_task_status(status)
        with self._lock:
            task = self._records[task_id]
            if next_status != task.status and next_status not in _ALLOWED_TRANSITIONS[task.status]:
                raise TaskTransitionError(
                    f"Cannot transition task {task_id} from {task.status.value} to {next_status.value}"
                )
            task.status = next_status
            task.updated_at = utc_now_iso()
            if next_status is TaskStatus.RUNNING and task.started_at is None:
                task.started_at = task.updated_at
            if next_status in _TERMINAL_STATUSES:
                task.completed_at = task.updated_at
            if waiting_reason is not None:
                task.waiting_reason = waiting_reason
            if error_summary is not None:
                task.error_summary = error_summary
            if result_reference is not None:
                task.result_reference = result_reference
            if approval_reference is not None:
                task.approval_reference = approval_reference
            self._persist()
            self._notify(task)
            return replace(task)

    def update_progress(
        self,
        task_id: str,
        *,
        progress: Optional[float] = None,
        current_action: Optional[str] = None,
        current_step: Optional[int] = None,
        total_steps: Optional[int] = None,
        waiting_reason: Optional[str] = None,
    ) -> TaskRecord:
        with self._lock:
            task = self._records[task_id]
            if progress is not None:
                task.progress = max(0.0, min(1.0, float(progress)))
            if current_action is not None:
                task.current_action = current_action
            if current_step is not None:
                task.current_step = max(0, int(current_step))
            if total_steps is not None:
                task.total_steps = max(0, int(total_steps))
            if waiting_reason is not None:
                task.waiting_reason = waiting_reason
            task.updated_at = utc_now_iso()
            self._persist()
            self._notify(task)
            return replace(task)

    def request_cancel(self, task_id: str) -> TaskRecord:
        with self._lock:
            task = self._records[task_id]
            task.cancel_requested = True
            task.updated_at = utc_now_iso()
            self._persist()
            self._notify(task)
            return replace(task)

    def cancel(self, task_id: str) -> TaskRecord:
        with self._lock:
            task = self._records[task_id]
            if task.status in _TERMINAL_STATUSES:
                return replace(task)
            task.cancel_requested = True
        return self.transition(task_id, TaskStatus.CANCELLED)

    def complete(self, task_id: str, *, result_reference: Optional[str] = None) -> TaskRecord:
        return self.transition(task_id, TaskStatus.COMPLETED, result_reference=result_reference)

    def fail(self, task_id: str, *, error_summary: Optional[str] = None) -> TaskRecord:
        return self.transition(task_id, TaskStatus.FAILED, error_summary=error_summary)

    def require_approval(self, task_id: str, *, approval_reference: Optional[str] = None) -> TaskRecord:
        return self.transition(task_id, TaskStatus.APPROVAL_REQUIRED, approval_reference=approval_reference)

    def _notify(self, task: TaskRecord) -> None:
        if self._on_change is not None:
            self._on_change(replace(task))

    def _load(self) -> None:
        if self._state_path is None or not self._state_path.exists():
            return
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
            records = raw.get("tasks", []) if isinstance(raw, dict) else raw
            for payload in records:
                task = TaskRecord.from_dict(payload)
                if task.id:
                    self._records[task.id] = task
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            self._records = {}

    def _persist(self) -> None:
        if self._state_path is None:
            return
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._state_path.with_name(f".{self._state_path.name}.tmp")
        payload = {"version": 1, "tasks": [task.to_dict() for task in self._records.values()]}
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temporary, self._state_path)
