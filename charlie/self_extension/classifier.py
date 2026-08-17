"""Deterministic and semantic classifier for extension requests."""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from charlie.self_extension.models import ExtensionClassification, ExtensionKind

logger = logging.getLogger("charlie.self_extension.classifier")

# 1. Config Patterns
_CONFIG_PATTERNS = [
    re.compile(r"\b(change|set|update|configure|adjust|toggle|switch)\b.*?\b(setting|config|sensitivity|voice|volume|model|theme|provider|rate|vad)\b", re.IGNORECASE),
    re.compile(r"\b(turn\s+on|turn\s+off|enable|disable)\s+(developer\s+mode|wake\s+word|vad|audio|auto\s+memory)\b", re.IGNORECASE),
]

# 2. Skill Patterns
_SKILL_PATTERNS = [
    re.compile(r"\b(remember|learn|teach yourself|store)\s+(this\s+)?(reusable\s+)?(procedure|process|workflow|playbook|steps?|routine)\b", re.IGNORECASE),
    re.compile(r"\b(add|create|install)\s+(a\s+)?(reusable\s+)?skill\b", re.IGNORECASE),
    re.compile(r"\b(skill\.md)\b", re.IGNORECASE),
]

# 3. MCP Tool Patterns
_MCP_PATTERNS = [
    re.compile(r"\b(connect|add|register|configure|install)\s+(the\s+)?(mcp|model\s+context\s+protocol|mcp\s+server)\b", re.IGNORECASE),
    re.compile(r"\bnpx\s+.*@.*mcp\b", re.IGNORECASE),
]

# 4. Large Architecture Patterns
_ARCH_LARGE_PATTERNS = [
    re.compile(r"\b(replace|rewrite|redesign|overhaul)\s+(your\s+)?(entire\s+)?(orchestration|architecture|memory\s+subsystem|event\s+bus|runtime\s+engine)\b", re.IGNORECASE),
    re.compile(r"\b(migrate\s+all|refactor\s+entire\s+codebase)\b", re.IGNORECASE),
]

# 5. Code Small Patterns
_CODE_SMALL_PATTERNS = [
    re.compile(r"\b(add|implement|write|create|code)\s+(a\s+)?(small\s+)?(function|helper|tool|capability|module|calculator|formatter)\b", re.IGNORECASE),
    re.compile(r"\b(extend\s+yourself\s+with|add\s+to\s+yourself)\b", re.IGNORECASE),
]


class ExtensionClassifier:
    """Classifies user self-extension intent and evaluates existing capability redundancy."""

    def __init__(
        self,
        capability_index: Optional[Any] = None,
        self_knowledge: Optional[Any] = None,
    ) -> None:
        self._capability_index = capability_index
        self._self_knowledge = self_knowledge

    def _check_existing_capabilities(self, prompt: str) -> Optional[str]:
        """Check if Charlie already natively supports the requested action."""
        p_lower = prompt.lower()
        if not self._capability_index:
            try:
                from charlie.capabilities import CapabilityIndex
                self._capability_index = CapabilityIndex()
            except Exception:
                return None

        # Check existing capability operations and descriptors
        caps = getattr(self._capability_index, "_capabilities", {})
        for cap_id, desc in caps.items():
            # Check ID match
            if cap_id in p_lower:
                return cap_id
            # Check operations
            ops = getattr(desc, "operations", {})
            if isinstance(ops, dict):
                for op_id, op in ops.items():
                    op_name = getattr(op, "name", op_id)
                    if op_name in p_lower or op_id in p_lower:
                        return cap_id
            elif isinstance(ops, list):
                for op in ops:
                    op_name = getattr(op, "name", str(op))
                    if op_name in p_lower:
                        return cap_id

        return None

    def classify(self, prompt: str) -> ExtensionClassification:
        """Classify a self-extension request deterministically."""
        p = prompt.strip()
        if not p:
            return ExtensionClassification(
                kind=ExtensionKind.CODE_SMALL,
                confidence=0.1,
                reason="Empty prompt defaulted to CODE_SMALL",
            )

        # 1. Check for existing capability match
        existing_cap = self._check_existing_capabilities(p)
        if existing_cap:
            return ExtensionClassification(
                kind=ExtensionKind.CODE_SMALL,
                confidence=0.9,
                reason=f"Capability '{existing_cap}' is already registered and supported in Charlie.",
                is_already_supported=True,
                existing_capability_id=existing_cap,
            )

        # 2. Large architecture rewrite check
        for pat in _ARCH_LARGE_PATTERNS:
            if pat.search(p):
                return ExtensionClassification(
                    kind=ExtensionKind.ARCHITECTURE_LARGE,
                    confidence=0.95,
                    reason="Request involves fundamental architecture or multi-subsystem overhaul.",
                )

        # 3. Config/Settings check
        for pat in _CONFIG_PATTERNS:
            if pat.search(p):
                return ExtensionClassification(
                    kind=ExtensionKind.CONFIG,
                    confidence=0.95,
                    reason="Request matches configuration or settings parameter update.",
                )

        # 4. MCP tool check
        for pat in _MCP_PATTERNS:
            if pat.search(p):
                return ExtensionClassification(
                    kind=ExtensionKind.MCP_TOOL,
                    confidence=0.95,
                    reason="Request specifies MCP server integration or configuration.",
                )

        # 5. Skill / reusable procedure check
        for pat in _SKILL_PATTERNS:
            if pat.search(p):
                return ExtensionClassification(
                    kind=ExtensionKind.SKILL,
                    confidence=0.95,
                    reason="Request asks to remember or register a reusable workflow or procedure.",
                )

        # 6. Small code extension check
        for pat in _CODE_SMALL_PATTERNS:
            if pat.search(p):
                return ExtensionClassification(
                    kind=ExtensionKind.CODE_SMALL,
                    confidence=0.85,
                    reason="Request asks to implement a targeted code function or tool.",
                )

        # Fallback default
        return ExtensionClassification(
            kind=ExtensionKind.CODE_SMALL,
            confidence=0.6,
            reason="Generic extension request mapped to code_small.",
        )
