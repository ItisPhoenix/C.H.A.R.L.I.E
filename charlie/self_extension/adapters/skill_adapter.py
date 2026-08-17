"""Skill lifecycle adapter for managing and executing reusable procedures."""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from charlie.extensions.skills import parse_skill_md
from charlie.self_extension.models import ExtensionKind
from charlie.self_extension.registry import ExtensionEntry, ExtensionRegistry

logger = logging.getLogger("charlie.self_extension.skill_adapter")

_DEFAULT_SKILLS_DIR = Path("data/skills")


class SkillAdapterResult:
    def __init__(self, success: bool, message: str, skill_name: str, tools: Optional[List[str]] = None):
        self.success = success
        self.message = message
        self.skill_name = skill_name
        self.tools = tools or []


class SkillAdapter:
    """Manages reusable SKILL.md files, registration, and capability exposure."""

    def __init__(
        self,
        skills_dir: Optional[Path] = None,
        registry: Optional[ExtensionRegistry] = None,
        capability_index: Optional[Any] = None,
    ) -> None:
        self._skills_dir = skills_dir or _DEFAULT_SKILLS_DIR
        self._registry = registry or ExtensionRegistry(capability_index=capability_index)
        self._capability_index = capability_index

    def save_skill(self, name: str, raw_text: str) -> SkillAdapterResult:
        """Parse, validate, persist, and register a new or updated skill."""
        try:
            manifest = parse_skill_md(raw_text)
        except Exception as e:
            return SkillAdapterResult(
                success=False,
                message=f"Invalid SKILL.md format: {e}",
                skill_name=name,
            )

        content_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:16]
        skill_dir = self._skills_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"

        try:
            skill_file.write_text(raw_text, encoding="utf-8")
        except Exception as e:
            return SkillAdapterResult(
                success=False,
                message=f"Failed to write skill file: {e}",
                skill_name=name,
            )

        ext_id = f"skill_{name}"
        entry = ExtensionEntry(
            extension_id=ext_id,
            name=name,
            kind=ExtensionKind.SKILL,
            source=str(skill_file),
            content_hash=content_hash,
            enabled=True,
            declared_tools=manifest.scripts,
            metadata={"description": manifest.description, "author": "user"},
        )
        self._registry.register(entry)

        # Register in capability index
        if self._capability_index:
            try:
                from charlie.capabilities import CapabilityDescriptor, CapabilityOperation

                ops = {}
                for s in manifest.scripts:
                    op_id = f"skill.{name}.{s.replace('.', '_')}"
                    ops[s] = CapabilityOperation(
                        id=op_id,
                        name=s,
                        description=f"[{name}] bundled script",
                        parameters_schema={"type": "object"},
                        risk_class="reversible",
                    )

                desc = CapabilityDescriptor(
                    id=ext_id,
                    name=name,
                    description=manifest.description or f"Reusable procedure '{name}'",
                    owner="extensions",
                    provenance="extension",
                    operations=ops,
                    availability_check=lambda: True,
                )
                self._capability_index.register_capability(desc)
            except Exception as e:
                logger.warning("Failed to register capability for skill %s: %s", name, e)

        return SkillAdapterResult(
            success=True,
            message=f"Skill '{name}' saved and registered successfully.",
            skill_name=name,
            tools=manifest.scripts,
        )

    def set_enabled(self, name: str, enabled: bool) -> SkillAdapterResult:
        """Enable or disable a registered skill."""
        ext_id = f"skill_{name}"
        ok = self._registry.set_enabled(ext_id, enabled)
        if not ok:
            return SkillAdapterResult(success=False, message=f"Skill '{name}' not found in registry.", skill_name=name)

        if self._capability_index:
            if not enabled:
                self._capability_index._capabilities.pop(ext_id, None)
            else:
                # Re-read and re-register
                skill_file = self._skills_dir / name / "SKILL.md"
                if skill_file.exists():
                    self.save_skill(name, skill_file.read_text(encoding="utf-8"))

        return SkillAdapterResult(
            success=True,
            message=f"Skill '{name}' {'enabled' if enabled else 'disabled'}.",
            skill_name=name,
        )

    def remove_skill(self, name: str) -> SkillAdapterResult:
        """Remove a skill from disk and unregister from registry."""
        ext_id = f"skill_{name}"
        self._registry.unregister(ext_id)

        skill_dir = self._skills_dir / name
        if skill_dir.exists():
            try:
                shutil.rmtree(skill_dir)
            except Exception as e:
                logger.warning("Failed to delete skill directory %s: %s", skill_dir, e)

        if self._capability_index:
            self._capability_index._capabilities.pop(ext_id, None)

        return SkillAdapterResult(
            success=True,
            message=f"Skill '{name}' removed successfully.",
            skill_name=name,
        )
