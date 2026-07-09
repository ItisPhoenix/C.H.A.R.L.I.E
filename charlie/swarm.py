"""Swarm execution loop for Charlie's agent orchestration.

Reads pending tasks from the Blackboard, verifies dependencies,
spawns worker agents, and handles escalation on failure.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from charlie.agents import AGENT_REGISTRY
from charlie.agents.base import BaseAgent
from charlie.blackboard import Blackboard, Task

logger = logging.getLogger("charlie.swarm")

# Concurrency cap: max active agent tasks at once.
# Remaining tasks queue until a slot opens.
MAX_CONCURRENT_AGENTS = 5

# MAX_RETRIES is defined in blackboard.py - import from there
from charlie.blackboard import MAX_RETRIES  # noqa: F811


class SwarmOrchestrator:
    """Background loop that picks up tasks from the Blackboard
    and dispatches them to the appropriate MARVEL agent."""

    def __init__(self, blackboard: Blackboard, broadcast_callback=None, llm_client: Any = None) -> None:
        self.blackboard = blackboard
        self._llm_client = llm_client
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_AGENTS)
        self._active_tasks: Dict[str, asyncio.Task] = {}
        self._agent_instances: Dict[str, BaseAgent] = {}
        self._active_agents: set[str] = set()
        self._running = False
        self._broadcast = broadcast_callback  # Optional: async fn(snapshot)

    @property
    def active_agents(self) -> List[str]:
        """Names of agents currently executing a task."""
        return sorted(self._active_agents)

    def _get_agent(self, name: str) -> Optional[BaseAgent]:
        """Get or create agent instance by name."""
        if name in self._agent_instances:
            return self._agent_instances[name]
        agent_cls = AGENT_REGISTRY.get(name)
        if not agent_cls:
            logger.warning("Unknown agent: %s", name)
            return None
        try:
            agent = agent_cls(self.blackboard, self._llm_client)
        except Exception:
            logger.error("Failed to instantiate agent %s", name, exc_info=True)
            return None
        self._agent_instances[name] = agent
        return agent

    async def _run_agent(self, task: Task) -> None:
        """Execute a single task with semaphore-controlled concurrency."""
        agent = self._get_agent(task.assigned_to)
        if not agent:
            self.blackboard.update_task(
                task.id, status="failed", column="done",
                result=f"Agent '{task.assigned_to}' not found",
            )
            return
        self._active_agents.add(agent.name)
        self.blackboard.update_agent(agent.name, status="working", current_task=task.name)
        self.blackboard.update_task(task.id, status="running", column="in_progress")
        await self._broadcast_state()
        try:
            async with self._semaphore:
                result = await agent.execute(task.id)
                status = result.get("status", "done")
                result_text = result.get("result", "")
                # Only an explicit "failed" status is a failure. Any other
                # status (including "running", which means the agent coroutine
                # has already returned) is treated as completed so tasks are
                # never stranded in the in_progress column.
                if status == "failed":
                    column = "done"
                else:
                    status = "done"
                    column = "done"
                self.blackboard.update_task(
                    task.id, status=status, column=column, result=result_text
                )
                logger.info("Task %s completed: %s", task.id, status)
        except Exception:
            logger.error("Agent %s failed task %s", agent.name, task.id, exc_info=True)
            self.blackboard.update_task(
                task.id, status="failed", column="done", result="Agent error"
            )
        finally:
            self._active_agents.discard(agent.name)
            self.blackboard.update_agent(agent.name, status="idle", current_task="")
            await self._broadcast_state()
        self._handle_escalation()

    def terminate_agent(self, name: str) -> bool:
        """Cancel the in-flight task for the named agent, if any.

        Returns True if a running task was cancelled, False otherwise.
        """
        target_task_id: Optional[str] = None
        for task_id, task in self._active_tasks.items():
            agent = self._agent_instances.get(name)
            if agent is not None and not task.done():
                target_task_id = task_id
                break
        if target_task_id is None:
            logger.info("terminate_agent: no active task for %s", name)
            return False
        self._active_tasks[target_task_id].cancel()
        self.blackboard.update_task(
            target_task_id, status="failed", column="done", result="Terminated by user"
        )
        logger.info("terminate_agent: cancelled task %s for %s", target_task_id, name)
        return True

    def _handle_escalation(self) -> None:
        """Check for failed tasks and escalate if retries exhausted."""
        failed = self.blackboard.check_escalation()
        for task in failed:
            if task.retry_count >= MAX_RETRIES:
                logger.warning(
                    "Task %s exceeded %d retries, marking permanently failed",
                    task.id,
                    MAX_RETRIES,
                )
                self.blackboard.update_task(
                    task.id, status="failed", result="Exceeded max retries"
                )
            else:
                logger.info("Retrying task %s (attempt %d)", task.id, task.retry_count + 1)
                self.blackboard.reset_for_retry(task.id)

    async def _broadcast_state(self) -> None:
        """Broadcast current blackboard state to connected clients."""
        if self._broadcast:
            try:
                snap = self.blackboard.snapshot()
                await self._broadcast(snap)
            except Exception:
                logger.debug("Broadcast failed", exc_info=True)

    async def run(self) -> None:
        """Main orchestration loop. Polls blackboard for pending tasks."""
        self._running = True
        logger.info("Swarm orchestrator started (max_concurrent=%d)", MAX_CONCURRENT_AGENTS)

        while self._running:
            # Handle escalations first
            self._handle_escalation()

            # Get pending tasks (dependencies already resolved)
            pending = self.blackboard.get_pending_tasks()
            for task in pending:
                if task.id not in self._active_tasks:
                    atask = asyncio.create_task(self._run_agent(task))
                    atask.add_done_callback(
                        lambda t, tid=task.id: self._active_tasks.pop(tid, None)
                        if t.cancelled()
                        else (
                            logger.error(
                                "Agent task %s raised: %s",
                                tid,
                                t.exception(),
                                exc_info=t.exception(),
                            )
                            if t.exception()
                            else None
                        )
                    )
                    self._active_tasks[task.id] = atask

            # Clean up completed tasks
            done_ids = [tid for tid, t in self._active_tasks.items() if t.done()]
            for tid in done_ids:
                self._active_tasks.pop(tid, None)

            await asyncio.sleep(1.0)  # Poll interval

    def stop(self) -> None:
        """Stop the orchestration loop."""
        self._running = False
        # Cancel active tasks
        for task in self._active_tasks.values():
            task.cancel()
        self._active_tasks.clear()
        logger.info("Swarm orchestrator stopped")
