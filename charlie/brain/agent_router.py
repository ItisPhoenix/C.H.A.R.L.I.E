"""
Agent Router -- LLM-powered agent selection with keyword fallback.

Routes user queries to the best-fit agent(s) using:
1. @agent_name override parsing
2. Query-level caching (5-min TTL)
3. LLM-powered intelligent routing
4. Keyword-based fallback matching
"""

import re
import time
from typing import Any

from charlie.utils.logger import get_logger

logger = get_logger(__name__)

# Routing prompt -- kept simple and direct for small models
_ROUTING_PROMPT = """You are an agent router. Given a user query and a list of available agents, select the best agent(s) to handle it.

Available agents:
{agent_list}

User query: {query}

Reply with ONLY a comma-separated list of agent names (e.g. "research,system"). Pick 1-2 agents max. If no agent fits well, say "system"."""


class AgentRouter:
    """Routes user queries to the best-fit agent using LLM or keyword matching."""

    def __init__(self, brain: Any = None, learning_tracker: Any = None):
        self.brain = brain
        self.learning = learning_tracker
        self._cache: dict[str, tuple[list[str], float]] = {}  # query -> (agent_names, timestamp)
        self._cache_ttl = 300  # 5 minutes

    async def route(
        self,
        query: str,
        available_agents: list,
        force_agent: str | None = None,
    ) -> list[str]:
        """Route a query to one or more agent names.

        1. Check for force_agent override
        2. Check cache for recent identical query
        3. Try LLM-powered routing
        4. Fall back to keyword matching
        """
        if force_agent:
            return [force_agent]

        # Check cache
        cached = self._check_cache(query)
        if cached is not None:
            logger.debug("route_cache_hit | query=%s -> %s", query[:40], cached)
            return cached

        # Try LLM routing
        try:
            agents = await self._llm_route(query, available_agents)
            if agents:
                self._cache_result(query, agents)
                return agents
        except Exception as e:
            logger.warning("llm_route_failed | %s", e)

        # Fallback: keyword matching
        agents = self._keyword_route(query, available_agents)
        self._cache_result(query, agents)
        return agents

    def parse_force_agent(self, text: str) -> str | None:
        """Extract @agent_name from text. Returns agent name or None."""
        match = re.search(r"@(\w+)", text)
        if match:
            return match.group(1).lower()
        return None

    # ------------------------------------------------------------------
    # LLM-powered routing
    # ------------------------------------------------------------------

    async def _llm_route(self, query: str, available_agents: list) -> list[str]:
        """Use LLM to select the best agent(s) for a query."""
        if not self.brain or not hasattr(self.brain, "llm_client"):
            return []

        # Build agent list description
        agent_lines = []
        for agent in available_agents:
            name = agent.name if hasattr(agent, "name") else str(agent)
            desc = agent.description if hasattr(agent, "description") else ""
            keywords = ""
            if hasattr(agent, "triggers") and isinstance(agent.triggers, dict):
                kw = agent.triggers.get("keywords", [])
                if kw:
                    keywords = f" (keywords: {', '.join(kw[:5])})"
            agent_lines.append(f"- {name}: {desc}{keywords}")

        if not agent_lines:
            return []

        prompt = _ROUTING_PROMPT.format(
            agent_list="\n".join(agent_lines),
            query=query,
        )

        messages = [
            {
                "role": "system",
                "content": "You are a precise routing assistant. Reply only with comma-separated agent names.",
            },
            {"role": "user", "content": prompt},
        ]

        response = await self.brain.llm_client.complete(
            messages,
            max_tokens=64,
            temperature=0.0,
        )

        result = self._parse_agent_names(response.content, available_agents)
        if result:
            logger.info("llm_route | query=%s -> %s", query[:40], result)
        return result

    def _parse_agent_names(self, raw: str, available_agents: list) -> list[str]:
        """Parse LLM response into valid agent names."""
        if not raw:
            return []

        # Normalise: strip whitespace, lowercase, remove quotes
        cleaned = raw.strip().lower().strip("\"'")
        # Split on comma or space
        candidates = re.split(r"[,\s]+", cleaned)

        valid_names = {(a.name if hasattr(a, "name") else str(a)).lower() for a in available_agents}

        selected = []
        for name in candidates:
            name = name.strip().strip(".")
            if not name:
                continue
            if name in valid_names and name not in selected:
                selected.append(name)

        return selected[:2]  # Cap at 2 agents

    # ------------------------------------------------------------------
    # Keyword fallback
    # ------------------------------------------------------------------

    def _keyword_route(self, query: str, available_agents: list) -> list[str]:
        """Fallback: match query keywords against agent trigger keywords.
        Uses learning scores to rank agents when multiple match."""
        query_lower = query.lower()
        matches: list[tuple[str, float]] = []

        for agent in available_agents:
            name = agent.name if hasattr(agent, "name") else str(agent)
            keywords: list[str] = []
            if hasattr(agent, "triggers") and isinstance(agent.triggers, dict):
                keywords = agent.triggers.get("keywords", [])
            if any(kw.lower() in query_lower for kw in keywords):
                # Get learning score if available
                score = 0.5
                if self.learning:
                    query_keywords = query_lower.split()[:5]
                    score = self.learning.get_score(name, query_keywords)
                matches.append((name, score))

        if not matches:
            return ["system"]

        # Sort by learning score (highest first)
        matches.sort(key=lambda x: x[1], reverse=True)
        return [name for name, _ in matches[:2]]

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    def _check_cache(self, query: str) -> list[str] | None:
        """Check if we have a cached routing decision for this query."""
        if query in self._cache:
            agents, timestamp = self._cache[query]
            if time.time() - timestamp < self._cache_ttl:
                return agents
            del self._cache[query]
        return None

    def _cache_result(self, query: str, agents: list[str]) -> None:
        """Cache a routing decision."""
        self._cache[query] = (agents, time.time())
        # Prune old entries
        if len(self._cache) > 100:
            now = time.time()
            self._cache = {k: v for k, v in self._cache.items() if now - v[1] < self._cache_ttl}

    def clear_cache(self) -> None:
        """Clear the routing cache."""
        self._cache.clear()
