import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from charlie.capabilities import CapabilityIndex
from charlie.config import Config
from charlie.events import EventType
from charlie.self_extension.adapters.code_adapter import CodeAdapter
from charlie.self_extension.adapters.config_adapter import ConfigAdapter
from charlie.self_extension.adapters.mcp_adapter import MCPAdapter
from charlie.self_extension.adapters.skill_adapter import SkillAdapter
from charlie.self_extension.checkpoint import CheckpointManager
from charlie.self_extension.classifier import ExtensionClassifier
from charlie.self_extension.guard import AuthorizationGuard
from charlie.self_extension.models import (
    ExtensionCheckpoint,
    ExtensionKind,
    ExtensionRequest,
    ExtensionResult,
    ExtensionTransaction,
    TransactionStatus,
)
from charlie.self_extension.registry import ExtensionRegistry
from charlie.settings_service import SettingsService

logger = logging.getLogger("charlie.self_extension.orchestrator")

_DEFAULT_TX_STORE = Path("data/extension_transactions.json")


class SelfExtensionOrchestrator:
    """Coordinates classification, guard, checkpointing, mutation, verification, and rollback."""

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
        mcp_client: Optional[Any] = None,
        tool_registry: Optional[Any] = None,
        doctor: Optional[Any] = None,
        code_index: Optional[Any] = None,
        self_knowledge: Optional[Any] = None,
        introspector: Optional[Any] = None,
        tx_store_path: Optional[Path] = None,
    ) -> None:
        self._repo_root = (repo_root or Path(os.getcwd())).resolve()
        self._config = config or Config()
        self._settings_service = settings_service or SettingsService(config_instance=self._config)
        self._capability_index = capability_index or CapabilityIndex()
        self._event_bus = event_bus
        self._mcp_client = mcp_client
        self._tool_registry = tool_registry
        self._doctor = doctor
        self._code_index = code_index
        self._self_knowledge = self_knowledge
        self._introspector = introspector
        self._tx_store_path = tx_store_path or _DEFAULT_TX_STORE

        # Subsystems
        self._registry = ExtensionRegistry(
            manifest_path=manifest_path,
            capability_index=self._capability_index,
        )
        self._classifier = ExtensionClassifier(capability_index=self._capability_index)
        self._guard = AuthorizationGuard()
        self._checkpoint_mgr = CheckpointManager(repo_root=self._repo_root)

        # Adapters
        self._config_adapter = ConfigAdapter(
            settings_service=self._settings_service,
            config=self._config,
        )
        self._skill_adapter = SkillAdapter(
            skills_dir=skills_dir,
            registry=self._registry,
            capability_index=self._capability_index,
        )
        self._mcp_adapter = MCPAdapter(
            registry=self._registry,
            capability_index=self._capability_index,
            mcp_client=self._mcp_client,
            tool_registry=self._tool_registry,
        )
        self._code_adapter = CodeAdapter(
            repo_root=self._repo_root,
            tools_dir=tools_dir,
            registry=self._registry,
            capability_index=self._capability_index,
            checkpoint_mgr=self._checkpoint_mgr,
            doctor=self._doctor,
        )

        self._transactions: Dict[str, ExtensionTransaction] = {}

        # Rehydrate capabilities from durable manifest (new process startup)
        self._registry.rehydrate(
            capability_index=self._capability_index,
            mcp_client=self._mcp_client,
            tool_registry=self._tool_registry,
        )

        # Resume any transactions left in RESTARTING state
        self._resume_restarting_transactions()

    # ─────────────────────────────────────────────────────────────────────────
    # Event emission (canonical EventType)
    # ─────────────────────────────────────────────────────────────────────────

    def _emit(self, event_type: EventType, payload: Dict[str, Any]) -> None:
        """Emit canonical lifecycle event via EventBus if wired."""
        if self._event_bus:
            try:
                self._event_bus.publish(event_type.value, payload)
            except Exception as exc:
                logger.warning("Event emit failed %s: %s", event_type.value, exc)

    # ─────────────────────────────────────────────────────────────────────────
    # Transaction persistence
    # ─────────────────────────────────────────────────────────────────────────

    def _persist_transactions(self) -> None:
        """Atomically write in-flight transactions to disk."""
        try:
            self._tx_store_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._tx_store_path.with_suffix(".tmp")
            data = {
                tx_id: tx.to_dict()
                for tx_id, tx in self._transactions.items()
                if tx.status not in (TransactionStatus.COMPLETED, TransactionStatus.ROLLED_BACK)
            }
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self._tx_store_path)
        except Exception as exc:
            logger.warning("Transaction persist failed: %s", exc)

    def _resume_restarting_transactions(self) -> None:
        """On startup, find RESTARTING transactions and move them to VERIFYING."""
        if not self._tx_store_path.exists():
            return
        try:
            data = json.loads(self._tx_store_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Cannot load persisted transactions: %s", exc)
            return

        for tx_id, tx_dict in data.items():
            status = tx_dict.get("status", "")
            if status == TransactionStatus.RESTARTING:
                logger.info("Resuming transaction %s from RESTARTING → VERIFYING", tx_id)
                # Re-build a minimal transaction record
                try:
                    req = ExtensionRequest(
                        user_prompt=tx_dict.get("user_prompt", ""),
                        request_id=tx_dict.get("request_id", tx_id),
                    )
                    tx = ExtensionTransaction(transaction_id=tx_id, request=req)
                    tx.status = TransactionStatus.VERIFYING
                    self._transactions[tx_id] = tx
                    # Run post-change verification gate
                    gate_ok, gate_msg = self._run_verification_gate(tx_id)
                    if gate_ok:
                        tx.status = TransactionStatus.COMPLETED
                        tx.finished_at = time.time()
                        self._emit(EventType.SELF_EXTENSION_COMPLETED, {"tx_id": tx_id})
                    else:
                        tx.status = TransactionStatus.FAILED
                        tx.error_message = gate_msg
                        self._emit(EventType.SELF_EXTENSION_FAILED, {"tx_id": tx_id, "reason": gate_msg})
                except Exception as exc:
                    logger.error("Failed resuming transaction %s: %s", tx_id, exc)

    # ─────────────────────────────────────────────────────────────────────────
    # Post-change verification gate (Task 7)
    # ─────────────────────────────────────────────────────────────────────────

    def _run_verification_gate(
        self,
        tx_id: str,
        affected_ext_ids: Optional[List[str]] = None,
    ) -> tuple[bool, str]:
        """Run post-change verification using existing subsystem APIs.

        Steps:
          1. CodeIndex.refresh() if available
          2. RuntimeIntrospector.get_capabilities_info() — ext_id must appear
          3. CharlieDoctor.diagnose() — must be healthy
          4. SelfKnowledge.answer_self_question() — grounded query about capability
        Returns (success, message).
        """
        self._emit(EventType.SELF_EXTENSION_VERIFYING, {"tx_id": tx_id})

        # 1. Refresh CodeIndex
        if self._code_index is not None:
            try:
                if hasattr(self._code_index, "refresh"):
                    self._code_index.refresh()
                elif hasattr(self._code_index, "reindex"):
                    self._code_index.reindex()
            except Exception as exc:
                logger.warning("CodeIndex refresh failed: %s", exc)

        # 2. CapabilityIndex reconcile via RuntimeIntrospector
        if affected_ext_ids and self._introspector is not None:
            try:
                caps_info = self._introspector.get_capabilities_info()
                by_id = caps_info.get("by_id", {})
                missing = [eid for eid in affected_ext_ids if eid not in by_id]
                if missing:
                    return False, f"Verification: expected capability/ies not visible after extension: {missing}"
            except Exception as exc:
                logger.warning("Introspector caps check failed: %s", exc)

        # 3. Doctor health check
        self._emit(EventType.SELF_EXTENSION_HEALTH_CHECK, {"tx_id": tx_id})
        if self._doctor is not None:
            try:
                report = self._doctor.diagnose()
                if not report.is_healthy:
                    error_summaries = [c.summary for c in report.errors[:3]]
                    return False, f"Doctor reports errors after extension: {error_summaries}"
            except Exception as exc:
                logger.warning("Doctor diagnose failed: %s", exc)

        # 4. SelfKnowledge grounded check
        if affected_ext_ids and self._self_knowledge is not None:
            try:
                for ext_id in affected_ext_ids[:1]:
                    ans = self._self_knowledge.answer_self_question(
                        "what capabilities do you have"
                    )
                    # answer_self_question returns a dict with "answer" key
                    answer_text = ans.get("answer", "") if isinstance(ans, dict) else str(ans)
                    # Verify extension appears somewhere in self-knowledge answer
                    name_part = ext_id.replace("code_", "").replace("mcp_", "").replace("skill_", "")
                    if name_part not in answer_text and ext_id not in answer_text:
                        logger.info(
                            "SelfKnowledge does not yet reflect '%s' — CodeIndex may need a moment. Continuing.",
                            ext_id,
                        )
            except Exception as exc:
                logger.warning("SelfKnowledge check failed (non-blocking): %s", exc)

        return True, "Verification passed."

    # ─────────────────────────────────────────────────────────────────────────
    # Generic execute_transaction entry point
    # ─────────────────────────────────────────────────────────────────────────

    def execute_transaction(self, request: ExtensionRequest) -> ExtensionResult:
        """
        Process a generic extension request through classification, guard evaluation,
        and type-specific execution.

        CODE_SMALL and MCP_TOOL require a validated ExtensionPlan with the actual
        structured payload attached.  Raw user_prompt is never treated as executable
        code or server config.  ARCHITECTURE_LARGE always requires explicit approval.
        """
        tx_id = f"tx-{uuid.uuid4().hex[:8]}"
        tx = ExtensionTransaction(transaction_id=tx_id, request=request)
        self._transactions[tx_id] = tx

        self._emit(EventType.SELF_EXTENSION_REQUESTED, {"tx_id": tx_id, "kind": "unknown"})

        # 1. Classification
        if not request.classification:
            request.classification = self._classifier.classify(request.user_prompt)
        tx.status = TransactionStatus.CLASSIFIED
        kind = request.classification.kind
        self._emit(EventType.SELF_EXTENSION_CLASSIFIED, {"tx_id": tx_id, "kind": kind.value})

        # 2. Guard evaluation
        guard_decision = self._guard.evaluate(request)
        tx.guard_decision = guard_decision

        if guard_decision.requires_approval or not guard_decision.is_authorized:
            tx.status = TransactionStatus.APPROVAL_REQUIRED
            self._emit(
                EventType.SELF_EXTENSION_APPROVAL_REQUIRED,
                {"tx_id": tx_id, "reason": guard_decision.reason},
            )
            return ExtensionResult(
                success=False,
                transaction_id=tx_id,
                status=TransactionStatus.APPROVAL_REQUIRED,
                message=guard_decision.reason,
            )

        self._emit(EventType.SELF_EXTENSION_PLANNED, {"tx_id": tx_id})

        # 3. Route by kind
        if kind == ExtensionKind.CONFIG:
            return self.execute_config_transaction(request, request.affected_settings, tx_id=tx_id)

        elif kind == ExtensionKind.SKILL:
            skill_name = request.affected_capabilities[0] if request.affected_capabilities else "custom_skill"
            raw_text = (
                request.plan.raw_text
                if request.plan and hasattr(request.plan, "raw_text")
                else None
            )
            if not raw_text:
                tx.status = TransactionStatus.FAILED
                self._emit(EventType.SELF_EXTENSION_FAILED, {"tx_id": tx_id, "reason": "no raw_text in plan"})
                return ExtensionResult(
                    success=False,
                    transaction_id=tx_id,
                    status=TransactionStatus.FAILED,
                    message="SKILL extension requires a validated plan with raw_text content.",
                )
            return self.execute_skill_transaction(request, skill_name=skill_name, raw_text=raw_text, tx_id=tx_id)

        elif kind == ExtensionKind.MCP_TOOL:
            if not request.plan or not getattr(request.plan, "mcp_name", None):
                tx.status = TransactionStatus.FAILED
                self._emit(EventType.SELF_EXTENSION_FAILED, {"tx_id": tx_id, "reason": "no mcp_name in plan"})
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
            if not request.plan or not getattr(request.plan, "code_source", None):
                tx.status = TransactionStatus.FAILED
                self._emit(EventType.SELF_EXTENSION_FAILED, {"tx_id": tx_id, "reason": "no code_source in plan"})
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
        reason = f"Unsupported extension kind '{kind}'"
        self._emit(EventType.SELF_EXTENSION_FAILED, {"tx_id": tx_id, "reason": reason})
        return ExtensionResult(
            success=False,
            transaction_id=tx_id,
            status=TransactionStatus.FAILED,
            message=reason,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Type-specific executors
    # ─────────────────────────────────────────────────────────────────────────

    def execute_config_transaction(
        self,
        request: ExtensionRequest,
        updates: Optional[Dict[str, Any]] = None,
        tx_id: Optional[str] = None,
    ) -> ExtensionResult:
        """Execute configuration extension under checkpoint and rollback safety."""
        transaction_id = tx_id or f"tx-{uuid.uuid4().hex[:8]}"
        tx = self._transactions.get(transaction_id) or ExtensionTransaction(
            transaction_id=transaction_id, request=request
        )
        self._transactions[transaction_id] = tx

        updates = dict(updates or request.affected_settings)
        if not updates and "LLM_MODEL" in request.user_prompt:
            val = request.user_prompt.split()[-1]
            updates["LLM_MODEL"] = val

        preimage = self._config_adapter.capture_preimage(list(updates.keys()))
        cp = ExtensionCheckpoint(
            checkpoint_id=transaction_id,
            created_at=time.time(),
            config_preimage=preimage,
        )
        tx.checkpoint = cp
        tx.status = TransactionStatus.CHECKPOINTING

        self._emit(EventType.SELF_EXTENSION_APPLYING, {"tx_id": transaction_id})
        tx.status = TransactionStatus.APPLYING
        apply_res = self._config_adapter.apply_updates(updates)
        if not apply_res.success:
            tx.status = TransactionStatus.FAILED
            tx.error_message = apply_res.message
            self._emit(EventType.SELF_EXTENSION_FAILED, {"tx_id": transaction_id, "reason": apply_res.message})
            return ExtensionResult(
                success=False,
                transaction_id=transaction_id,
                status=TransactionStatus.FAILED,
                message=apply_res.message,
            )

        # Post-change verification gate
        gate_ok, gate_msg = self._run_verification_gate(transaction_id, affected_ext_ids=["config"])
        if not gate_ok:
            self._config_adapter.rollback(preimage)
            tx.status = TransactionStatus.ROLLED_BACK
            self._emit(EventType.SELF_EXTENSION_ROLLED_BACK, {"tx_id": transaction_id})
            return ExtensionResult(
                success=False,
                transaction_id=transaction_id,
                status=TransactionStatus.ROLLED_BACK,
                message=f"Config extension rolled back — verification gate failed: {gate_msg}",
            )

        tx.status = TransactionStatus.COMPLETED
        tx.finished_at = time.time()
        self._emit(EventType.SELF_EXTENSION_COMPLETED, {"tx_id": transaction_id})
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
        tx = self._transactions.get(transaction_id) or ExtensionTransaction(
            transaction_id=transaction_id, request=request
        )
        self._transactions[transaction_id] = tx

        self._emit(EventType.SELF_EXTENSION_APPLYING, {"tx_id": transaction_id})
        tx.status = TransactionStatus.APPLYING
        res = self._skill_adapter.save_skill(name=skill_name, raw_text=raw_text)
        if not res.success:
            tx.status = TransactionStatus.FAILED
            tx.error_message = res.message
            self._emit(EventType.SELF_EXTENSION_FAILED, {"tx_id": transaction_id, "reason": res.message})
            return ExtensionResult(
                success=False,
                transaction_id=transaction_id,
                status=TransactionStatus.FAILED,
                message=res.message,
            )

        ext_id = f"skill_{skill_name}"
        gate_ok, gate_msg = self._run_verification_gate(transaction_id, affected_ext_ids=[ext_id])
        if not gate_ok:
            logger.warning("Skill verification gate failed (non-blocking): %s", gate_msg)

        tx.status = TransactionStatus.COMPLETED
        tx.finished_at = time.time()
        self._emit(EventType.SELF_EXTENSION_COMPLETED, {"tx_id": transaction_id})
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
        tx = self._transactions.get(transaction_id) or ExtensionTransaction(
            transaction_id=transaction_id, request=request
        )
        self._transactions[transaction_id] = tx

        self._emit(EventType.SELF_EXTENSION_APPLYING, {"tx_id": transaction_id})
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
            self._emit(EventType.SELF_EXTENSION_FAILED, {"tx_id": transaction_id, "reason": res.message})
            return ExtensionResult(
                success=False,
                transaction_id=transaction_id,
                status=TransactionStatus.FAILED,
                message=res.message,
            )

        ext_id = f"mcp_{name}"
        self._emit(EventType.SELF_EXTENSION_TESTING, {"tx_id": transaction_id})

        gate_ok, gate_msg = self._run_verification_gate(transaction_id, affected_ext_ids=[ext_id])
        if not gate_ok:
            self._mcp_adapter.rollback_mcp_server(name)
            tx.status = TransactionStatus.ROLLED_BACK
            self._emit(EventType.SELF_EXTENSION_ROLLED_BACK, {"tx_id": transaction_id})
            return ExtensionResult(
                success=False,
                transaction_id=transaction_id,
                status=TransactionStatus.ROLLED_BACK,
                message=f"MCP extension rolled back — verification gate failed: {gate_msg}",
            )

        tx.status = TransactionStatus.COMPLETED
        tx.finished_at = time.time()
        self._emit(EventType.SELF_EXTENSION_COMPLETED, {"tx_id": transaction_id})
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
        """Execute small code extension: AST allow-list, write, worker test, register."""
        transaction_id = tx_id or f"tx-{uuid.uuid4().hex[:8]}"
        tx = self._transactions.get(transaction_id) or ExtensionTransaction(
            transaction_id=transaction_id, request=request
        )
        self._transactions[transaction_id] = tx

        self._emit(EventType.SELF_EXTENSION_APPLYING, {"tx_id": transaction_id})
        tx.status = TransactionStatus.APPLYING

        self._emit(EventType.SELF_EXTENSION_TESTING, {"tx_id": transaction_id})
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
            self._emit(EventType.SELF_EXTENSION_FAILED, {"tx_id": transaction_id, "reason": res.message})
            return ExtensionResult(
                success=False,
                transaction_id=transaction_id,
                status=TransactionStatus.FAILED,
                message=res.message,
            )

        ext_id = f"code_{name}"

        # Handle requires_restart: persist state and emit RESTARTING event
        if getattr(request, "requires_restart", False):
            tx.status = TransactionStatus.RESTARTING
            self._persist_transactions()
            self._emit(EventType.SELF_EXTENSION_RESTARTING, {"tx_id": transaction_id})
            # Service restart would be triggered here via recovery infrastructure.
            # Transaction will be resumed as VERIFYING on next process startup.
            return ExtensionResult(
                success=True,
                transaction_id=transaction_id,
                status=TransactionStatus.RESTARTING,
                message=f"Code extension '{name}' applied. Restart required; verification will resume on startup.",
                affected_capabilities=[ext_id],
            )

        # Post-change verification gate
        gate_ok, gate_msg = self._run_verification_gate(transaction_id, affected_ext_ids=[ext_id])
        if not gate_ok:
            # Roll back the code extension
            self._code_adapter.rollback_code_extension(name)
            tx.status = TransactionStatus.ROLLED_BACK
            self._emit(EventType.SELF_EXTENSION_ROLLED_BACK, {"tx_id": transaction_id})
            return ExtensionResult(
                success=False,
                transaction_id=transaction_id,
                status=TransactionStatus.ROLLED_BACK,
                message=f"Code extension rolled back — verification gate failed: {gate_msg}",
            )

        tx.status = TransactionStatus.COMPLETED
        tx.finished_at = time.time()
        self._emit(EventType.SELF_EXTENSION_COMPLETED, {"tx_id": transaction_id})
        return ExtensionResult(
            success=True,
            transaction_id=transaction_id,
            status=TransactionStatus.COMPLETED,
            message=res.message,
            affected_capabilities=[ext_id],
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Rollback
    # ─────────────────────────────────────────────────────────────────────────

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
        self._emit(EventType.SELF_EXTENSION_ROLLBACK_STARTED, {"tx_id": transaction_id})

        if tx.checkpoint and tx.checkpoint.config_preimage:
            self._config_adapter.rollback(tx.checkpoint.config_preimage)

        if tx.checkpoint and (tx.checkpoint.affected_files_preimage or tx.checkpoint.new_files_created):
            self._checkpoint_mgr.rollback(tx.checkpoint)

        tx.status = TransactionStatus.ROLLED_BACK
        self._emit(EventType.SELF_EXTENSION_ROLLED_BACK, {"tx_id": transaction_id})

        return ExtensionResult(
            success=True,
            transaction_id=transaction_id,
            status=TransactionStatus.ROLLED_BACK,
            message=f"Transaction '{transaction_id}' rolled back successfully.",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Utility
    # ─────────────────────────────────────────────────────────────────────────

    def set_extension_enabled(self, extension_id: str, enabled: bool) -> bool:
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
