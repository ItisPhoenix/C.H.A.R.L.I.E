"""
Agent Creator — Programmatic agent plugin folder creation.

Creates new agent directories with validated agent.json manifests.
Supports both dict-based and natural-language-based agent specifications.

Usage::

    creator = AgentCreator()
    path = creator.create_from_dict({
        "name": "helper",
        "description": "A helpful assistant agent",
        "system_prompt": "You are a helpful assistant.",
        "tools": ["search", "browser_fetch"],
    })

    spec = creator.create_from_nl("a research agent that searches the web")
    # spec is a dict -- caller confirms, then passes to create_from_dict()
"""

import json
import os
import re
import time

from charlie.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Keyword-to-tool mapping for NL parsing
# ---------------------------------------------------------------------------

_KEYWORD_TOOL_MAP: dict[str, list[str]] = {
    "search": ["search", "browser_fetch", "browser_search"],
    "research": ["search", "browser_fetch", "get_news", "read_file"],
    "find": ["search", "browser_search"],
    "look up": ["search", "browser_fetch"],
    "code": ["code_analyze", "code_search", "apply_edit"],
    "coding": ["code_analyze", "code_search", "apply_edit"],
    "programming": ["code_analyze", "code_search", "apply_edit"],
    "system": ["run_command", "get_pc_status", "get_system_status"],
    "process": ["get_active_processes", "kill_process", "run_command"],
    "app": ["open_app", "close_app"],
    "launch": ["open_app"],
    "email": ["read_gmail", "send_gmail"],
    "gmail": ["read_gmail", "send_gmail"],
    "mail": ["read_gmail", "send_gmail"],
    "calendar": ["manage_calendar"],
    "event": ["manage_calendar"],
    "schedule": ["manage_calendar"],
    "music": ["play_music", "pause_music", "get_music_status"],
    "play": ["play_music", "control_media"],
    "song": ["play_music"],
    "track": ["play_music", "skip_track"],
    "weather": ["get_weather"],
    "news": ["get_news", "news_briefing"],
    "file": ["list_files", "read_file", "write_file"],
    "files": ["list_files", "read_file", "write_file"],
    "telegram": ["send_telegram"],
    "message": ["send_telegram"],
    "vision": ["analyze_screen", "describe_image", "read_screen_text"],
    "screen": ["analyze_screen", "read_screen_text"],
    "image": ["describe_image"],
    "ocr": ["read_screen_text"],
    "notion": ["manage_notion"],
    "browser": ["open_website", "browser_search", "browser_fetch"],
    "web": ["open_website", "browser_search", "browser_fetch"],
    "calculate": ["calculate"],
    "math": ["calculate"],
    "timer": ["set_timer", "cancel_timer"],
    "alarm": ["set_alarm"],
    "stopwatch": ["start_stopwatch", "check_stopwatch", "stop_stopwatch"],
    "volume": ["set_volume"],
    "screenshot": ["screenshot_save"],
    "clipboard": ["get_pc_clipboard", "sync_clipboard"],
    "shell": ["run_command"],
    "command": ["run_command"],
    "write": ["write_file"],
    "read": ["read_file"],
    "delete": ["delete_file"],
}


# ---------------------------------------------------------------------------
# AgentCreator
# ---------------------------------------------------------------------------

