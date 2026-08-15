import pytest

from charlie.subsystem_health import HealthRegistry, HealthStatus


def test_registry_reports_default_disabled_subsystems() -> None:
    registry = HealthRegistry(("voice", "web"))

    assert registry.snapshot() == {
        "voice": {"status": "disabled", "detail": "Disabled"},
        "web": {"status": "disabled", "detail": "Disabled"},
    }


def test_registry_replaces_failure_detail_with_safe_degraded_message() -> None:
    registry = HealthRegistry(("voice",))

    registry.set("voice", HealthStatus.DEGRADED, "ConnectionError: api-key=secret")

    assert registry.snapshot()["voice"] == {
        "status": "degraded",
        "detail": "Unavailable",
    }


def test_registry_builds_typed_safe_event() -> None:
    registry = HealthRegistry(("voice",))
    registry.set("voice", HealthStatus.RUNNING)

    assert registry.event() == {
        "type": "subsystem_health",
        "payload": {"voice": {"status": "running", "detail": "Running"}},
    }


def test_registry_rejects_unknown_subsystem() -> None:
    registry = HealthRegistry(("voice",))

    try:
        registry.set("unknown", HealthStatus.RUNNING)
    except ValueError as exc:
        assert str(exc) == "Unknown subsystem: unknown"
    else:
        raise AssertionError("Unknown subsystem must be rejected")


def test_web_server_replays_current_subsystem_health() -> None:
    from charlie import web_server

    old = web_server._subsystem_health
    web_server._subsystem_health = {
        "voice": {"status": "degraded", "detail": "Unavailable"},
    }
    try:
        event = next(event for event in web_server._initial_state_events() if event["type"] == "subsystem_health")
        assert event["payload"] == web_server._subsystem_health
        assert event["replay"] is True
    finally:
        web_server._subsystem_health = old


@pytest.mark.asyncio
async def test_services_status_reports_only_current_runtime_subsystems() -> None:
    from charlie import web_server

    old = web_server._subsystem_health
    web_server._subsystem_health = {
        "voice": {"status": "degraded", "detail": "Unavailable"},
        "mcp": {"status": "disabled", "detail": "Disabled"},
    }
    try:
        result = await web_server.get_services_status()
    finally:
        web_server._subsystem_health = old

    assert result == {
        "services": [
            {"name": "mcp", "status": "disabled", "details": "Disabled", "type": "subsystem"},
            {"name": "voice", "status": "degraded", "details": "Unavailable", "type": "subsystem"},
        ]
    }


@pytest.mark.asyncio
async def test_main_publishes_runtime_health_snapshot() -> None:
    import main

    class FakeBus:
        def __init__(self) -> None:
            self.events = []

        async def emit(self, event_type, payload, meta=None) -> None:
            self.events.append((event_type, payload, meta))

    bus = FakeBus()
    await main._publish_subsystem_health(bus)

    assert bus.events[0][0] == "subsystem_health"
    assert set(bus.events[0][1]) == {
        "brain", "memory", "plugins", "mcp", "web", "companion", "hud", "telegram", "voice", "watchers",
    }


def test_main_records_subsystem_transition() -> None:
    import main

    old = main._runtime_health
    main._runtime_health = HealthRegistry(("voice",))
    try:
        main._set_subsystem_health("voice", HealthStatus.DEGRADED)
        assert main._runtime_health.snapshot()["voice"]["status"] == "degraded"
    finally:
        main._runtime_health = old


def test_main_degrades_failed_subsystem_process_start(monkeypatch) -> None:
    import main

    old = main._runtime_health
    main._runtime_health = HealthRegistry(("web",))

    def fail_process(*args, **kwargs):
        raise OSError("launch failed: api-key=secret")

    monkeypatch.setattr(main.subprocess, "Popen", fail_process)
    try:
        process = main._start_subsystem_process(
            "web",
            ("python", "web_server_entry.py"),
        )

        assert process is None
        assert main._runtime_health.snapshot()["web"] == {
            "status": "degraded",
            "detail": "Unavailable",
        }
    finally:
        main._runtime_health = old


def test_main_keeps_core_alive_when_voice_start_fails(monkeypatch) -> None:
    import main

    class FailingVoice:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def start(self) -> None:
            raise OSError("microphone failed: device-token=secret")

    old = main._runtime_health
    main._runtime_health = HealthRegistry(("voice",))
    monkeypatch.setattr(main, "VoiceEngine", FailingVoice)
    try:
        voice = main._start_voice_or_degrade(object(), lambda text: None, lambda: None, lambda: None)

        assert voice.is_available is False
        voice.speak("This must be safe", "neutral")
        assert main._runtime_health.snapshot()["voice"] == {
            "status": "degraded",
            "detail": "Unavailable",
        }
    finally:
        main._runtime_health = old
