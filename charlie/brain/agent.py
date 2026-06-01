"""
charlie/brain/agent.py

Agent Runtime — autonomous multi-step task execution with manifest-driven agents.

Agent Runtime — autonomous multi-step task execution with manifest-driven agents.

This module provides:
- AgentRegistry for managing agents loaded from charlie/agents/ manifests
- Orchestrator for goal routing and agent execution
- is_complex_goal() heuristic for detecting multi-step goals
"""

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

    def __init__(self, agents_dir: str = "charlie/agents", brain=None, learning_tracker=None):
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
    Orchestrates multi-step goal execution using manifest-driven agents.

    Flow:
    1. Route goal to best-fit agent via AgentRegistry
    2. Execute agent via AgentRuntime (ReAct loop with LLM + tools)
    3. Return result summary
    """

    def __init__(self, brain, agent_registry: AgentRegistry | None = None):
        self.brain = brain
        self.registry = agent_registry or AgentRegistry(brain=brain)

    async def route_goal(self, goal: str) -> str:
        """Route a goal to the best agent or decompose if complex."""
        if is_complex_goal(goal):
            # For now, route to best single agent
            # Full decomposition comes later
            agent_name = self.registry.route(goal)
            return await self._execute_agent(agent_name, goal)

        agent_name = self.registry.route(goal)
        return await self._execute_agent(agent_name, goal)

    async def _execute_agent(self, agent_name: str, task: str) -> str:
        """Execute a task using the specified agent."""
        spec = self.registry.get_agent(agent_name)
        if not spec:
            return f"Agent '{agent_name}' not found"

        from charlie.brain.agent_runtime import AgentRuntime
        runtime = AgentRuntime(self.brain)
        result = await runtime.execute(spec, task)
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
            summary = await self._execute_agent(agent_name, goal)
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

    # Long goals are usually complex
    if len(goal.split()) > 12:
        score += 1

    # Multiple sentences suggest multiple steps
    if goal.count(".") > 1 or goal.count("\n") > 0:
        score += 1

    # Question marks with multiple parts
    if "?" in goal and len(goal.split("?")) > 2:
        score += 1

    return score >= 2
