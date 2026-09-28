"""Canonical subsystem-health registry contracts."""

import pytest

import main
from charlie.events import EventMeta, EventSource
from charlie.subsystem_health import HealthRegistry, HealthStatus


def test_registry_reports_default_disabled_subsystems():
    registry = HealthRegistry(("brain", "voice", "browser"))
    snapshot = registry.snapshot()
    assert snapshot["brain"]["status"] == "disabled"
    assert snapshot["voice"]["status"] == "disabled"


def test_registry_replaces_failure_detail_with_safe_degraded_message():
    registry = HealthRegistry(("voice",))
    registry.set("voice", HealthStatus.DEGRADED, "secret token should not escape")
    assert registry.snapshot()["voice"]["status"] == "degraded"
    assert "secret token" not in str(registry.snapshot())


def test_registry_builds_typed_safe_event():
    registry = HealthRegistry(("brain",))
    registry.set("brain", HealthStatus.RUNNING)
    event = registry.event()
    assert event["type"] == "subsystem_health"
    assert event["payload"]["brain"]["status"] == "running"


def test_registry_rejects_unknown_subsystem():
    registry = HealthRegistry(("brain",))
    with pytest.raises(ValueError):
        registry.set("missing", HealthStatus.RUNNING)


@pytest.mark.asyncio
async def test_main_publishes_runtime_health_snapshot():
    events = []

    class Bus:
        async def emit(self, event_type, payload, meta=None):
            events.append((event_type, payload, meta))

    main._runtime_health = HealthRegistry(("brain",))
    await main._publish_subsystem_health(Bus())
    assert events
    event_type, payload, meta = events[0]
    assert event_type == "subsystem_health"
    assert isinstance(meta, EventMeta)
    assert meta.source is EventSource.VOICE
    assert isinstance(payload, dict)
