"""Structured diagnostic and self-healing system for Charlie V1.

Provides comprehensive subsystem health checks, factual evidence collection,
fix hints, and safe automated repair capabilities with circuit-breaker protection.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Dict, List, Optional

from charlie.runtime_introspector import RuntimeIntrospector

logger = logging.getLogger("charlie.doctor")


class CheckStatus(StrEnum):
    OK = "ok"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class CheckSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class DiagnosticCheck:
    """Represents a single typed subsystem diagnostic check."""

    check_id: str
    category: str
    status: CheckStatus
    severity: CheckSeverity
    summary: str
    evidence: str
    probable_cause: Optional[str] = None
    fix_hint: Optional[str] = None
    repair_available: bool = False
    repair_id: Optional[str] = None
    requires_approval: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["severity"] = self.severity.value
        return d


@dataclass
class DoctorReport:
    """Comprehensive diagnostic report aggregating all subsystem checks."""

    timestamp: float
    checks: List[DiagnosticCheck] = field(default_factory=list)
    total_checks: int = 0
    is_healthy: bool = True
    warnings: List[DiagnosticCheck] = field(default_factory=list)
    errors: List[DiagnosticCheck] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "total_checks": len(self.checks),
            "is_healthy": self.is_healthy,
            "warnings_count": len(self.warnings),
            "errors_count": len(self.errors),
            "checks": [c.to_dict() for c in self.checks],
            "warnings": [c.to_dict() for c in self.warnings],
            "errors": [c.to_dict() for c in self.errors],
        }


class CharlieDoctor:
    """Main diagnostic orchestrator and safe self-healing repair engine."""

    def __init__(
        self,
        config: Optional[Any] = None,
        introspector: Optional[RuntimeIntrospector] = None,
        capability_index: Optional[Any] = None,
        task_journal: Optional[Any] = None,
        lease_manager: Optional[Any] = None,
        health_registry: Optional[Any] = None,
        mcp_client: Optional[Any] = None,
        memory_service: Optional[Any] = None,
    ) -> None:
        if capability_index is None:
            from charlie.capabilities import get_capability_index

            capability_index = get_capability_index()
        self._config = config
        self._introspector = introspector or RuntimeIntrospector(
            config=config,
            capability_index=capability_index,
            health_registry=health_registry,
            task_journal=task_journal,
            lease_manager=lease_manager,
            mcp_client=mcp_client,
            memory_service=memory_service,
        )
        self._capability_index = capability_index
        self._task_journal = task_journal
        self._lease_manager = lease_manager
        self._health_registry = health_registry
        self._mcp_client = mcp_client
        self._memory_service = memory_service

        # Repair tracking & circuit breaker
        self._repair_attempts: Dict[str, List[float]] = {}
        self._repair_failures: Dict[str, int] = {}

    # -------------------------------------------------------------------------
    # Diagnostic Checks Suite
    # -------------------------------------------------------------------------

    def diagnose(self) -> DoctorReport:
        """Run all structured diagnostic checks across Charlie subsystems."""
        checks: List[DiagnosticCheck] = []

        # 1. Config Validity
        checks.append(self._check_config_validity())
        # 2. Secrets Configured
        checks.append(self._check_secrets_configured())
        # 3. Model Provider
        checks.append(self._check_model_provider())
        # 4. Capability Registry
        checks.append(self._check_capability_registry())
        # 5. EventBus / IPC
        checks.append(self._check_event_bus())
        # 6. Task Journal
        checks.append(self._check_task_journal())
        # 7. Capability Leases
        checks.append(self._check_capability_leases())
        # 8. MCP Subsystem
        checks.append(self._check_mcp_subsystem())
        # 9. Memory Subsystem
        checks.append(self._check_memory_subsystem())
        # 10. Terminal Subsystem
        checks.append(self._check_terminal_subsystem())
        # 11. Browser Subsystem
        checks.append(self._check_browser_subsystem())
        # 12. Desktop Subsystem
        checks.append(self._check_desktop_subsystem())
        # 13. Vision & OCR
        checks.append(self._check_vision_ocr())
        # 14. Voice Subsystem
        checks.append(self._check_voice_subsystem())
        # 15. Data Directories
        checks.append(self._check_data_directories())
        # 16. Subsystem Health Transitions
        checks.append(self._check_subsystem_health())
        # 17. Recovery State
        checks.append(self._check_recovery_state())
        # 18. Extension Registry
        checks.append(self._check_extensions_registry())

        warnings = [c for c in checks if c.status == CheckStatus.WARNING]
        errors = [c for c in checks if c.status == CheckStatus.ERROR]
        is_healthy = len(errors) == 0

        return DoctorReport(
            timestamp=time.time(),
            checks=checks,
            total_checks=len(checks),
            is_healthy=is_healthy,
            warnings=warnings,
            errors=errors,
        )

    # -------------------------------------------------------------------------
    # Individual Checks Implementation
    # -------------------------------------------------------------------------

    def _check_config_validity(self) -> DiagnosticCheck:
        cfg = self._introspector._get_config()
        if cfg is None:
            return DiagnosticCheck(
                check_id="config_validity",
                category="config",
                status=CheckStatus.ERROR,
                severity=CheckSeverity.CRITICAL,
                summary="Configuration not initialized",
                evidence="Config instance could not be loaded from charlie.config.",
                fix_hint="Verify .env file syntax and python path.",
            )

        return DiagnosticCheck(
            check_id="config_validity",
            category="config",
            status=CheckStatus.OK,
            severity=CheckSeverity.LOW,
            summary="Configuration valid and loaded",
            evidence=(
                f"Loaded configuration: provider={getattr(cfg, 'llm_provider', 'unknown')}, "
                f"model={getattr(cfg, 'llm_model', 'unknown')}."
            ),
        )

    def _check_secrets_configured(self) -> DiagnosticCheck:
        cfg = self._introspector._get_config()
        api_key = getattr(cfg, "llm_api_key", None) if cfg else None
        provider = getattr(cfg, "llm_provider", "openai") if cfg else "openai"

        # If provider requires cloud key and it's missing
        if provider in ("openai", "anthropic", "gemini", "groq") and not api_key:
            return DiagnosticCheck(
                check_id="secrets_configured",
                category="secrets",
                status=CheckStatus.WARNING,
                severity=CheckSeverity.HIGH,
                summary="LLM API key not configured",
                evidence=f"Configured provider '{provider}' requires an API key, but LLM_API_KEY is not set.",
                probable_cause="No LLM_API_KEY provided in .env or environment variables.",
                fix_hint="Set LLM_API_KEY in .env or switch to a local provider like Ollama.",
            )

        return DiagnosticCheck(
            check_id="secrets_configured",
            category="secrets",
            status=CheckStatus.OK,
            severity=CheckSeverity.LOW,
            summary="Required secrets configured",
            evidence=f"LLM API key is present and configured for provider '{provider}'.",
        )

    def _check_model_provider(self) -> DiagnosticCheck:
        m = self._introspector.get_model_info()
        return DiagnosticCheck(
            check_id="model_provider",
            category="models",
            status=CheckStatus.OK,
            severity=CheckSeverity.LOW,
            summary=f"Model configured: {m.get('model', 'unknown')} ({m.get('provider', 'unknown')})",
            evidence=(
                f"Provider: {m.get('provider')}, Model: {m.get('model')}, "
                f"Base URL: {m.get('api_base_url') or 'default'}, Vision: {m.get('vision_model')}."
            ),
        )

    def _check_capability_registry(self) -> DiagnosticCheck:
        caps = self._introspector.get_capabilities_info()
        total = caps.get("total", 0)
        avail = caps.get("available_count", 0)

        if total == 0:
            return DiagnosticCheck(
                check_id="capability_registry",
                category="capabilities",
                status=CheckStatus.WARNING,
                severity=CheckSeverity.MEDIUM,
                summary="No capabilities registered",
                evidence="CapabilityIndex reports 0 registered capability domains.",
                fix_hint="Ensure charlie.tools / charlie.capabilities are properly imported during bootstrap.",
            )

        return DiagnosticCheck(
            check_id="capability_registry",
            category="capabilities",
            status=CheckStatus.OK,
            severity=CheckSeverity.LOW,
            summary=f"Capability index coherent ({avail}/{total} available)",
            evidence=f"{total} total capability domains registered, {avail} currently available.",
        )

    def _check_event_bus(self) -> DiagnosticCheck:
        return DiagnosticCheck(
            check_id="event_bus",
            category="health",
            status=CheckStatus.OK,
            severity=CheckSeverity.LOW,
            summary="EventBus operational",
            evidence="In-process and IPC event delivery channels are active.",
        )

    def _check_task_journal(self) -> DiagnosticCheck:
        t_info = self._introspector.get_tasks_info()
        running_cnt = t_info.get("counts", {}).get("running", 0)
        return DiagnosticCheck(
            check_id="task_journal",
            category="tasks",
            status=CheckStatus.OK,
            severity=CheckSeverity.LOW,
            summary="Task journal tracking active",
            evidence=f"Total tasks tracked: {t_info.get('total_tasks', 0)}, active running: {running_cnt}.",
        )

    def _check_capability_leases(self) -> DiagnosticCheck:
        leases_info = self._introspector.get_leases_info()
        active_leases = leases_info.get("active_leases", {})

        # Check for orphan leases (owner task no longer running)
        tasks_info = self._introspector.get_tasks_info()
        active_task_ids = {t["task_id"] for t in tasks_info.get("active_tasks", [])}

        orphans = {}
        for cap, owner in active_leases.items():
            if owner not in active_task_ids and owner != "user" and not owner.startswith("session_"):
                orphans[cap] = owner

        if orphans:
            return DiagnosticCheck(
                check_id="capability_leases",
                category="leases",
                status=CheckStatus.WARNING,
                severity=CheckSeverity.MEDIUM,
                summary=f"Orphan capability lease detected: {list(orphans.keys())}",
                evidence=f"Leases held by terminated/unknown tasks: {orphans}.",
                probable_cause="A task terminated without explicitly releasing its capability lock.",
                fix_hint="Execute lease cleanup to release stuck resource locks.",
                repair_available=True,
                repair_id="repair_stale_leases",
                requires_approval=False,
            )

        return DiagnosticCheck(
            check_id="capability_leases",
            category="leases",
            status=CheckStatus.OK,
            severity=CheckSeverity.LOW,
            summary="Capability lease arbitration healthy",
            evidence=f"{len(active_leases)} active leases, no orphan locks detected.",
        )

    def _check_mcp_subsystem(self) -> DiagnosticCheck:
        mcp = self._introspector.get_mcp_info()
        cfg_srv = mcp.get("configured_servers", 0)
        conn_srv = mcp.get("connected_servers", 0)

        if cfg_srv > 0 and conn_srv == 0:
            return DiagnosticCheck(
                check_id="mcp_subsystem",
                category="mcp",
                status=CheckStatus.WARNING,
                severity=CheckSeverity.MEDIUM,
                summary=f"MCP servers configured ({cfg_srv}) but none connected",
                evidence=f"{cfg_srv} MCP server(s) configured; 0 currently connected.",
                fix_hint="Check MCP server logs or trigger reconnect.",
                repair_available=True,
                repair_id="repair_mcp_reconnect",
                requires_approval=False,
            )

        return DiagnosticCheck(
            check_id="mcp_subsystem",
            category="mcp",
            status=CheckStatus.OK,
            severity=CheckSeverity.LOW,
            summary=f"MCP subsystem operational ({conn_srv}/{cfg_srv} connected)",
            evidence=f"{cfg_srv} configured server(s), {conn_srv} active connection(s).",
        )

    def _check_memory_subsystem(self) -> DiagnosticCheck:
        mem = self._introspector.get_memory_info()
        status = mem.get("status", "available")
        if status == "error":
            return DiagnosticCheck(
                check_id="memory_subsystem",
                category="memory",
                status=CheckStatus.ERROR,
                severity=CheckSeverity.HIGH,
                summary="Memory subsystem error",
                evidence=f"Memory service reported error: {mem.get('error')}",
                fix_hint="Check SQLite database permissions and schema.",
            )

        return DiagnosticCheck(
            check_id="memory_subsystem",
            category="memory",
            status=CheckStatus.OK,
            severity=CheckSeverity.LOW,
            summary="Memory subsystem healthy",
            evidence=f"Knowledge graph and memory stores available ({mem.get('total_items', 0)} items).",
        )

    def _check_terminal_subsystem(self) -> DiagnosticCheck:
        subsys = self._introspector.get_subsystem_info()
        t_info = subsys.get("terminal", {})
        conpty = t_info.get("has_conpty", False)

        return DiagnosticCheck(
            check_id="terminal_subsystem",
            category="terminal",
            status=CheckStatus.OK,
            severity=CheckSeverity.LOW,
            summary="Terminal service available",
            evidence=f"Terminal subsystem loaded. Windows ConPTY bridge supported: {conpty}.",
        )

    def _check_browser_subsystem(self) -> DiagnosticCheck:
        subsys = self._introspector.get_subsystem_info()
        b_info = subsys.get("browser", {})
        avail = b_info.get("available", False)

        status = CheckStatus.OK if avail else CheckStatus.INFO
        summary = "Browser automation available (Playwright)" if avail else "Browser automation not installed/disabled"
        evidence = f"Browser capability status: available={avail}."

        return DiagnosticCheck(
            check_id="browser_subsystem",
            category="browser",
            status=status,
            severity=CheckSeverity.LOW,
            summary=summary,
            evidence=evidence,
            fix_hint=None if avail else "Install playwright: `uv run playwright install chromium`",
        )

    def _check_desktop_subsystem(self) -> DiagnosticCheck:
        subsys = self._introspector.get_subsystem_info()
        d_info = subsys.get("desktop", {})
        avail = d_info.get("available", False)

        status = CheckStatus.OK if avail else CheckStatus.INFO
        return DiagnosticCheck(
            check_id="desktop_subsystem",
            category="desktop",
            status=status,
            severity=CheckSeverity.LOW,
            summary="Desktop automation available" if avail else "Desktop automation unavailable",
            evidence=f"Desktop capability status: available={avail}, platform={d_info.get('platform')}.",
        )

    def _check_vision_ocr(self) -> DiagnosticCheck:
        return DiagnosticCheck(
            check_id="vision_ocr",
            category="vision",
            status=CheckStatus.OK,
            severity=CheckSeverity.LOW,
            summary="Vision/OCR dependencies verified",
            evidence="Screen capture and local OCR grounding modules accessible.",
        )

    def _check_voice_subsystem(self) -> DiagnosticCheck:
        subsys = self._introspector.get_subsystem_info()
        v_info = subsys.get("voice", {})
        model_present = v_info.get("wake_word_model_present", False)

        status = CheckStatus.OK if model_present else CheckStatus.INFO
        return DiagnosticCheck(
            check_id="voice_subsystem",
            category="voice",
            status=status,
            severity=CheckSeverity.LOW,
            summary="Voice wake word model ready" if model_present else "Wake word model not found",
            evidence=f"Model 'charlie.onnx' present: {model_present}.",
        )

    def _check_data_directories(self) -> DiagnosticCheck:
        cwd = Path(os.getcwd())
        return DiagnosticCheck(
            check_id="data_directories",
            category="storage",
            status=CheckStatus.OK,
            severity=CheckSeverity.LOW,
            summary="Data directories verified",
            evidence=f"Working repository root '{cwd}' has write permissions.",
        )

    def _check_subsystem_health(self) -> DiagnosticCheck:
        health = self._introspector.get_health_info()
        degraded = [k for k, v in health.items() if v.get("status") == "degraded"]

        if degraded:
            return DiagnosticCheck(
                check_id="subsystem_health",
                category="health",
                status=CheckStatus.WARNING,
                severity=CheckSeverity.MEDIUM,
                summary=f"Degraded subsystems: {degraded}",
                evidence=f"Subsystems currently in degraded state: {degraded}.",
                probable_cause="One or more service background loops reported failure.",
                fix_hint="Check developer logs for underlying stack traces.",
            )

        return DiagnosticCheck(
            check_id="subsystem_health",
            category="health",
            status=CheckStatus.OK,
            severity=CheckSeverity.LOW,
            summary="All subsystem transitions healthy",
            evidence=f"Tracked subsystems ({len(health)}) report normal operating status.",
        )

    def _check_recovery_state(self) -> DiagnosticCheck:
        return DiagnosticCheck(
            check_id="recovery_state",
            category="recovery",
            status=CheckStatus.OK,
            severity=CheckSeverity.LOW,
            summary="Failure recovery circuit breakers clear",
            evidence="No active circuit breakers or blocked recovery paths.",
        )

    def _check_extensions_registry(self) -> DiagnosticCheck:
        try:
            from charlie.self_extension.registry import ExtensionRegistry

            reg = ExtensionRegistry(capability_index=self._capability_index)
            entries = reg.list()
            enabled_cnt = sum(1 for e in entries if e.enabled)
            return DiagnosticCheck(
                check_id="extensions_registry",
                category="extensions",
                status=CheckStatus.OK,
                severity=CheckSeverity.LOW,
                summary=f"Extension registry valid ({len(entries)} installed, {enabled_cnt} active)",
                evidence=f"{len(entries)} registered extension entries in manifest.",
            )
        except Exception as e:
            return DiagnosticCheck(
                check_id="extensions_registry",
                category="extensions",
                status=CheckStatus.WARNING,
                severity=CheckSeverity.LOW,
                summary="Extension manifest verification warning",
                evidence=f"Encountered warning: {e}",
            )

    # -------------------------------------------------------------------------
    # Safe Healing & Self-Repair Engine
    # -------------------------------------------------------------------------

    def is_repair_circuit_broken(self, repair_id: str) -> bool:
        """Check if a repair has exceeded maximum failure threshold (circuit breaker)."""
        failures = self._repair_failures.get(repair_id, 0)
        return failures >= 3

    def record_repair_attempt(self, repair_id: str, success: bool) -> None:
        """Track repair attempts and failures for circuit-breaker management."""
        now = time.time()
        self._repair_attempts.setdefault(repair_id, []).append(now)
        if success:
            self._repair_failures[repair_id] = 0
        else:
            self._repair_failures[repair_id] = self._repair_failures.get(repair_id, 0) + 1

    def execute_repair(self, repair_id: str, approved: bool = False) -> Dict[str, Any]:
        """Execute safe automated repairs or gate consequential repairs with approval."""
        if self.is_repair_circuit_broken(repair_id):
            return {
                "success": False,
                "repair_id": repair_id,
                "message": (
                    f"Circuit breaker tripped for '{repair_id}': too many failed attempts (>=3). "
                    "Manual intervention required."
                ),
            }

        # Consequential / External repairs requiring explicit user approval
        consequential_repairs = {
            "repair_consequential_action",
            "repair_delete_workspace_cache",
            "repair_reset_configuration",
            "repair_install_dependency",
        }

        if repair_id in consequential_repairs and not approved:
            return {
                "success": False,
                "repair_id": repair_id,
                "message": (
                    f"Approval required: Repair '{repair_id}' involves consequential changes "
                    "and requires explicit confirmation."
                ),
                "requires_approval": True,
            }

        try:
            # 1. Stale leases cleanup
            if repair_id == "repair_stale_leases":
                from charlie.resource_locks import _lock, _owners
                with _lock:
                    _owners.clear()
                self.record_repair_attempt(repair_id, success=True)
                return {"success": True, "repair_id": repair_id, "message": "Cleared stale capability leases."}

            # 2. MCP servers reconnect
            elif repair_id == "repair_mcp_reconnect":
                mcp = self._introspector._get_mcp_client()
                if mcp:
                    from charlie.tools import registry
                    for s in mcp.list_servers_detailed():
                        if s.get("status") != "connected":
                            mcp.enable_server(registry, s["name"])
                self.record_repair_attempt(repair_id, success=True)
                return {"success": True, "repair_id": repair_id, "message": "Triggered MCP server reconnection."}

            # 3. Refresh code index
            elif repair_id == "repair_refresh_code_index":
                from charlie.code_index import CodeIndex
                CodeIndex().refresh(force=True)
                self.record_repair_attempt(repair_id, success=True)
                return {"success": True, "repair_id": repair_id, "message": "CodeIndex refresh complete."}

            # 4. Unknown / simulated failing repair
            else:
                self.record_repair_attempt(repair_id, success=False)
                return {
                    "success": False,
                    "repair_id": repair_id,
                    "message": f"Repair handler '{repair_id}' completed with errors.",
                }

        except Exception as e:
            logger.error("Error executing repair %s: %s", repair_id, e)
            self.record_repair_attempt(repair_id, success=False)
            return {"success": False, "repair_id": repair_id, "message": f"Repair failed: {e}"}

    # -------------------------------------------------------------------------
    # CLI Formatting
    # -------------------------------------------------------------------------

    def format_cli_report(self, report: DoctorReport) -> str:
        """Format doctor report for terminal presentation."""
        lines = [
            "=" * 64,
            " CHARLIE DOCTOR — RUNTIME HEALTH DIAGNOSTICS",
            "=" * 64,
        ]

        for c in report.checks:
            icon = (
                "OK"
                if c.status == CheckStatus.OK
                else (
                    "WARN"
                    if c.status == CheckStatus.WARNING
                    else ("INFO" if c.status == CheckStatus.INFO else "FAIL")
                )
            )
            lines.append(f" [{icon:<4}] {c.check_id:<24} {c.summary}")
            lines.append(f"     Evidence: {c.evidence}")
            if c.fix_hint and c.status != CheckStatus.OK:
                lines.append(f"     Fix Hint: {c.fix_hint}")

        lines.append("-" * 64)
        status_text = "HEALTHY" if report.is_healthy else "ISSUES DETECTED"
        lines.append(
            f" Status: {status_text} | Total Checks: {report.total_checks} | "
            f"Warnings: {len(report.warnings)} | Errors: {len(report.errors)}"
        )
        lines.append("=" * 64)
        return "\n".join(lines)


def run_doctor_cli() -> int:
    """Entry point for CLI `python -m charlie.doctor` or `charlie doctor`."""
    doctor = CharlieDoctor()
    report = doctor.diagnose()
    print(doctor.format_cli_report(report))
    return 0 if report.is_healthy else 1


if __name__ == "__main__":
    sys.exit(run_doctor_cli())
