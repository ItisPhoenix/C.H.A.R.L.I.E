"""
charlie/brain/agent.py

Agent Runtime — autonomous multi-step task execution with manifest-driven agents.

This module provides:
- AgentRegistry for managing agents loaded from charlie/agents/ manifests
- Orchestrator for multi-agent coordination, goal decomposition, and merge
- is_complex_goal() heuristic for detecting multi-step goals
"""

import asyncio
import json
import time
from dataclasses import dataclass, field

from charlie.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class AgentResult:
    """Result of an agent's goal execution."""
    success: bool
    goal: str
    agent_name: str
    summary: str = ""
    results: list = field(default_factory=list)
    duration_seconds: float = 0.0
    replan_count: int = 0


class AgentRegistry:
    """Manifest-driven agent registry. Loads agents from charlie/agents/."""

    _brain_ref = None

    def __init__(self, agents_dir: str = "charlie/agents", brain=None, learning_tracker=None):
        AgentRegistry._brain_ref = brain
        from charlie.brain.agent_loader import AgentLoader
        from charlie.brain.agent_router import AgentRouter

        self.loader = AgentLoader(agents_dir=agents_dir)
        self.router = AgentRouter(brain=brain, learning_tracker=learning_tracker)
        self._specs: dict = {}
        self._load()

    def _load(self):
        """Load all agents from disk."""
        specs = self.loader.load_all()
        self._specs = {s.name: s for s in specs}
        logger.info("agent_registry_loaded | agents=%d", len(self._specs))

    def route(self, query: str) -> str:
        """Route a query to the best agent name."""
        specs = list(self._specs.values())
        if not specs:
            return "system"

        # Check for @agent override
        forced = self.router.parse_force_agent(query)
        if forced and forced in self._specs:
            return forced

        # Keyword fallback
        agents = self.router._keyword_route(query, specs)
        return agents[0] if agents else "system"

    def get_agent(self, name: str):
        """Get an agent spec by name."""
        return self._specs.get(name)

    def list_agents(self) -> list:
        """List all loaded agent specs."""
        return list(self._specs.values())

    def register(self, spec):
        """Register a new agent at runtime."""
        self._specs[spec.name] = spec

    def unregister(self, name: str) -> bool:
        """Unregister an agent."""
        if name in self._specs:
            del self._specs[name]
            return True
        return False

    def reload(self):
        """Reload all agents from disk."""
        self._load()


