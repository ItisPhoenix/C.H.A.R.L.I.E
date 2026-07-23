"""SKILL.md adapter (Phase 5, adapter #2).

Parses the Claude Agent Skills format (frontmatter + Markdown body) used
across Claude Code, Cursor, and the wider Skills Hub ecosystem, so skills
authored for those tools load into Charlie too. No new execution engine:
instructions become a CONTEXT-tier block alongside MEMORY/USER/OPINIONS, and
any bundled scripts register as ordinary charlie.tools.registry tools.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

import yaml

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?\n)---\s*\n?", re.DOTALL)
_SLUG_RE = re.compile(r"[^a-zA-Z0-9_]")


@dataclass
class SkillManifest:
    """A parsed SKILL.md file."""

    name: str
    description: str
    metadata: Dict[str, Any]
    instructions: str
    scripts: List[str] = field(default_factory=list)


def parse_skill_md(text: str) -> SkillManifest:
    """Parse a SKILL.md file's YAML frontmatter and Markdown body."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError("SKILL.md missing YAML frontmatter (--- ... ---)")
    frontmatter = yaml.safe_load(match.group(1)) or {}
    if not isinstance(frontmatter, dict) or not frontmatter.get("name"):
        raise ValueError("SKILL.md frontmatter must be a mapping with a 'name' field")
    return SkillManifest(
        name=str(frontmatter["name"]),
        description=str(frontmatter.get("description", "")),
        metadata=dict(frontmatter.get("metadata") or {}),
        instructions=text[match.end():].strip(),
        scripts=[str(s) for s in (frontmatter.get("scripts") or [])],
    )


def format_skill_block(manifest: SkillManifest) -> str:
    """Render a parsed skill as a CONTEXT-tier block, matching the
    [MEMORY]/[USER]/[OPINIONS] bracket-header style _build_context_tier
    already uses in charlie/core.py."""
    return f"[SKILL: {manifest.name}]\n{manifest.instructions}"


def _slugify(path: str) -> str:
    base = os.path.splitext(os.path.basename(path))[0]
    return _SLUG_RE.sub("_", base)


def register_skill_scripts(
    registry: Any,
    manifest: SkillManifest,
    runner: Callable[[str, List[str]], str],
) -> List[str]:
    """Register each of a skill's bundled scripts as an ordinary registry
    tool -- a thin wrapper that hands off to `runner(script_path, args)`.
    Returns the registered tool names."""
    registered: List[str] = []
    for script_path in manifest.scripts:
        tool_name = f"skill_{_slugify(manifest.name)}_{_slugify(script_path)}"

        def _invoke(script_path=script_path, args: List[str] = None, **_: Any) -> str:
            return runner(script_path, list(args or []))

        registry.register_tool(
            name=tool_name,
            description=f"[{manifest.name}] Run bundled script: {script_path}",
            schema={
                "type": "object",
                "properties": {
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Command-line arguments for the script",
                    },
                },
            },
        )(_invoke)
        registered.append(tool_name)
    return registered
