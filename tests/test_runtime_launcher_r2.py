"""Canonical runtime launcher and shutdown contract tests."""

import ast
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest
import zmq

import main
import run
from charlie.config import Config
from charlie.ipc import EventBus


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_run_py_is_the_single_canonical_entrypoint():
    source = Path("run.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert not any(
        isinstance(node, ast.Import)
        and any(alias.name == "argparse" for alias in node.names)
        for node in tree.body
    )
    assert "mode-selection" not in source
    assert "asyncio.run(main())" in source
    assert run.main is not None


def test_main_contains_no_removed_process_startup_or_client_command_loop():
    source = Path("main.py").read_text(encoding="utf-8")
    assert "_start_web_subprocess" not in source
    assert "_start_subsystem_process" not in source
    assert "consume_web_commands" not in source
    assert "web_proc" not in source
    assert "pet_proc" not in source


@pytest.mark.asyncio
async def test_runtime_constructs_no_publisher_of_its_own(monkeypatch):
    """Every main-owned projection must ride the one publisher, not build one.

    A second EventBus would bind the same canonical port, and the first
    publisher then stops delivering while the runtime still reports healthy --
    the silent two-instance failure single_instance.py exists to prevent. This
    fails the moment any publish path constructs a bus instead of using the
    one the launcher owns.
    """
    constructed = []

    class ForbiddenBus:
        def __init__(self, *args, **kwargs):
            constructed.append((args, kwargs))
            raise AssertionError("publish paths must not construct an EventBus")

    monkeypatch.setattr(main, "EventBus", ForbiddenBus)

    class RecordingBus:
        def __init__(self):
            self.events = []

        async def emit(self, event_type, payload, meta=None):
            self.events.append(event_type)

    bus = RecordingBus()
    monkeypatch.setattr(main, "_main_event_bus", bus, raising=False)
    monkeypatch.setattr(
        main,
        "_runtime_health",
        type(
            "Health",
            (),
            {
                "event": staticmethod(lambda: {"type": "subsystem_health", "payload": {}}),
                "runtime_event": staticmethod(lambda: {"type": "runtime_truth", "payload": {}}),
            },
        )(),
    )

    await main._publish_subsystem_health()
    await main._publish_runtime_truth()
    await main._publish_task_snapshot()
    await main._publish_tool_snapshot()
    await main._publish_mcp_snapshot()
    await main._publish_runtime_telemetry()

    assert constructed == [], f"publish paths constructed their own publisher: {constructed}"
    # bus-less projections must publish nothing at all rather than improvise one.
    monkeypatch.setattr(main, "_main_event_bus", None, raising=False)
    before = len(bus.events)
    await main._publish_runtime_telemetry()
    assert len(bus.events) == before
    assert constructed == []


@pytest.mark.asyncio
async def test_canonical_event_port_admits_exactly_one_publisher():
    """Second publisher on the live port must fail loudly, not silently coexist."""
    port = _free_port()
    async with EventBus(pub_port=port) as bus:
        assert bus.pub_port == port
        with pytest.raises(zmq.ZMQError):
            async with EventBus(pub_port=port):
                pass


def test_production_event_port_is_not_reachable_from_tests():
    """The launcher's canonical port is refused under CHARLIE_TEST_MODE.

    conftest.py sets that mode, so if the gate regressed, a test could bind the
    user's real event port and steal events from the running Charlie.
    """
    with pytest.raises(RuntimeError, match="cannot use production port"):
        EventBus(pub_port=5555)


def test_main_exposes_canonical_async_entrypoint():
    assert callable(main.main)


def test_voice_disabled_skips_audio_engine_and_marks_subsystems_disabled(monkeypatch):
    status_updates = []
    health_policy = {}

    class FakeHealth:
        def configure_subsystem(self, name, **kwargs):
            health_policy[name] = kwargs

    def forbidden_voice_engine(*_args, **_kwargs):
        raise AssertionError("VoiceEngine must not initialize in text-only mode")

    monkeypatch.setattr(main, "VoiceEngine", forbidden_voice_engine)
    monkeypatch.setattr(
        main,
        "_set_subsystem_health",
        lambda name, status, detail=None: status_updates.append((name, status, detail)),
    )
    monkeypatch.setattr(main, "_runtime_health", FakeHealth())

    main._configure_runtime_health(
        SimpleNamespace(
            voice_enabled=False,
            llm_url="",
            plugins_enabled=False,
            mcp_enabled=False,
            telegram_enabled=False,
            browser_enabled=False,
        )
    )
    voice_settings = Config.__dataclass_fields__["voice_enabled"]
    assert voice_settings.default is True
    assert voice_settings.metadata["restart"] == "process"
    assert all(health_policy[name]["enabled"] is False for name in ("voice", "voice_capture", "asr"))

    voice = main._start_voice_or_degrade(
        SimpleNamespace(voice_enabled=False),
        on_speech=lambda *_args, **_kwargs: None,
        on_tts_start=lambda: None,
        on_tts_stop=lambda: None,
    )

    assert isinstance(voice, main._UnavailableVoiceEngine)
    assert voice.is_ready is False
    assert "disabled" in voice.readiness_detail().lower()
    assert {name for name, status, _ in status_updates if status == main.HealthStatus.DISABLED} == {
        "voice",
        "voice_capture",
        "asr",
    }
