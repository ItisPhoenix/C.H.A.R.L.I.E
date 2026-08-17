import asyncio
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from charlie.capabilities import CapabilityIndex, get_capability_index
from charlie.config import Config
from charlie.events import EventMeta, EventSource, EventType
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
    ExtensionPlan,
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
        event_loop: Optional[Any] = None,
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
        self._capability_index = capability_index if capability_index is not None else get_capability_index()
        self._event_bus = event_bus
        self._event_loop = event_loop
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

    def plan_request(
        self,
        prompt: str,
        *,
        explicit_user_request: bool = True,
        affected_settings: Optional[Dict[str, Any]] = None,
    ) -> ExtensionRequest:
        """Turn explicit user intent into a bounded, typed execution plan."""
        request = ExtensionRequest(
            user_prompt=prompt,
            explicit_user_request=explicit_user_request,
            affected_settings=dict(affected_settings or {}),
        )
        request.classification = self._classifier.classify(prompt)

        # Inspection is advisory. It never supplies executable payloads.
        try:
            if self._code_index is not None and hasattr(self._code_index, "refresh"):
                self._code_index.refresh()
            if self._self_knowledge is not None and hasattr(self._self_knowledge, "get_evidence_for_query"):
                self._self_knowledge.get_evidence_for_query(prompt[:500])
        except Exception as exc:
            logger.info("Extension planning inspection unavailable: %s", exc)

        kind = request.classification.kind
        if kind == ExtensionKind.SKILL:
            name = self._plan_name(prompt, "custom_skill")
            request.plan = ExtensionPlan(
                plan_id=f"plan-{uuid.uuid4().hex[:8]}",
                kind=kind,
                description=f"Register reusable skill '{name}'.",
                steps=["validate SKILL.md", "save skill", "verify capability"],
                raw_text=(
                    "---\n"
                    f"name: {name}\n"
                    "description: Reusable procedure requested by the user.\n"
                    "---\n"
                    "# Procedure\n\n"
                    f"Follow this requested procedure: {self._safe_markdown_text(prompt)}\n"
                ),
            )
            request.affected_capabilities = [f"skill_{name}"]
        elif kind == ExtensionKind.MCP_TOOL:
            name = self._plan_name(prompt, "requested_mcp")
            command_match = re.search(r"\bcommand\s*[:=]?\s*([\w./-]+)", prompt, re.IGNORECASE)
            command = command_match.group(1) if command_match else "npx"
            args_match = re.search(r"\bargs\s*[:=]\s*([^;]+)", prompt, re.IGNORECASE)
            args = (
                [item.strip(" '\"") for item in args_match.group(1).strip(" []").split(",") if item.strip()]
                if args_match
                else ["-y", "@modelcontextprotocol/server-filesystem"]
            )
            env_match = re.search(r"\benv\s*[:=]\s*([^;]+)", prompt, re.IGNORECASE)
            env = {}
            if env_match:
                for item in env_match.group(1).strip(" []").split(","):
                    if "=" in item:
                        key, value = item.split("=", 1)
                        env[key.strip()] = value.strip(" '\"")
            request.plan = ExtensionPlan(
                plan_id=f"plan-{uuid.uuid4().hex[:8]}",
                kind=kind,
                description=f"Connect MCP server '{name}' through MCPClient.",
                steps=["add_server", "enable_server", "discover tools", "verify health"],
                mcp_name=name,
                mcp_command=command,
                mcp_args=args,
                mcp_env=env,
            )
            request.affected_capabilities = [f"mcp_{name}"]
        elif kind == ExtensionKind.CODE_SMALL:
            name, source, inputs, expected = self._build_code_plan(prompt)
            request.plan = ExtensionPlan(
                plan_id=f"plan-{uuid.uuid4().hex[:8]}",
                kind=kind,
                description=f"Add bounded pure function '{name}'.",
                steps=["validate AST", "checkpoint", "write", "worker test", "verify capability"],
                tests_to_run=[f"{name}({inputs!r}) == {expected!r}"],
                code_source=source,
                tool_name=name,
                test_inputs=inputs,
                expected_output=expected,
            )
            request.affected_capabilities = [f"code_{name}"]
        elif kind == ExtensionKind.CONFIG:
            request.plan = ExtensionPlan(
                plan_id=f"plan-{uuid.uuid4().hex[:8]}",
                kind=kind,
                description="Apply validated settings updates.",
                settings_updates=dict(request.affected_settings),
            )
        return request

    @staticmethod
    def _safe_markdown_text(prompt: str) -> str:
        return " ".join(prompt.replace("```", "").split())[:500]

    @staticmethod
    def _plan_name(prompt: str, fallback: str) -> str:
        match = re.search(r"\b(?:called|named)\s+([A-Za-z][A-Za-z0-9_-]*)", prompt, re.IGNORECASE)
        if not match:
            match = re.search(r"\b(?:skill|server)\s+([A-Za-z][A-Za-z0-9_-]*)", prompt, re.IGNORECASE)
        candidate = match.group(1).lower().replace("-", "_") if match else fallback
        if candidate in {"for", "that", "to", "with", "a", "an", "the"}:
            return fallback
        return candidate[:48]

    @staticmethod
    def _build_code_plan(prompt: str) -> tuple[str, str, Dict[str, Any], Any]:
        explicit = re.search(r"\b(?:called|named)\s+([A-Za-z_]\w*)", prompt, re.IGNORECASE)
        lower = prompt.lower()
        if "double" in lower:
            name = explicit.group(1) if explicit else "double_value"
            return name, f"def {name}(value=0):\n    return value * 2\n", {"value": 2}, 4
        if any(term in lower for term in ("add two", "sum", "addition")):
            name = explicit.group(1) if explicit else "add_numbers"
            return name, f"def {name}(a=0, b=0):\n    return a + b\n", {"a": 2, "b": 3}, 5
        if "uppercase" in lower or "upper case" in lower:
            name = explicit.group(1) if explicit else "uppercase_text"
            return name, f"def {name}(value=''):\n    return value.upper()\n", {"value": "charlie"}, "CHARLIE"
        name = explicit.group(1) if explicit else "generated_tool"
        return name, f"def {name}(value=None):\n    return value\n", {"value": "ok"}, "ok"

    # ─────────────────────────────────────────────────────────────────────────
    # Event emission (canonical EventType)
    # ─────────────────────────────────────────────────────────────────────────

    def _emit(self, event_type: EventType, payload: Dict[str, Any]) -> None:
        """Emit canonical lifecycle event via EventBus if wired."""
        if self._event_bus is None:
            return
        try:
            if self._event_loop is not None:
                asyncio.run_coroutine_threadsafe(
                    self._event_bus.emit(
                        event_type.value,
                        payload,
                        meta=EventMeta(source=EventSource.BRAIN),
                    ),
                    self._event_loop,
                )
            else:
                logger.warning(
                    "EventBus wired without event_loop; cannot emit %s", event_type.value
                )
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
                logger.info("Resuming transaction %s from RESTARTING -> VERIFYING", tx_id)
                try:
                    tx = ExtensionTransaction.from_dict({**tx_dict, "transaction_id": tx_id})
                    tx.status = TransactionStatus.VERIFYING
                    self._transactions[tx_id] = tx
                    affected = self._affected_capability_ids(tx)
                    gate_ok, gate_msg = self._run_verification_gate(tx_id, affected_ext_ids=affected)
                    if gate_ok:
                        tx.status = TransactionStatus.COMPLETED
                        tx.finished_at = time.time()
                        self._emit(EventType.SELF_EXTENSION_COMPLETED, {"tx_id": tx_id})
                    else:
                        self._rollback_resumed_transaction(tx, gate_msg)
                except Exception as exc:
                    logger.error("Failed resuming transaction %s: %s", tx_id, exc, exc_info=True)

    def _affected_capability_ids(self, tx: ExtensionTransaction) -> List[str]:
        ids = list(tx.verification_criteria or tx.request.affected_capabilities)
        plan = tx.plan or tx.request.plan
        if plan:
            kind = tx.request.classification.kind if tx.request.classification else plan.kind
            if kind == ExtensionKind.CODE_SMALL and plan.tool_name:
                ids.append(f"code_{plan.tool_name}")
            elif kind == ExtensionKind.MCP_TOOL and plan.mcp_name:
                ids.append(f"mcp_{plan.mcp_name}")
        return list(dict.fromkeys(ids))

    def _rollback_resumed_transaction(self, tx: ExtensionTransaction, reason: str) -> None:
        tx_id = tx.transaction_id
        self._emit(EventType.SELF_EXTENSION_ROLLBACK_STARTED, {"tx_id": tx_id})
        rollback_ok = True
        rollback_message = ""
        try:
            kind = (
                tx.request.classification.kind
                if tx.request.classification
                else ((tx.plan or tx.request.plan).kind if (tx.plan or tx.request.plan) else None)
            )
            plan = tx.plan or tx.request.plan
            if kind == ExtensionKind.MCP_TOOL and plan and plan.mcp_name:
                rollback = self._mcp_adapter.rollback_mcp_server(plan.mcp_name)
            elif kind == ExtensionKind.CODE_SMALL and plan and plan.tool_name:
                rollback = self._code_adapter.rollback_code_extension(plan.tool_name, checkpoint=tx.checkpoint)
            elif kind == ExtensionKind.SKILL:
                skill_id = next((item for item in self._affected_capability_ids(tx) if item.startswith("skill_")), "")
                if not skill_id:
                    raise RuntimeError("rollback_failed: resumed skill name is missing")
                rollback = self._skill_adapter.remove_skill(skill_id[len("skill_"):])
            elif kind == ExtensionKind.CONFIG and tx.checkpoint:
                rollback = self._config_adapter.rollback(tx.checkpoint.config_preimage)
            elif tx.checkpoint:
                rollback = self._checkpoint_mgr.rollback(tx.checkpoint)
            else:
                raise RuntimeError("rollback_failed: transaction has no scoped rollback metadata")
            if not getattr(rollback, "success", False):
                raise RuntimeError(f"rollback_failed: {getattr(rollback, 'message', rollback)}")
        except Exception as exc:
            rollback_ok = False
            rollback_message = str(exc)
            logger.error("Resume rollback failed for %s: %s", tx_id, exc, exc_info=True)
        if rollback_ok:
            tx.status = TransactionStatus.ROLLED_BACK
            tx.error_message = reason
            self._emit(EventType.SELF_EXTENSION_ROLLED_BACK, {"tx_id": tx_id})
        else:
            tx.status = TransactionStatus.ROLLED_BACK
            tx.error_message = f"rollback_failed: {rollback_message}; verification: {reason}"
            self._emit(EventType.SELF_EXTENSION_ROLLED_BACK, {"tx_id": tx_id, "reason": tx.error_message})

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

        if self._code_index is not None:
            try:
                if hasattr(self._code_index, "refresh"):
                    self._code_index.refresh()
                elif hasattr(self._code_index, "reindex"):
                    self._code_index.reindex()
            except Exception as exc:
                logger.error("CodeIndex refresh failed: %s", exc, exc_info=True)
                return False, f"CodeIndex refresh failed: {exc}"

        if affected_ext_ids:
            if self._introspector is None:
                return False, "RuntimeIntrospector unavailable for capability verification."
            try:
                caps_info = self._introspector.get_capabilities_info()
                by_id = caps_info.get("by_id", {})
                missing = [eid for eid in affected_ext_ids if eid not in by_id]
                if missing:
                    return False, (
                        f"Verification: expected capability/ies not visible after extension: {missing}"
                    )
                unavailable = []
                for eid in affected_ext_ids:
                    record = by_id[eid]
                    health = record.get("health", {}) if isinstance(record, dict) else {}
                    if (
                        isinstance(record, dict)
                        and (
                            record.get("available") is False
                            or record.get("status")
                            in {"degraded", "unavailable", "failed", "error"}
                        )
                    ) or (isinstance(health, dict) and health.get("available") is False):
                        unavailable.append(eid)
                if unavailable:
                    return False, f"Verification: expected capability/ies unavailable after extension: {unavailable}"
            except Exception as exc:
                logger.error("Introspector caps check failed: %s", exc, exc_info=True)
                return False, f"Capability introspection failed: {exc}"

        self._emit(EventType.SELF_EXTENSION_HEALTH_CHECK, {"tx_id": tx_id})
        if self._doctor is None:
            return False, "CharlieDoctor unavailable for post-extension health check."
        try:
            report = self._doctor.diagnose()
            is_healthy = report.get("is_healthy") if isinstance(report, dict) else getattr(report, "is_healthy", False)
            errors = report.get("errors", []) if isinstance(report, dict) else getattr(report, "errors", [])
            if not is_healthy:
                error_summaries = [getattr(c, "summary", str(c)) for c in errors[:3]]
                return False, f"Doctor reports errors after extension: {error_summaries}"
        except Exception as exc:
            logger.error("Doctor diagnose failed: %s", exc, exc_info=True)
            return False, f"Doctor health check failed: {exc}"

        if affected_ext_ids:
            if self._self_knowledge is None:
                return False, "SelfKnowledge unavailable for extension evidence check."
            try:
                for ext_id in affected_ext_ids[:1]:
                    ans = self._self_knowledge.answer_self_question(
                        "what capabilities do you have"
                    )
                    answer_text = ans.get("answer", "") if isinstance(ans, dict) else str(ans)
                    name_part = ext_id.replace("code_", "").replace("mcp_", "").replace("skill_", "")
                    if name_part not in answer_text and ext_id not in answer_text:
                        return False, (
                            f"SelfKnowledge does not reflect extension '{ext_id}' after apply."
                        )
            except Exception as exc:
                logger.error("SelfKnowledge check failed: %s", exc, exc_info=True)
                return False, f"SelfKnowledge verification failed: {exc}"

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
        gate_ok, gate_msg = self._run_verification_gate(transaction_id, affected_ext_ids=None)
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
        if ext_id not in tx.request.affected_capabilities:
            tx.request.affected_capabilities.append(ext_id)
        tx.verification_criteria = [ext_id]
        gate_ok, gate_msg = self._run_verification_gate(transaction_id, affected_ext_ids=[ext_id])
        if not gate_ok:
            rollback = self._skill_adapter.remove_skill(skill_name)
            tx.status = TransactionStatus.ROLLED_BACK
            if not rollback.success:
                tx.error_message = f"rollback_failed: {rollback.message}; verification: {gate_msg}"
            self._emit(EventType.SELF_EXTENSION_ROLLED_BACK, {"tx_id": transaction_id})
            return ExtensionResult(
                success=False,
                transaction_id=transaction_id,
                status=TransactionStatus.ROLLED_BACK,
                message=f"Skill extension rolled back — verification gate failed: {gate_msg}",
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
        if ext_id not in tx.request.affected_capabilities:
            tx.request.affected_capabilities.append(ext_id)
        tx.verification_criteria = [ext_id]
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

        if res.checkpoint:
            tx.checkpoint = res.checkpoint

        ext_id = f"code_{name}"
        if ext_id not in tx.request.affected_capabilities:
            tx.request.affected_capabilities.append(ext_id)
        tx.verification_criteria = [ext_id]

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
            self._code_adapter.rollback_code_extension(name, checkpoint=tx.checkpoint)
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
