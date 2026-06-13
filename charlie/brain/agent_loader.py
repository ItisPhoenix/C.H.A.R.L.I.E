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
    config: dict  # {"max_chain_depth": 8, "timeout_seconds": 120, "priority": "NORMAL"}
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

            manifest = entry / "AGENT.md"
            if not manifest.is_file():
                logger.debug("No AGENT.md in %s — skipping", entry.name)
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
        manifest = agent_path / "AGENT.md"

        if not manifest.is_file():
            logger.warning("agent.json not found at %s", manifest)
            return None

        # -- Parse YAML frontmatter from AGENT.md -------------------------
        try:
            raw = manifest.read_text(encoding="utf-8")
            data: dict[str, Any] = {}
            if raw.startswith("---"):
                parts = raw.split("---", 2)
                if len(parts) >= 3:
                    lines = parts[1].strip().split("\n")
                    current_key = None
                    current_sub: dict = {}
                    for line in lines:
                        stripped = line.strip()
                        if not stripped or stripped.startswith("#"):
                            continue
                        # Detect indented sub-keys (2+ spaces)
                        if line.startswith("  ") and ":" in stripped and current_key:
                            sk, sv = stripped.split(":", 1)
                            sk, sv = sk.strip(), sv.strip()
                            if sv.startswith("[") and sv.endswith("]"):
                                try:
                                    sv = json.loads(sv)
                                except (json.JSONDecodeError, ValueError):
                                    pass
                            elif sv.lower() == "true":
                                sv = True
                            elif sv.lower() == "false":
                                sv = False
                            elif sv.startswith('"') and sv.endswith('"'):
                                sv = sv[1:-1]
                            current_sub[sk] = sv
                            continue
                        # Flush previous sub-dict
                        if current_key and current_sub:
                            data[current_key] = current_sub
                        elif current_key and current_key not in data:
                            data[current_key] = ""
                        current_key = None
                        current_sub = {}
                        # Top-level key: value
                        if ":" in stripped:
                            k, v = stripped.split(":", 1)
                            k, v = k.strip(), v.strip()
                            if not v:
                                current_key = k
                                current_sub = {}
                                continue
                            if v.startswith("[") and v.endswith("]"):
                                try:
                                    v = json.loads(v)
                                except (json.JSONDecodeError, ValueError):
                                    pass
                            elif v.lower() == "true":
                                v = True
                            elif v.lower() == "false":
                                v = False
                            elif v.startswith('"') and v.endswith('"'):
                                v = v[1:-1]
                            data[k] = v
                    # Flush last sub-dict
                    if current_key and current_sub:
                        data[current_key] = current_sub
            if "system_prompt" not in data and raw.startswith("---"):
                data["system_prompt"] = raw.split("---", 2)[-1].strip()
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
        """Validate required fields from AGENT.md dict.

        Returns ``True`` when all required fields are present and
        have the expected types.
        """
        missing = [f for f in REQUIRED_FIELDS if f not in data]
        if missing:
            logger.warning("Manifest %s missing required fields: %s", path, missing)
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
