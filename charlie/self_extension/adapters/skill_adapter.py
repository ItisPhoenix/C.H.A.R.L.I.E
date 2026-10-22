"""Skill lifecycle adapter for managing and executing reusable procedures."""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
from pathlib import Path
from typing import Any, List, Optional

from charlie.extensions.skills import format_skill_block, parse_skill_md
from charlie.self_extension.models import ExtensionKind
from charlie.self_extension.registry import ExtensionEntry, ExtensionRegistry

logger = logging.getLogger("charlie.self_extension.skill_adapter")

_DEFAULT_SKILLS_DIR = Path(__file__).resolve().parents[3] / "skills"
_SKILL_NAME_RE = re.compile(r"\A[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\Z")


class SkillAdapterResult:
    def __init__(
        self,
        success: bool,
        message: str,
        skill_name: str,
        tools: Optional[List[str]] = None,
        content_hash: str = "",
    ):
        self.success = success
        self.message = message
        self.skill_name = skill_name
        self.tools = tools or []
        self.content_hash = content_hash


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

    def _candidate_path(self, name: str, content_hash: str) -> Path:
        return self._skills_dir / ".candidates" / name / content_hash

    def _candidate_entry(self, name: str, content_hash: str) -> Optional[ExtensionEntry]:
        return self._registry.get(f"skill_candidate_{name}_{content_hash}")

    def list_skill_candidates(self) -> List[dict]:
        """Hide legacy candidates so startup cannot re-submit their reviews."""
        return []

    def get_active_skill_blocks(self) -> dict[str, str]:
        """Read verified instructions from enabled installed skills only."""
        blocks = {}
        for entry in self._registry.list():
            if (
                entry.kind != ExtensionKind.SKILL
                or not entry.enabled
                or entry.extension_id != f"skill_{entry.name}"
            ):
                continue
            try:
                raw = Path(entry.source).read_bytes()
                if hashlib.sha256(raw).hexdigest()[:16] != entry.content_hash:
                    logger.warning("Skipping skill context with hash mismatch: %s", entry.name)
                    continue
                manifest = parse_skill_md(raw.decode("utf-8"))
                if manifest.name != entry.name:
                    logger.warning("Skipping skill context with name mismatch: %s", entry.name)
                    continue
                blocks[entry.name] = format_skill_block(manifest)
            except (OSError, UnicodeDecodeError, ValueError):
                logger.warning("Could not load active skill context: %s", entry.name)
        return blocks

    def stage_skill_candidate(self, name: str, raw_text: str) -> SkillAdapterResult:
        """Reject prompt-created skill candidates; manual imports use save_skill."""
        return SkillAdapterResult(
            False,
            "Prompt-created skills are disabled; import a manually authored SKILL.md through Extensions.",
            name,
        )

    def resolve_skill_candidate_review_token(
        self, token: str, expected_status: str = "pending"
    ) -> Optional[tuple[str, str]]:
        """Resolve a persisted review button token to the candidate's full hash."""
        if not re.fullmatch(r"[a-f0-9]{16}", token):
            return None
        if expected_status not in {"pending", "approved"}:
            return None
        for entry in self._registry.list():
            if (
                entry.extension_id.startswith("skill_candidate_")
                and entry.enabled is False
                and entry.metadata.get("status") == expected_status
                and entry.metadata.get("review_token") == token
            ):
                return entry.name, entry.content_hash
        return None

    def mark_skill_candidate_review_submitted(self, name: str, content_hash: str) -> bool:
        """Do not update or re-submit legacy candidate reviews."""
        return False

    def _resolve_candidate(self, name: str, content_hash: str) -> tuple[Optional[ExtensionEntry], Optional[str]]:
        if not _SKILL_NAME_RE.fullmatch(name):
            return None, "Invalid skill candidate name."
        entry = self._candidate_entry(name, content_hash)
        if entry is None or entry.content_hash != content_hash:
            return None, "Skill candidate not found for the supplied content hash."
        candidate_file = self._candidate_path(name, content_hash) / "SKILL.md"
        if Path(entry.source).resolve() != candidate_file.resolve():
            return None, "Skill candidate source path mismatch."
        try:
            content_bytes = Path(entry.source).read_bytes()
            text = content_bytes.decode("utf-8")
        except OSError:
            return None, "Skill candidate content is unavailable."
        if hashlib.sha256(content_bytes).hexdigest() != content_hash:
            return None, "Skill candidate content hash mismatch."
        try:
            manifest = parse_skill_md(text)
        except Exception as exc:
            return None, f"Skill candidate is invalid: {exc}"
        if manifest.name != name or manifest.scripts:
            return None, "Skill candidate no longer meets the instructions-only contract."
        return entry, None

    def approve_skill_candidate(self, name: str, content_hash: str) -> SkillAdapterResult:
        """Refuse activation of any persisted prompt-created candidate."""
        return SkillAdapterResult(False, "Prompt-created skill candidate approval is disabled.", name)

    def reject_skill_candidate(self, name: str, content_hash: str) -> SkillAdapterResult:
        """Reject an exact candidate hash while retaining its inactive review record."""
        entry, error = self._resolve_candidate(name, content_hash)
        if error:
            return SkillAdapterResult(False, error, name)
        assert entry is not None
        if entry.metadata.get("status") != "pending" or entry.enabled:
            return SkillAdapterResult(False, "Skill candidate is not pending review.", name)
        entry.metadata["status"] = "rejected"
        entry.verification_status = "rejected"
        self._registry.register(entry)
        return SkillAdapterResult(True, f"Skill candidate '{name}' rejected.", name)

    def disable_skill_candidate(self, name: str, content_hash: str) -> SkillAdapterResult:
        """Disable an approved update by restoring its exact prior skill version."""
        entry, error = self._resolve_candidate(name, content_hash)
        if error:
            return SkillAdapterResult(False, error, name)
        assert entry is not None
        if entry.metadata.get("status") != "approved" or entry.enabled:
            return SkillAdapterResult(False, "Skill candidate is not an approved update.", name)

        active_entry = self._registry.get(f"skill_{name}")
        skill_file = self._skills_dir / name / "SKILL.md"
        if active_entry is None or not skill_file.is_file():
            return SkillAdapterResult(False, "Approved skill is unavailable; rollback stopped.", name)
        current_bytes = skill_file.read_bytes()
        current_hash = hashlib.sha256(current_bytes).hexdigest()
        if current_hash != entry.metadata.get("approved_skill_hash"):
            return SkillAdapterResult(False, "Installed skill changed after approval; rollback stopped.", name)

        previous_path = entry.metadata.get("previous_skill_path")
        if previous_path:
            try:
                previous_bytes = Path(previous_path).read_bytes()
                previous_text = previous_bytes.decode("utf-8")
            except OSError:
                return SkillAdapterResult(False, "Prior skill version is unavailable; rollback stopped.", name)
            previous_hash = hashlib.sha256(previous_bytes).hexdigest()
            if previous_hash != entry.metadata.get("previous_skill_hash"):
                return SkillAdapterResult(False, "Prior skill version hash mismatch; rollback stopped.", name)
            restored = self.save_skill(name=name, raw_text=previous_text)
            if not restored.success:
                return SkillAdapterResult(False, restored.message, name)
            if not entry.metadata.get("previous_skill_enabled", True):
                self.set_enabled(name, False)
        else:
            self.remove_skill(name)

        entry.metadata["status"] = "disabled"
        self._registry.register(entry)
        return SkillAdapterResult(True, f"Approved skill update '{name}' disabled and prior version restored.", name)

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
            skill_file.write_bytes(raw_text.encode("utf-8"))
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
                        required_leases=("file", "terminal"),
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
        entry = self._registry.get(ext_id)
        if entry is None:
            return SkillAdapterResult(success=False, message=f"Skill '{name}' not found in registry.", skill_name=name)
        if enabled:
            try:
                content = Path(entry.source).read_bytes()
                if hashlib.sha256(content).hexdigest()[:16] != entry.content_hash:
                    return SkillAdapterResult(False, "Installed skill content hash mismatch; activation stopped.", name)
                manifest = parse_skill_md(content.decode("utf-8"))
                if manifest.name != name:
                    return SkillAdapterResult(False, "Installed skill name mismatch; activation stopped.", name)
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                return SkillAdapterResult(False, f"Installed skill is invalid; activation stopped: {exc}", name)

        ok = self._registry.set_enabled(ext_id, enabled)
        if not ok:
            return SkillAdapterResult(success=False, message=f"Skill '{name}' not found in registry.", skill_name=name)

        if self._capability_index:
            if not enabled:
                self._capability_index.unregister_capability(ext_id)
            else:
                self._registry._rehydrate_skill(ext_id, entry, self._capability_index)

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
            self._capability_index.unregister_capability(ext_id)

        return SkillAdapterResult(
            success=True,
            message=f"Skill '{name}' removed successfully.",
            skill_name=name,
        )
