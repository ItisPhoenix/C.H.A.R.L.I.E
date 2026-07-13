"""Blackboard state engine for the agent swarm.

Holds shared context, task board, and agent statuses in-memory.
Periodically flushes changes to blackboard.json in a background thread.
Uses the Blackboard pattern: all agents read/write to shared state.
"""

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from charlie.utils import make_id

logger = logging.getLogger("charlie.blackboard")

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class Task:
    """Represents a unit of work on the Kanban board."""
    id: str
    name: str
    assigned_to: str = ""
    status: str = "pending"  # pending | running | done | failed
    column: str = "backlog"  # backlog | todo | in_progress | done
    priority: int = 2  # 0=critical, 1=high, 2=normal, 3=low
    dependencies: List[str] = field(default_factory=list)
    parent_task_id: Optional[str] = None
    result: str = ""
    retry_count: int = 0
    approval_status: str = "approved"  # pending_approval | approved | rejected

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AgentCard:
    name: str
    status: str = "idle"  # idle | working
    current_task: str = ""
    logs: List[str] = field(default_factory=list)
    token_cost: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Blackboard
# ---------------------------------------------------------------------------

MAX_RETRIES = 3
FLUSH_INTERVAL_S = 5.0


class Blackboard:
    """Shared state for the agent swarm. Thread-safe via lock."""

    def __init__(self, persist_path: str = "blackboard.json") -> None:
        self._lock = threading.RLock()
        self._persist_path = Path(persist_path)
        self._tasks: Dict[str, Task] = {}
        self._agents: Dict[str, AgentCard] = {}
        self._findings: Dict[str, Any] = {}
        self._findings_summary: str = ""
        self._dirty = False
        self._flush_thread: Optional[threading.Thread] = None
        self._running = False
        self._start_flush_thread()

    # -- Lifecycle --

    def _start_flush_thread(self) -> None:
        self._running = True
        self._flush_thread = threading.Thread(
            target=self._flush_loop, daemon=True, name="blackboard-flush"
        )
        self._flush_thread.start()

    def _flush_loop(self) -> None:
        while self._running:
            time.sleep(FLUSH_INTERVAL_S)
            self._flush()

    def _flush(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            state = self.snapshot()
            self._dirty = False
        try:
            self._persist_path.write_text(
                json.dumps(state, indent=2, default=str), encoding="utf-8"
            )
            logger.debug("Blackboard flushed to %s", self._persist_path)
        except Exception:
            logger.warning("Failed to flush blackboard", exc_info=True)

    def stop(self) -> None:
        self._running = False
        self._flush()

    def get_task(self, task_id: str) -> Optional[Task]:
        """Retrieve a task by its ID."""
        with self._lock:
            return self._tasks.get(task_id)

    def add_task(
        self,
        name: str,
        assigned_to: str = "",
        dependencies: Optional[List[str]] = None,
        parent_task_id: Optional[str] = None,
        priority: int = 2,
        column: str = "backlog",
        approval_status: str = "pending_approval",
    ) -> Task:
        """Add a new task to the blackboard Kanban board."""
        task = Task(
            id=make_id(8),
            name=name,
            assigned_to=assigned_to,
            dependencies=dependencies or [],
            parent_task_id=parent_task_id,
            priority=priority,
            column=column,
            approval_status=approval_status,
        )
        with self._lock:
            self._tasks[task.id] = task
            self._dirty = True
        logger.info(
            "Task added: %s [%s] col=%s pri=%d app=%s",
            task.name, task.id, task.column, task.priority, task.approval_status
        )
        return task

    def update_task(self, task_id: str, **kwargs: Any) -> Optional[Task]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            for key, val in kwargs.items():
                if hasattr(task, key):
                    setattr(task, key, val)
            self._dirty = True
        return task
    def get_pending_tasks(self) -> List[Task]:
        """Return tasks whose dependencies are met, sorted by priority (0=highest)."""
        with self._lock:
            done_ids = {
                tid for tid, t in self._tasks.items() if t.status == "done"
            }
            ready = [
                t
                for t in self._tasks.values()
                if t.status == "pending"
                and getattr(t, "approval_status", "approved") == "approved"
                and all(dep in done_ids for dep in t.dependencies)
            ]
            return sorted(ready, key=lambda t: t.priority)

    def move_task(self, task_id: str, new_column: str) -> Optional[Task]:
        """Move a task to a different Kanban column."""
        valid_columns = ("backlog", "todo", "in_progress", "done")
        if new_column not in valid_columns:
            logger.warning("Invalid column: %s", new_column)
            return None
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            old_col = task.column
            task.column = new_column
            self._dirty = True
            logger.info("Task %s moved: %s -> %s", task_id, old_col, new_column)
            return task

    def get_kanban(self) -> Dict[str, List[Task]]:
        """Return tasks grouped by Kanban column."""
        with self._lock:
            kanban: Dict[str, List[Task]] = {
                "backlog": [], "todo": [], "in_progress": [], "done": [],
            }
            for t in self._tasks.values():
                if t.column in kanban:
                    kanban[t.column].append(t)
            return kanban


    def get_all_tasks(self) -> List[Task]:
        with self._lock:
            return list(self._tasks.values())

    # -- Agent operations --

    def register_agent(self, name: str) -> AgentCard:
        with self._lock:
            if name not in self._agents:
                self._agents[name] = AgentCard(name=name)
                self._dirty = True
        return self._agents[name]

    def update_agent(self, name: str, **kwargs: Any) -> Optional[AgentCard]:
        with self._lock:
            card = self._agents.get(name)
            if not card:
                return None
            for key, val in kwargs.items():
                if hasattr(card, key):
                    setattr(card, key, val)
            self._dirty = True
        return card

    def get_agents(self) -> Dict[str, AgentCard]:
        with self._lock:
            return dict(self._agents)

    # -- Findings --

    def add_finding(self, key: str, value: Any) -> None:
        with self._lock:
            self._findings[key] = value
            self._dirty = True

    def get_findings(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._findings)

    # -- Snapshot (for WebSocket broadcast) --

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            kanban = {
                "backlog": [], "todo": [], "in_progress": [], "done": [],
            }
            for t in self._tasks.values():
                col = t.column if t.column in kanban else "backlog"
                kanban[col].append(t.to_dict())
            return {
                "tasks": [t.to_dict() for t in self._tasks.values()],
                "kanban": kanban,
                "agents": {k: v.to_dict() for k, v in self._agents.items()},
                "findings": dict(self._findings),
                "findings_summary": self._findings_summary,
            }

    # -- Escalation --

    def check_escalation(self) -> List[Task]:
        """Return failed tasks for the swarm to either retry or permanently
        fail. Includes tasks at or past MAX_RETRIES -- the caller
        (SwarmOrchestrator._handle_escalation) is what decides retry vs.
        terminal failure, and needs to see retry-exhausted tasks at least
        once to mark them permanently failed. Previously this filtered out
        retry_count >= MAX_RETRIES entirely, so a task that ran out of
        retries was silently excluded before it ever reached that decision
        and just sat in "failed" status forever with no terminal message."""
        with self._lock:
            return [t for t in self._tasks.values() if t.status == "failed"]

    def reset_for_retry(self, task_id: str) -> Optional[Task]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            task.status = "pending"
            task.retry_count += 1
            task.result = ""
            self._dirty = True
        return task
