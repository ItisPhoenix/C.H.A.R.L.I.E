"""Data models for the automation engine."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import IntEnum

from charlie.security.tiers import RiskTier


class Urgency(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4
    EMERGENCY = 5


@dataclass
class Event:
    """An event from any source that may trigger automation."""

    type: str  # "email_received", "calendar_alert", "system_warning"
    source: str  # "gmail", "calendar", "system", "news", "pattern"
    data: dict = field(default_factory=dict)
    urgency: Urgency = Urgency.LOW
    timestamp: float = field(default_factory=time.time)

    def __repr__(self):
        return f"Event({self.type}, {self.source}, urgency={self.urgency.name})"


@dataclass
class AutomationRule:
    """A rule that matches events and executes actions."""

    name: str
    trigger: str  # event type to match
    condition: str  # Python expression evaluated against event.data
    action: str  # tool name or agent name
    action_args: dict = field(default_factory=dict)
    risk_tier: RiskTier = RiskTier.TIER_0
    enabled: bool = True
    description: str = ""
    priority: str = "auto"  # auto, low, medium, high, critical

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "trigger": self.trigger,
            "condition": self.condition,
            "action": self.action,
            "action_args": self.action_args,
            "risk_tier": self.risk_tier.value,
            "enabled": self.enabled,
            "description": self.description,
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, d: dict) -> AutomationRule:
        return cls(
            name=d["name"],
            trigger=d["trigger"],
            condition=d.get("condition", "True"),
            action=d["action"],
            action_args=d.get("action_args", {}),
            risk_tier=RiskTier(d.get("risk_tier", 0)),
            enabled=d.get("enabled", True),
            description=d.get("description", ""),
            priority=d.get("priority", "auto"),
        )


@dataclass
class Outcome:
    """Result of executing an automation action."""

    event_type: str
    action: str
    success: bool
    user_approved: bool = True
    user_feedback: str = ""  # "good", "bad", "neutral"
    timestamp: float = field(default_factory=time.time)


@dataclass
class Prediction:
    """A predicted user need based on patterns."""

    description: str
    confidence: float  # 0.0 to 1.0
    suggested_action: str
    suggested_args: dict = field(default_factory=dict)
