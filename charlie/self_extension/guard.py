"""Authorization and security policy guard for self-extension requests."""

from __future__ import annotations

import logging
import re

from charlie.self_extension.models import ExtensionKind, ExtensionRequest, GuardDecision, RiskClass

logger = logging.getLogger("charlie.self_extension.guard")

# The decision reason is interpolated straight into the owner's prompt by
# charlie.core (``f"Need your OK: {reason}. Yes or no?"``) and handed to
# ``on_tool_approval_request``.  For ``self_extension_proposal`` the hardened
# preview channel (``charlie.core._approval_operation_preview``) returns None, so
# nothing escapes or redacts it.  The argv is LLM-planned from natural language,
# so a prompt-injected plan could otherwise plant control characters or its own
# "Yes." inside the owner's own question.  Only the executable name survives, and
# only after being reduced to a character class that cannot carry structure.
_UNSAFE_COMMAND_CHARS = re.compile(r"[^A-Za-z0-9._+-]")
_MAX_COMMAND_TOKEN = 48


def _safe_command_token(token: str) -> str:
    """Reduce an argv element to a printable, structure-free token."""
    cleaned = _UNSAFE_COMMAND_CHARS.sub("", str(token or ""))[:_MAX_COMMAND_TOKEN]
    return cleaned or "unresolved"


class AuthorizationGuard:
    """Evaluates whether an extension request is authorized or requires explicit user approval."""

    def evaluate(self, request: ExtensionRequest) -> GuardDecision:
        """Evaluate an extension request against authorization and safety policies."""
        kind = request.classification.kind if request.classification else ExtensionKind.CODE_SMALL

        if request.requires_approval or request.planning_error:
            return self._bound(
                GuardDecision(
                    is_authorized=False,
                    requires_approval=True,
                    reason=request.planning_error or "APPROVAL_REQUIRED: extension plan needs review.",
                    risk_class=RiskClass.DANGEROUS,
                ),
                request,
            )

        # Rule 1: Spontaneous self-modification is strictly blocked without explicit approval
        if not request.explicit_user_request:
            return self._bound(
                GuardDecision(
                    is_authorized=False,
                    requires_approval=True,
                    reason=(
                        "Spontaneous self-modification detected: Charlie cannot modify its own source "
                        "or architecture without explicit human approval."
                    ),
                    risk_class=RiskClass.DANGEROUS,
                ),
                request,
            )

        # Rule 2: Large architecture overhauls always require explicit confirmation
        if kind == ExtensionKind.ARCHITECTURE_LARGE:
            return self._bound(
                GuardDecision(
                    is_authorized=False,
                    requires_approval=True,
                    reason="Large architecture overhaul requires human confirmation and impact review.",
                    risk_class=RiskClass.CRITICAL,
                ),
                request,
            )

        # Rule 3: External dependency additions or system-level mutations require approval.
        # MCP_TOOL deliberately falls through to Rule 6 instead. Rule 6 requires
        # approval too, but it also binds the decision to the exact argv, and an
        # owner cannot grant an approval that carries no digest. Pre-empting it
        # here would leave every MCP request permanently unapprovable.
        if request.required_dependencies and kind != ExtensionKind.MCP_TOOL:
            return self._bound(
                GuardDecision(
                    is_authorized=False,
                    requires_approval=True,
                    reason=(
                        "Adding new external dependencies "
                        f"({', '.join(str(item) for item in request.required_dependencies)}) "
                        "requires explicit approval."
                    ),
                    risk_class=RiskClass.DANGEROUS,
                ),
                request,
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

        # Rule 6: MCP tools launch an external server process on this host, so they
        # are never auto-authorized. The decision carries the exact argv so the
        # owner's approval is bound to one executable and argument list.
        if kind == ExtensionKind.MCP_TOOL:
            argv = request.plan.resolve_argv() if request.plan else []
            if not argv:
                return self._bound(
                    GuardDecision(
                        is_authorized=False,
                        requires_approval=True,
                        reason=(
                            "APPROVAL_REQUIRED: MCP server integration cannot be authorized "
                            "without an exact command and argument list to review."
                        ),
                        risk_class=RiskClass.DANGEROUS,
                    ),
                    request,
                )
            return self._bound(
                GuardDecision(
                    is_authorized=False,
                    requires_approval=True,
                    reason=(
                        "MCP server integration launches a new external process; "
                        f"review and approve the exact command '{_safe_command_token(argv[0])}' "
                        "before it is started."
                    ),
                    risk_class=RiskClass.DANGEROUS,
                ),
                request,
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
        return self._bound(
            GuardDecision(
                is_authorized=False,
                requires_approval=True,
                reason="Unclassified extension request gated by default.",
                risk_class=RiskClass.DANGEROUS,
            ),
            request,
        )

    @staticmethod
    def _bound(decision: GuardDecision, request: ExtensionRequest) -> GuardDecision:
        """Attach the reviewed argv to an approval-gated decision.

        A decision with no resolvable argv stays unbound, and an unbound decision
        can never satisfy :meth:`GuardDecision.binds_plan`.
        """
        if request.plan is not None:
            argv = request.plan.resolve_argv()
            if argv:
                decision.approved_argv = list(argv)
                decision.approval_binding = request.plan.approval_binding()
        return decision
