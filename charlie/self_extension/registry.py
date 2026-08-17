from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from charlie.self_extension.models import ExtensionKind

logger = logging.getLogger("charlie.self_extension.registry")

_DEFAULT_MANIFEST_PATH = Path("data/extensions.json")
_SECRET_KEYS = frozenset({"api_key", "token", "secret", "password", "auth", "private_key", "key"})


@dataclass
class RehydrationReport:
    """Outcome of manifest rehydration on startup."""

    restored: int = 0
    skipped_disabled: int = 0
    failed: int = 0
    details: List[Dict[str, Any]] = field(default_factory=list)


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

    def rehydrate(
        self,
        capability_index: Any,
        code_worker_module: Any = None,
        mcp_client: Any = None,
        tool_registry: Any = None,
    ) -> RehydrationReport:
        """Restore capabilities from the durable manifest into *capability_index*.

        Called once on new orchestrator/process startup so extensions registered in
        a previous run become available again without re-applying them.

        Rules:
          - Disabled entries are skipped (capability NOT registered).
          - CODE_SMALL: validates source file exists and content hash matches before
            registering.  Invalid → verification_status=failed, skip registration.
          - SKILL: registers a read-only capability descriptor.
          - MCP_TOOL: attempts reconnect via mcp_client if available; on failure
            marks entry verification_status=failed but does NOT remove the entry.
          - No duplicate ownership: re-registration replaces any stale descriptor.
        """
        report = RehydrationReport()

        for ext_id, entry in list(self._entries.items()):
            if not entry.enabled:
                report.skipped_disabled += 1
                continue

            kind = entry.kind
            try:
                if kind == ExtensionKind.CODE_SMALL:
                    self._rehydrate_code(ext_id, entry, capability_index, code_worker_module)
                elif kind == ExtensionKind.SKILL:
                    self._rehydrate_skill(ext_id, entry, capability_index)
                elif kind == ExtensionKind.MCP_TOOL:
                    self._rehydrate_mcp(ext_id, entry, capability_index, mcp_client, tool_registry)
                else:
                    # CONFIG / ARCHITECTURE_LARGE: no runtime capability descriptor needed
                    pass
                report.restored += 1
                report.details.append({"id": ext_id, "status": "restored"})
            except Exception as exc:
                logger.warning("Rehydration failed for '%s': %s", ext_id, exc)
                entry.verification_status = "failed"
                self._save()
                report.failed += 1
                report.details.append({"id": ext_id, "status": "failed", "error": str(exc)})

        return report

    def _rehydrate_code(
        self,
        ext_id: str,
        entry: ExtensionEntry,
        capability_index: Any,
        code_worker_module: Any,
    ) -> None:
        """Validate source file + hash, then register subprocess-backed capability."""
        from charlie.capabilities import CapabilityDescriptor, CapabilityOperation

        src_path_str = entry.metadata.get("module_path") or entry.source
        src_path = Path(src_path_str)
        if not src_path.exists():
            raise FileNotFoundError(f"Source file not found: {src_path}")

        content = src_path.read_text(encoding="utf-8").encode("utf-8")
        actual_hash = hashlib.sha256(content).hexdigest()[:16]
        if actual_hash != entry.content_hash:
            raise ValueError(
                f"Hash mismatch for '{entry.name}': expected {entry.content_hash}, got {actual_hash}"
            )

        _mp = src_path
        _fn = entry.name
        _run = None
        if code_worker_module is not None:
            _run = code_worker_module.run_worker
        else:
            try:
                from charlie.self_extension.code_worker import run_worker as _rw
                _run = _rw
            except ImportError:
                pass

        def _dispatch(**kwargs: Any) -> Any:
            if _run is None:
                raise RuntimeError("code_worker not available for rehydrated extension")
            ok, out, err = _run(module_path=_mp, func_name=_fn, test_inputs=kwargs or None)
            if not ok:
                raise RuntimeError(err)
            return out

        desc = CapabilityDescriptor(
            id=ext_id,
            name=entry.name,
            description=entry.metadata.get("description", entry.name),
            owner="extensions",
            provenance="extension",
            operations={
                entry.name: CapabilityOperation(
                    id=f"code.{entry.name}",
                    name=entry.name,
                    description=entry.metadata.get("description", entry.name),
                    parameters_schema={"type": "object"},
                    risk_class="reversible",
                    func=_dispatch,
                )
            },
            availability_check=lambda: _mp.exists(),
        )
        capability_index.register_capability(desc)

    def _rehydrate_skill(
        self,
        ext_id: str,
        entry: "ExtensionEntry",
        capability_index: Any,
    ) -> None:
        from charlie.capabilities import CapabilityDescriptor

        desc = CapabilityDescriptor(
            id=ext_id,
            name=entry.name,
            description=f"Skill: {entry.name}",
            owner="skills",
            provenance="extension",  # skills are locally installed extensions
            operations={},
            availability_check=lambda: True,
        )
        capability_index.register_capability(desc)

    def _rehydrate_mcp(
        self,
        ext_id: str,
        entry: "ExtensionEntry",
        capability_index: Any,
        mcp_client: Any,
        tool_registry: Any,
    ) -> None:
        from charlie.capabilities import CapabilityDescriptor, CapabilityOperation

        name = entry.name
        discovered: list = entry.declared_tools or []

        if mcp_client is not None:
            try:
                from charlie.mcp_client import MCPServerConfig
                meta = entry.metadata or {}
                config = MCPServerConfig(
                    name=name,
                    command=meta.get("command", ""),
                    args=list(meta.get("args", [])),
                    env=dict(meta.get("env", {})),
                )
                mcp_client.add_server(config)
                if tool_registry is not None:
                    mcp_client.enable_server(tool_registry, name)
                discovered = [
                    t.name
                    for t in mcp_client.list_tools()
                    if getattr(t, "server_name", "") == name
                ] or discovered
            except Exception as exc:
                logger.warning("MCP rehydration connect failed for '%s': %s — using declared tools", name, exc)
                # Use declared tools but mark availability as unhealthy
                if not discovered:
                    raise

        _client = mcp_client
        _name = name

        def _avail() -> bool:
            if _client is None:
                return False
            return _client.health_check().get(_name, False)

        ops: Dict[str, Any] = {}
        for t in discovered:
            _t = t

            def _invoke(_t: str = _t, **kwargs: Any) -> Any:
                if _client is None:
                    raise RuntimeError("No MCP client")
                res = _client.call_tool(_name, _t, kwargs)
                if res.get("success"):
                    return res.get("result", "")
                raise RuntimeError(res.get("error", "MCP tool error"))

            ops[t] = CapabilityOperation(
                id=f"mcp.{name}.{t}",
                name=t,
                description=f"[{name}] MCP tool",
                parameters_schema={"type": "object"},
                risk_class="reversible",
                func=_invoke,
            )

        desc = CapabilityDescriptor(
            id=ext_id,
            name=name,
            description=f"MCP server '{name}'",
            owner="mcp",
            provenance="mcp",
            operations=ops,
            availability_check=_avail,
        )
        capability_index.register_capability(desc)

