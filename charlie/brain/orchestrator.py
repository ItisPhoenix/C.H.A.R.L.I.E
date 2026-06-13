"""Task decomposition and multi-agent orchestration."""

import asyncio
import json
import time
from dataclasses import dataclass, field
from enum import Enum

from pydantic import BaseModel, Field, ValidationError

from charlie.brain.trace import trace as _trace
from charlie.utils.logger import get_logger
from charlie.watchdog.metrics import get_collector

logger = get_logger(__name__)
_metrics = get_collector()


class SubTaskSpec(BaseModel):
    """Schema for a single subtask as returned by the LLM.

    Pydantic validates that the LLM's JSON output has the right shape.
    If fields are missing, they get defaults; if types are wrong, we
    catch ValidationError in the caller.
    """

    id: str = ""
    description: str
    suggested_agent: str = "research"
    dependencies: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)


class DecompositionPlan(BaseModel):
    """The full decomposition plan returned by the LLM.

    Wraps a list of SubTaskSpec. Provides a from_llm_response helper
    that extracts the JSON array and validates it against the schema,
    giving better error messages than raw json.loads.
    """

    subtasks: list[SubTaskSpec]

    @classmethod
    def from_llm_response(cls, raw_text: str) -> "DecompositionPlan":
        """Parse LLM response text, extracting the JSON array.

        Tries to find a JSON array in the response and validate it
        against the DecompositionPlan schema. Raises ValueError on
        parse failure or schema validation failure.
        """
        text = raw_text.strip()
        # Try direct parse first
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Fall back to extracting [...] substring
            if "[" not in text or "]" not in text:
                raise ValueError("no JSON array found in LLM response")
            start = text.index("[")
            end = text.rindex("]") + 1
            data = json.loads(text[start:end])
        if not isinstance(data, list):
            raise ValueError("LLM response is not a JSON array")
        return cls(subtasks=data)


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
            kw in goal.lower() for kw in ["and then", "also", "additionally", "first.*then", "step"]
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
        self, goal: str, available_agents: list[str], max_attempts: int = 2
    ) -> list[SubTask] | None:
        """Use LLM to decompose goal into subtasks.

        Retries up to max_attempts times if the LLM returns invalid JSON
        or the JSON doesn't match the DecompositionPlan schema. Returns
        None if all attempts fail (caller will fall back to single task).
        """
        agent_list = ", ".join(available_agents)
        base_prompt = f"""Break this goal into 2-5 subtasks. For each subtask, specify:
- description: what to do
- suggested_agent: one of [{agent_list}]
- dependencies: list of subtask IDs this depends on (empty if independent)
- required_tools: tools needed

Goal: {goal}

Respond with ONLY a JSON array (no prose, no markdown):
[{{"id": "task-0", "description": "...", "suggested_agent": "...", "dependencies": [], "required_tools": []}}]"""

        for attempt in range(1, max_attempts + 1):
            try:
                if attempt == 1:
                    prompt = base_prompt
                    system = "You are a task planner. Break goals into subtasks. Respond with only valid JSON."
                else:
                    # Retry with a stricter prompt
                    prompt = base_prompt
                    system = (
                        "You are a task planner. Return ONLY a valid JSON array of objects. "
                        "No prose, no markdown code fences, no explanation. "
                        "Just the JSON array, nothing else."
                    )

                response = await self.brain.call_llm(prompt, system=system)
                if not response:
                    logger.warning("llm_decompose_empty_response | attempt=%d", attempt)
                    continue

                plan = DecompositionPlan.from_llm_response(response)
                # Assign sequential IDs if the LLM omitted them
                for i, spec in enumerate(plan.subtasks):
                    if not spec.id:
                        spec.id = f"task-{i}"
                return [SubTask(**spec.model_dump()) for spec in plan.subtasks]

            except (json.JSONDecodeError, ValueError, ValidationError) as e:
                logger.warning(
                    "llm_decompose_parse_failed | attempt=%d | error=%s",
                    attempt,
                    e,
                )
                continue
            except Exception as e:
                logger.warning(
                    "llm_decompose_unexpected | attempt=%d | error=%s",
                    attempt,
                    e,
                )
                continue

        logger.warning("llm_decompose_all_attempts_failed | goal=%s", goal[:80])
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
            "system": ["run", "execute", "install", "configure", "system", "file"],
        }
        for agent, keywords in keyword_map.items():
            if agent in available_agents and any(kw in goal_lower for kw in keywords):
                return agent
        return available_agents[0] if available_agents else "research"


