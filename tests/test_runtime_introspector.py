"""Tests for RuntimeIntrospector."""

import os

import pytest

from charlie.capabilities import CapabilityDescriptor, CapabilityIndex, CapabilityOperation
from charlie.config import Config
from charlie.resource_locks import CapabilityLeaseManager
from charlie.runtime_introspector import RuntimeIntrospector
from charlie.subsystem_health import HealthRegistry, HealthStatus
from charlie.task_journal import TaskJournal, TaskStatus


@pytest.fixture
def mock_runtime():
    """Create isolated runtime components for testing RuntimeIntrospector."""
    cfg = Config()
    cfg.llm_provider = "openai"
    cfg.llm_model = "gpt-4o"
    cfg.llm_api_key = "sk-super-secret-key-12345"

    # Capability Index
    cap_idx = CapabilityIndex()
    cap_idx.register_capability(
        CapabilityDescriptor(
            id="test_system",
            name="Test System",
            description="Test system capability",
            owner="charlie.tools",
            operations={
                "get_time": CapabilityOperation(
                    id="get_time",
                    name="get_time",
                    description="Get current time",
                    parameters_schema={"type": "object"},
                    risk_class="safe",
                )
            },
            availability_check=lambda: True,
            health_check=lambda: {"status": "ok"},
            provenance="builtin",
        )
    )
    cap_idx.register_capability(
        CapabilityDescriptor(
            id="test_unavailable",
            name="Test Unavailable",
            description="Test unavailable capability",
            owner="charlie.desktop",
            operations={
                "click_ui": CapabilityOperation(
                    id="click_ui",
                    name="click_ui",
                    description="Click UI",
                    parameters_schema={"type": "object"},
                    risk_class="reversible",
                )
            },
            availability_check=lambda: False,
            provenance="builtin",
        )
    )

    # Health Registry
    health = HealthRegistry(("brain", "voice", "browser", "desktop", "terminal", "memory"))
    health.set("brain", HealthStatus.RUNNING)
    health.set("browser", HealthStatus.DEGRADED)

    # Task Journal
    journal = TaskJournal()
    journal.create_task(
        "Running background research",
        task_id="task-test-01",
        status=TaskStatus.RUNNING,
    )

    # Lease Manager
    from charlie.resource_locks import acquire as sync_acquire
    sync_acquire("terminal", "task-test-01")
    lease_mgr = CapabilityLeaseManager()

    introspector = RuntimeIntrospector(
        config=cfg,
        capability_index=cap_idx,
        health_registry=health,
        task_journal=journal,
        lease_manager=lease_mgr,
    )

    return introspector, cfg


def test_runtime_snapshot_structure(mock_runtime):
    """Verify runtime snapshot aggregates all core subsystems cleanly."""
    introspector, _ = mock_runtime
    snapshot = introspector.get_snapshot()

    assert isinstance(snapshot, dict)
    assert "process" in snapshot
    assert "model" in snapshot
    assert "capabilities" in snapshot
    assert "tasks" in snapshot
    assert "leases" in snapshot
    assert "subsystem_health" in snapshot

    # Verify process info
    proc = snapshot["process"]
    assert proc["pid"] == os.getpid()
    assert "python_version" in proc
    assert "uptime_seconds" in proc


def test_runtime_secret_masking(mock_runtime):
    """Verify strictly NO secrets or raw API keys are exposed in runtime snapshot."""
    introspector, cfg = mock_runtime
    snapshot = introspector.get_snapshot()

    # Model info
    model_info = snapshot["model"]
    assert model_info["provider"] == "openai"
    assert model_info["model"] == "gpt-4o"
    assert model_info["api_key_configured"] is True
    assert "sk-super-secret" not in str(snapshot)
    assert "llm_api_key" not in model_info or model_info.get("llm_api_key") is None


def test_runtime_capability_grounding(mock_runtime):
    """Verify live capability inspection reflects exact registered truth and availability."""
    introspector, _ = mock_runtime
    snapshot = introspector.get_snapshot()

    caps = snapshot["capabilities"]
    assert "test_system" in caps["by_id"]
    assert caps["by_id"]["test_system"]["available"] is True
    assert caps["by_id"]["test_system"]["provenance"] == "builtin"

    # Unavailable capability is honestly reported as unavailable
    assert "test_unavailable" in caps["by_id"]
    assert caps["by_id"]["test_unavailable"]["available"] is False


def test_runtime_tasks_and_leases(mock_runtime):
    """Verify active tasks and resource leases are reported truthfully."""
    introspector, _ = mock_runtime
    snapshot = introspector.get_snapshot()

    tasks = snapshot["tasks"]
    assert tasks["counts"]["running"] == 1
    assert any(t["task_id"] == "task-test-01" for t in tasks["active_tasks"])

    leases = snapshot["leases"]
    assert "terminal" in leases["active_leases"]
    assert leases["active_leases"]["terminal"] == "task-test-01"


def test_runtime_health_snapshot(mock_runtime):
    """Verify subsystem health statuses match registered health."""
    introspector, _ = mock_runtime
    snapshot = introspector.get_snapshot()

    health = snapshot["subsystem_health"]
    assert health["brain"]["status"] == "running"
    assert health["browser"]["status"] == "degraded"


def test_runtime_subsystems_and_mcp(mock_runtime):
    """Verify subsystem flags and MCP server stats."""
    introspector, _ = mock_runtime
    subsystems = introspector.get_subsystem_info()
    assert "desktop" in subsystems
    assert "browser" in subsystems
    assert "terminal" in subsystems
    assert "voice" in subsystems

    mcp_info = introspector.get_mcp_info()
    assert "configured_servers" in mcp_info
    assert "connected_servers" in mcp_info


def test_runtime_default_introspector_sanity():
    """Verify default RuntimeIntrospector instantiates and queries without error."""
    default_introspector = RuntimeIntrospector()
    snapshot = default_introspector.get_snapshot()
    assert snapshot["process"]["pid"] == os.getpid()
    assert "capabilities" in snapshot
    assert "model" in snapshot

