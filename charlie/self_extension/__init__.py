"""Controlled Self-Extension subsystem for Charlie V1."""

from charlie.self_extension.models import (
    ExtensionClassification,
    ExtensionCheckpoint,
    ExtensionKind,
    ExtensionPlan,
    ExtensionRequest,
    ExtensionResult,
    ExtensionTransaction,
    GuardDecision,
    RiskClass,
    TransactionStatus,
)
from charlie.self_extension.classifier import ExtensionClassifier
from charlie.self_extension.guard import AuthorizationGuard
from charlie.self_extension.checkpoint import CheckpointManager
from charlie.self_extension.registry import ExtensionEntry, ExtensionRegistry
from charlie.self_extension.adapters.config_adapter import ConfigAdapter
from charlie.self_extension.adapters.skill_adapter import SkillAdapter
from charlie.self_extension.adapters.mcp_adapter import MCPAdapter
from charlie.self_extension.adapters.code_adapter import CodeAdapter
from charlie.self_extension.orchestrator import SelfExtensionOrchestrator

__all__ = [
    "ExtensionClassification",
    "ExtensionCheckpoint",
    "ExtensionKind",
    "ExtensionPlan",
    "ExtensionRequest",
    "ExtensionResult",
    "ExtensionTransaction",
    "GuardDecision",
    "RiskClass",
    "TransactionStatus",
    "ExtensionClassifier",
    "AuthorizationGuard",
    "CheckpointManager",
    "ExtensionEntry",
    "ExtensionRegistry",
    "ConfigAdapter",
    "SkillAdapter",
    "MCPAdapter",
    "CodeAdapter",
    "SelfExtensionOrchestrator",
]