class TaskOrchestrator:
    """Plans and executes tasks across multiple agents."""

    def __init__(self, brain=None, learning_tracker=None):
        self.brain = brain
        self.planner = TaskPlanner(brain)
        self.learning = learning_tracker
        # Per-agent failure tracking for circuit breaker
        self._agent_failures: dict[str, int] = {}  # agent_name -> consecutive failures
        self._agent_unhealthy: set[str] = set()  # agents to skip
        self._agent_max_failures: int = 3  # trip after N consecutive failures
        self._agent_timeout_seconds: int = 30  # default per-agent timeout

    async def plan(self, goal: str, available_agents: list[str]) -> list[SubTask]:
        """Decompose a goal into subtasks."""
        return await self.planner.plan_task(goal, available_agents)

    async def execute_plan(self, subtasks: list[SubTask], agent_registry) -> list[dict]:
        """Execute subtasks respecting dependencies."""
        results = []
        completed = set()

        # Build dependency graph
        max_rounds = len(subtasks) + 1
        for _ in range(max_rounds):
            ready = [
                t
                for t in subtasks
                if t.status == SubTaskStatus.PENDING and all(dep in completed for dep in t.dependencies)
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
        agent_name = subtask.suggested_agent

        # Circuit breaker check: skip agents marked unhealthy from prior failures
        if agent_name in self._agent_unhealthy:
            return {
                "output": f"agent {agent_name} is unhealthy (circuit breaker open)",
                "duration_ms": 0.0,
            }

        # Per-agent timeout (default 30s). Could be made per-agent from config.
        timeout = self._agent_timeout_seconds

        try:
            # Try to get the agent from registry
            agent = None
            if hasattr(agent_registry, "get"):
                agent = agent_registry.get(subtask.suggested_agent)

            if agent and hasattr(agent, "run"):
                result = await asyncio.wait_for(
                    asyncio.to_thread(agent.run, subtask.description, context={}),
                    timeout=timeout,
                )
                # Success: reset failure counter for this agent
                self._agent_failures[agent_name] = 0
                duration = (time.monotonic() - start) * 1000
                _metrics.record_agent_invocation(agent_name, "ok")
                return {
                    "output": result.output if hasattr(result, "output") else str(result),
                    "duration_ms": duration,
                }

            # Fallback: use brain LLM directly
            if self.brain and hasattr(self.brain, "call_llm"):
                output = await asyncio.wait_for(
                    self.brain.call_llm(subtask.description),
                    timeout=timeout,
                )
                self._agent_failures[agent_name] = 0
                duration = (time.monotonic() - start) * 1000
                _metrics.record_agent_invocation(agent_name, "ok")
                return {"output": output or "Task completed", "duration_ms": duration}

            duration = (time.monotonic() - start) * 1000
            _metrics.record_agent_invocation(agent_name, "error")
            return {"output": f"No handler for agent: {agent_name}", "duration_ms": duration}

        except asyncio.TimeoutError:
            self._record_failure(agent_name, "timeout")
            duration = (time.monotonic() - start) * 1000
            _metrics.record_agent_invocation(agent_name, "timeout")
            logger.error("subtask_timeout | %s | agent=%s | timeout=%ds", subtask.id, agent_name, timeout)
            return {"output": f"Error: agent {agent_name} timed out after {timeout}s", "duration_ms": duration}
        except Exception as e:
            self._record_failure(agent_name, str(e))
            duration = (time.monotonic() - start) * 1000
            _metrics.record_agent_invocation(agent_name, "error")
            logger.error("subtask_failed | %s | agent=%s | %s", subtask.id, agent_name, e)
            return {"output": f"Error: {e}", "duration_ms": duration}

    def _record_failure(self, agent_name: str, error: str) -> None:
        """Increment failure counter, trip circuit breaker at threshold."""
        self._agent_failures[agent_name] = self._agent_failures.get(agent_name, 0) + 1
        if self._agent_failures[agent_name] >= self._agent_max_failures:
            self._agent_unhealthy.add(agent_name)
            _metrics.set_circuit_breaker_state(agent_name, True)
            _trace(
                "circuit_breaker_open",
                agent=agent_name,
                error=error,
                extra={"failures": self._agent_failures[agent_name]},
            )
            logger.warning(
                "agent_circuit_breaker_open | name=%s | failures=%d | last_error=%s",
                agent_name,
                self._agent_failures[agent_name],
                error,
            )

    def reset_circuit_breaker(self, agent_name: str = None) -> None:
        """Reset circuit breaker for one agent or all agents.

        If agent_name is provided, clear that agent's failure counter and
        remove it from the unhealthy set. If agent_name is None, reset all
        agents (useful for tests or manual recovery).
        """
        if agent_name:
            self._agent_failures.pop(agent_name, None)
            self._agent_unhealthy.discard(agent_name)
            logger.info("agent_circuit_breaker_reset | name=%s", agent_name)
        else:
            self._agent_failures.clear()
            self._agent_unhealthy.clear()
            logger.info("agent_circuit_breaker_reset | all")
