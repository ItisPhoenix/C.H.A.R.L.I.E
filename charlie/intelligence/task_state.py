"""
charlie/intelligence/task_state.py

Persistent task state management using SQLite.
Enables task survival across crashes/restarts and recovery of interrupted work.

"""

import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger("charlie.intelligence.task_state")


class TaskStatus(str, Enum):
    """Task execution status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


@dataclass
class SubTask:
    """A single executable unit within a task graph."""

    id: str
    description: str
    tool: str
    args: dict = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[any] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "tool": self.tool,
            "args": self.args,
            "depends_on": self.depends_on,
            "status": self.status.value if isinstance(self.status, TaskStatus) else self.status,
            "result": str(self.result) if self.result is not None else None,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SubTask":
        status = data.get("status", "pending")
        if isinstance(status, str):
            status = TaskStatus(status)
        return cls(
            id=data["id"],
            description=data["description"],
            tool=data["tool"],
            args=data.get("args", {}),
            depends_on=data.get("depends_on", []),
            status=status,
            result=data.get("result"),
            error=data.get("error"),
            created_at=data.get("created_at", time.time()),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
        )


@dataclass
class TaskGraph:
    """A collection of subtasks forming a directed acyclic graph."""

    goal: str
    tasks: list = field(default_factory=list)
    max_parallel: int = 3
    deadline: Optional[datetime] = None
    created_at: float = field(default_factory=time.time)

    def add_task(self, description: str, tool: str, args: dict = None, depends_on: list = None) -> SubTask:
        """Add a subtask to the graph."""
        task = SubTask(
            id=str(uuid.uuid4())[:8],
            description=description,
            tool=tool,
            args=args or {},
            depends_on=depends_on or [],
        )
        self.tasks.append(task)
        return task

    def get_ready_tasks(self) -> list:
        """Get tasks whose dependencies are all satisfied."""
        completed_ids = {t.id for t in self.tasks if t.status == TaskStatus.COMPLETED}
        ready = []
        for task in self.tasks:
            if task.status != TaskStatus.PENDING:
                continue
            if all(dep_id in completed_ids for dep_id in task.depends_on):
                ready.append(task)
        return ready

    def has_circular_dependencies(self) -> bool:
        """Check if the task graph has circular dependencies."""
        visited = set()
        rec_stack = set()

        def visit(task_id: str) -> bool:
            if task_id in rec_stack:
                return True
            if task_id in visited:
                return False
            visited.add(task_id)
            rec_stack.add(task_id)

            for task in self.tasks:
                if task.id == task_id:
                    for dep_id in task.depends_on:
                        if visit(dep_id):
                            return True
                    break
            rec_stack.remove(task_id)
            return False

        for task in self.tasks:
            if task.id not in visited:
                if visit(task.id):
                    return True
        return False

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "tasks": [t.to_dict() for t in self.tasks],
            "max_parallel": self.max_parallel,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "created_at": self.created_at,
        }


class TaskState:
    """
    Persistent task state manager using SQLite.

    Provides:
    - Task CRUD operations
    - Dependency tracking
    - Recovery of interrupted tasks on startup
    - Task graph persistence
    """

    DB_PATH = Path(__file__).parent / "task_state.db"

    def __init__(self):
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()
        # Recover any interrupted tasks on startup
        self.recover_interrupted_tasks()

    def _init_db(self):
        """Initialize the database schema."""
        self._conn = sqlite3.connect(str(self.DB_PATH), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                goal TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at REAL NOT NULL,
                started_at REAL,
                completed_at REAL,
                result TEXT,
                error TEXT,
                parent_id TEXT,
                max_parallel INTEGER DEFAULT 3,
                deadline TEXT,
                task_graph_json TEXT
            )
        """)

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS task_dependencies (
                task_id TEXT,
                depends_on_id TEXT,
                PRIMARY KEY (task_id, depends_on_id),
                FOREIGN KEY (task_id) REFERENCES tasks(id),
                FOREIGN KEY (depends_on_id) REFERENCES tasks(id)
            )
        """)

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS subtasks (
                id TEXT PRIMARY KEY,
                parent_task_id TEXT NOT NULL,
                description TEXT NOT NULL,
                tool TEXT NOT NULL,
                args TEXT,
                status TEXT DEFAULT 'pending',
                result TEXT,
                error TEXT,
                created_at REAL NOT NULL,
                started_at REAL,
                completed_at REAL,
                FOREIGN KEY (parent_task_id) REFERENCES tasks(id)
            )
        """)

        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_subtasks_parent
            ON subtasks(parent_task_id)
        """)

        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_status
            ON tasks(status)
        """)

        self._conn.commit()
        logger.info(f"task_state_db_initialized | path={self.DB_PATH}")

    def create_task(self, goal: str, task_graph: TaskGraph = None, deadline: datetime = None) -> str:
        """Create a new persistent task."""
        task_id = str(uuid.uuid4())[:8]
        now = time.time()

        self._conn.execute(
            """
            INSERT INTO tasks (id, goal, status, created_at, deadline, task_graph_json)
            VALUES (?, ?, 'pending', ?, ?, ?)
        """,
            (
                task_id,
                goal,
                now,
                deadline.isoformat() if deadline else None,
                str(task_graph.to_dict()) if task_graph else None,
            ),
        )

        if task_graph:
            for subtask in task_graph.tasks:
                self._conn.execute(
                    """
                    INSERT INTO subtasks (id, parent_task_id, description, tool, args, status, created_at)
                    VALUES (?, ?, ?, ?, ?, 'pending', ?)
                """,
                    (subtask.id, task_id, subtask.description, subtask.tool, str(subtask.args), subtask.created_at),
                )

                for dep_id in subtask.depends_on:
                    self._conn.execute(
                        """
                        INSERT INTO task_dependencies (task_id, depends_on_id)
                        VALUES (?, ?)
                    """,
                        (subtask.id, dep_id),
                    )

        self._conn.commit()
        logger.info(f"task_created | id={task_id} | goal={goal[:50]}")
        return task_id

    def get_task(self, task_id: str) -> Optional[dict]:
        """Get a task by ID."""
        row = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None

    def get_subtasks(self, parent_task_id: str) -> list:
        """Get all subtasks for a parent task."""
        rows = self._conn.execute("SELECT * FROM subtasks WHERE parent_task_id = ?", (parent_task_id,)).fetchall()

        subtasks = []
        for row in rows:
            deps = self._conn.execute(
                "SELECT depends_on_id FROM task_dependencies WHERE task_id = ?", (row["id"],)
            ).fetchall()
            dep_ids = [d["depends_on_id"] for d in deps]

            subtasks.append(
                SubTask(
                    id=row["id"],
                    description=row["description"],
                    tool=row["tool"],
                    args=json.loads(row["args"]) if row["args"] else {},
                    depends_on=dep_ids,
                    status=TaskStatus(row["status"]),
                    result=row["result"],
                    error=row["error"],
                    created_at=row["created_at"],
                    started_at=row["started_at"],
                    completed_at=row["completed_at"],
                )
            )
        return subtasks

    def update_subtask_status(self, subtask_id: str, status: TaskStatus, result: any = None, error: str = None):
        """Update a subtask's status."""
        now = time.time()

        if status == TaskStatus.RUNNING:
            self._conn.execute(
                """
                UPDATE subtasks SET status = ?, started_at = ? WHERE id = ?
            """,
                (status.value, now, subtask_id),
            )
        elif status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            self._conn.execute(
                """
                UPDATE subtasks SET status = ?, completed_at = ?, result = ?, error = ?
                WHERE id = ?
            """,
                (status.value, now, str(result) if result else None, error, subtask_id),
            )
        else:
            self._conn.execute(
                """
                UPDATE subtasks SET status = ? WHERE id = ?
            """,
                (status.value, subtask_id),
            )

        self._conn.commit()

    def update_task_status(self, task_id: str, status: TaskStatus, result: str = None, error: str = None):
        """Update a task's status."""
        now = time.time()

        if status == TaskStatus.RUNNING:
            self._conn.execute(
                """
                UPDATE tasks SET status = ?, started_at = ? WHERE id = ?
            """,
                (status.value, now, task_id),
            )
        elif status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            self._conn.execute(
                """
                UPDATE tasks SET status = ?, completed_at = ?, result = ?, error = ?
                WHERE id = ?
            """,
                (status.value, now, result, error, task_id),
            )
        else:
            self._conn.execute(
                """
                UPDATE tasks SET status = ? WHERE id = ?
            """,
                (status.value, task_id),
            )

        self._conn.commit()

    def get_interrupted_tasks(self) -> list:
        """Get tasks that were running when the system crashed."""
        rows = self._conn.execute("""
            SELECT * FROM tasks WHERE status = 'running'
        """).fetchall()
        return [dict(row) for row in rows]

    def recover_interrupted_tasks(self) -> list:
        """
        Recover interrupted tasks on startup.

        Returns list of tasks that need user decision: resume, discard, or convert.
        """
        interrupted = self.get_interrupted_tasks()
        recovered = []

        for task in interrupted:
            self._conn.execute(
                """
                UPDATE tasks SET status = 'interrupted' WHERE id = ?
            """,
                (task["id"],),
            )

            self._conn.execute(
                """
                UPDATE subtasks SET status = 'interrupted'
                WHERE parent_task_id = ? AND status = 'running'
            """,
                (task["id"],),
            )

            recovered.append(
                {
                    "id": task["id"],
                    "goal": task["goal"],
                    "created_at": task["created_at"],
                    "started_at": task["started_at"],
                }
            )

        self._conn.commit()

        if recovered:
            logger.warning(f"tasks_recovered | count={len(recovered)}")
            for task in recovered:
                logger.info(f"task_needs_decision | id={task['id']} | goal={task['goal'][:50]}")

        return recovered

    def get_pending_tasks(self) -> list:
        """Get all pending tasks ordered by creation time."""
        rows = self._conn.execute("""
            SELECT * FROM tasks WHERE status = 'pending' ORDER BY created_at ASC
        """).fetchall()
        return [dict(row) for row in rows]

    def delete_task(self, task_id: str):
        """Delete a task and its subtasks."""
        self._conn.execute("DELETE FROM task_dependencies WHERE task_id = ?", (task_id,))
        self._conn.execute("DELETE FROM subtasks WHERE parent_task_id = ?", (task_id,))
        self._conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        self._conn.commit()
        logger.info(f"task_deleted | id={task_id}")

    def get_task_stats(self) -> dict:
        """Get task statistics."""
        stats = {}
        for status in TaskStatus:
            count = self._conn.execute("SELECT COUNT(*) FROM tasks WHERE status = ?", (status.value,)).fetchone()[0]
            stats[status.value] = count
        return stats

    def close(self):
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None


# Singleton instance
_task_state: Optional[TaskState] = None


def get_task_state() -> TaskState:
    """Get the singleton TaskState instance."""
    global _task_state
    if _task_state is None:
        _task_state = TaskState()
        _task_state.recover_interrupted_tasks()
    return _task_state
