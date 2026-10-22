import ast
import asyncio
import hashlib
import json
import logging
import os
import re
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from charlie.capabilities import CapabilityIndex, get_capability_index
from charlie.config import Config
from charlie.events import EventMeta, EventSource, EventType
from charlie.self_extension.adapters.code_adapter import CodeAdapter
from charlie.self_extension.adapters.config_adapter import ConfigAdapter
from charlie.self_extension.adapters.skill_adapter import SkillAdapter, SkillAdapterResult
from charlie.self_extension.checkpoint import CheckpointManager
from charlie.self_extension.classifier import ExtensionClassifier
from charlie.self_extension.code_worker import run_worker, validate_ast_allow_list
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

def repeated_success_signature(request: str, capability: str, operation: str) -> Optional[str]:
    """Hash an exact, normalized owner request and the operation that verified it."""
    from charlie.tools import _contains_sensitive_memory_content

    text = str(request or "").strip()
    capability_id = str(capability or "").strip().casefold()
    operation_id = str(operation or "").strip().casefold()
    if (
        not text
        or len(text) > 500
        or _contains_sensitive_memory_content(text)
        or not re.fullmatch(r"[a-z0-9_.:-]{1,100}", capability_id)
        or not re.fullmatch(r"[a-z0-9_.:-]{1,100}", operation_id)
    ):
        return None

    normalized = unicodedata.normalize("NFKC", text)
    normalized = re.sub(r"(?i)^\s*charlie(?:[,:\s]+)", "", normalized, count=1)
    normalized = " ".join(normalized.casefold().split()).rstrip(" .!?")
    if len(normalized) < 8:
        return None

    # ponytail: exact normalized wording only; semantic clustering risks merging unrelated actions.
    payload = json.dumps([normalized, capability_id, operation_id], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def repeated_success_audit_metadata(envelope: Any, channel: str, tool_name: str = "") -> Dict[str, Any]:
    """Return privacy-safe learning metadata only for safe owner Telegram successes."""
    verification_status = getattr(envelope.verification_status, "value", envelope.verification_status)
    risk_class = getattr(envelope.risk_class, "value", envelope.risk_class)
    turn_id = envelope.turn_id
    if (
        channel != "telegram"
        or tool_name == "shell_execute"
        or not turn_id
        or verification_status != "verified_success"
        or risk_class != "safe"
        or envelope.requires_approval
        or envelope.data.get("persistence_status") == "failed"
    ):
        return {}
    signature = repeated_success_signature(
        envelope.request,
        envelope.capability or "",
        envelope.operation or tool_name,
    )
    if not signature:
        return {}
    return {
        "turn_id": turn_id,
        "verification_status": verification_status,
        "risk_class": risk_class,
        "requires_approval": False,
        "repeated_success_signature": signature,
    }


def verified_success_turn_ids(audit_entries: List[dict], signature: str) -> set[str]:
    """Return distinct turns with persisted, safe, semantically verified successes."""
    turns: set[str] = set()
    for entry in audit_entries:
        try:
            arguments = json.loads(entry.get("arguments", "{}"))
        except (TypeError, ValueError):
            continue
        outcome = str(entry.get("outcome", ""))
        if (
            not isinstance(arguments, dict)
            or outcome.split(":", 1)[0] != "completed"
            or "persistence_failed" in outcome
            or arguments.get("repeated_success_signature") != signature
            or arguments.get("verification_status") != "verified_success"
            or arguments.get("risk_class") != "safe"
            or arguments.get("requires_approval") is not False
        ):
            continue
        turn_id = arguments.get("turn_id")
        if isinstance(turn_id, str) and turn_id:
            turns.add(turn_id)
    return turns


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
        tool_registry: Optional[Any] = None,
        doctor: Optional[Any] = None,
        code_index: Optional[Any] = None,
        self_knowledge: Optional[Any] = None,
        introspector: Optional[Any] = None,
        tx_store_path: Optional[Path] = None,
        settings_snapshot_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._repo_root = (repo_root or Path(os.getcwd())).resolve()
        self._config = config or Config()
        if settings_service is None:
            raise ValueError("SelfExtensionOrchestrator requires main-owned SettingsService")
        self._settings_service = settings_service
        self._settings_snapshot_callback = settings_snapshot_callback
        self._capability_index = capability_index if capability_index is not None else get_capability_index()
        self._event_bus = event_bus
        self._event_loop = event_loop
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
            skills_dir=skills_dir or self._repo_root / "skills",
            registry=self._registry,
            capability_index=self._capability_index,
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
        )

        # Resume any transactions left in RESTARTING state
        self._resume_restarting_transactions()

    def _notify_settings_snapshot(self, rationale: str) -> None:
        if self._settings_snapshot_callback is None:
            return
        try:
            self._settings_snapshot_callback(rationale)
        except Exception:
            logger.warning("Settings snapshot callback failed", exc_info=True)

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
            request.planning_error = (
                "Prompt-created skills are disabled; import a manually authored SKILL.md through Extensions."
            )
        elif kind == ExtensionKind.CODE_SMALL:
            request.planning_error = "Model-authored code generation and repair are disabled."
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

    @classmethod
    def _skill_instruction_text(cls, prompt: str) -> str:
        """Keep staging commands out of the candidate's reusable instructions."""
        text = cls._safe_markdown_text(prompt)
        text = re.sub(
            r"(?i)^(?:please\s+)?(?:create|add|stage|propose|install)\b.*?\bskill\b"
            r"(?:\s+(?:named|called)\s+[A-Za-z0-9_-]+)?\s*",
            "",
            text,
            count=1,
        )
        purpose = re.search(r"(?i)\b(?:purpose|instructions?)\s*:\s*(.+)$", text)
        if purpose:
            text = purpose.group(1)
        else:
            text = re.sub(r"(?i)^(?:the skill|it)\s+(?:must|should|will)\s+", "", text)
        text = re.sub(
            r"(?i)\s*(?:this skill is for review only|stage it(?: as)? inactive|"
            r"do not activate(?: it)?|don't activate(?: it)?|do not inspect files).*$",
            "",
            text,
        ).strip(" \t\r\n.;:")
        return text or cls._safe_markdown_text(prompt)

    @staticmethod
    def _plan_name(prompt: str, fallback: str) -> str:
        match = re.search(r"\b(?:called|named)\s+([A-Za-z][A-Za-z0-9_-]*)", prompt, re.IGNORECASE)
        if not match:
            match = re.search(r"\b(?:skill|server)\s+([A-Za-z][A-Za-z0-9_-]*)", prompt, re.IGNORECASE)
        candidate = match.group(1).lower().replace("-", "_") if match else fallback
        if candidate in {"for", "that", "to", "with", "a", "an", "the"}:
            return fallback
        return candidate[:48]

    def _build_code_plan(self, prompt: str) -> tuple[str, str, Dict[str, Any], Any]:
        """Refuse legacy callers that request model-authored Python source."""
        raise ValueError("Model-authored code generation and repair are disabled.")

    @staticmethod
    def _parse_code_proposal(
        raw: str,
        requested_name: str,
    ) -> tuple[str, str, Dict[str, Any]]:
        """Read the proposed name, module source, and worker test inputs."""
        text = raw.strip()
        fenced = re.search(r"```(?:json|python)?\s*(.+?)```", text, re.DOTALL)
        if fenced:
            text = fenced.group(1).strip()
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("the completion contained no JSON object.")
        try:
            payload = json.loads(text[start:end + 1])
        except ValueError as exc:
            raise ValueError(f"the completion's JSON did not parse: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("the completion's JSON was not an object.")

        name = requested_name or str(payload.get("name") or "").strip()
        source = payload.get("source") or payload.get("code")
        inputs = payload.get("test_inputs")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,47}", name):
            raise ValueError(f"the proposed function name {name!r} is not a usable identifier.")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("the completion carried no source.")
        if not isinstance(inputs, dict):
            raise ValueError("the completion carried no test_inputs object.")
        return name, source.strip(), inputs

    @staticmethod
    def _check_code_contract(source: str, name: str, inputs: Dict[str, Any]) -> None:
        """Enforce the function contract the worker and the capability registry need."""
        tree = validate_ast_allow_list(source, name)
        defs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
        if len(defs) != 1 or any(
            not isinstance(n, (ast.FunctionDef, ast.Import, ast.ImportFrom)) for n in tree.body
        ):
            raise ValueError("the module must contain exactly one top-level function and its imports.")

        func = defs[0]
        if func.name != name:
            raise ValueError(f"the module defines '{func.name}', not the requested '{name}'.")
        if not ast.get_docstring(func):
            raise ValueError("the function needs a docstring; it becomes the capability description.")

        positional = {a.arg for a in func.args.args}
        if not set(inputs).issubset(positional):
            raise ValueError(
                f"test_inputs {sorted(inputs)} are not all parameters of '{name}' {sorted(positional)}."
            )
        required = [a.arg for a in func.args.args if a.default is None and a.arg not in inputs]
        if required:
            raise ValueError(f"test_inputs omit required parameter(s) {required}.")

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
                    kind = tx.request.classification.kind if tx.request.classification else None
                    if kind == ExtensionKind.CODE_SMALL or (tx.plan and tx.plan.kind == ExtensionKind.CODE_SMALL):
                        tx.status = TransactionStatus.FAILED
                        tx.error_message = "Model-authored code generation and repair are disabled; no rollback was attempted."
                        tx.finished_at = time.time()
                        self._transactions[tx_id] = tx
                        self._emit(EventType.SELF_EXTENSION_FAILED, {"tx_id": tx_id, "reason": tx.error_message})
                        self._persist_transactions()
                        continue
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
        return list(dict.fromkeys(ids))

    def _rollback_resumed_transaction(self, tx: ExtensionTransaction, reason: str) -> None:
        tx_id = tx.transaction_id
        settings_transaction = False
        self._emit(EventType.SELF_EXTENSION_ROLLBACK_STARTED, {"tx_id": tx_id})
        rollback_ok = True
        rollback_message = ""
        try:
            kind = (
                tx.request.classification.kind
                if tx.request.classification
                else ((tx.plan or tx.request.plan).kind if (tx.plan or tx.request.plan) else None)
            )
            settings_transaction = kind == ExtensionKind.CONFIG
            plan = tx.plan or tx.request.plan
            if kind == ExtensionKind.CODE_SMALL and plan and plan.tool_name:
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
        if settings_transaction:
            self._notify_settings_snapshot("self-extension settings rollback finalized")

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
          2. RuntimeIntrospector.get_capabilities_info() — affected capability must appear and be available
          3. CharlieDoctor.diagnose() — checks required by this extension kind must pass
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
                by_id = caps_info.get("by_id") if isinstance(caps_info, dict) else None
                if not isinstance(by_id, dict):
                    return False, "Required capability health is unknown: capability index was not readable."
                missing = [eid for eid in affected_ext_ids if eid not in by_id]
                if missing:
                    return False, (
                        f"Verification: expected capability/ies not visible after extension: {missing}"
                    )
                unavailable = []
                unknown = []
                for eid in affected_ext_ids:
                    record = by_id[eid]
                    if not isinstance(record, dict):
                        unknown.append(eid)
                        continue
                    health = record.get("health")
                    available = record.get("available")
                    unhealthy = (
                        available is False
                        or record.get("status") in {"degraded", "unavailable", "failed", "error"}
                        or (isinstance(health, dict) and health.get("available") is False)
                    )
                    if unhealthy:
                        unavailable.append(eid)
                    elif available is not True:
                        unknown.append(eid)
                    elif health is not None and (
                        not isinstance(health, dict) or health.get("available") is not True
                    ):
                        unknown.append(eid)
                if unavailable:
                    return False, f"Verification: expected capability/ies unavailable after extension: {unavailable}"
                if unknown:
                    return False, f"Verification: required capability health is unknown after extension: {unknown}"
            except Exception as exc:
                logger.error("Introspector caps check failed: %s", exc, exc_info=True)
                return False, f"Capability introspection failed: {exc}"

        self._emit(EventType.SELF_EXTENSION_HEALTH_CHECK, {"tx_id": tx_id})
        if self._doctor is None:
            return False, "CharlieDoctor unavailable for post-extension health check."
        try:
            report = self._doctor.diagnose()
            tx = self._transactions.get(tx_id)
            if tx is None:
                return False, "Required doctor checks are unknown: extension transaction metadata is unavailable."
            plan = tx.plan or tx.request.plan
            classification = tx.request.classification
            kind = classification.kind if classification is not None else (plan.kind if plan is not None else None)
            if kind == ExtensionKind.CONFIG:
                required_check_ids = ("config_validity",)
            elif kind in {ExtensionKind.SKILL, ExtensionKind.CODE_SMALL}:
                if not affected_ext_ids:
                    return False, "Required capability health is unknown: extension capability IDs are missing."
                required_check_ids = ("extensions_registry",)
            else:
                kind_name = getattr(kind, "value", kind) or "unknown"
                return False, f"Required doctor checks are unknown for extension kind '{kind_name}'."

            checks = report.get("checks") if isinstance(report, dict) else getattr(report, "checks", None)
            if not isinstance(checks, list):
                return False, "Required doctor checks are unknown: diagnostic checks were not returned."
            checks_by_id = {}
            for check in checks:
                check_id = (
                    check.get("check_id")
                    if isinstance(check, dict)
                    else getattr(check, "check_id", None)
                )
                if isinstance(check_id, str):
                    checks_by_id[check_id] = check
            for check_id in required_check_ids:
                check = checks_by_id.get(check_id)
                if check is None:
                    return False, f"Required doctor check '{check_id}' is unknown after extension."
                check_status = (
                    check.get("status")
                    if isinstance(check, dict)
                    else getattr(check, "status", None)
                )
                check_status = getattr(check_status, "value", check_status)
                summary = (
                    check.get("summary")
                    if isinstance(check, dict)
                    else getattr(check, "summary", None)
                )
                if check_status != "ok":
                    state = {
                        "error": "unhealthy",
                        "warning": "degraded",
                        "info": "unknown",
                    }.get(check_status, "unknown")
                    return False, (
                        f"Required doctor check '{check_id}' is {state} after extension: "
                        f"{summary or 'no diagnostic evidence'}"
                    )
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
                    name_part = ext_id.replace("code_", "").replace("skill_", "")
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

    @staticmethod
    def _approval_covers(guard_decision: Any, approved_binding: Optional[str]) -> bool:
        """An approval only counts when it matches the launch binding reviewed.

        An unbound decision (no resolvable argv) is never covered, so an
        approval recorded for one command cannot authorise another.
        """
        if not approved_binding or not guard_decision.approval_binding:
            return False
        return approved_binding == guard_decision.approval_binding

    def execute_transaction(
        self,
        request: ExtensionRequest,
        *,
        approved_binding: Optional[str] = None,
    ) -> ExtensionResult:
        """
        Process a generic extension request through classification, guard evaluation,
        and type-specific execution.

        CODE_SMALL requires a validated ExtensionPlan with the actual structured
        payload attached. Raw user_prompt is never treated as executable code.
        ARCHITECTURE_LARGE always requires explicit approval.

        ``approved_binding`` is the approval digest the owner granted.  It only
        authorises the request when it matches the exact argv the guard just
        reviewed; a different command or argument list is a different decision.
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

        if kind in {ExtensionKind.SKILL, ExtensionKind.CODE_SMALL}:
            message = (
                "Prompt-created skills are disabled; import a manually authored SKILL.md through Extensions."
                if kind == ExtensionKind.SKILL
                else "Model-authored code generation and repair are disabled."
            )
            tx.status = TransactionStatus.FAILED
            tx.error_message = message
            self._emit(EventType.SELF_EXTENSION_FAILED, {"tx_id": tx_id, "reason": message})
            return ExtensionResult(
                success=False,
                transaction_id=tx_id,
                status=TransactionStatus.FAILED,
                message=message,
            )

        # 2. Guard evaluation
        guard_decision = self._guard.evaluate(request)
        tx.guard_decision = guard_decision

        if guard_decision.requires_approval or not guard_decision.is_authorized:
            approved = self._approval_covers(guard_decision, approved_binding)
            if not approved:
                reason = guard_decision.reason
                if approved_binding:
                    reason = (
                        "APPROVAL_MISMATCH: the recorded approval does not cover this exact command "
                        f"('{' '.join(guard_decision.approved_argv) or 'unresolved'}'). A different "
                        "executable or argument list is a different decision; review it again."
                    )
                tx.status = TransactionStatus.APPROVAL_REQUIRED
                self._emit(
                    EventType.SELF_EXTENSION_APPROVAL_REQUIRED,
                    {"tx_id": tx_id, "reason": reason},
                )
                return ExtensionResult(
                    success=False,
                    transaction_id=tx_id,
                    status=TransactionStatus.APPROVAL_REQUIRED,
                    message=reason,
                )

        self._emit(EventType.SELF_EXTENSION_PLANNED, {"tx_id": tx_id})

        # 3. Route by kind
        if kind == ExtensionKind.CONFIG:
            return self.execute_config_transaction(request, request.affected_settings, tx_id=tx_id)

        elif kind == ExtensionKind.SKILL:
            skill_id = request.affected_capabilities[0] if request.affected_capabilities else "skill_custom_skill"
            skill_name = skill_id.removeprefix("skill_")
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
            if not request.explicit_user_request:
                tx.status = TransactionStatus.APPROVAL_REQUIRED
                return ExtensionResult(
                    success=False,
                    transaction_id=tx_id,
                    status=TransactionStatus.APPROVAL_REQUIRED,
                    message="Staging a skill requires an explicit owner request.",
                )
            return self.stage_skill_candidate_transaction(
                request,
                skill_name=skill_name,
                raw_text=raw_text,
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

    def stage_skill_candidate_transaction(
        self,
        request: ExtensionRequest,
        skill_name: str,
        raw_text: str,
        tx_id: Optional[str] = None,
    ) -> ExtensionResult:
        """Refuse prompt-generated skill candidates; manual imports remain supported."""
        transaction_id = tx_id or f"tx-{uuid.uuid4().hex[:8]}"
        return ExtensionResult(
            success=False,
            transaction_id=transaction_id,
            status=TransactionStatus.FAILED,
            message="Prompt-created skills are disabled; import a manually authored SKILL.md through Extensions.",
        )

    def stage_reusable_correction_candidate(self, correction: str) -> Optional[ExtensionResult]:
        """Automatic correction learning is disabled; user memory remains available."""
        return None

    def stage_repeated_success_candidate(
        self,
        request_text: str,
        capability: str,
        operation: str,
        audit_entries: List[dict],
        *,
        current_turn_id: str,
        expected_signature: str,
        risk_class: str,
        requires_approval: bool,
    ) -> Optional[ExtensionResult]:
        """Automatic repeated-success learning is disabled."""
        return None

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
        try:
            gate_ok, gate_msg = self._run_verification_gate(transaction_id, affected_ext_ids=None)
        except Exception:
            self._notify_settings_snapshot("self-extension settings transaction failed")
            raise
        if not gate_ok:
            self._config_adapter.rollback(preimage)
            tx.status = TransactionStatus.ROLLED_BACK
            self._emit(EventType.SELF_EXTENSION_ROLLED_BACK, {"tx_id": transaction_id})
            self._notify_settings_snapshot("self-extension settings transaction rolled back")
            return ExtensionResult(
                success=False,
                transaction_id=transaction_id,
                status=TransactionStatus.ROLLED_BACK,
                message=f"Config extension rolled back — verification gate failed: {gate_msg}",
            )

        tx.status = TransactionStatus.COMPLETED
        tx.finished_at = time.time()
        self._emit(EventType.SELF_EXTENSION_COMPLETED, {"tx_id": transaction_id})
        self._notify_settings_snapshot("self-extension settings transaction completed")
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
        """Refuse prompt-generated skill activation; manual imports use Extensions."""
        transaction_id = tx_id or f"tx-{uuid.uuid4().hex[:8]}"
        return ExtensionResult(
            success=False,
            transaction_id=transaction_id,
            status=TransactionStatus.FAILED,
            message="Prompt-created skills are disabled; import a manually authored SKILL.md through Extensions.",
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
        """Refuse legacy model-authored code activation without touching files."""
        transaction_id = tx_id or f"tx-{uuid.uuid4().hex[:8]}"
        return ExtensionResult(
            success=False,
            transaction_id=transaction_id,
            status=TransactionStatus.FAILED,
            message="Model-authored code generation and repair are disabled.",
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

        request_kind = getattr(getattr(tx.request, "classification", None), "kind", None)
        settings_transaction = getattr(request_kind, "value", request_kind) == "config"
        tx.status = TransactionStatus.ROLLING_BACK
        self._emit(EventType.SELF_EXTENSION_ROLLBACK_STARTED, {"tx_id": transaction_id})

        if tx.checkpoint and tx.checkpoint.config_preimage:
            self._config_adapter.rollback(tx.checkpoint.config_preimage)

        if tx.checkpoint and (tx.checkpoint.affected_files_preimage or tx.checkpoint.new_files_created):
            self._checkpoint_mgr.rollback(tx.checkpoint)

        tx.status = TransactionStatus.ROLLED_BACK
        self._emit(EventType.SELF_EXTENSION_ROLLED_BACK, {"tx_id": transaction_id})
        if settings_transaction:
            self._notify_settings_snapshot("self-extension settings rollback finalized")

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

    def list_skill_candidates(self) -> List[dict]:
        return self._skill_adapter.list_skill_candidates()

    def stage_skill_candidate(self, name: str, raw_text: str):
        return SkillAdapterResult(
            False,
            "Prompt-created skills are disabled; import a manually authored SKILL.md through Extensions.",
            name,
        )

    def resolve_skill_candidate_review_token(self, token: str, expected_status: str = "pending"):
        return self._skill_adapter.resolve_skill_candidate_review_token(token, expected_status)

    def mark_skill_candidate_review_submitted(self, name: str, content_hash: str) -> bool:
        return self._skill_adapter.mark_skill_candidate_review_submitted(name, content_hash)

    def approve_skill_candidate(self, name: str, content_hash: str):
        return self._skill_adapter.approve_skill_candidate(name, content_hash)

    def reject_skill_candidate(self, name: str, content_hash: str):
        return self._skill_adapter.reject_skill_candidate(name, content_hash)

    def disable_skill_candidate(self, name: str, content_hash: str):
        return self._skill_adapter.disable_skill_candidate(name, content_hash)

    def get_active_skill_blocks(self) -> Dict[str, str]:
        return self._skill_adapter.get_active_skill_blocks()

    def get_transaction(self, transaction_id: str) -> Optional[Dict[str, Any]]:
        tx = self._transactions.get(transaction_id)
        return tx.to_dict() if tx else None

    def list_transactions(self) -> List[Dict[str, Any]]:
        return [tx.to_dict() for tx in self._transactions.values()]
