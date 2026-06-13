"""Agent-creation tools — expose AgentFactory / AgentCreator as @tool
functions so the LLM can propose new agents from a natural-language
description, with the user able to confirm via the existing approval
queue.

These tools only *generate* the spec / *stage* the folder; they never
auto-activate a new agent at TIER 0. The new agent is visible to the
loader on the next reload (the manifest format is AGENT.md with YAML
frontmatter, matching what ``AgentLoader`` reads).
"""

from __future__ import annotations

from charlie.brain.agent_creator import AgentCreator
from charlie.brain.agent_factory import AgentFactory
from charlie.security.tiers import RiskTier
from charlie.tools.tool_decorator import tool
from charlie.utils.logger import get_logger

logger = get_logger(__name__)

_AGENTS_DIR = "charlie/agents"


@tool(
    name="propose_new_agent",
    description=(
        "Propose a new agent from a natural-language description. Returns the "
        "spec dict (name, tools, system_prompt) and writes the agent folder "
        "to disk. The user will be asked to confirm before activation."
    ),
    category="agent",
    risk_tier=RiskTier.TIER_1,
    timeout=20,
)
def propose_new_agent(description: str) -> str:
    """Generate an agent spec from a description and write it to disk.

    Parameters
    ----------
    description : str
        Natural-language description of what the agent should do.

    Returns
    -------
    str
        A short summary of the created agent and the on-disk path.
    """
    try:
        creator = AgentCreator(agents_dir=_AGENTS_DIR)
        spec = creator.create_from_nl(description)
        path = creator.create_from_dict(spec)
        return (
            f"Proposed new agent '{spec['name']}' at {path}. "
            f"Tools: {spec.get('tools', [])}. "
            "The agent will be available after the user confirms and the loader reloads."
        )
    except Exception as e:
        logger.error("propose_new_agent_failed | %s", e)
        return f"Error proposing agent: {e}"


@tool(
    name="create_agent_from_spec",
    description=(
        "Create a new agent from a fully-specified dict (name, description, "
        "system_prompt, tools). Higher-fidelity than propose_new_agent; use "
        "when the LLM has already decided the spec."
    ),
    category="agent",
    risk_tier=RiskTier.TIER_1,
    timeout=20,
)
def create_agent_from_spec(
    name: str,
    description: str,
    system_prompt: str,
    tools_json: str = "[]",
) -> str:
    """Create an agent from explicit fields.

    Parameters
    ----------
    name : str
        Folder name for the new agent.
    description : str
        One-line description (becomes the agent's intent).
    system_prompt : str
        Body of the agent's system prompt.
    tools_json : str
        JSON-encoded list of tool names, e.g. ``["search", "read_file"]``.

    Returns
    -------
    str
        Confirmation message including the on-disk path.
    """
    import json

    try:
        tools = json.loads(tools_json) if tools_json else []
        if not isinstance(tools, list):
            return "Error: tools_json must be a JSON array of tool-name strings."
        factory = AgentFactory(agents_dir=_AGENTS_DIR)
        manifest = factory.create_agent(
            name=name,
            description=description,
            tools=tools,
            skills=[],
            system_prompt=system_prompt,
        )
        return f"Created agent '{name}' with tools {tools}. Manifest keys: {list(manifest.keys())}."
    except Exception as e:
        logger.error("create_agent_from_spec_failed | %s", e)
        return f"Error creating agent: {e}"


@tool(
    name="detect_agent_gap",
    description=(
        "Given a list of keywords that describe a recent failure pattern, "
        "suggest whether a new agent should be created and which one."
    ),
    category="agent",
    risk_tier=RiskTier.TIER_0,
    timeout=10,
)
def detect_agent_gap(failed_keywords_json: str, existing_agents_json: str = "[]") -> str:
    """Suggest a new-agent spec if a capability gap is detected.

    Parameters
    ----------
    failed_keywords_json : str
        JSON-encoded list of strings (e.g. ``["database", "sql"]``).
    existing_agents_json : str
        JSON-encoded list of existing agent folder names.

    Returns
    -------
    str
        The suggested agent spec, or a message saying no gap was detected.
    """
    import json

    try:
        failed = json.loads(failed_keywords_json) if failed_keywords_json else []
        existing = json.loads(existing_agents_json) if existing_agents_json else []
        if not isinstance(failed, list) or not isinstance(existing, list):
            return "Error: both arguments must be JSON arrays."
        factory = AgentFactory(agents_dir=_AGENTS_DIR)
        suggestion = factory.detect_gap(failed, existing)
        if suggestion is None:
            return "No capability gap detected for the given keywords."
        return (
            f"Capability gap detected. Suggested agent: name='{suggestion.get('name')}', "
            f"description='{suggestion.get('description')}', tools={suggestion.get('tools', [])}."
        )
    except Exception as e:
        logger.error("detect_agent_gap_failed | %s", e)
        return f"Error detecting gap: {e}"
