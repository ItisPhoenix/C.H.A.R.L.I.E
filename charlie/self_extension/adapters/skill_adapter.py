"""Skill lifecycle adapter for managing and executing reusable procedures."""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, List, Optional

from charlie.extensions.skills import format_skill_block, parse_skill_md
from charlie.self_extension.models import ExtensionKind
from charlie.self_extension.registry import ExtensionEntry, ExtensionRegistry

logger = logging.getLogger("charlie.self_extension.skill_adapter")

_DEFAULT_SKILLS_DIR = Path("data/skills")
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
        self._repair_pending_review_tokens()

    def _repair_pending_review_tokens(self) -> None:
        """Rotate legacy redacted review IDs and resubmit those pending buttons."""
        list_entries = getattr(self._registry, "list", None)
        register = getattr(self._registry, "register", None)
        if not callable(list_entries) or not callable(register):
            return
        for entry in list_entries():
            metadata = dict(entry.metadata or {})
            if (
                not entry.extension_id.startswith("skill_candidate_")
                or entry.enabled
                or metadata.get("status") != "pending"
                or re.fullmatch(r"[a-f0-9]{16}", str(metadata.get("review_token") or ""))
            ):
                continue
            metadata["review_token"] = uuid.uuid4().hex[:16]
            metadata["review_submitted"] = False
            entry.metadata = metadata
            try:
                register(entry)
            except Exception:
                logger.warning("Could not repair a pending skill review token", exc_info=True)

    def _candidate_path(self, name: str, content_hash: str) -> Path:
        return self._skills_dir / ".candidates" / name / content_hash

    def _candidate_entry(self, name: str, content_hash: str) -> Optional[ExtensionEntry]:
        return self._registry.get(f"skill_candidate_{name}_{content_hash}")

    def list_skill_candidates(self) -> List[dict]:
        """Return inspectable candidate records; candidates are never capabilities."""
        candidates = []
        for entry in self._registry.list():
            if not entry.extension_id.startswith("skill_candidate_"):
                continue
            path = Path(entry.source)
            try:
                content_bytes = path.read_bytes()
                content = content_bytes.decode("utf-8")
                matches_hash = hashlib.sha256(content_bytes).hexdigest() == entry.content_hash
            except OSError:
                content, matches_hash = None, False
            candidates.append({
                "name": entry.name,
                "content_hash": entry.content_hash,
                "status": entry.metadata.get("status", "pending"),
                "enabled": entry.enabled,
                "review_token": entry.metadata.get("review_token", ""),
                "review_submitted": bool(entry.metadata.get("review_submitted", False)),
                "content": content,
                "matches_hash": matches_hash,
            })
        return candidates

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
        """Persist an inspectable, instructions-only candidate without registering it."""
        if not _SKILL_NAME_RE.fullmatch(name):
            return SkillAdapterResult(False, "Invalid skill candidate name.", name)
        from charlie.tools import _contains_sensitive_memory_content

        if _contains_sensitive_memory_content(raw_text):
            return SkillAdapterResult(
                False,
                "Skill candidates cannot contain credentials, payment data, or government IDs.",
                name,
            )
        try:
            manifest = parse_skill_md(raw_text)
            if manifest.name != name:
                raise ValueError("frontmatter name must match the candidate name")
            if manifest.scripts:
                raise ValueError("skill candidates may contain instructions only; scripts are rejected")
        except Exception as exc:
            return SkillAdapterResult(False, f"Invalid skill candidate: {exc}", name)

        content_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        candidate_dir = self._candidate_path(name, content_hash)
        candidate_file = candidate_dir / "SKILL.md"
        try:
            candidate_dir.mkdir(parents=True, exist_ok=True)
            candidate_file.write_bytes(raw_text.encode("utf-8"))
        except OSError as exc:
            return SkillAdapterResult(False, f"Failed to persist skill candidate: {exc}", name)

        entry = ExtensionEntry(
            extension_id=f"skill_candidate_{name}_{content_hash}",
            name=name,
            kind=ExtensionKind.SKILL,
            source=str(candidate_file),
            content_hash=content_hash,
            enabled=False,
            declared_tools=[],
            metadata={
                "status": "pending",
                "instructions_only": True,
                "review_token": uuid.uuid4().hex[:16],
                "review_submitted": False,
            },
            verification_status="pending_approval",
        )
        self._registry.register(entry)
        return SkillAdapterResult(
            True,
            f"Skill candidate '{name}' staged for owner review.",
            name,
            content_hash=content_hash,
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
        """Record successful Telegram API submission so restart does not resend it."""
        entry = self._candidate_entry(name, content_hash)
        if entry is None or entry.metadata.get("status") != "pending":
            return False
        entry.metadata["review_submitted"] = True
        self._registry.register(entry)
        return True

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
        """Activate only the candidate whose complete content hash was approved."""
        entry, error = self._resolve_candidate(name, content_hash)
        if error:
            return SkillAdapterResult(False, error, name)
        assert entry is not None
        if entry.metadata.get("status") != "pending" or entry.enabled:
            return SkillAdapterResult(False, "Skill candidate is not pending approval.", name)

        candidate_text = Path(entry.source).read_bytes().decode("utf-8")
        active_entry = self._registry.get(f"skill_{name}")
        skill_file = self._skills_dir / name / "SKILL.md"
        previous_text = None
        previous_enabled = False
        if active_entry is not None:
            if not skill_file.is_file():
                return SkillAdapterResult(False, "Installed skill content is unavailable; approval stopped.", name)
            previous_bytes = skill_file.read_bytes()
            previous_text = previous_bytes.decode("utf-8")
            previous_hash = hashlib.sha256(previous_bytes).hexdigest()
            if previous_hash[:16] != active_entry.content_hash:
                return SkillAdapterResult(False, "Installed skill content hash mismatch; approval stopped.", name)
            previous_enabled = active_entry.enabled
            backup_path = self._candidate_path(name, content_hash) / "previous.SKILL.md"
            try:
                backup_path.write_bytes(previous_bytes)
            except OSError as exc:
                return SkillAdapterResult(False, f"Could not preserve prior skill version: {exc}", name)
        else:
            previous_hash = ""
            backup_path = None

        entry.metadata.update({
            "status": "approval_in_progress",
            "approved_skill_hash": content_hash,
            "previous_skill_hash": previous_hash,
            "previous_skill_path": str(backup_path) if backup_path else "",
            "previous_skill_enabled": previous_enabled,
        })
        self._registry.register(entry)
        result = self.save_skill(name=name, raw_text=candidate_text)
        if not result.success:
            if previous_text is not None:
                self.save_skill(name=name, raw_text=previous_text)
                if not previous_enabled:
                    self.set_enabled(name, False)
            else:
                self.remove_skill(name)
            entry.metadata["status"] = "pending"
            self._registry.register(entry)
            return SkillAdapterResult(False, result.message, name)

        entry.metadata["status"] = "approved"
        self._registry.register(entry)
        return SkillAdapterResult(True, f"Skill candidate '{name}' approved and activated.", name)

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
                self._capability_index.unregister_capability(ext_id)
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
            self._capability_index.unregister_capability(ext_id)

        return SkillAdapterResult(
            success=True,
            message=f"Skill '{name}' removed successfully.",
            skill_name=name,
        )
