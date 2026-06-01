"""Task decomposition and multi-agent orchestration."""

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from charlie.utils.logger import get_logger

logger = get_logger(__name__)


class SubTaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class SubTask:
    id: str
    description: str
    required_tools: list[str] = field(default_factory=list)
    suggested_agent: str = "research"
    dependencies: list[str] = field(default_factory=list)
    status: SubTaskStatus = SubTaskStatus.PENDING
    result: str = ""
    duration_ms: float = 0.0


class TaskPlanner:
    """Decomposes complex goals into subtasks using LLM."""

    def __init__(self, brain=None):
        self.brain = brain

    async def plan_task(self, goal: str, available_agents: list[str]) -> list[SubTask]:
        """Break a goal into subtasks. Simple goals return a single subtask."""
        # If goal is simple (short, single intent), skip decomposition
        if len(goal.split()) < 10 and not any(
            kw in goal.lower()
            for kw in ["and then", "also", "additionally", "first.*then", "step"]
        ):
            return [
                SubTask(
                    id="task-0",
                    description=goal,
                    suggested_agent=self._pick_agent(goal, available_agents),
                )
            ]

        # Try LLM decomposition
        if self.brain and hasattr(self.brain, "call_llm"):
            try:
                subtasks = await self._llm_decompose(goal, available_agents)
                if subtasks:
                    return subtasks
            except Exception as e:
                logger.warning("llm_decompose_failed | %s", e)

        # Fallback: single task
        return [
            SubTask(
                id="task-0",
                description=goal,
                suggested_agent=self._pick_agent(goal, available_agents),
            )
        ]

    async def _llm_decompose(
        self, goal: str, available_agents: list[str]
    ) -> list[SubTask] | None:
        """Use LLM to decompose goal into subtasks."""
        agent_list = ", ".join(available_agents)
        prompt = f"""Break this goal into 2-5 subtasks. For each subtask, specify:
- description: what to do
- suggested_agent: one of [{agent_list}]
- dependencies: list of subtask IDs this depends on (empty if independent)
- required_tools: tools needed

Goal: {goal}

Respond with ONLY a JSON array:
[{{"id": "task-0", "description": "...", "suggested_agent": "...", "dependencies": [], "required_tools": []}}]"""

        try:
            response = await self.brain.call_llm(
                prompt,
                system="You are a task planner. Break goals into subtasks. Respond with only valid JSON.",
            )
            if not response:
                return None

            # Extract JSON from response
            text = response.strip()
            if "[" in text and "]" in text:
                start = text.index("[")
                end = text.rindex("]") + 1
                data = json.loads(text[start:end])

                subtasks = []
                for item in data:
                    subtasks.append(
                        SubTask(
                            id=item.get("id", f"task-{len(subtasks)}"),
                            description=item.get("description", ""),
                            suggested_agent=item.get("suggested_agent", "research"),
                            dependencies=item.get("dependencies", []),
                            required_tools=item.get("required_tools", []),
                        )
                    )
                return subtasks if subtasks else None
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning("llm_decompose_parse_failed | %s", e)
        return None

    def _pick_agent(self, goal: str, available_agents: list[str]) -> str:
        """Keyword-based agent selection fallback."""
        goal_lower = goal.lower()
        keyword_map = {
            "coding": ["code", "program", "function", "class", "bug", "fix", "implement"],
            "research": ["find", "search", "look up", "what is", "how to", "explain"],
            "writer": ["write", "draft", "compose", "email", "letter", "document"],
            "comms": ["send", "message", "notify", "announce", "post"],
            "vision": ["image", "photo", "screenshot", "visual", "picture"],
            "redteam": ["test", "security", "vulnerability", "audit", "penetration"],
            "system": ["run", "execute", "install", "configure", "system", "file"],
        }
        for agent, keywords in keyword_map.items():
            if agent in available_agents and any(kw in goal_lower for kw in keywords):
                return agent
        return available_agents[0] if available_agents else "research"


class Orchestrator:
    """Plans and executes tasks across multiple agents."""

    def __init__(self, brain=None, learning_tracker=None):
        self.brain = brain
        self.planner = TaskPlanner(brain)
        self.learning = learning_tracker

    async def plan(self, goal: str, available_agents: list[str]) -> list[SubTask]:
        """Decompose a goal into subtasks."""
        return await self.planner.plan_task(goal, available_agents)

    async def execute_plan(
        self, subtasks: list[SubTask], agent_registry
    ) -> list[dict]:
        """Execute subtasks respecting dependencies."""
        results = []
        completed = set()

        # Build dependency graph
        max_rounds = len(subtasks) + 1
        for _ in range(max_rounds):
            ready = [
                t
                for t in subtasks
                if t.status == SubTaskStatus.PENDING
                and all(dep in completed for dep in t.dependencies)
            ]
            if not ready:
                break

            # Execute ready tasks in parallel
            tasks = []
            for subtask in ready:
                tasks.append(self._execute_single(subtask, agent_registry))

            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            for subtask, result in zip(ready, batch_results):
                if isinstance(result, Exception):
                    subtask.status = SubTaskStatus.FAILED
                    subtask.result = str(result)
                else:
                    subtask.status = SubTaskStatus.DONE
                    subtask.result = result.get("output", "")
                    subtask.duration_ms = result.get("duration_ms", 0)

                completed.add(subtask.id)
                results.append(
                    {
                        "subtask_id": subtask.id,
                        "description": subtask.description,
                        "agent": subtask.suggested_agent,
                        "status": subtask.status.value,
                        "output": subtask.result,
                        "duration_ms": subtask.duration_ms,
                    }
                )

                # Record learning data
                if self.learning:
                    keywords = subtask.description.lower().split()[:5]
                    self.learning.record(
                        agent_name=subtask.suggested_agent,
                        keywords=keywords,
                        success=subtask.status == SubTaskStatus.DONE,
                        duration_ms=subtask.duration_ms,
                    )

        return results

    async def _execute_single(self, subtask: SubTask, agent_registry) -> dict:
        """Execute a single subtask using the appropriate agent."""
        subtask.status = SubTaskStatus.RUNNING
        start = time.monotonic()

        try:
            # Try to get the agent from registry
            agent = None
            if hasattr(agent_registry, "get"):
                agent = agent_registry.get(subtask.suggested_agent)

            if agent and hasattr(agent, "run"):
                result = await asyncio.to_thread(
                    agent.run, subtask.description, context={}
                )
                duration = (time.monotonic() - start) * 1000
                return {
                    "output": result.output if hasattr(result, "output") else str(result),
                    "duration_ms": duration,
                }

            # Fallback: use brain LLM directly
            if self.brain and hasattr(self.brain, "call_llm"):
                output = await self.brain.call_llm(subtask.description)
                duration = (time.monotonic() - start) * 1000
                return {"output": output or "Task completed", "duration_ms": duration}

            duration = (time.monotonic() - start) * 1000
            return {"output": f"No handler for agent: {subtask.suggested_agent}", "duration_ms": duration}

        except Exception as e:
            duration = (time.monotonic() - start) * 1000
            logger.error("subtask_failed | %s | %s", subtask.id, e)
            return {"output": f"Error: {e}", "duration_ms": duration}
