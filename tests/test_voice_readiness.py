"""Launcher readiness contract: voice never claims microphone readiness.

These are behavioral tests. They construct the real launcher seam
(``main._configure_runtime_health`` + ``main._start_voice_or_degrade``) with a
controlled voice engine and assert the readiness/health state it produces.

Evidence class: TEST/MOCK. No microphone is opened here and none of these
tests may be cited as evidence that native voice capture works. See
``charlie/AGENTS.md`` section 8 for the real-host voice acceptance list.
"""

from types import SimpleNamespace

import pytest

import main


class _VoiceSpy:
    """Stand-in for VoiceEngine whose readiness the test controls."""

    def __init__(self, *, is_ready, starts=None, raises=None, readiness="ready"):
        self.is_ready = is_ready
        self.is_available = True
        self.asr_ready = is_ready
        self.asr_readiness_status = "ready" if is_ready else "failed"
        self._readiness = readiness
        self.started = False
        self._raises = raises

    def start(self):
        self.started = True
        if self._raises is not None:
            raise self._raises

    def readiness_detail(self):
        return self._readiness

    def asr_readiness_detail(self):
        return "ASR loading" if self.is_ready else "ASR unavailable"


@pytest.fixture
def health_probe(monkeypatch):
    """Record every subsystem health transition the launcher projects."""
    updates = []
    monkeypatch.setattr(
        main,
        "_set_subsystem_health",
        lambda name, status, detail=None: updates.append((name, status, detail)),
    )
    return updates


def test_disabled_voice_never_claims_microphone_readiness(health_probe, monkeypatch):
    def forbidden_voice_engine(*_args, **_kwargs):
        raise AssertionError("VoiceEngine must not initialize when voice is disabled")

    monkeypatch.setattr(main, "VoiceEngine", forbidden_voice_engine)

    voice = main._start_voice_or_degrade(
        SimpleNamespace(voice_enabled=False),
        on_speech=lambda *_args, **_kwargs: None,
        on_tts_start=lambda: None,
        on_tts_stop=lambda: None,
    )

    assert isinstance(voice, main._UnavailableVoiceEngine)
    assert voice.is_ready is False
    assert voice.is_available is False
    assert voice.asr_ready is False
    assert voice.asr_readiness_status == "disabled"
    assert "disabled" in voice.readiness_detail().lower()
    assert "listening" not in voice.readiness_detail().lower()
    # Nothing may be projected as RUNNING when the engine never came up.
    assert not [u for u in health_probe if u[1] is main.HealthStatus.RUNNING]
    assert {
        name for name, status, _ in health_probe if status is main.HealthStatus.DISABLED
    } == {
        "voice",
        "voice_capture",
        "asr",
    }


def test_failing_voice_startup_projects_degraded_not_running(monkeypatch, health_probe):
    engine = _VoiceSpy(is_ready=True, raises=RuntimeError("microphone busy"))
    monkeypatch.setattr(main, "VoiceEngine", lambda *_a, **_k: engine)

    voice = main._start_voice_or_degrade(
        SimpleNamespace(voice_enabled=True),
        on_speech=lambda *_args, **_kwargs: None,
        on_tts_start=lambda: None,
        on_tts_stop=lambda: None,
    )

    assert engine.started is True
    assert isinstance(voice, main._UnavailableVoiceEngine)
    assert voice.is_ready is False
    assert voice.asr_ready is False
    assert not [u for u in health_probe if u[1] is main.HealthStatus.RUNNING]
    assert {
        name for name, status, _ in health_probe if status is main.HealthStatus.DEGRADED
    } == {
        "voice",
        "voice_capture",
        "asr",
    }


def test_engine_that_starts_but_is_not_ready_never_projects_running(monkeypatch, health_probe):
    engine = _VoiceSpy(is_ready=False, readiness="microphone stream delivered no samples")
    monkeypatch.setattr(main, "VoiceEngine", lambda *_a, **_k: engine)

    voice = main._start_voice_or_degrade(
        SimpleNamespace(voice_enabled=True),
        on_speech=lambda *_args, **_kwargs: None,
        on_tts_start=lambda: None,
        on_tts_stop=lambda: None,
    )

    assert engine.started is True
    assert voice.is_ready is False
    assert not [u for u in health_probe if u[1] is main.HealthStatus.RUNNING]
    assert {
        name for name, status, _ in health_probe if status is main.HealthStatus.DEGRADED
    } == {
        "voice",
        "voice_capture",
        "asr",
    }


def test_health_policy_disables_voice_subsystems_when_voice_is_off(monkeypatch):
    policy = {}

    class FakeHealth:
        def configure_subsystem(self, name, **kwargs):
            policy[name] = kwargs

    monkeypatch.setattr(main, "_runtime_health", FakeHealth())

    main._configure_runtime_health(SimpleNamespace(voice_enabled=False))

    assert policy["voice"]["enabled"] is False
    assert policy["voice_capture"]["enabled"] is False
    assert policy["asr"]["enabled"] is False
    # Readiness is never claimed from configuration alone.
    assert policy["voice"]["required"] is False