class Orchestrator:
    """
    Multi-agent coordinator with goal decomposition and result merging.

    Flow:
    1. Decompose complex goals into sub-tasks (LLM-driven)
    2. Route each sub-task to the best-fit specialist agent
    3. Execute agents in parallel (independent) or sequential (dependent)
    4. Merge results into a unified response
    """

    def __init__(self, brain, agent_registry: AgentRegistry | None = None):
        self.brain = brain
        self.registry = agent_registry or AgentRegistry(brain=brain)

    async def route_goal(self, goal: str) -> str:
        """Route a goal to the best agent or decompose if complex."""
        if is_complex_goal(goal):
            return await self._coordinate(goal)
        agent_name = self.registry.route(goal)
        return await self._execute_agent(agent_name, goal)

    async def _coordinate(self, goal: str) -> str:
        """Multi-agent coordination: decompose, dispatch, merge."""
        brain = AgentRegistry._brain_ref
        self._emit_status(brain, "decomposing", goal)

        sub_tasks = await self._decompose_goal_llm(goal)
        if not sub_tasks:
            sub_tasks = self._decompose_goal_heuristic(goal)

        if len(sub_tasks) <= 1:
            agent_name = self.registry.route(goal)
            self._emit_status(brain, "executing", goal, agent_name, 1)
            result = await self._execute_agent(agent_name, goal)
            self._emit_status(brain, "idle", goal)
            return result

        # Route each sub-task to best agent
        agent_tasks = []
        for task in sub_tasks:
            desc = task.get("description", goal)
            agent_name = task.get("agent") or self.registry.route(desc)
            agent_tasks.append((agent_name, desc))

        self._emit_status(brain, "executing", goal, "multi", len(agent_tasks))

        # Execute in parallel
        results = await asyncio.gather(
            *[self._execute_agent(name, desc) for name, desc in agent_tasks],
            return_exceptions=True,
        )

        # Merge results
        successful = [str(r) for r in results if not isinstance(r, Exception)]
        failed = [str(r) for r in results if isinstance(r, Exception)]

        merged = await self._merge_results(goal, successful, [t[1] for t in agent_tasks])

        self._emit_status(brain, "idle", goal)
        logger.info("coordinator_done | sub_tasks=%d | merged=%d | failed=%d",
                     len(agent_tasks), len(successful), len(failed))
        return merged

    def _emit_status(self, brain, status, goal, agent=None, count=0):
        if brain and hasattr(brain, "status_q") and brain.status_q:
            try:
                content = {"status": status, "goal": goal[:100]}
                if agent:
                    content["agent"] = agent
                if count:
                    content["active_agents"] = count
                if status == "idle":
                    content["active_agents"] = 0
                brain._safe_put(brain.status_q, {"type": "ORCHESTRATOR_UPDATE", "content": content})
            except Exception as e:
                logger.debug("emit_status_failed | %s", e)

    async def _decompose_goal_llm(self, goal: str) -> list[dict]:
        """LLM-driven goal decomposition into sub-tasks with agent routing."""
        llm = getattr(self.brain, "llm_client", None)
        if not llm:
            return []

        agents = self.registry.list_agents()
        agent_names = ", ".join(s.name for s in agents)

        prompt = (
            f"Break this goal into 2-4 concrete sub-tasks. Each sub-task should be "
            f"assigned to one of these agents: [{agent_names}].\n\n"
            f'Goal: "{goal}"\n\n'
            f'Respond with ONLY a JSON array like:\n'
            f'[{{"description": "search for X", "agent": "research"}}, '
            f'{{"description": "write summary of X", "agent": "writer"}}]\n'
            f'If the goal is simple (1 step), return an empty array [].'
        )

        try:
            messages = [{"role": "user", "content": prompt}]
            response = await llm.complete(messages, task_type="chat")

            # Extract JSON array from response
            text = response.content.strip()
            start = text.find("[")
            end = text.rfind("]") + 1
            if start >= 0 and end > start:
                tasks = json.loads(text[start:end])
                if isinstance(tasks, list) and len(tasks) > 1:
                    logger.info("llm_decompose | sub_tasks=%d", len(tasks))
                    return tasks
        except Exception as e:
            logger.debug("llm_decompose_failed | %s", e)

        return []

    def _decompose_goal_heuristic(self, goal: str) -> list[dict]:
        """Fallback keyword-based decomposition when LLM is unavailable."""
        steps = []
        gl = goal.lower()
        if any(k in gl for k in ("research", "find", "search", "look")):
            steps.append({"description": f"Research: {goal[:80]}", "agent": "research"})
        if any(k in gl for k in ("summarize", "write", "report", "save")):
            steps.append({"description": f"Write summary of: {goal[:80]}", "agent": "writer"})
        if any(k in gl for k in ("send", "notify", "email", "gmail")):
            steps.append({"description": f"Send notification about: {goal[:80]}", "agent": "comms"})
        return steps if steps else [{"description": goal, "agent": None}]

    async def _merge_results(self, goal: str, results: list[str], sub_tasks: list[str]) -> str:
        """Merge multiple agent results into a unified response using LLM."""
        if len(results) == 1:
            return results[0]

        llm = getattr(self.brain, "llm_client", None)
        if not llm:
            return "\n\n---\n\n".join(results)

        parts = []
        for i, (task, result) in enumerate(zip(sub_tasks, results)):
            parts.append(f"Sub-task {i+1}: {task}\nResult: {result[:500]}")

        prompt = (
            f'The user asked: "{goal}"\n\n'
            f'Multiple agents completed sub-tasks. Merge into one coherent response:\n\n'
            f'{"\n\n".join(parts)}\n\n'
            f'Merged response (concise, no redundancy):'
        )

        try:
            messages = [{"role": "user", "content": prompt}]
            response = await llm.complete(messages, task_type="chat")
            return response.content.strip()
        except Exception as e:
            logger.debug("merge_failed | %s", e)
            return "\n\n---\n\n".join(results)

    async def _execute_agent(self, agent_name: str, task: str, task_chain: list = None) -> str:
        """Execute a task using the specified agent."""
        spec = self.registry.get_agent(agent_name)
        if not spec:
            return f"Agent '{agent_name}' not found"

        from charlie.brain.agent_runtime import AgentRuntime
        runtime = AgentRuntime(self.brain)
        result = await runtime.execute(spec, task, task_chain=task_chain)
        return result.summary

    async def execute_goal(self, goal: str, source: str = "local") -> AgentResult:
        """
        Backward-compatible entry point used by autonomy_loop.

        Delegates to route_goal and wraps the result in AgentResult
        for callers that expect the old interface.
        """
        start_time = time.time()
        agent_name = self.registry.route(goal)

        try:
            summary = await self.route_goal(goal)
            return AgentResult(
                success=True,
                goal=goal,
                agent_name=agent_name,
                summary=summary,
                duration_seconds=time.time() - start_time,
            )
        except Exception as e:
            logger.error("orchestrator_execute_failed | %s", e)
            return AgentResult(
                success=False,
                goal=goal,
                agent_name=agent_name,
                summary=f"Execution failed: {e}",
                duration_seconds=time.time() - start_time,
            )


def is_complex_goal(goal: str) -> bool:
    """Heuristic: detect if a goal requires multi-step agent execution."""
    indicators = [
        " and then ", " first ", " after that ", " also ",
        " then ", " finally ",
        "research", "analyze", "compare", "summarize",
        "create a report", "send me", "notify",
        "find all", "look up", "check if", "write a",
        "automate", "schedule", "monitor",
    ]
    goal_lower = goal.lower()
    score = sum(1 for k in indicators if k in goal_lower)

    if len(goal.split()) > 12:
        score += 1

    if goal.count(".") > 1 or goal.count("\n") > 0:
        score += 1

    if "?" in goal and len(goal.split("?")) > 2:
        score += 1

    return score >= 2
