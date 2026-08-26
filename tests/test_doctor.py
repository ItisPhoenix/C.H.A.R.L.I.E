"""Tests for Charlie Doctor & Self-Healing."""

import tempfile

import pytest

from charlie.capabilities import CapabilityDescriptor, CapabilityIndex, CapabilityOperation
from charlie.config import Config
from charlie.doctor import CharlieDoctor, CheckSeverity, CheckStatus, DiagnosticCheck, DoctorReport
from charlie.resource_locks import CapabilityLeaseManager
from charlie.runtime_introspector import RuntimeIntrospector
from charlie.subsystem_health import HealthRegistry, HealthStatus
from charlie.task_journal import TaskJournal


@pytest.fixture
def mock_doctor_env():
    """Create isolated environment with healthy and simulated broken subsystems."""
    with tempfile.TemporaryDirectory():
        cfg = Config()
        cfg.llm_provider = "openai"
        cfg.llm_model = "gpt-4o"
        cfg.llm_api_key = "sk-test-valid-key"

        # Capabilities
        cap_idx = CapabilityIndex()
        cap_idx.register_capability(
            CapabilityDescriptor(
                id="system",
                name="System Control",
                description="OS commands",
                owner="charlie.tools",
                operations={
                    "ping": CapabilityOperation(
                        id="ping",
                        name="ping",
                        description="Ping",
                        parameters_schema={"type": "object"},
                        risk_class="safe",
                    )
                },
                availability_check=lambda: True,
                provenance="builtin",
            )
        )

        # Health Registry
        health = HealthRegistry(("brain", "voice", "browser", "desktop", "terminal", "memory"))
        health.set("brain", HealthStatus.RUNNING)
        health.set("voice", HealthStatus.RUNNING)
        health.set("browser", HealthStatus.RUNNING)
        health.set("desktop", HealthStatus.RUNNING)
        health.set("terminal", HealthStatus.RUNNING)
        health.set("memory", HealthStatus.RUNNING)

        # Task journal & lease manager
        journal = TaskJournal()
        lease_mgr = CapabilityLeaseManager()

        introspector = RuntimeIntrospector(
            config=cfg,
            capability_index=cap_idx,
            health_registry=health,
            task_journal=journal,
            lease_manager=lease_mgr,
        )

        doctor = CharlieDoctor(
            config=cfg,
            introspector=introspector,
            capability_index=cap_idx,
            task_journal=journal,
            lease_manager=lease_mgr,
            health_registry=health,
        )

        yield doctor, cfg, health, lease_mgr, journal


def test_doctor_healthy_report(mock_doctor_env):
    """Verify healthy subsystem diagnostics yield overall ok status."""
    doctor, _, _, _, _ = mock_doctor_env
    report = doctor.diagnose()

    assert isinstance(report, DoctorReport)
    assert report.total_checks > 10
    assert report.is_healthy is True
    assert len(report.errors) == 0

    # Verify each check has evidence and typed fields
    for check in report.checks:
        assert isinstance(check, DiagnosticCheck)
        assert check.status in (CheckStatus.OK, CheckStatus.INFO, CheckStatus.WARNING)
        assert len(check.evidence) > 0
        assert check.category in (
            "config", "secrets", "models", "capabilities", "tasks",
            "leases", "mcp", "memory", "terminal", "browser",
            "desktop", "vision", "voice", "storage", "health", "recovery", "extensions"
        )


def test_doctor_detects_unconfigured_api_key(mock_doctor_env):
    """Verify Doctor flags unconfigured cloud LLM API key with clear evidence."""
    doctor, cfg, _, _, _ = mock_doctor_env
    cfg.llm_api_key = ""
    cfg.llm_key = ""

    report = doctor.diagnose()
    secret_checks = [c for c in report.checks if c.check_id == "secrets_configured"]
    assert len(secret_checks) == 1
    sc = secret_checks[0]

    assert sc.status == CheckStatus.WARNING
    assert sc.severity == CheckSeverity.HIGH
    assert "not set" in sc.evidence.lower() or "missing" in sc.evidence.lower()
    assert sc.fix_hint is not None


def test_doctor_detects_orphan_capability_lease(mock_doctor_env):
    """Verify Doctor detects orphaned capability lease and offers safe auto-repair."""
    doctor, _, _, lease_mgr, journal = mock_doctor_env

    # Acquire lease for non-existent task
    from charlie.resource_locks import acquire as sync_acquire
    sync_acquire("desktop", "orphan-task-999")

    report = doctor.diagnose()
    lease_checks = [c for c in report.checks if c.check_id == "capability_leases"]
    assert len(lease_checks) == 1
    lc = lease_checks[0]

    assert lc.status == CheckStatus.WARNING
    assert "orphan" in lc.evidence.lower() or "desktop" in lc.evidence.lower()
    assert lc.repair_available is True
    assert lc.repair_id == "repair_stale_leases"
    assert lc.requires_approval is False  # Safe internal repair

    # Execute safe repair
    repair_res = doctor.execute_repair("repair_stale_leases")
    assert repair_res["success"] is True

    # Post-repair verification: lease should be cleared
    post_report = doctor.diagnose()
    post_lc = [c for c in post_report.checks if c.check_id == "capability_leases"][0]
    assert post_lc.status == CheckStatus.OK


def test_doctor_consequential_repair_requires_approval(mock_doctor_env):
    """Verify consequential repairs fail without explicit approval."""
    doctor, _, _, _, _ = mock_doctor_env

    res = doctor.execute_repair("repair_consequential_action", approved=False)
    assert res["success"] is False
    assert "approval required" in res["message"].lower()


def test_doctor_repair_circuit_breaker_and_bounded_retries(mock_doctor_env):
    """Verify repair circuit breaker prevents endless retry loops."""
    doctor, _, _, _, _ = mock_doctor_env

    # Simulate failing repair
    for _ in range(3):
        doctor.record_repair_attempt("repair_failing_service", success=False)

    assert doctor.is_repair_circuit_broken("repair_failing_service") is True
    res = doctor.execute_repair("repair_failing_service")
    assert res["success"] is False
    assert "circuit breaker" in res["message"].lower() or "too many failed" in res["message"].lower()


def test_doctor_report_serialization_and_cli(mock_doctor_env):
    """Verify Doctor report converts to dict and CLI formatting works cleanly."""
    doctor, _, _, _, _ = mock_doctor_env
    report = doctor.diagnose()

    rep_dict = report.to_dict()
    assert "checks" in rep_dict
    assert rep_dict["total_checks"] > 10

    cli_text = doctor.format_cli_report(report)
    assert "CHARLIE DOCTOR" in cli_text
    assert "Evidence:" in cli_text
