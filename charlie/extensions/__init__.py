"""Extension system: shared gated-install flow for MCP/SKILL.md/OpenAPI/
plugin adapters. No new execution engine -- every adapter
still registers ordinary tools into charlie.tools.registry. This module only
provides the shared safety gate: build a provenance "Skill Card", scan its
content for red flags, and route the install/enable decision through
Brain.request_tool_approval so nothing activates silently.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional

from charlie.log_redaction import redact_sensitive_text

if TYPE_CHECKING:
    from charlie.core import Brain

# Heuristic hidden-instruction / prompt-injection phrasing. Not a security
# product -- catches common phrasing, not adversarial obfuscation.
_INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore (?:all )?(?:previous|prior|above) instructions",
        r"disregard (?:the )?(?:system prompt|previous instructions)",
        r"you are now\b",
        r"new instructions?:",
        r"reveal (?:your|the) (?:system prompt|instructions)",
        r"print (?:your|the) (?:system prompt|instructions)",
        r"do anything now",
        r"jailbreak",
    )
]

# Raw IP literals and known paste/webhook/tunnel hosts an exfiltrating tool
# might use instead of its declared API.
_SUSPICIOUS_HOST_RE = re.compile(
    r"https?://(?:\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"[\w.-]*\.(?:pastebin\.com|webhook\.site|ngrok\.io|requestbin\.com))",
    re.IGNORECASE,
)


def _scan_for_warnings(raw_text: str) -> List[str]:
    """Heuristic scan included in the owner approval record for review --
    doesn't block installation on its own."""
    warnings = []
    for pattern in _INJECTION_PATTERNS:
        m = pattern.search(raw_text)
        if m:
            warnings.append(f'Possible hidden instruction: matches "{m.group(0)}"')
    for m in _SUSPICIOUS_HOST_RE.finditer(raw_text):
        warnings.append(f"Suspicious endpoint referenced: {m.group(0)}")
    return warnings


@dataclass
class SkillCard:
    """Provenance record reviewed before an extension activates."""

    name: str
    source: str
    declared_tools: List[str]
    content_hash: str
    warnings: List[str] = field(default_factory=list)

    def describe(self) -> str:
        lines = [
            f"Extension: {self.name}",
            f"Source: {self.source}",
            f"Declared tools: {', '.join(self.declared_tools) or 'none'}",
            f"Content hash: {self.content_hash}",
        ]
        if self.warnings:
            lines.append("Warnings:")
            lines.extend(f"  - {w}" for w in self.warnings)
        return "\n".join(lines)


def build_skill_card(
    name: str, source: str, declared_tools: List[str], raw_text: str
) -> SkillCard:
    """Hash and scan an extension's raw manifest/spec text into a SkillCard."""
    content_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:16]
    return SkillCard(
        name=name,
        source=source,
        declared_tools=list(declared_tools),
        content_hash=content_hash,
        warnings=_scan_for_warnings(raw_text),
    )


async def request_extension_install(brain: "Brain", card: SkillCard) -> bool:
    """Route an LLM-tool-call-initiated extension install/enable through the
    existing HITL approval channel -- no extension activates silently. Use
    this only where the main Brain instance is reachable."""
    return await brain.request_tool_approval(
        "install_extension",
        {"command": f"{card.name} (source: {card.source})", "skill_card": card.describe()},
        f"install the '{card.name}' extension",
    )


@dataclass
class RuntimeExtension:
    """Main-process extension runtime entry with private reinstall material."""

    name: str
    kind: str
    source: str
    card: SkillCard
    raw_text: str = ""
    enabled: bool = True
    tool_names: List[str] = field(default_factory=list)
    runtime_warning: Optional[str] = None

    def snapshot(self) -> Dict[str, object]:
        safe_warnings = []
        for warning in self.card.warnings:
            if warning.startswith("Possible hidden instruction"):
                safe_warnings.append("Possible hidden instruction detected")
            elif warning.startswith("Suspicious endpoint referenced"):
                safe_warnings.append("Suspicious endpoint referenced")
            else:
                safe_warnings.append("Extension content warning")
        if self.runtime_warning:
            safe_warnings.append("Runtime extension degraded")
        return {
            "name": self.name,
            "kind": self.kind,
            "source": "mcp" if self.kind == "mcp" else redact_sensitive_text(self.source)[:500],
            "enabled": self.enabled,
            "tool_names": list(self.tool_names),
            "warnings": safe_warnings,
            "content_hash": self.card.content_hash,
        }


class ExtensionRuntimeRegistry:
    """Process-lifetime main authority for installed extension runtime state."""

    def __init__(self) -> None:
        self._entries: Dict[str, RuntimeExtension] = {}

    def get(self, name: str) -> Optional[RuntimeExtension]:
        return self._entries.get(name)

    def list(self) -> List[RuntimeExtension]:
        return list(self._entries.values())

    def record(self, entry: RuntimeExtension) -> None:
        if entry.name in self._entries:
            raise ValueError(f"Extension '{entry.name}' is already installed in main runtime.")
        self._entries[entry.name] = entry

    def remove(self, name: str) -> Optional[RuntimeExtension]:
        return self._entries.pop(name, None)

    def snapshot(self) -> List[Dict[str, object]]:
        return [entry.snapshot() for entry in self._entries.values()]


def canonical_extension_request_fingerprint(operation: str, payload: Dict[str, object]) -> str:
    """Stable identity without retaining raw extension material."""
    identity: Dict[str, object] = {
        "operation": operation,
        "name": payload.get("name"),
    }
    if operation == "install":
        raw_text = str(payload.get("raw_text", ""))
        source = str(payload.get("source", ""))
        identity.update(
            {
                "kind": payload.get("kind"),
                "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
                "raw_sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
            }
        )
    return json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str)