class AgentCreator:
    """Creates new agent plugin folders with validated agent.json manifests."""

    def __init__(self, agents_dir: str = "charlie/agents") -> None:
        self.agents_dir = agents_dir
        self._template_path = os.path.join(agents_dir, "_template", "agent.json")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_from_dict(self, spec: dict) -> str:
        """Create an agent from a dict specification.

        1. Validate required fields
        2. Fill defaults from template
        3. Write to ``charlie/agents/<name>/agent.json``
        4. Return the agent directory path

        Raises
        ------
        ValueError
            If the spec is missing required fields.
        OSError
            If the directory or file cannot be written.
        """
        if not self.validate_spec(spec):
            raise ValueError("Invalid agent spec: missing required fields")

        name = spec["name"]
        agent_dir = os.path.join(self.agents_dir, name)

        try:
            os.makedirs(agent_dir, exist_ok=True)
        except OSError as exc:
            logger.error("agent_dir_create_failed | name=%s | %s", name, exc)
            raise

        # Fill defaults
        agent_json = self._fill_defaults(dict(spec))

        # Write manifest
        manifest_path = os.path.join(agent_dir, "agent.json")
        try:
            with open(manifest_path, "w", encoding="utf-8") as fh:
                json.dump(agent_json, fh, indent=2, ensure_ascii=False)
        except OSError as exc:
            logger.error("agent_manifest_write_failed | path=%s | %s", manifest_path, exc)
            raise

        logger.info("agent_created | name=%s | path=%s", name, agent_dir)
        return agent_dir

    def create_from_nl(self, description: str) -> dict:
        """Generate an agent spec dict from a natural language description.

        This is a simple heuristic parser -- not LLM-based.  It extracts
        tool names and keywords from the description text.

        Returns the spec dict ready for :meth:`create_from_dict`.  Does
        **not** write to disk -- the caller should confirm first.
        """
        desc_lower = description.lower().strip()

        # --- Name -----------------------------------------------------------
        # Derive a slug from the first few meaningful words
        name = self._extract_name(desc_lower)

        # --- Description ----------------------------------------------------
        # Use the raw description, capitalised
        agent_description = description.strip().capitalize()
        if not agent_description.endswith("."):
            agent_description += "."

        # --- System prompt --------------------------------------------------
        system_prompt = self._build_system_prompt(name, description)

        # --- Tools ----------------------------------------------------------
        tools = self._extract_tools(desc_lower)

        # --- Keywords / triggers --------------------------------------------
        keywords = self._extract_keywords(desc_lower)

        # --- Assemble spec --------------------------------------------------
        spec: dict = {
            "name": name,
            "description": agent_description,
            "system_prompt": system_prompt,
            "tools": tools,
            "triggers": {
                "keywords": keywords,
                "intent_description": agent_description,
            },
        }

        logger.info("nl_spec_generated | name=%s | tools=%s", name, tools)
        return spec

    def validate_spec(self, spec: dict) -> bool:
        """Validate required fields in an agent spec.

        Required fields: ``name``, ``description``, ``system_prompt``, ``tools``.
        ``tools`` must be a list.
        """
        required = ("name", "description", "system_prompt", "tools")
        for field in required:
            if field not in spec:
                logger.debug("validate_spec_missing | field=%s", field)
                return False
        if not isinstance(spec["tools"], list):
            logger.debug("validate_spec_tools_not_list | got %s", type(spec["tools"]).__name__)
            return False
        # Name must be a non-empty string suitable for a directory
        name = spec["name"]
        if not isinstance(name, str) or not name or not re.match(r"^[a-zA-Z0-9_-]+$", name):
            logger.debug("validate_spec_bad_name | name=%s", name)
            return False
        return True

    def list_agents(self) -> list[str]:
        """List all agent folder names (excluding folders starting with ``_``)."""
        if not os.path.exists(self.agents_dir):
            return []
        return [
            d for d in os.listdir(self.agents_dir)
            if os.path.isdir(os.path.join(self.agents_dir, d))
            and not d.startswith("_")
        ]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fill_defaults(self, spec: dict) -> dict:
        """Fill missing optional fields with sensible defaults.

        Mirrors the structure in ``charlie/agents/_template/agent.json``.
        """
        defaults: dict = {
            "version": "1.0.0",
            "enabled": True,
            "skills": [],
            "triggers": {"keywords": [], "intent_description": ""},
            "config": {
                "max_chain_depth": 8,
                "timeout_seconds": 120,
                "priority": "NORMAL",
            },
            "mcp_servers": {},
            "metadata": {
                "author": "user",
                "created": time.strftime("%Y-%m-%d"),
                "icon": "\U0001f916",  # robot face
            },
        }

        for key, value in defaults.items():
            if key not in spec:
                spec[key] = value
            elif isinstance(value, dict) and isinstance(spec[key], dict):
                # Merge nested dicts -- spec values take priority
                merged = dict(value)
                merged.update(spec[key])
                spec[key] = merged

        return spec

    def _extract_name(self, desc_lower: str) -> str:
        """Derive a short slug name from the description."""
        # Look for explicit patterns: "a/an X agent", "X assistant", "X bot"
        patterns = [
            r"(?:a|an)\s+(\w[\w_-]*)\s+(?:agent|assistant|bot|specialist)",
            r"(\w[\w_-]*)\s+(?:agent|assistant|bot|specialist)",
        ]
        for pattern in patterns:
            match = re.search(pattern, desc_lower)
            if match:
                candidate = match.group(1)
                # Skip generic words
                if candidate not in ("the", "a", "an", "new", "my", "this"):
                    return self._slugify(candidate)

        # Fallback: take first 2-3 meaningful words
        stop_words = {
            "a", "an", "the", "that", "for", "and", "or", "is", "it",
            "to", "of", "in", "on", "with", "can", "will", "do", "my",
            "new", "create", "make", "build", "i", "want", "need",
        }
        words = [w for w in re.split(r"\W+", desc_lower) if w and w not in stop_words]
        if words:
            slug = "_".join(words[:3])
            return self._slugify(slug)

        return "custom_agent"

    def _slugify(self, text: str) -> str:
        """Convert text to a valid directory-name slug."""
        slug = re.sub(r"[^a-zA-Z0-9_-]", "_", text)
        slug = re.sub(r"_+", "_", slug).strip("_")
        return slug.lower() or "custom_agent"

    def _build_system_prompt(self, name: str, description: str) -> str:
        """Build a default system prompt for the agent."""
        return (
            f"You are {name}, a specialized AI agent for C.H.A.R.L.I.E. "
            f"Your role is: {description.strip().rstrip('.')}. "
            f"Be precise, thorough, and always explain your reasoning."
        )

    def _extract_tools(self, desc_lower: str) -> list[str]:
        """Extract relevant tool names from the description using keyword mapping."""
        matched: list[str] = []
        # Sort keywords longest-first so multi-word patterns match before single-word
        sorted_keywords = sorted(_KEYWORD_TOOL_MAP.keys(), key=len, reverse=True)
        for keyword in sorted_keywords:
            if keyword in desc_lower:
                for tool in _KEYWORD_TOOL_MAP[keyword]:
                    if tool not in matched:
                        matched.append(tool)
        return matched

    def _extract_keywords(self, desc_lower: str) -> list[str]:
        """Extract trigger keywords from the description.

        Picks out meaningful words that could be used for routing.
        """
        stop_words = {
            "a", "an", "the", "that", "for", "and", "or", "is", "it",
            "to", "of", "in", "on", "with", "can", "will", "do", "my",
            "new", "create", "make", "build", "i", "want", "need",
            "agent", "assistant", "bot", "specialist", "who", "does",
            "about", "helps", "helping", "able", "uses",
        }
        words = re.split(r"\W+", desc_lower)
        keywords: list[str] = []
        for word in words:
            if word and word not in stop_words and len(word) > 2 and word not in keywords:
                keywords.append(word)
        return keywords[:10]  # Cap at 10 keywords
