"""
Skill Loader — Scans charlie/skills/ for skill.json manifests and loads markdown content.

Walks the skills directory, parses each skill.json manifest, validates required
fields, and loads associated markdown content files into SkillSpec objects.
Supports hot-reload and filtering by inject_mode and tags.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from charlie.utils.logger import get_logger

logger = get_logger(__name__)

# Separator between concatenated markdown content files
_CONTENT_SEPARATOR = "\n\n---\n\n"


@dataclass
class SkillSpec:
    """Specification loaded from a skill.json manifest."""

    name: str
    description: str
    content_files: list[str]  # Filenames from manifest
    content: str = ""  # Combined markdown content loaded from files
    inject_mode: str = "on_demand"  # "always", "on_demand", "manual"
    tags: list[str] = field(default_factory=list)
    version: str = "1.0.0"
    enabled: bool = True
    metadata: dict = field(default_factory=dict)
    path: str = ""  # Path to the skill folder


class SkillLoader:
    """Scans charlie/skills/ for skill.json manifests and loads markdown content."""

    def __init__(self, skills_dir: str = "charlie/skills"):
        self.skills_dir = Path(skills_dir)
        self._specs: dict[str, SkillSpec] = {}

    def load_all(self) -> list[SkillSpec]:
        """Scan skills_dir for folders containing skill.json. Return list of SkillSpec."""
        if not self.skills_dir.is_dir():
            logger.warning("skills_dir_missing | path=%s", self.skills_dir)
            return []

        specs: list[SkillSpec] = []
        for entry in sorted(self.skills_dir.iterdir()):
            # Skip non-directories and folders starting with _
            if not entry.is_dir() or entry.name.startswith("_"):
                continue

            manifest = entry / "skill.json"
            if not manifest.is_file():
                continue

            spec = self.load_single(str(entry))
            if spec is not None:
                specs.append(spec)

        self._specs = {s.name: s for s in specs}
        logger.info(
            "skills_loaded | count=%d skills=[%s]",
            len(specs),
            ", ".join(s.name for s in specs) if specs else "none",
        )
        return specs

    def load_single(self, skill_path: str) -> SkillSpec | None:
        """Load a single skill from its folder path."""
        folder = Path(skill_path)
        manifest = folder / "skill.json"

        if not manifest.is_file():
            logger.warning("manifest_missing | path=%s", manifest)
            return None

        try:
            with open(manifest, encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as exc:
            logger.error("manifest_invalid_json | path=%s error=%s", manifest, exc)
            return None
        except OSError as exc:
            logger.error("manifest_read_error | path=%s error=%s", manifest, exc)
            return None

        if not self.validate_manifest(data, str(folder)):
            return None

        content_files = data["content_files"]
        content = self.load_content(str(folder), content_files)

        tags = data.get("tags", [])
        if not isinstance(tags, list):
            logger.warning("manifest_tags_not_list | path=%s — defaulting to []", folder)
            tags = []

        metadata = data.get("metadata", {})
        if not isinstance(metadata, dict):
            logger.warning(
                "manifest_metadata_not_dict | path=%s — defaulting to {}", folder
            )
            metadata = {}

        spec = SkillSpec(
            name=data["name"],
            description=data["description"],
            content_files=content_files,
            content=content,
            inject_mode=data.get("inject_mode", "on_demand"),
            tags=tags,
            version=data.get("version", "1.0.0"),
            enabled=data.get("enabled", True),
            metadata=metadata,
            path=str(folder),
        )

        logger.info(
            "skill_loaded | name=%s version=%s mode=%s files=%d path=%s",
            spec.name,
            spec.version,
            spec.inject_mode,
            len(spec.content_files),
            spec.path,
        )
        return spec

    def validate_manifest(self, data: dict, path: str) -> bool:
        """Validate required fields in skill.json."""
        required = ("name", "description", "content_files")
        missing = [f for f in required if f not in data]
        if missing:
            logger.warning(
                "manifest_missing_fields | path=%s missing=%s", path, missing
            )
            return False

        # Type checks
        if not isinstance(data["name"], str) or not data["name"].strip():
            logger.warning("manifest_invalid_name | path=%s", path)
            return False
        if not isinstance(data["description"], str) or not data["description"].strip():
            logger.warning("manifest_invalid_description | path=%s", path)
            return False
        if not isinstance(data["content_files"], list):
            logger.warning("manifest_invalid_content_files | path=%s", path)
            return False

        return True

    def load_content(self, skill_folder: str, content_files: list[str]) -> str:
        """Load and concatenate markdown content from content_files."""
        parts: list[str] = []
        folder = Path(skill_folder)

        for filename in content_files:
            filepath = folder / filename
            if not filepath.is_file():
                logger.warning(
                    "content_file_missing | skill=%s file=%s", folder.name, filename
                )
                continue
            try:
                text = filepath.read_text(encoding="utf-8").strip()
                if text:
                    parts.append(text)
            except OSError as exc:
                logger.warning(
                    "content_file_read_error | skill=%s file=%s error=%s",
                    folder.name,
                    filename,
                    exc,
                )

        return _CONTENT_SEPARATOR.join(parts)

    def get_specs(self) -> dict[str, SkillSpec]:
        """Return loaded specs as name -> SkillSpec dict."""
        return dict(self._specs)

    def get_skill(self, name: str) -> SkillSpec | None:
        """Get a skill by name."""
        return self._specs.get(name)

    def get_skills_by_mode(self, mode: str) -> list[SkillSpec]:
        """Get all skills with a specific inject_mode."""
        return [s for s in self._specs.values() if s.inject_mode == mode]

    def get_skills_by_tag(self, tag: str) -> list[SkillSpec]:
        """Get all skills that have a specific tag."""
        return [s for s in self._specs.values() if tag in s.tags]

    def reload(self) -> list[SkillSpec]:
        """Reload all skills (for hot-reload support)."""
        self._specs.clear()
        return self.load_all()
