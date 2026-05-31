from enum import Enum


class RiskTier(Enum):
    """Canonical, single source of truth for tool risk tiers.

    Every component that classifies or compares risk tiers MUST use this enum
    (Req 3.6). The integer module constants below are kept only as thin
    backward-compatibility aliases derived from this enum.
    """

    TIER_0 = 0  # READ-ONLY — no confirmation
    TIER_1 = 1  # MEDIUM-RISK — verbal/Telegram "Yes" required
    TIER_2 = 2  # HIGH-RISK — explicit confirmation + 10s countdown
    TIER_3 = 3  # DESTRUCTIVE — typed "CONFIRM DELETE" required


# Backward-compatibility constants — thin aliases derived from the canonical
# enum so existing integer-based imports keep working without duplicating the
# tier values.
TIER_0 = RiskTier.TIER_0.value
TIER_1 = RiskTier.TIER_1.value
TIER_2 = RiskTier.TIER_2.value
TIER_3 = RiskTier.TIER_3.value

# Sentinel: tool requires user confirmation before execution
CONFIRMATION_PENDING = "PENDING_CONFIRMATION"


def risk_tier(level):
    """Decorator. Attaches tier metadata to any tool function."""
    # Handle both int and enum values
    if hasattr(level, "value"):
        level_value = level.value
    else:
        level_value = int(level)

    def decorator(func):
        func._risk_tier = level_value
        func._risk_tier_name = {
            0: "READ-ONLY",
            1: "MEDIUM-RISK",
            2: "HIGH-RISK",
            3: "DESTRUCTIVE",
        }[level_value]
        return func

    return decorator


def get_tool_tier(tool_func) -> RiskTier:
    """Extract the risk tier from a tool function.

    Reads the integer tier stored by the ``risk_tier`` decorator and by the
    ``@tool`` decorator (both set ``func._risk_tier = <int>``) and maps it back
    to the canonical :class:`RiskTier` enum.

    Fails closed: when a tool function has no resolvable ``_risk_tier`` (or the
    stored value is not a valid tier), it resolves to ``RiskTier.TIER_3`` — the
    most restrictive tier — so an unknown tool is never silently under-gated
    (Req 3.5).
    """
    if hasattr(tool_func, "_risk_tier"):
        try:
            return RiskTier(tool_func._risk_tier)
        except ValueError:
            # Stored tier is out of range — treat as unknown/most restrictive.
            return RiskTier.TIER_3
    # Default to the most restrictive tier (TIER_3) when no tier is declared.
    return RiskTier.TIER_3
