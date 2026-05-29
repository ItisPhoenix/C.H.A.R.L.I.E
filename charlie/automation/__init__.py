"""C.H.A.R.L.I.E. — Autonomous Research & Automation Engine"""
from charlie.automation.autonomy_loop import AutonomyLoop
from charlie.automation.event_router import EventRouter
from charlie.automation.learning_tracker import LearningTracker
from charlie.automation.models import AutomationRule, Event, Outcome, Prediction
from charlie.automation.risk_gate import RiskGate
from charlie.automation.rule_engine import RuleEngine

__all__ = [
    "Event", "AutomationRule", "Outcome", "Prediction",
    "EventRouter", "RuleEngine", "AutonomyLoop", "RiskGate", "LearningTracker",
]
