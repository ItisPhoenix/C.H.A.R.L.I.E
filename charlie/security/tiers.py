from enum import Enum


class RiskTier(Enum):
    TIER_0 = 0  # READ-ONLY — no confirmation
    TIER_1 = 1  # MEDIUM-RISK — verbal/Telegram "Yes" required
    TIER_2 = 2  # HIGH-RISK — explicit confirmation + 10s countdown
    TIER_3 = 3  # DESTRUCTIVE — typed "CONFIRM DELETE" required

# Backward compatibility constants
TIER_0 = 0  # READ-ONLY — no confirmation
TIER_1 = 1  # MEDIUM-RISK — verbal/Telegram "Yes" required
TIER_2 = 2  # HIGH-RISK — explicit confirmation + 10s countdown
TIER_3 = 3  # DESTRUCTIVE — typed "CONFIRM DELETE" required

# Sentinel: tool requires user confirmation before execution
CONFIRMATION_PENDING = "PENDING_CONFIRMATION"

def risk_tier(level):
    """Decorator. Attaches tier metadata to any tool function."""
    # Handle both int and enum values
    if hasattr(level, 'value'):
        level_value = level.value
    else:
        level_value = int(level)

    def decorator(func):
        func._risk_tier = level_value
        func._risk_tier_name = {0:"READ-ONLY", 1:"MEDIUM-RISK", 2:"HIGH-RISK", 3:"DESTRUCTIVE"}[level_value]
        return func
    return decorator


def get_tool_tier(tool_func) -> RiskTier:
    """Extracts the risk tier from a tool function."""
    if hasattr(tool_func, '_risk_tier'):
        tier_value = tool_func._risk_tier
        return RiskTier(tier_value)
    # Default to TIER_1 for safety
    return RiskTier.TIER_1
