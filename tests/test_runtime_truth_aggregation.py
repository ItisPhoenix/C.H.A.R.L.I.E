"""Focused tests for main-owned aggregate runtime truth."""

from __future__ import annotations

import pytest

from charlie.runtime_introspector import RuntimeIntrospector
from charlie.subsystem_health import HealthRegistry, HealthStatus, RuntimeStatus


def _registry(*, required: bool = True) -> HealthRegistry:
    registry = HealthRegistry(("brain", "voice", "terminal", "browser"), launch_id="launch-test")
    registry.configure_subsystem(
        "brain",
        enabled=True,
        required=required,
        static_available=True,
        evidence_authority="main.brain",
    )
    registry.configure_subsystem(
        "voice",
        enabled=True,
        required=False,
        static_available=True,
        evidence_authority="main.voice",
    )
    registry.configure_subsystem(
        "terminal",
        enabled=True,
        required=False,
        static_available=True,
        evidence_authority="web.terminal_manager",
    )
    registry.configure_subsystem(
        "browser",
        enabled=False,
        required=False,
        static_available=True,
        evidence_authority="browser.controller",
    )
    return registry


def test_required_running_is_aggregate_running() -> None:
    registry = _registry()
    registry.set("brain", HealthStatus.RUNNING)

    truth = registry.runtime_snapshot()

    assert truth["status"] == "running"
    assert truth["required_failures"] == []
    assert truth["subsystems"]["brain"]["live_ready"] is True


@pytest.mark.parametrize(
    ("status", "aggregate"),
    [
        (HealthStatus.DEGRADED, "degraded"),
        (HealthStatus.UNKNOWN, "degraded"),
        (HealthStatus.UNAVAILABLE, "unavailable"),
    ],
)
def test_required_failure_matrix(status: HealthStatus, aggregate: str) -> None:
    registry = _registry()
    registry.set("brain", status)

    truth = registry.runtime_snapshot()

    assert truth["status"] == aggregate
    assert truth["required_failures"] == ["brain"]


def test_static_available_with_missing_live_evidence_is_unknown() -> None:
    registry = _registry()
    registry.set("brain", HealthStatus.RUNNING, live_ready=None)

    truth = registry.runtime_snapshot()

    assert truth["subsystems"]["brain"]["static_available"] is True
    assert truth["subsystems"]["brain"]["live_ready"] is None
    assert truth["subsystems"]["brain"]["status"] == "unknown"
    assert truth["status"] == "degraded"


def test_optional_disabled_is_neutral_and_optional_unavailable_is_exposed() -> None:
    registry = _registry()
    registry.set("brain", HealthStatus.RUNNING)
    registry.set("voice", HealthStatus.UNAVAILABLE, public_detail="Microphone unavailable")

    truth = registry.runtime_snapshot()

    assert truth["status"] == "running"
    assert truth["subsystems"]["voice"]["status"] == "unavailable"
    assert truth["subsystems"]["browser"]["status"] == "disabled"
    assert truth["subsystems"]["browser"]["live_ready"] is False


def test_terminal_without_live_evidence_stays_unknown() -> None:
    registry = _registry()

    terminal = registry.runtime_snapshot()["subsystems"]["terminal"]

    assert terminal["static_available"] is True
    assert terminal["live_ready"] is None
    assert terminal["status"] == "unknown"


def test_revision_monotonic_and_launch_identity() -> None:
    registry = _registry()
    first = registry.runtime_snapshot()
    registry.set("brain", HealthStatus.RUNNING)
    second = registry.runtime_snapshot()
    registry.set_runtime_lifecycle(RuntimeStatus.SHUTTING_DOWN)
    third = registry.runtime_snapshot()

    assert first["launch_id"] == second["launch_id"] == third["launch_id"] == "launch-test"
    assert first["revision"] < second["revision"] < third["revision"]


def test_shutdown_lifecycle_and_non_quiescent_voice_truth() -> None:
    registry = _registry()
    registry.set("brain", HealthStatus.RUNNING)
    registry.set("voice", HealthStatus.DEGRADED, public_detail="Voice shutdown incomplete")
    registry.set_runtime_lifecycle(RuntimeStatus.SHUTTING_DOWN)

    shutting_down = registry.runtime_snapshot()
    assert shutting_down["status"] == "shutting_down"
    assert shutting_down["subsystems"]["voice"]["status"] == "degraded"

    registry.mark_shutdown_failed()
    failed = registry.runtime_snapshot()
    assert failed["status"] == "degraded"
    assert failed["subsystems"]["voice"]["status"] != "stopped"

    clean = _registry()
    clean.set("brain", HealthStatus.RUNNING)
    clean.set("voice", HealthStatus.STOPPED)
    clean.set_runtime_lifecycle(RuntimeStatus.STOPPED)
    assert clean.runtime_snapshot()["status"] == "stopped"


