"""Skill Creator — programmatically creates new skill plugin folders."""

from __future__ import annotations

import json
import os
import re
import time

from charlie.utils.logger import get_logger

logger = get_logger(__name__)


class SkillCreator:
    """Creates new skill plugin folders with validated skill.json manifests and content files."""

    def __init__(self, skills_dir: str = "charlie/skills"):
        self.skills_dir = skills_dir
        self._template_path = os.path.join(skills_dir, "_template", "skill.json")

    def create_from_dict(
        self, spec: dict, content: dict[str, str] | None = None
    ) -> str:
        """Create a skill from a dict specification.

        1. Validate required fields
        2. Fill defaults from template
        3. Write to charlie/skills/<name>/skill.json
        4. Write content files (markdown)
        5. Return the path
        """
        if not self.validate_spec(spec):
            raise ValueError("Invalid skill spec: missing required fields")

        name = spec["name"]
        skill_dir = os.path.join(self.skills_dir, name)

        try:
            os.makedirs(skill_dir, exist_ok=True)
        except OSError:
            logger.exception("skill_dir_create_failed | path=%s", skill_dir)
            raise

        # Fill defaults
        skill_json = self._fill_defaults(spec)

        # Write manifest
        manifest_path = os.path.join(skill_dir, "skill.json")
        try:
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(skill_json, f, indent=2, ensure_ascii=False)
        except OSError:
            logger.exception("manifest_write_failed | path=%s", manifest_path)
            raise

        # Write content files
        if content:
            for filename, file_content in content.items():
                content_path = os.path.join(skill_dir, filename)
                try:
                    with open(content_path, "w", encoding="utf-8") as f:
                        f.write(file_content)
                except OSError:
                    logger.exception(
                        "content_write_failed | path=%s", content_path
                    )
                    raise

        logger.info("skill_created | name=%s | path=%s", name, skill_dir)
        return skill_dir

    def create_from_nl(self, description: str) -> tuple[dict, dict[str, str]]:
        """Generate a skill spec + content from natural language description.

        Returns (spec_dict, content_dict) — does NOT write to disk.
        """
        # Sanitize and extract a slug-style name from the description
        slug = self._slugify(description)
        name = slug if slug else "unnamed_skill"

        # Build spec
        spec: dict = {
            "name": name,
            "description": description.strip(),
            "content_files": ["instructions.md"],
            "version": "1.0.0",
            "enabled": True,
            "inject_mode": "on_demand",
            "tags": self._extract_tags(description),
            "metadata": {
                "author": "user",
                "created": time.strftime("%Y-%m-%d"),
                "icon": "\U0001f4da",  # books emoji
            },
        }

        # Generate basic instructions.md
        instructions = (
            f"# {name.replace('_', ' ').title()}\n\n"
            f"## Purpose\n\n{description.strip()}\n\n"
            f"## Instructions\n\n"
            f"<!-- Add detailed instructions for CHARLIE here -->\n"
        )

        content: dict[str, str] = {"instructions.md": instructions}

        logger.info("skill_spec_generated | name=%s", name)
        return spec, content

    def validate_spec(self, spec: dict) -> bool:
        """Validate required fields in a skill spec."""
        required = ["name", "description", "content_files"]
        return all(field in spec for field in required)

    def _fill_defaults(self, spec: dict) -> dict:
        """Fill missing optional fields with defaults."""
        defaults = {
            "version": "1.0.0",
            "enabled": True,
            "inject_mode": "on_demand",
            "tags": [],
            "metadata": {
                "author": "user",
                "created": time.strftime("%Y-%m-%d"),
                "icon": "\U0001f4da",
            },
        }
        for key, value in defaults.items():
            if key not in spec:
                spec[key] = value
        return spec

    def list_skills(self) -> list[str]:
        """List all skill folder names (excluding _template)."""
        if not os.path.exists(self.skills_dir):
            return []
        return [
            d
            for d in os.listdir(self.skills_dir)
            if os.path.isdir(os.path.join(self.skills_dir, d))
            and not d.startswith("_")
        ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _slugify(text: str, max_len: int = 40) -> str:
        """Turn free-form text into a snake_case identifier."""
        text = text.lower().strip()
        # Keep only alphanumeric and spaces
        text = re.sub(r"[^a-z0-9\s]", "", text)
        words = text.split()
        slug = "_".join(words[:5])  # cap at 5 words
        return slug[:max_len] if slug else ""

    @staticmethod
    def _extract_tags(text: str) -> list[str]:
        """Pull obvious keyword tags from a description."""
        common_stop = {
            "a", "an", "the", "is", "it", "to", "for", "and", "or", "of",
            "in", "on", "with", "that", "this", "my", "me", "i", "you",
            "we", "do", "make", "create", "build", "skill", "plugin",
        }
        words = re.findall(r"[a-z]{3,}", text.lower())
        seen: set[str] = set()
        tags: list[str] = []
        for w in words:
            if w not in common_stop and w not in seen:
                seen.add(w)
                tags.append(w)
        return tags[:8]
