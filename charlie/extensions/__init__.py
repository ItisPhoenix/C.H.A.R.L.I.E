"""Extension system (Phase 5): shared gated-install flow for future MCP/
SKILL.md/OpenAPI/plugin adapters. No new execution engine -- every adapter
still registers ordinary tools into charlie.tools.registry. This module only
provides the shared safety gate: build a provenance "Skill Card", scan its
content for red flags, and route the install/enable decision through
Brain.request_tool_approval so nothing activates silently.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional

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
    """Heuristic scan surfaced in the approval dialog for a human to weigh --
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
    """Provenance record shown in the approval dialog before an extension activates."""

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
    this only where a live Brain instance is reachable (the normal chat tool
    loop). The web dashboard has no Brain in its process; ExtensionManager's
    propose()/confirm() below is the equivalent gate for that path -- the
    approval dialog itself is a REST round-trip within the dashboard's own
    process instead of a cross-process voice/IPC round-trip."""
    return await brain.request_tool_approval(
        "install_extension",
        {"command": f"{card.name} (source: {card.source})", "skill_card": card.describe()},
        f"install the '{card.name}' extension",
    )


@dataclass
class InstalledExtension:
    """A live extension entry -- one of the four adapter kinds, tracked so
    the web dashboard's list/enable/disable/uninstall endpoints have
    something to act on."""

    name: str
    kind: str  # "mcp" | "skill" | "openapi" | "plugin"
    source: str
    card: SkillCard
    enabled: bool = True
    tool_names: List[str] = field(default_factory=list)


class ExtensionManager:
    """In-process registry of installed extensions plus pending (proposed
    but not yet confirmed) installs -- the web dashboard's gate: propose()
    returns a SkillCard for the user to review, confirm() only then calls
    the caller-supplied installer. Not persisted across restarts; each
    process starts with an empty registry (a known gap, see
    plans/PHASE_5_plugin_skill_system.md's REST section)."""

    def __init__(self) -> None:
        self._installed: Dict[str, InstalledExtension] = {}
        self._pending: Dict[str, SkillCard] = {}

    def propose(self, card: SkillCard) -> str:
        """Stage a SkillCard for confirmation. Returns a pending_id."""
        pending_id = hashlib.sha256(
            f"{card.name}:{card.content_hash}:{len(self._pending)}".encode("utf-8")
        ).hexdigest()[:12]
        self._pending[pending_id] = card
        return pending_id

    def pop_pending(self, pending_id: str) -> Optional[SkillCard]:
        return self._pending.pop(pending_id, None)

    def record(self, ext: InstalledExtension) -> None:
        self._installed[ext.name] = ext

    def get(self, name: str) -> Optional[InstalledExtension]:
        return self._installed.get(name)

    def list(self) -> List[InstalledExtension]:
        return list(self._installed.values())

    def remove(self, name: str) -> Optional[InstalledExtension]:
        return self._installed.pop(name, None)
