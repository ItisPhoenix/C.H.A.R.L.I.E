"""
Agent Loader — Scans charlie/agents/ for agent.json manifests.

Walks the agents directory, parses each subfolder's agent.json into an
AgentSpec dataclass, validates required fields, and logs successes/failures.
Skips folders starting with ``_`` (e.g. _template).
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from charlie.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# AgentSpec dataclass
# ---------------------------------------------------------------------------

@dataclass
class AgentSpec:
    """Specification loaded from an agent.json manifest."""

    name: str
    description: str
    system_prompt: str
    tools: list[str]
    skills: list[str]
    triggers: dict  # {"keywords": [...], "intent_description": "..."}
    config: dict    # {"max_chain_depth": 8, "timeout_seconds": 120, "priority": "NORMAL"}
    version: str = "1.0.0"
    enabled: bool = True
    mcp_servers: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    path: str = ""  # Path to the agent folder


# ---------------------------------------------------------------------------
# Required manifest fields
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = ("name", "description", "system_prompt", "tools")


# ---------------------------------------------------------------------------
# AgentLoader
# ---------------------------------------------------------------------------

class AgentLoader:
    """Scans charlie/agents/ for agent.json manifests and returns AgentSpec objects."""

    def __init__(self, agents_dir: str | Path = "charlie/agents") -> None:
        self.agents_dir = Path(agents_dir)
        self._specs: dict[str, AgentSpec] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_all(self) -> list[AgentSpec]:
        """Scan *agents_dir* for folders containing ``agent.json``.

        Returns a list of valid :class:`AgentSpec` objects.  Invalid or
        unreadable manifests are logged and skipped — never raised.
        """
        if not self.agents_dir.is_dir():
            logger.warning("Agents directory does not exist: %s", self.agents_dir)
            return []

        specs: list[AgentSpec] = []
        for entry in sorted(self.agents_dir.iterdir()):
            # Skip non-directories and folders starting with _
            if not entry.is_dir() or entry.name.startswith("_"):
                continue

            manifest = entry / "agent.json"
            if not manifest.is_file():
                logger.debug("No agent.json in %s — skipping", entry.name)
                continue

            spec = self.load_single(entry)
            if spec is not None:
                specs.append(spec)

        self._specs = {s.name: s for s in specs}
        logger.info("Loaded %d agent(s) from %s", len(specs), self.agents_dir)
        return specs

    def load_single(self, agent_path: str | Path) -> AgentSpec | None:
        """Load a single agent from its folder path.

        Returns ``None`` if the manifest is missing, malformed, or invalid.
        """
        agent_path = Path(agent_path)
        manifest = agent_path / "agent.json"

        if not manifest.is_file():
            logger.warning("agent.json not found at %s", manifest)
            return None

        # -- Parse JSON --------------------------------------------------
        try:
            with open(manifest, encoding="utf-8") as fh:
                data: dict[str, Any] = json.load(fh)
        except json.JSONDecodeError as exc:
            logger.error("Invalid JSON in %s: %s", manifest, exc)
            return None
        except OSError as exc:
            logger.error("Cannot read %s: %s", manifest, exc)
            return None

        # -- Validate ----------------------------------------------------
        if not self.validate_manifest(data, str(manifest)):
            return None

        # -- Build AgentSpec ---------------------------------------------
        spec = AgentSpec(
            name=data["name"],
            description=data["description"],
            system_prompt=data["system_prompt"],
            tools=data["tools"],
            skills=data.get("skills", []),
            triggers=data.get("triggers", {}),
            config=data.get("config", {}),
            version=data.get("version", "1.0.0"),
            enabled=data.get("enabled", True),
            mcp_servers=data.get("mcp_servers", {}),
            metadata=data.get("metadata", {}),
            path=str(agent_path),
        )
        logger.info("Loaded agent '%s' from %s", spec.name, agent_path)
        return spec

    def validate_manifest(self, data: dict, path: str) -> bool:
        """Validate required fields in an ``agent.json`` dict.

        Returns ``True`` when all required fields are present and
        have the expected types.
        """
        missing = [f for f in REQUIRED_FIELDS if f not in data]
        if missing:
            logger.warning(
                "Manifest %s missing required fields: %s", path, missing
            )
            return False

        # tools must be a list
        if not isinstance(data["tools"], list):
            logger.warning(
                "Manifest %s: 'tools' must be a list, got %s",
                path,
                type(data["tools"]).__name__,
            )
            return False

        return True

    def get_specs(self) -> dict[str, AgentSpec]:
        """Return loaded specs as a ``name -> AgentSpec`` dict."""
        return dict(self._specs)

    def reload(self) -> list[AgentSpec]:
        """Reload all agents (for hot-reload support)."""
        self._specs.clear()
        return self.load_all()
