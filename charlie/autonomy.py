"""Generalized autonomy policy: risk x action x context -> an approval requirement.

Risk x action x preferences x context -> Requirement. Reuses the existing
rule sources instead of duplicating their keyword/path/injection lists:
tools.py's hard-block/gated-keyword shell checks and charlie.security.policy's
path-containment + injection-heuristic checks become RiskClass values here,
not separate architecture.

core.py's _exec_one calls evaluate() to compute gate_reason.
"""

from enum import StrEnum
from typing import Any, Dict, List, Optional, Tuple

from charlie.security import policy as security_policy
from charlie.tools import is_shell_command_blocked, is_shell_command_gated
from charlie.tools import registry as _tool_registry


class RiskClass(StrEnum):
    SAFE = "safe"
    REVERSIBLE = "reversible"
    DESTRUCTIVE = "destructive"
    IRREVERSIBLE = "irreversible"
    SECURITY_SENSITIVE = "security_sensitive"


class ActionClass(StrEnum):
    OBSERVE = "observe"
    INFORM = "inform"
    SUGGEST = "suggest"
    EXECUTE = "execute"


class Requirement(StrEnum):
    ALLOW = "allow"
    NOTIFY = "notify"
    APPROVE = "approve"
    BLOCK = "block"


_RISK_TO_REQUIREMENT: Dict[RiskClass, Requirement] = {
    RiskClass.SAFE: Requirement.ALLOW,
    RiskClass.REVERSIBLE: Requirement.ALLOW,
    RiskClass.DESTRUCTIVE: Requirement.APPROVE,
    RiskClass.IRREVERSIBLE: Requirement.BLOCK,
    RiskClass.SECURITY_SENSITIVE: Requirement.APPROVE,
}

_DESKTOP_EFFECTOR_TOOLS = frozenset({
    "desktop_click",
    "desktop_type",
    "desktop_invoke",
    "desktop_key",
    "desktop_click_at",
    "desktop_drag",
    "desktop_scroll",
    "desktop_window",
    "desktop_move_window",
    "system_control",
})


def classify_action(
    tool_name: str,
    arguments: Dict[str, Any],
    recent_external_texts: Optional[List[str]] = None,
) -> Tuple[RiskClass, str]:
    """Risk class for one tool call, mirroring core.py:_exec_one's real gate order.

    Shell hard-block/gated-keyword checks first (shell_execute only), then
    charlie.security.policy's path-containment (file_read/file_write) and
    injection heuristic (shell_execute/browser_task) -- the same precedence
    core.py already applies. Reason is "" for SAFE.
    """
    if tool_name == "shell_execute":
        command = str(arguments.get("command", ""))
        blocked = is_shell_command_blocked(command)
        if blocked:
            return RiskClass.IRREVERSIBLE, blocked
        gated = is_shell_command_gated(command)
        if gated:
            return RiskClass.DESTRUCTIVE, gated
        return RiskClass.SECURITY_SENSITIVE, "arbitrary shell commands require explicit approval"

    if tool_name in _DESKTOP_EFFECTOR_TOOLS:
        return RiskClass.SAFE, ""

    policy_result = security_policy.check_tool_call(tool_name, arguments, recent_external_texts)
    if policy_result.needs_approval:
        return RiskClass.SECURITY_SENSITIVE, policy_result.reason or ""

    from charlie.capabilities import capability_index
    op = capability_index.get_operation(tool_name)
    if op is not None and op.risk_class:
        return RiskClass(op.risk_class), ""

    default_risk = _tool_registry.get_risk_class(tool_name)
    if default_risk is not None:
        return RiskClass(default_risk), ""
    return RiskClass.SECURITY_SENSITIVE, "tool has no registered risk metadata"


def evaluate(
    tool_name: str,
    arguments: Dict[str, Any],
    ctx: Optional[Any] = None,
    prefs: Optional[Dict[str, Any]] = None,
    recent_external_texts: Optional[List[str]] = None,
) -> Tuple[Requirement, RiskClass, str]:
    """Risk x action x preferences x context -> (Requirement, RiskClass, reason).

    ctx/prefs are accepted for interface compatibility with the plan's
    signature but unused so far -- no existing rule branches on either.
    Wire in real context/preference logic only once a rule needs it.
    """
    risk, reason = classify_action(tool_name, arguments, recent_external_texts)
    return _RISK_TO_REQUIREMENT[risk], risk, reason
