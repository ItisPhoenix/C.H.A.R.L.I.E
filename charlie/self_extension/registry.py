"""Durable manifest store for installed extensions."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from charlie.self_extension.models import ExtensionKind

logger = logging.getLogger("charlie.self_extension.registry")

_DEFAULT_MANIFEST_PATH = Path("data/extensions.json")
_SECRET_KEYS = frozenset({"api_key", "token", "secret", "password", "auth", "private_key", "key"})


def _redact_metadata(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Scrub raw secrets from metadata before persisting."""
    clean: Dict[str, Any] = {}
    for k, v in meta.items():
        if any(sk in k.lower() for sk in _SECRET_KEYS):
            clean[k] = "[REDACTED]"
        elif isinstance(v, dict):
            clean[k] = _redact_metadata(v)
        else:
            clean[k] = v
    return clean


@dataclass
class ExtensionEntry:
    """Durable record of an installed extension."""

    extension_id: str
    name: str
    kind: ExtensionKind
    source: str
    content_hash: str
    enabled: bool = True
    installed_at: float = field(default_factory=time.time)
    declared_tools: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    verification_status: str = "verified"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        d["metadata"] = _redact_metadata(self.metadata)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ExtensionEntry:
        return cls(
            extension_id=str(data["extension_id"]),
            name=str(data["name"]),
            kind=ExtensionKind(data.get("kind", "code_small")),
            source=str(data.get("source", "")),
            content_hash=str(data.get("content_hash", "")),
            enabled=bool(data.get("enabled", True)),
            installed_at=float(data.get("installed_at", time.time())),
            declared_tools=list(data.get("declared_tools", [])),
            metadata=dict(data.get("metadata", {})),
            verification_status=str(data.get("verification_status", "verified")),
        )


class ExtensionRegistry:
    """Durable registry persisting installed extensions and synchronizing capabilities."""

    def __init__(
        self,
        manifest_path: Optional[Path] = None,
        capability_index: Optional[Any] = None,
    ) -> None:
        self._manifest_path = manifest_path or _DEFAULT_MANIFEST_PATH
        self._capability_index = capability_index
        self._entries: Dict[str, ExtensionEntry] = {}
        self.reload()

    def reload(self) -> None:
        """Load manifest from disk safely."""
        self._entries.clear()
        if not self._manifest_path.exists():
            return

        try:
            with open(self._manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    for eid, edict in data.items():
                        self._entries[eid] = ExtensionEntry.from_dict(edict)
        except Exception as e:
            logger.error("Failed to load extension manifest %s: %s", self._manifest_path, e)

    def _save(self) -> None:
        """Atomically persist manifest to disk."""
        self._manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._manifest_path.with_suffix(".tmp")
        try:
            serialized = {eid: entry.to_dict() for eid, entry in self._entries.items()}
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(serialized, f, indent=2)
            temp_path.replace(self._manifest_path)
        except Exception as e:
            logger.error("Failed to persist extension manifest: %s", e)
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass

    def register(self, entry: ExtensionEntry) -> None:
        """Register or update an extension entry and persist."""
        self._entries[entry.extension_id] = entry
        self._save()

    def unregister(self, extension_id: str) -> Optional[ExtensionEntry]:
        """Remove an extension entry from registry and persist."""
        entry = self._entries.pop(extension_id, None)
        if entry:
            self._save()
        return entry

    def get(self, extension_id: str) -> Optional[ExtensionEntry]:
        return self._entries.get(extension_id)

    def list(self) -> List[ExtensionEntry]:
        return list(self._entries.values())

    def set_enabled(self, extension_id: str, enabled: bool) -> bool:
        """Toggle enabled status for an extension."""
        entry = self._entries.get(extension_id)
        if not entry:
            return False
        entry.enabled = enabled
        self._save()
        return True