def test_background_shutdown_does_not_fail_pre_shutdown_aggregate() -> None:
    registry = HealthRegistry(("brain", "background_tasks"), launch_id="launch-test")
    registry.configure_subsystem("brain", enabled=True, required=True, static_available=True)
    registry.configure_subsystem("background_tasks", enabled=True, required=True, static_available=True)
    registry.set("brain", HealthStatus.RUNNING)
    registry.set("background_tasks", HealthStatus.RUNNING)
    registry.set_runtime_lifecycle(RuntimeStatus.SHUTTING_DOWN)
    registry.set("background_tasks", HealthStatus.SHUTTING_DOWN)

    truth = registry.runtime_snapshot()

    assert truth["status"] == "shutting_down"
    assert truth["subsystems"]["background_tasks"]["status"] == "shutting_down"


def test_runtime_introspector_returns_canonical_projection() -> None:
    registry = _registry()
    registry.set("brain", HealthStatus.RUNNING)

    introspector = RuntimeIntrospector(health_registry=registry)

    truth = registry.runtime_snapshot()
    assert introspector.get_runtime_truth() == truth
    assert introspector.get_health_info() == {
        name: {"status": value["status"], "detail": value["detail"]}
        for name, value in truth["subsystems"].items()
    }
    assert introspector.get_snapshot()["runtime_truth"] == registry.runtime_snapshot()


def test_web_rejects_stale_and_wrong_launch_runtime_truth(monkeypatch) -> None:
    from charlie import web_server

    monkeypatch.setattr(web_server, "LAUNCH_ID", "launch-test")
    monkeypatch.setattr(web_server, "_runtime_truth", None)
    payload = {
        "schema_version": 1,
        "authority": "main_runtime",
        "launch_id": "launch-test",
        "revision": 2,
        "observed_at": "2026-01-01T00:00:00+00:00",
        "status": "running",
        "required_failures": [],
        "subsystems": {"brain": {"status": "running", "live_ready": True}},
    }

    assert web_server._apply_runtime_truth_event({"type": "runtime_truth", "payload": payload}) is True
    assert web_server._apply_runtime_truth_event(
        {"type": "runtime_truth", "payload": {**payload, "revision": 1}}
    ) is False
    assert web_server._apply_runtime_truth_event(
        {"type": "runtime_truth", "payload": {**payload, "launch_id": "old-launch", "revision": 3}}
    ) is False
    assert web_server._runtime_truth["revision"] == 2


@pytest.mark.asyncio
async def test_web_api_and_projection_use_main_runtime_truth(monkeypatch) -> None:
    from charlie import web_server

    truth = {
        "schema_version": 1,
        "authority": "main_runtime",
        "launch_id": "launch-test",
        "revision": 4,
        "observed_at": "2026-01-01T00:00:00+00:00",
        "status": "degraded",
        "required_failures": ["brain"],
        "subsystems": {
            "brain": {
                "enabled": True,
                "required": True,
                "static_available": True,
                "live_ready": False,
                "status": "degraded",
                "detail": "Unavailable",
                "evidence_authority": "main.brain",
                "observed_at": "2026-01-01T00:00:00+00:00",
            }
        },
    }
    monkeypatch.setattr(web_server, "LAUNCH_ID", "launch-test")
    monkeypatch.setattr(web_server, "_runtime_truth", truth)
    monkeypatch.setattr(web_server, "_subsystem_health", {"brain": {"status": "running"}})

    response = await web_server.health()

    assert response["runtime_truth"] == truth
    assert response["subsystems"] == {
        name: {"status": value["status"], "detail": value["detail"]}
        for name, value in truth["subsystems"].items()
    }
    assert all(set(value) == {"status", "detail"} for value in response["subsystems"].values())
    assert all(
        {"enabled", "required", "static_available", "live_ready", "evidence_authority", "observed_at"}
        <= set(value)
        for value in response["runtime_truth"]["subsystems"].values()
    )
    assert web_server._runtime_introspector.get_runtime_truth() == truth
    assert web_server._runtime_introspector.get_health_info() == response["subsystems"]
