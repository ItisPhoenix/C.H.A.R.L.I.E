"""Authorization and security policy guard for self-extension requests."""

from __future__ import annotations

import logging

from charlie.self_extension.models import ExtensionKind, ExtensionRequest, GuardDecision, RiskClass

logger = logging.getLogger("charlie.self_extension.guard")


class AuthorizationGuard:
    """Evaluates whether an extension request is authorized or requires explicit user approval."""

    def evaluate(self, request: ExtensionRequest) -> GuardDecision:
        """Evaluate an extension request against authorization and safety policies."""
        kind = request.classification.kind if request.classification else ExtensionKind.CODE_SMALL

        # Rule 1: Spontaneous self-modification is strictly blocked without explicit approval
        if not request.explicit_user_request:
            return GuardDecision(
                is_authorized=False,
                requires_approval=True,
                reason=(
                    "Spontaneous self-modification detected: Charlie cannot modify its own source "
                    "or architecture without explicit human approval."
                ),
                risk_class=RiskClass.DANGEROUS,
            )

        # Rule 2: Large architecture overhauls always require explicit confirmation
        if kind == ExtensionKind.ARCHITECTURE_LARGE:
            return GuardDecision(
                is_authorized=False,
                requires_approval=True,
                reason="Large architecture overhaul requires human confirmation and impact review.",
                risk_class=RiskClass.CRITICAL,
            )

        # Rule 3: External dependency additions or system-level mutations require approval
        if request.required_dependencies:
            return GuardDecision(
                is_authorized=False,
                requires_approval=True,
                reason=(
                    f"Adding new external dependencies ({request.required_dependencies}) "
                    "requires explicit approval."
                ),
                risk_class=RiskClass.DANGEROUS,
            )

        # Rule 4: Config changes for standard settings are safe
        if kind == ExtensionKind.CONFIG:
            return GuardDecision(
                is_authorized=True,
                requires_approval=False,
                reason="Configuration update authorized within standard settings schema.",
                risk_class=RiskClass.SAFE,
            )

        # Rule 5: Reusable skills/instructions are reversible and safe
        if kind == ExtensionKind.SKILL:
            return GuardDecision(
                is_authorized=True,
                requires_approval=False,
                reason="Reusable procedure registration authorized.",
                risk_class=RiskClass.REVERSIBLE,
            )

        # Rule 6: MCP tools are reversible
        if kind == ExtensionKind.MCP_TOOL:
            return GuardDecision(
                is_authorized=True,
                requires_approval=False,
                reason="MCP server integration authorized.",
                risk_class=RiskClass.REVERSIBLE,
            )

        # Rule 7: Bounded small code additions with explicit user command
        if kind == ExtensionKind.CODE_SMALL and request.explicit_user_request:
            return GuardDecision(
                is_authorized=True,
                requires_approval=False,
                reason="Explicit user-requested code extension authorized under sandbox verification.",
                risk_class=RiskClass.REVERSIBLE,
            )

        # Fallback
        return GuardDecision(
            is_authorized=False,
            requires_approval=True,
            reason="Unclassified extension request gated by default.",
            risk_class=RiskClass.DANGEROUS,
        )
