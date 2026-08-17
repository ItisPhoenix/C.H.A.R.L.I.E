"""Authoritative orchestrator for controlled self-extension transactions."""

from __future__ import annotations

import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from charlie.config import Config
from charlie.settings_service import SettingsService
from charlie.capabilities import CapabilityIndex
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
from charlie.self_extension.registry import ExtensionRegistry
from charlie.self_extension.adapters.config_adapter import ConfigAdapter
from charlie.self_extension.adapters.skill_adapter import SkillAdapter
from charlie.self_extension.adapters.mcp_adapter import MCPAdapter
from charlie.self_extension.adapters.code_adapter import CodeAdapter

logger = logging.getLogger("charlie.self_extension.orchestrator")


class SelfExtensionOrchestrator:
    """Coordinates classification, guard evaluation, checkpointing, mutation, verification, and rollback."""

    def __init__(
        self,
        repo_root: Optional[Path] = None,
        manifest_path: Optional[Path] = None,
        skills_dir: Optional[Path] = None,
        tools_dir: Optional[Path] = None,
        settings_service: Optional[SettingsService] = None,
        config: Optional[Config] = None,
        capability_index: Optional[CapabilityIndex] = None,
        event_bus: Optional[Any] = None,
    ) -> None:
        self._repo_root = (repo_root or Path(os.getcwd())).resolve()
        self._config = config or Config()
        self._settings_service = settings_service or SettingsService(config_instance=self._config)
        self._capability_index = capability_index or CapabilityIndex()
        self._event_bus = event_bus

        # Subsystems
        self._registry = ExtensionRegistry(
            manifest_path=manifest_path,
            capability_index=self._capability_index,
        )
        self._classifier = ExtensionClassifier(capability_index=self._capability_index)
        self._guard = AuthorizationGuard()
        self._checkpoint_mgr = CheckpointManager(repo_root=self._repo_root)

        # Adapters
        self._config_adapter = ConfigAdapter(settings_service=self._settings_service, config=self._config)
        self._skill_adapter = SkillAdapter(
            skills_dir=skills_dir,
            registry=self._registry,
            capability_index=self._capability_index,
        )
        self._mcp_adapter = MCPAdapter(
            registry=self._registry,
            capability_index=self._capability_index,
        )
        self._code_adapter = CodeAdapter(
            repo_root=self._repo_root,
            tools_dir=tools_dir,
            registry=self._registry,
            capability_index=self._capability_index,
            checkpoint_mgr=self._checkpoint_mgr,
        )

        self._transactions: Dict[str, ExtensionTransaction] = {}

    def _emit_event(self, event_name: str, payload: Dict[str, Any]) -> None:
        """Emit audit event via EventBus if available."""
        if self._event_bus:
            try:
                self._event_bus.publish(event_name, payload)
            except Exception as e:
                logger.warning("Failed to emit event %s: %s", event_name, e)

    def execute_transaction(self, request: ExtensionRequest) -> ExtensionResult:
        """
        Process a generic extension request through classification, guard evaluation, and type-specific execution.

        CODE_SMALL and MCP_TOOL require a validated ExtensionPlan with the actual structured payload
        (code/server spec) attached.  Raw user_prompt is never treated as executable code or server config.
        ARCHITECTURE_LARGE is always gated for explicit user approval.
        """
        tx_id = f"tx-{uuid.uuid4().hex[:8]}"
        tx = ExtensionTransaction(transaction_id=tx_id, request=request)
        self._transactions[tx_id] = tx

        # 1. Classification
        if not request.classification:
            request.classification = self._classifier.classify(request.user_prompt)
        tx.status = TransactionStatus.CLASSIFIED

        kind = request.classification.kind

        # 2. Guard evaluation
        guard_decision = self._guard.evaluate(request)
        tx.guard_decision = guard_decision

        if guard_decision.requires_approval or not guard_decision.is_authorized:
            tx.status = TransactionStatus.APPROVAL_REQUIRED
            self._emit_event("extension_approval_required", {"tx_id": tx_id, "reason": guard_decision.reason})
            return ExtensionResult(
                success=False,
                transaction_id=tx_id,
                status=TransactionStatus.APPROVAL_REQUIRED,
                message=guard_decision.reason,
            )

        # 3. Route according to classification
        if kind == ExtensionKind.CONFIG:
            return self.execute_config_transaction(request, request.affected_settings, tx_id=tx_id)

        elif kind == ExtensionKind.SKILL:
            skill_name = request.affected_capabilities[0] if request.affected_capabilities else "custom_skill"
            # Skill raw_text must come from the plan, not the user_prompt directly.
            raw_text = (
                request.plan.raw_text
                if request.plan and hasattr(request.plan, "raw_text")
                else None
            )
            if not raw_text:
                tx.status = TransactionStatus.FAILED
                return ExtensionResult(
                    success=False,
                    transaction_id=tx_id,
                    status=TransactionStatus.FAILED,
                    message="SKILL extension requires a validated plan with raw_text content.",
                )
            return self.execute_skill_transaction(request, skill_name=skill_name, raw_text=raw_text, tx_id=tx_id)

        elif kind == ExtensionKind.MCP_TOOL:
            # MCP server spec must come from the validated plan — never inferred from prompt.
            if not request.plan or not hasattr(request.plan, "mcp_name") or not request.plan.mcp_name:
                tx.status = TransactionStatus.FAILED
                return ExtensionResult(
                    success=False,
                    transaction_id=tx_id,
                    status=TransactionStatus.FAILED,
                    message=(
                        "MCP_TOOL extension requires a validated plan with mcp_name and mcp_command. "
                        "Provide an ExtensionPlan with the server specification."
                    ),
                )
            return self.execute_mcp_transaction(
                request,
                name=request.plan.mcp_name,
                command=request.plan.mcp_command,
                args=getattr(request.plan, "mcp_args", None),
                env=getattr(request.plan, "mcp_env", None),
                declared_tools=getattr(request.plan, "mcp_declared_tools", None),
                tx_id=tx_id,
            )

        elif kind == ExtensionKind.CODE_SMALL:
            # Generated code must come from the validated plan — never from raw user_prompt.
            if not request.plan or not hasattr(request.plan, "code_source") or not request.plan.code_source:
                tx.status = TransactionStatus.FAILED
                return ExtensionResult(
                    success=False,
                    transaction_id=tx_id,
                    status=TransactionStatus.FAILED,
                    message=(
                        "CODE_SMALL extension requires a validated plan with code_source and tool_name. "
                        "Raw user_prompt is never executed as code."
                    ),
                )
            return self.execute_code_transaction(
                request,
                name=request.plan.tool_name or "generated_tool",
                code=request.plan.code_source,
                test_inputs=getattr(request.plan, "test_inputs", None),
                expected_output=getattr(request.plan, "expected_output", None),
                tx_id=tx_id,
            )

        tx.status = TransactionStatus.FAILED
        return ExtensionResult(
            success=False,
            transaction_id=tx_id,
            status=TransactionStatus.FAILED,
            message=f"Unsupported extension kind '{kind}'",
        )

    def execute_config_transaction(
        self,
        request: ExtensionRequest,
        updates: Optional[Dict[str, Any]] = None,
        tx_id: Optional[str] = None,
    ) -> ExtensionResult:
        """Execute configuration extension under checkpoint and rollback safety."""
        transaction_id = tx_id or f"tx-{uuid.uuid4().hex[:8]}"
        tx = self._transactions.get(transaction_id) or ExtensionTransaction(transaction_id=transaction_id, request=request)
        self._transactions[transaction_id] = tx

        updates = dict(updates or request.affected_settings)
        if not updates:
            # Parse from prompt if possible
            if "LLM_MODEL" in request.user_prompt:
                val = request.user_prompt.split()[-1]
                updates["LLM_MODEL"] = val

        # Checkpoint config preimage
        preimage = self._config_adapter.capture_preimage(list(updates.keys()))
        cp = ExtensionCheckpoint(
            checkpoint_id=transaction_id,
            created_at=time.time(),
            config_preimage=preimage,
        )
        tx.checkpoint = cp
        tx.status = TransactionStatus.CHECKPOINTING

        # Apply
        tx.status = TransactionStatus.APPLYING
        apply_res = self._config_adapter.apply_updates(updates)
        if not apply_res.success:
            tx.status = TransactionStatus.FAILED
            tx.error_message = apply_res.message
            return ExtensionResult(success=False, transaction_id=transaction_id, status=TransactionStatus.FAILED, message=apply_res.message)

        tx.status = TransactionStatus.COMPLETED
        tx.finished_at = time.time()
        self._emit_event("extension_applied", {"tx_id": transaction_id, "kind": "config", "updates": list(updates.keys())})

        return ExtensionResult(
            success=True,
            transaction_id=transaction_id,
            status=TransactionStatus.COMPLETED,
            message="Configuration extension completed successfully.",
            affected_capabilities=["config"],
        )

    def execute_skill_transaction(
        self,
        request: ExtensionRequest,
        skill_name: str,
        raw_text: str,
        tx_id: Optional[str] = None,
    ) -> ExtensionResult:
        """Execute reusable skill creation and capability registration."""
        transaction_id = tx_id or f"tx-{uuid.uuid4().hex[:8]}"
        tx = self._transactions.get(transaction_id) or ExtensionTransaction(transaction_id=transaction_id, request=request)
        self._transactions[transaction_id] = tx

        tx.status = TransactionStatus.APPLYING
        res = self._skill_adapter.save_skill(name=skill_name, raw_text=raw_text)
        if not res.success:
            tx.status = TransactionStatus.FAILED
            tx.error_message = res.message
            return ExtensionResult(success=False, transaction_id=transaction_id, status=TransactionStatus.FAILED, message=res.message)

        tx.status = TransactionStatus.COMPLETED
        tx.finished_at = time.time()
        ext_id = f"skill_{skill_name}"
        self._emit_event("extension_applied", {"tx_id": transaction_id, "kind": "skill", "name": skill_name})

        return ExtensionResult(
            success=True,
            transaction_id=transaction_id,
            status=TransactionStatus.COMPLETED,
            message=res.message,
            affected_capabilities=[ext_id],
        )

    def execute_mcp_transaction(
        self,
        request: ExtensionRequest,
        name: str,
        command: str,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        declared_tools: Optional[List[str]] = None,
        tx_id: Optional[str] = None,
    ) -> ExtensionResult:
        """Execute MCP server integration and capability indexing."""
        transaction_id = tx_id or f"tx-{uuid.uuid4().hex[:8]}"
        tx = self._transactions.get(transaction_id) or ExtensionTransaction(transaction_id=transaction_id, request=request)
        self._transactions[transaction_id] = tx

        tx.status = TransactionStatus.APPLYING
        res = self._mcp_adapter.register_mcp_server(
            name=name,
            command=command,
            args=args,
            env=env,
            declared_tools=declared_tools,
        )
        if not res.success:
            tx.status = TransactionStatus.FAILED
            tx.error_message = res.message
            return ExtensionResult(success=False, transaction_id=transaction_id, status=TransactionStatus.FAILED, message=res.message)

        tx.status = TransactionStatus.COMPLETED
        tx.finished_at = time.time()
        ext_id = f"mcp_{name}"
        self._emit_event("extension_applied", {"tx_id": transaction_id, "kind": "mcp", "name": name})

        return ExtensionResult(
            success=True,
            transaction_id=transaction_id,
            status=TransactionStatus.COMPLETED,
            message=res.message,
            affected_capabilities=[ext_id],
        )

    def execute_code_transaction(
        self,
        request: ExtensionRequest,
        name: str,
        code: str,
        test_inputs: Optional[Dict[str, Any]] = None,
        expected_output: Optional[Any] = None,
        tx_id: Optional[str] = None,
    ) -> ExtensionResult:
        """Execute small code extension with AST check, execution test, and rollback safety."""
        transaction_id = tx_id or f"tx-{uuid.uuid4().hex[:8]}"
        tx = self._transactions.get(transaction_id) or ExtensionTransaction(transaction_id=transaction_id, request=request)
        self._transactions[transaction_id] = tx

        tx.status = TransactionStatus.APPLYING
        res = self._code_adapter.apply_code_extension(
            name=name,
            code=code,
            test_inputs=test_inputs,
            expected_output=expected_output,
            transaction_id=transaction_id,
        )

        if not res.success:
            tx.status = TransactionStatus.FAILED
            tx.error_message = res.message
            return ExtensionResult(success=False, transaction_id=transaction_id, status=TransactionStatus.FAILED, message=res.message)

        tx.status = TransactionStatus.COMPLETED
        tx.finished_at = time.time()
        ext_id = f"code_{name}"
        self._emit_event("extension_applied", {"tx_id": transaction_id, "kind": "code_small", "name": name})

        return ExtensionResult(
            success=True,
            transaction_id=transaction_id,
            status=TransactionStatus.COMPLETED,
            message=res.message,
            affected_capabilities=[ext_id],
        )

    def rollback_transaction(self, transaction_id: str) -> ExtensionResult:
        """Roll back mutations performed by a recorded transaction."""
        tx = self._transactions.get(transaction_id)
        if not tx:
            return ExtensionResult(
                success=False,
                transaction_id=transaction_id,
                status=TransactionStatus.FAILED,
                message=f"Transaction '{transaction_id}' not found.",
            )

        tx.status = TransactionStatus.ROLLING_BACK

        # Rollback config if checkpoint exists
        if tx.checkpoint and tx.checkpoint.config_preimage:
            self._config_adapter.rollback(tx.checkpoint.config_preimage)

        # Rollback files if checkpoint exists
        if tx.checkpoint and (tx.checkpoint.affected_files_preimage or tx.checkpoint.new_files_created):
            self._checkpoint_mgr.rollback(tx.checkpoint)

        tx.status = TransactionStatus.ROLLED_BACK
        self._emit_event("extension_rolled_back", {"tx_id": transaction_id})

        return ExtensionResult(
            success=True,
            transaction_id=transaction_id,
            status=TransactionStatus.ROLLED_BACK,
            message=f"Transaction '{transaction_id}' rolled back successfully.",
        )

    def set_extension_enabled(self, extension_id: str, enabled: bool) -> bool:
        """Enable or disable an extension by ID and synchronize capabilities."""
        if extension_id.startswith("skill_"):
            name = extension_id[len("skill_"):]
            res = self._skill_adapter.set_enabled(name, enabled)
            return res.success
        return self._registry.set_enabled(extension_id, enabled)

    def get_transaction(self, transaction_id: str) -> Optional[Dict[str, Any]]:
        tx = self._transactions.get(transaction_id)
        return tx.to_dict() if tx else None

    def list_transactions(self) -> List[Dict[str, Any]]:
        return [tx.to_dict() for tx in self._transactions.values()]
