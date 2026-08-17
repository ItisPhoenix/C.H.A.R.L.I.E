"""Typed request, classification, transaction, and result models for self-extension."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Dict, List, Optional


class ExtensionKind(StrEnum):
    CONFIG = "config"
    SKILL = "skill"
    MCP_TOOL = "mcp_tool"
    CODE_SMALL = "code_small"
    ARCHITECTURE_LARGE = "architecture_large"


class TransactionStatus(StrEnum):
    REQUESTED = "requested"
    CLASSIFIED = "classified"
    INSPECTING = "inspecting"
    PLANNING = "planning"
    APPROVAL_REQUIRED = "approval_required"
    CHECKPOINTING = "checkpointing"
    APPLYING = "applying"
    TESTING = "testing"
    HEALTH_CHECK = "health_check"
    RESTARTING = "restarting"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RiskClass(StrEnum):
    SAFE = "safe"
    REVERSIBLE = "reversible"
    DANGEROUS = "dangerous"
    CRITICAL = "critical"


@dataclass
class ExtensionClassification:
    """Outcome of extension request classification."""

    kind: ExtensionKind
    confidence: float = 1.0
    reason: str = ""
    is_already_supported: bool = False
    existing_capability_id: Optional[str] = None
    suggested_scope: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d


@dataclass
class ExtensionRequest:
    """Authoritative input model for a requested self-extension."""

    user_prompt: str
    request_id: str = field(default_factory=lambda: f"ext-{uuid.uuid4().hex[:8]}")
    task_id: Optional[str] = None
    classification: Optional[ExtensionClassification] = None
    plan: Optional["ExtensionPlan"] = None
    explicit_user_request: bool = True
    scope: str = "local"
    affected_capabilities: List[str] = field(default_factory=list)
    affected_files: List[str] = field(default_factory=list)
    affected_settings: Dict[str, Any] = field(default_factory=dict)
    required_dependencies: List[str] = field(default_factory=list)
    risk_class: RiskClass = RiskClass.REVERSIBLE
    requires_approval: bool = False
    requires_restart: bool = False
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["risk_class"] = self.risk_class.value
        if self.classification:
            d["classification"] = self.classification.to_dict()
        if self.plan:
            d["plan"] = self.plan.to_dict()
        return d


@dataclass
class GuardDecision:
    """Authorization evaluation outcome for an extension request."""

    is_authorized: bool
    requires_approval: bool
    reason: str
    risk_class: RiskClass = RiskClass.REVERSIBLE

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["risk_class"] = self.risk_class.value
        return d


@dataclass
class ExtensionPlan:
    """
    Validated execution plan for applying a self-extension.

    Kind-specific payload fields (code_source, mcp_name, raw_text, etc.) carry the
    actual structured content that will be applied.  These must be explicitly set by
    the planner/orchestrator — raw user_prompt is never promoted into any payload field
    automatically.
    """

    plan_id: str
    kind: ExtensionKind
    description: str
    steps: List[str] = field(default_factory=list)
    files_to_create: List[str] = field(default_factory=list)
    files_to_modify: List[str] = field(default_factory=list)
    files_to_delete: List[str] = field(default_factory=list)
    settings_updates: Dict[str, Any] = field(default_factory=dict)
    tests_to_run: List[str] = field(default_factory=list)
    rollback_strategy: str = "restore_preimages"
    requires_restart: bool = False

    # --- CODE_SMALL payload ---
    code_source: Optional[str] = None
    """Validated Python source for a CODE_SMALL extension. Never the raw user_prompt."""
    tool_name: Optional[str] = None
    """Name of the top-level callable defined in code_source."""
    test_inputs: Optional[Dict[str, Any]] = None
    """Keyword arguments to pass to the function during subprocess verification."""
    expected_output: Optional[Any] = None
    """Expected return value from the function under test_inputs."""

    # --- SKILL payload ---
    raw_text: Optional[str] = None
    """Raw SKILL.md markdown text for a SKILL extension."""

    # --- MCP_TOOL payload ---
    mcp_name: Optional[str] = None
    """Canonical name for the MCP server."""
    mcp_command: Optional[str] = None
    """Executable command used to launch the MCP server (e.g. 'npx', 'uvx')."""
    mcp_args: Optional[List[str]] = None
    """Arguments to pass to the MCP server command."""
    mcp_env: Optional[Dict[str, str]] = None
    """Environment variables for the MCP server process."""
    mcp_declared_tools: Optional[List[str]] = None
    """Tool names exposed by the MCP server for capability indexing."""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d


@dataclass
class ExtensionCheckpoint:
    """Point-in-time snapshot of affected state before mutation."""

    checkpoint_id: str
    created_at: float
    affected_files_preimage: Dict[str, bytes] = field(default_factory=dict)
    new_files_created: List[str] = field(default_factory=list)
    config_preimage: Dict[str, Any] = field(default_factory=dict)
    git_head: Optional[str] = None
    git_branch: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "created_at": self.created_at,
            "affected_files_count": len(self.affected_files_preimage),
            "new_files_count": len(self.new_files_created),
            "config_keys_count": len(self.config_preimage),
            "git_head": self.git_head,
            "git_branch": self.git_branch,
        }


@dataclass
class ExtensionTransaction:
    """Lifecycle state container for an in-flight or finished self-extension."""

    transaction_id: str
    request: ExtensionRequest
    status: TransactionStatus = TransactionStatus.REQUESTED
    plan: Optional[ExtensionPlan] = None
    checkpoint: Optional[ExtensionCheckpoint] = None
    guard_decision: Optional[GuardDecision] = None
    applied_changes: List[str] = field(default_factory=list)
    test_results: Dict[str, Any] = field(default_factory=dict)
    doctor_results: Dict[str, Any] = field(default_factory=dict)
    error_message: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "status": self.status.value,
            "request": self.request.to_dict(),
            "plan": self.plan.to_dict() if self.plan else None,
            "checkpoint": self.checkpoint.to_dict() if self.checkpoint else None,
            "guard_decision": self.guard_decision.to_dict() if self.guard_decision else None,
            "applied_changes": self.applied_changes,
            "test_results": self.test_results,
            "doctor_results": self.doctor_results,
            "error_message": self.error_message,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


@dataclass
class ExtensionResult:
    """Final output returned to the caller or orchestrator."""

    success: bool
    transaction_id: str
    status: TransactionStatus
    message: str
    affected_capabilities: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d
