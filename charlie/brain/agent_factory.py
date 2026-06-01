"""Auto-agent creation when capability gaps are detected."""

import json
from pathlib import Path

from charlie.utils.logger import get_logger

logger = get_logger(__name__)

# Default system prompt template
DEFAULT_SYSTEM_PROMPT = """You are {name}, a specialized AI agent.
Description: {description}

Your capabilities: {tools_list}
Your skills: {skills_list}

Execute tasks efficiently and report results clearly."""


class AgentFactory:
    """Creates new agent manifests when gaps are detected."""

    def __init__(self, agents_dir: str | Path | None = None):
        self._agents_dir = Path(agents_dir or "charlie/agents")

    def create_agent(
        self,
        name: str,
        description: str,
        tools: list[str],
        skills: list[str] | None = None,
        system_prompt: str | None = None,
    ) -> dict:
        """Create a new agent manifest on disk."""
        agent_dir = self._agents_dir / name
        agent_dir.mkdir(parents=True, exist_ok=True)

        tools_list = ", ".join(tools) if tools else "general"
        skills_list = ", ".join(skills) if skills else "none"

        prompt = system_prompt or DEFAULT_SYSTEM_PROMPT.format(
            name=name,
            description=description,
            tools_list=tools_list,
            skills_list=skills_list,
        )

        manifest = {
            "name": name,
            "description": description,
            "system_prompt": prompt,
            "tools": tools,
            "skills": skills or [],
        }

        manifest_path = agent_dir / "agent.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        logger.info("agent_created | name=%s path=%s", name, manifest_path)
        return manifest

    def detect_gap(
        self, failed_keywords: list[str], existing_agents: list[str]
    ) -> dict | None:
        """Detect if a new agent is needed based on failed task keywords."""
        if not failed_keywords:
            return None

        # Keyword-to-capability mapping
        capability_map = {
            "database": {
                "name": "db_admin",
                "description": "Database administration and query optimization",
                "tools": ["run_query", "analyze_schema", "optimize_query"],
                "keywords": ["database", "sql", "query", "table", "schema", "migration"],
            },
            "devops": {
                "name": "devops",
                "description": "Infrastructure management, deployment, and CI/CD",
                "tools": ["run_command", "deploy", "check_status"],
                "keywords": ["deploy", "docker", "kubernetes", "ci", "cd", "infrastructure"],
            },
            "data": {
                "name": "data_analyst",
                "description": "Data analysis, visualization, and reporting",
                "tools": ["analyze_data", "create_chart", "generate_report"],
                "keywords": ["data", "analysis", "chart", "graph", "statistics", "report"],
            },
            "math": {
                "name": "math_solver",
                "description": "Mathematical computation and problem solving",
                "tools": ["calculate", "solve_equation", "plot_function"],
                "keywords": ["math", "calculate", "equation", "formula", "statistics"],
            },
        }

        # Check if any capability gap matches
        failed_set = set(k.lower() for k in failed_keywords)
        for cap_key, cap_info in capability_map.items():
            cap_keywords = set(cap_info["keywords"])
            overlap = failed_set & cap_keywords
            if len(overlap) >= 2 and cap_info["name"] not in existing_agents:
                logger.info(
                    "gap_detected | capability=%s overlap=%s", cap_key, overlap
                )
                return cap_info

        return None
