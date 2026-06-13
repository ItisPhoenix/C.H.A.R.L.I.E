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


def _render_agent_md(manifest: dict) -> str:
    """Render an AgentSpec dict as YAML-frontmatter AGENT.md text.

    Used by both AgentFactory and AgentCreator so the loader (which reads
    ``AGENT.md`` with frontmatter, not ``agent.json``) sees the new agent
    on the next reload.
    """
    tools = manifest.get("tools") or []
    skills = manifest.get("skills") or []
    triggers = manifest.get("triggers") or {"keywords": [], "intent_description": ""}
    config = manifest.get("config") or {
        "max_chain_depth": 8,
        "timeout_seconds": 120,
        "priority": "NORMAL",
    }
    body = manifest.get("system_prompt", "").strip()
    tools_json = json.dumps(tools, ensure_ascii=False)
    skills_json = json.dumps(skills, ensure_ascii=False)
    keywords_json = json.dumps(triggers.get("keywords", []), ensure_ascii=False)
    frontmatter = (
        "---\n"
        f"name: {manifest.get('name', '')}\n"
        f"description: {json.dumps(manifest.get('description', ''), ensure_ascii=False)}\n"
        f"version: \"{manifest.get('version', '1.0.0')}\"\n"
        f"enabled: {str(bool(manifest.get('enabled', True))).lower()}\n"
        f"tools: {tools_json}\n"
        f"skills: {skills_json}\n"
        "triggers:\n"
        f"  keywords: {keywords_json}\n"
        f"  intent_description: {json.dumps(triggers.get('intent_description', ''), ensure_ascii=False)}\n"
        "config:\n"
        f"  max_chain_depth: {config.get('max_chain_depth', 8)}\n"
        f"  timeout_seconds: {config.get('timeout_seconds', 120)}\n"
        f"  priority: {config.get('priority', 'NORMAL')}\n"
        "---\n"
    )
    return frontmatter + (("\n" + body) if body else "")


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

        # Write AGENT.md (with YAML frontmatter) so the loader
        # finds the new agent on the next reload.
        manifest_path = agent_dir / "AGENT.md"
        manifest_path.write_text(_render_agent_md(manifest), encoding="utf-8")

        logger.info("agent_created | name=%s path=%s", name, manifest_path)
        return manifest

    def detect_gap(self, failed_keywords: list[str], existing_agents: list[str]) -> dict | None:
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
                logger.info("gap_detected | capability=%s overlap=%s", cap_key, overlap)
                return cap_info

        return None
