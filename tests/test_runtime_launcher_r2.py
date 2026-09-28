"""Canonical runtime launcher and shutdown contract tests."""

import ast
from pathlib import Path
from types import SimpleNamespace

import main
import run
from charlie.config import Config


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


def test_runtime_uses_one_event_publisher():
    source = Path("main.py").read_text(encoding="utf-8")
    assert source.count("EventBus(") == 1
    assert "pull_port" not in source
    assert "5556" not in source


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
