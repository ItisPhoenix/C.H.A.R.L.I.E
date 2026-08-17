"""Typed request, classification, transaction, and result models for self-extension."""

from __future__ import annotations

import base64
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

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ExtensionClassification:
        return cls(
            kind=ExtensionKind(data.get("kind", ExtensionKind.CODE_SMALL.value)),
            confidence=float(data.get("confidence", 1.0)),
            reason=str(data.get("reason", "")),
            is_already_supported=bool(data.get("is_already_supported", False)),
            existing_capability_id=data.get("existing_capability_id"),
            suggested_scope=list(data.get("suggested_scope", [])),
        )


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

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ExtensionRequest:
        classification = None
        if data.get("classification"):
            classification = ExtensionClassification.from_dict(data["classification"])
        plan = None
        if data.get("plan"):
            plan = ExtensionPlan.from_dict(data["plan"])
        return cls(
            user_prompt=str(data.get("user_prompt", "")),
            request_id=str(data.get("request_id", f"ext-{uuid.uuid4().hex[:8]}")),
            task_id=data.get("task_id"),
            classification=classification,
            plan=plan,
            explicit_user_request=bool(data.get("explicit_user_request", True)),
            scope=str(data.get("scope", "local")),
            affected_capabilities=list(data.get("affected_capabilities", [])),
            affected_files=list(data.get("affected_files", [])),
            affected_settings=dict(data.get("affected_settings", {})),
            required_dependencies=list(data.get("required_dependencies", [])),
            risk_class=RiskClass(data.get("risk_class", RiskClass.REVERSIBLE.value)),
            requires_approval=bool(data.get("requires_approval", False)),
            requires_restart=bool(data.get("requires_restart", False)),
            created_at=float(data.get("created_at", time.time())),
        )


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

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> GuardDecision:
        return cls(
            is_authorized=bool(data.get("is_authorized", False)),
            requires_approval=bool(data.get("requires_approval", False)),
            reason=str(data.get("reason", "")),
            risk_class=RiskClass(data.get("risk_class", RiskClass.REVERSIBLE.value)),
        )


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

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ExtensionPlan:
        return cls(
            plan_id=str(data.get("plan_id", f"plan-{uuid.uuid4().hex[:8]}")),
            kind=ExtensionKind(data.get("kind", ExtensionKind.CODE_SMALL.value)),
            description=str(data.get("description", "")),
            steps=list(data.get("steps", [])),
            files_to_create=list(data.get("files_to_create", [])),
            files_to_modify=list(data.get("files_to_modify", [])),
            files_to_delete=list(data.get("files_to_delete", [])),
            settings_updates=dict(data.get("settings_updates", {})),
            tests_to_run=list(data.get("tests_to_run", [])),
            rollback_strategy=str(data.get("rollback_strategy", "restore_preimages")),
            requires_restart=bool(data.get("requires_restart", False)),
            code_source=data.get("code_source"),
            tool_name=data.get("tool_name"),
            test_inputs=data.get("test_inputs"),
            expected_output=data.get("expected_output"),
            raw_text=data.get("raw_text"),
            mcp_name=data.get("mcp_name"),
            mcp_command=data.get("mcp_command"),
            mcp_args=list(data.get("mcp_args") or []) if data.get("mcp_args") is not None else None,
            mcp_env=dict(data.get("mcp_env") or {}) if data.get("mcp_env") is not None else None,
            mcp_declared_tools=(
                list(data.get("mcp_declared_tools") or [])
                if data.get("mcp_declared_tools") is not None
                else None
            ),
        )


@dataclass
class ExtensionCheckpoint:
    """Point-in-time snapshot of affected state before mutation."""

    checkpoint_id: str
    created_at: float
    affected_files_preimage: Dict[str, bytes] = field(default_factory=dict)
    new_files_created: List[str] = field(default_factory=list)
    postimage_hashes: Dict[str, str] = field(default_factory=dict)
    config_preimage: Dict[str, Any] = field(default_factory=dict)
    git_head: Optional[str] = None
    git_branch: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "created_at": self.created_at,
            "affected_files_preimage": {
                path: base64.b64encode(data).decode("ascii")
                for path, data in self.affected_files_preimage.items()
            },
            "new_files_created": list(self.new_files_created),
            "postimage_hashes": dict(self.postimage_hashes),
            "config_preimage": dict(self.config_preimage),
            "git_head": self.git_head,
            "git_branch": self.git_branch,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ExtensionCheckpoint:
        preimages: Dict[str, bytes] = {}
        raw_pre = data.get("affected_files_preimage", {})
        if isinstance(raw_pre, dict):
            for path, encoded in raw_pre.items():
                if isinstance(encoded, str):
                    preimages[str(path)] = base64.b64decode(encoded.encode("ascii"))
        return cls(
            checkpoint_id=str(data.get("checkpoint_id", "")),
            created_at=float(data.get("created_at", time.time())),
            affected_files_preimage=preimages,
            new_files_created=list(data.get("new_files_created", [])),
            postimage_hashes=dict(data.get("postimage_hashes", {})),
            config_preimage=dict(data.get("config_preimage", {})),
            git_head=data.get("git_head"),
            git_branch=data.get("git_branch"),
        )


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
    verification_criteria: List[str] = field(default_factory=list)
    error_message: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "status": self.status.value,
            "request": self.request.to_dict(),
            "affected_capability_ids": list(self.request.affected_capabilities),
            "plan": self.plan.to_dict() if self.plan else None,
            "checkpoint": self.checkpoint.to_dict() if self.checkpoint else None,
            "guard_decision": self.guard_decision.to_dict() if self.guard_decision else None,
            "applied_changes": self.applied_changes,
            "test_results": self.test_results,
            "doctor_results": self.doctor_results,
            "verification_criteria": list(self.verification_criteria),
            "error_message": self.error_message,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ExtensionTransaction:
        request_data = dict(data.get("request") or {})
        plan_data = data.get("plan")
        if plan_data and not request_data.get("plan"):
            request_data["plan"] = plan_data
        request = ExtensionRequest.from_dict(request_data)
        plan = ExtensionPlan.from_dict(plan_data) if plan_data else request.plan
        if request.plan is None and plan is not None:
            request.plan = plan
        if request.classification is None and plan is not None:
            request.classification = ExtensionClassification(kind=plan.kind)
        criteria = list(data.get("verification_criteria", []))
        if not request.affected_capabilities and data.get("affected_capability_ids"):
            request.affected_capabilities = list(data["affected_capability_ids"])
        if not criteria:
            criteria = list(request.affected_capabilities)
        checkpoint = (
            ExtensionCheckpoint.from_dict(data["checkpoint"]) if data.get("checkpoint") else None
        )
        guard_decision = (
            GuardDecision.from_dict(data["guard_decision"]) if data.get("guard_decision") else None
        )
        return cls(
            transaction_id=str(data.get("transaction_id", request.request_id)),
            request=request,
            status=TransactionStatus(data.get("status", TransactionStatus.REQUESTED.value)),
            plan=plan,
            checkpoint=checkpoint,
            guard_decision=guard_decision,
            applied_changes=list(data.get("applied_changes", [])),
            test_results=dict(data.get("test_results", {})),
            doctor_results=dict(data.get("doctor_results", {})),
            verification_criteria=criteria,
            error_message=data.get("error_message"),
            started_at=float(data.get("started_at", time.time())),
            finished_at=data.get("finished_at"),
        )


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
