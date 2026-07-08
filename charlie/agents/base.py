"""Base class for all Charlie agents."""

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict

if __name__ != "__main__":
    from charlie.blackboard import Blackboard


class BaseAgent(ABC):
    """Abstract base for swarm agents. Each agent has a name, role, and tools."""

    name: str = "BaseAgent"
    role: str = "worker"
    description: str = ""
    _action_verb: str = "Working"
    _done_log: str = "Done"
    _fail_log: str = "Failed"
    _success_msg: str = "Completed"

    def __init__(self, blackboard: "Blackboard", llm_client: Any = None) -> None:
        self.blackboard = blackboard
        self.llm_client = llm_client
        self.logger = logging.getLogger(f"charlie.agents.{self.name}")
        self.blackboard.register_agent(self.name)

    async def execute(self, task_id: str) -> Dict[str, Any]:
        """Template method: fetches task, runs _do_action, handles status/error."""
        task = self.blackboard.get_task(task_id)
        if not task:
            return {"status": "failed", "result": "Task not found"}

        self._update_status("working", task_id)
        self.log(f"{self._action_verb}: {task.name}")

        try:
            result = await self._do_action(task.name, task)
            self.blackboard.update_task(task_id, status="done", result=result)
            self.log(self._done_log)
            self._update_status("idle")
            return {"status": "done", "result": result}
        except Exception as exc:
            self.log(f"{self._fail_log}: {exc}")
            self._update_status("error")
            self.blackboard.update_task(task_id, status="failed", result=str(exc))
            return {"status": "failed", "result": str(exc)}

    @abstractmethod
    async def _do_action(self, task_name: str, task) -> str:
        """Override in subclass: perform the actual work. Return result string.

        ``task`` is the live Blackboard Task object, passed so planner agents
        can read fields like ``parent_task_id``.
        """
        ...

    def log(self, message: str) -> None:
        self.logger.info("[%s] %s", self.name, message)
        card = self.blackboard.get_agents().get(self.name)
        if card:
            card.logs.append(message)
            # Keep only last 50 log entries
            if len(card.logs) > 50:
                card.logs = card.logs[-50:]

    def _update_status(self, status: str, task_id: str = "") -> None:
        self.blackboard.update_agent(
            self.name, status=status, current_task=task_id
        )
