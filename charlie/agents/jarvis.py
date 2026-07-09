"""J.A.R.V.I.S. - Orchestrator agent.

System entry point. Analyzes requests, coordinates tasks,
handles supervisor routing, presents final response.
"""

import asyncio
import time
from typing import Any, Dict, List

from charlie.agents.base import BaseAgent


class JarvisAgent(BaseAgent):
    name = "J.A.R.V.I.S."
    role = "orchestrator"
    description = (
        "System entry point. Analyzes initial requests, coordinates tasks, "
        "handles supervisor routing, and presents the final response."
    )

    async def _do_action(self, task_name: str, task=None) -> str:
        """Orchestrate: break down request, spawn sub-tasks via Vision."""
        task_id = task.id if task else None
        self.log(f"Analyzing request: {task_name}")

        # Spawn Vision for planning
        self.blackboard.add_task(
            name=f"Plan: {task_name}",
            assigned_to="Vision",
            parent_task_id=task_id,
        )
        self.blackboard.update_task(task_id, status="running")

        # Poll (briefly) for Vision to create sub-tasks, capped at 30s.
        deadline = time.monotonic() + 30.0
        sub_tasks: List[Dict[str, Any]] = []
        while time.monotonic() < deadline:
            sub_tasks = [
                t
                for t in self.blackboard.get_all_tasks()
                if t.parent_task_id == task_id
            ]
            if sub_tasks and all(
                t.status != "pending" or t.assigned_to for t in sub_tasks
            ):
                break
            await asyncio.sleep(1.0)

        self.log(f"Plan created: {len(sub_tasks)} sub-tasks")
        return f"Spawned {len(sub_tasks)} sub-tasks"
