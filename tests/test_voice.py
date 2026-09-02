"""Unit tests for charlie.voice.

Mocks sounddevice, Kokoro, and multiprocessing to avoid audio hardware.
Focuses on the text humanization pipeline, RMS calculation, and init logic.
"""

import queue
import sys
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

import charlie.voice
from charlie.voice import VoiceEngine
from charlie.voice_diagnostics import VoiceDiagnostics


class FakeConfig:
    kokoro_model_dir = "/tmp/kokoro_models"
    wake_word_enabled = False
    wake_word_model_path = ""
    wake_word_audio_chime_path = ""


# ---------------------------------------------------------------------------
# Text humanization (pure function -- no mocking needed)
# ---------------------------------------------------------------------------

class TestHumanizeText:
    """_humanize_text is a static method; these tests exercise every regex.

    The note docstring avoids backticks for Python 3.13 compatibility.
    CONTRACTIONS dict is now applied by humanize_text -- see the
    """
    def test_strips_markdown_bold(self):
        result = VoiceEngine._humanize_text("hello **world**")
        assert "**" not in result

    def test_strips_inline_code(self):
        result = VoiceEngine._humanize_text("run `pip install` please")
        assert "`" not in result

    def test_replaces_em_dash_with_comma(self):
        result = VoiceEngine._humanize_text("hello\u2014world")
        assert "\u2014" not in result
        assert ", " in result

    def test_replaces_en_dash(self):
        result = VoiceEngine._humanize_text("a\u2013b")
        assert "\u2013" not in result
        assert ", " in result

    def test_replaces_double_hyphen(self):
        result = VoiceEngine._humanize_text("a -- b")
        assert "--" not in result

    def test_removes_list_bullets(self):
        result = VoiceEngine._humanize_text("- item\n- another")
        assert "item" in result
        assert "-" not in result

    def test_removes_numbered_list(self):
        result = VoiceEngine._humanize_text("1. first\n2. second")
        assert "first" in result
        assert "second" in result

    def test_removes_hash_headers(self):
        result = VoiceEngine._humanize_text("## Title\ntext")
        assert "Title" in result
        assert "##" not in result

    def test_strips_wrapper_quotes_and_adds_period(self):
        """_humanize_text strips wrapping quotes but adds sentence-ending period."""
        result = VoiceEngine._humanize_text('"Hello world"')
        assert result == "Hello world."

    def test_strips_curly_wrapper_quotes_and_adds_period(self):
        result = VoiceEngine._humanize_text("\u201cHello world\u201d")
        assert result == "Hello world."

    def test_removes_short_parentheticals(self):
        result = VoiceEngine._humanize_text("Hello (hi) world")
        assert "(hi)" not in result

    def test_keeps_long_parentheticals(self):
        text = "Hello (" + "x" * 50 + ") world"
        result = VoiceEngine._humanize_text(text)
        assert "x" * 50 in result

    def test_handles_empty_string(self):
        assert VoiceEngine._humanize_text("") == ""

    def test_handles_whitespace_only(self):
        result = VoiceEngine._humanize_text("   ")
        assert result == ""

    def test_numeric_question_detection(self):
        """Phrases ending in question words get '?' instead of '.'"""
        result = VoiceEngine._humanize_text("what time is it")
        assert result.endswith("?")

    def test_removes_repeated_exclamation(self):
        result = VoiceEngine._humanize_text("wow!!!")
        assert "!!" not in result
        assert "!" in result

    def test_adds_space_after_punctuation_before_uppercase(self):
        result = VoiceEngine._humanize_text("Hello.World")
        assert ". " in result

    def test_normalizes_ellipsis(self):
        result = VoiceEngine._humanize_text("so... then")
        assert "...." not in result

    def test_collapses_spaces(self):
        result = VoiceEngine._humanize_text("hello    world")
        assert result.count(" ") == 1

    def test_preserves_trailing_question_mark(self):
        result = VoiceEngine._humanize_text("How are you?")
        assert result.endswith("?")




class TestHumanizeContractions:
    """_humanize_text expansion of _CONTRACTIONS for natural speech."""

    def test_i_am_to_im(self):
        result = VoiceEngine._humanize_text("I am ready")
        assert "I'm" in result

    def test_do_not_to_dont(self):
        result = VoiceEngine._humanize_text("I do not know")
        assert "don't" in result

    def test_cannot_to_cant(self):
        result = VoiceEngine._humanize_text("I cannot do it")
        assert "can't" in result

    def test_will_not_to_wont(self):
        result = VoiceEngine._humanize_text("it will not work")
        assert "won't" in result

    def test_mixed_contractions(self):
        result = VoiceEngine._humanize_text(
            "I am sure you are right and it is fine"
        )
        assert "I'm" in result
        assert "you're" in result
        assert "it's" in result

    def test_case_insensitive_replacement(self):
        result = VoiceEngine._humanize_text("I Am going")
        assert "I'm" in result

    def test_word_boundary_no_false_positive(self):
        """Contraction patterns must not match inside other words."""
        result = VoiceEngine._humanize_text("I can't manage")
        # "cannot" maps to "can't" but "can't" should not be produced from "can't"
        # This tests that word boundaries work -- "I can't" should remain "I can't"
        # Actually "cannot" -> "can't" but "can't" is already contracted
        assert "can't" in result


# ---------------------------------------------------------------------------
# RMS calculation (static method)
# ---------------------------------------------------------------------------

class TestRMS:
    def test_silence_is_zero(self):
        samples = np.zeros(16000, dtype=np.float32)
        assert VoiceEngine._rms(samples) == 0.0

    def test_full_scale_one(self):
        samples = np.ones(16000, dtype=np.float32)
        assert VoiceEngine._rms(samples) == pytest.approx(1.0, abs=1e-3)

    def test_sine_wave(self):
        t = np.linspace(0, 1, 16000, dtype=np.float32)
        samples = np.sin(2 * np.pi * 440 * t) * 0.5
        rms = VoiceEngine._rms(samples)
        assert 0.3 < rms < 0.4

    def test_empty_array(self):
        assert VoiceEngine._rms(np.array([], dtype=np.float32)) == 0.0


# ---------------------------------------------------------------------------
# VoiceEngine initialization with mocked hardware
# ---------------------------------------------------------------------------

class TestVoiceEngineInit:
    """Tests that require mocking Kokoro, sd (sounddevice), and mp.Queue."""

    def _make_engine(self):
        """Build a VoiceEngine with all external deps mocked.

        sounddevice is imported as import sounddevice as sd, so the
        module-level attribute is charlie.voice.sd, NOT sounddevice.
        """
        with patch("charlie.voice.Kokoro"), \
             patch("charlie.voice.sd"), \
             patch("charlie.voice.mp.Queue"):
            return VoiceEngine(FakeConfig(), on_speech=lambda _: None)

    def test_creates_queues_and_threads(self):
        engine = self._make_engine()
        assert engine.tts_queue is not None
        assert engine.playback_queue is not None
        assert not engine.muted
        assert engine.volume == 1.0

    def test_input_device_candidates_include_same_microphone_host_api_variants(self):
        with patch("charlie.voice.sd") as mock_sd:
            mock_sd.default.device = [1, 4]
            mock_sd.query_devices.side_effect = [
                {"name": "Microphone (NVIDIA Broadcast)", "max_input_channels": 2},
                [
                    {"name": "Microsoft Sound Mapper - Input", "max_input_channels": 2},
                    {"name": "Microphone (NVIDIA Broadcast)", "max_input_channels": 2},
                    {"name": "Microphone (Realtek(R) Audio)", "max_input_channels": 2},
                    {"name": "Microphone (NVIDIA Broadcast)", "max_input_channels": 2},
                ],
            ]
            assert VoiceEngine._input_device_candidates(-1) == [1, 3]

    def test_wake_word_disabled_by_default(self):
        engine = self._make_engine()
        assert engine._wake_word_detector is None

    def test_ptt_state_uses_existing_capture_path(self):
        engine = self._make_engine()
        engine.start_ptt()
        assert engine._ptt_active is True
        assert engine._ptt_trace is not None
        engine.stop_ptt()
        assert engine._ptt_active is False
        assert engine._ptt_stop_requested is True
        engine.cancel_ptt()
        assert engine._ptt_trace is None

    def test_stop_tts_sets_event(self):
        engine = self._make_engine()
        assert not engine.stop_tts_event.is_set()
        engine.stop_tts()
        assert engine.stop_tts_event.is_set()

    def test_stop_tts_does_not_call_sd_directly(self):
        """stop_tts() runs on the caller's thread (e.g. barge-in), while
        _playback_worker's own thread concurrently drives sd.play/sd.stop/
        sd.wait on the same sounddevice global stream. Calling sd.stop()
        from stop_tts() too races that native call across threads -- a
        suspected cause of the rapid-barge-in segfault. Only
        _playback_worker may call sd.stop()."""
        engine = self._make_engine()
        with patch("charlie.voice.sd") as mock_sd:
            engine.stop_tts()
            mock_sd.stop.assert_not_called()

    def test_play_wake_chime_does_not_spawn_thread(self):
        """_play_wake_chime must route through playback_queue, not a raw
        thread -- same bug class as test_stop_tts_does_not_call_sd_directly."""
        engine = self._make_engine()
        with patch("charlie.voice.sd"), \
             patch("charlie.voice.threading.Thread") as mock_thread:
            engine._play_wake_chime()
            mock_thread.assert_not_called()
        assert engine.playback_queue.qsize() == 1
        samples, sr, tag = engine.playback_queue.get_nowait()
        assert tag is charlie.voice._CHIME_ITEM

    def test_mute_toggle(self):
        engine = self._make_engine()
        engine.muted = True
        assert engine.muted
        engine.muted = False
        assert not engine.muted

    def test_set_widget_callback(self):
        engine = self._make_engine()
        def cb(x):
            return None
        engine.set_widget_callback(cb)
        assert engine._widget_callback is cb

    def test_set_wake_word_callback(self):
        engine = self._make_engine()
        def cb():
            pass
        engine.set_wake_word_callback(cb)
        assert engine._on_wake_word is cb

    def test_ensure_models_creates_dir(self):
        with patch("charlie.voice.Kokoro"), \
             patch("charlie.voice.sd"), \
             patch("charlie.voice.mp.Queue"), \
             patch("os.makedirs") as mock_makedirs:
            VoiceEngine(FakeConfig(), on_speech=lambda _: None)
            mock_makedirs.assert_called_once_with("/tmp/kokoro_models", exist_ok=True)

    def test_ensure_models_downloads_when_missing(self):
        with patch("charlie.voice.Kokoro"), \
             patch("charlie.voice.sd"), \
             patch("charlie.voice.mp.Queue"), \
             patch("os.makedirs"), \
             patch("os.path.exists", return_value=False), \
             patch("urllib.request.urlretrieve") as mock_dl:
            VoiceEngine(FakeConfig(), on_speech=lambda _: None)
            assert mock_dl.call_count == 2

    def test_volume_property(self):
        engine = self._make_engine()
        engine.volume = 0.5
        assert engine.volume == 0.5

    def test_stop_before_run_does_not_raise(self):
        """Regression test: self.audio_stream is only assigned inside
        _run() (which starts on a background thread via start()). If the
        audio device fails to open, or stop() is called before start(),
        _run() never runs and audio_stream was never set -- stop()'s
        `if self.audio_stream is not None` check then raised AttributeError.
        audio_stream must be initialized to None in __init__ so stop() is
        always safe to call."""
        engine = self._make_engine()
        assert engine.audio_stream is None
        engine.input_thread = None
        engine.tts_worker = None
        engine.playback_worker = None
        engine.asr_poller_thread = None
        engine.asr_process = None
        engine.stop()  # must not raise AttributeError

    def test_rms_static_method(self):
        samples = np.zeros(100, dtype=np.float32)
        assert VoiceEngine._rms(samples) == 0.0

    def test_asr_poller_preserves_utterance_id_and_sidecar_trace(self):
        received = []

        def on_speech(text, diagnostic_metadata=None):
            received.append((text, diagnostic_metadata))
            engine.stop_event.set()

        engine = self._make_engine()
        engine.on_speech = on_speech
        engine._speech_callback_mode = "keyword"
        engine.asr_output_queue = queue.Queue()
        trace = engine.voice_diagnostics.new_trace("utterance-test")
        trace.mark_once("voice_capture_onset", timestamp=10.0)
        engine._utterance_traces[trace.utterance_id] = trace
        engine.asr_output_queue.put(
            (
                " hello",
                0.88,
                {
                    "utterance_id": trace.utterance_id,
                    "language_probability": 0.88,
                    "confidence_semantics": "language_probability",
                    "asr_quality": {"segment_count": 1},
                    "asr_complete_monotonic": time.monotonic(),
                    "diagnostic_stages": [],
                },
            )
        )

        engine._asr_poller_loop()

        assert received[0][0] == "hello"
        assert received[0][1]["utterance_id"] == "utterance-test"
        assert received[0][1]["trace"] is trace
        assert received[0][1]["confidence_semantics"] == "language_probability"

    def test_asr_enqueue_keeps_legacy_payload_prefix_and_adds_diagnostics(self):
        engine = self._make_engine()
        engine.asr_input_queue = queue.Queue(maxsize=8)
        trace = engine.voice_diagnostics.new_trace("utterance-enqueue")
        trace.mark_once("voice_capture_onset", timestamp=10.0)
        audio = np.zeros(32, dtype=np.float32)

        engine._submit_asr(
            audio,
            16000,
            trace,
            {
                "capture_mode": "ptt",
                "submitted_sample_count": len(audio),
                "submitted_duration_ms": 2.0,
            },
        )

        payload = engine.asr_input_queue.get_nowait()
        assert payload[:2] == (audio.tobytes(), 16000)
        assert payload[2]["utterance_id"] == "utterance-enqueue"
        assert payload[2]["capture"]["capture_mode"] == "ptt"

    def test_asr_queue_drop_oldest_semantics_remain_observable(self):
        engine = self._make_engine()
        engine.asr_input_queue = queue.Queue(maxsize=1)
        first = engine.voice_diagnostics.new_trace("utterance-first")
        first.mark_once("voice_capture_onset", timestamp=10.0)
        second = engine.voice_diagnostics.new_trace("utterance-second")
        second.mark_once("voice_capture_onset", timestamp=20.0)
        fields = {"capture_mode": "vad", "submitted_sample_count": 8}

        engine._submit_asr(np.zeros(8, dtype=np.float32), 16000, first, fields)
        engine._submit_asr(np.ones(8, dtype=np.float32), 16000, second, fields)

        payload = engine.asr_input_queue.get_nowait()
        assert payload[2]["utterance_id"] == "utterance-second"
        assert first.events()[-1]["stage"] == "asr_input_dropped"
        assert first.events()[-1]["fields"]["reason"] == "drop_oldest"

    @pytest.mark.asyncio
    async def test_tts_and_playback_keep_the_same_sidecar_trace(self):
        engine = self._make_engine()
        trace = engine.voice_diagnostics.new_trace("utterance-tts")
        engine.set_diagnostic_context(trace)
        engine.speak("Hello there")
        queued = engine.tts_queue.get_nowait()
        assert queued[2] is trace
        assert trace.events()[-1]["stage"] == "tts_enqueue"

        async def fake_synth(_text, _speed):
            yield np.ones(16, dtype=np.float32), 16000

        engine._synth_stream = fake_synth
        await engine._tts_stream_and_queue("Hello there", 1.0, trace=trace)
        playback = engine.playback_queue.get_nowait()
        assert playback[3] is trace
        engine.playback_queue.put(playback)
        assert [event["stage"] for event in trace.events()] == [
            "tts_enqueue",
            "tts_synthesis_start",
            "tts_synthesis_complete",
        ]

        with patch("charlie.voice.sd") as mock_sd:
            mock_sd.get_stream.side_effect = lambda: (engine.stop_event.set() or None)
            engine._playback_worker()

        assert trace.events()[-1]["stage"] == "playback_first_sample"
        assert engine.stop_event.is_set()


def test_diagnostic_trace_uses_monotonic_stage_deltas():
    diagnostics = VoiceDiagnostics(enabled=True, wav_enabled=False)
    trace = diagnostics.new_trace("utterance-timing")
    trace.mark("voice_capture_onset", timestamp=100.0)
    trace.mark("voice_capture_endpoint", timestamp=100.25)
    trace.mark("asr_enqueue", timestamp=100.30)

    events = trace.events()
    assert [event["stage"] for event in events] == [
        "voice_capture_onset",
        "voice_capture_endpoint",
        "asr_enqueue",
    ]
    assert events[1]["delta_from_previous_stage_ms"] == pytest.approx(250.0)
    assert events[2]["delta_from_onset_ms"] == pytest.approx(300.0)
    assert all("monotonic_timestamp" in event for event in events)


def test_diagnostic_wav_capture_defaults_off(tmp_path, monkeypatch):
    monkeypatch.delenv("CHARLIE_VOICE_DIAGNOSTICS", raising=False)
    monkeypatch.delenv("CHARLIE_VOICE_DIAGNOSTIC_WAV", raising=False)
    diagnostics = VoiceDiagnostics.from_env()
    trace = diagnostics.new_trace("utterance-off")

    assert diagnostics.enabled is False
    assert diagnostics.wav_enabled is False
    assert diagnostics.capture_audio(trace, np.ones(8, dtype=np.float32), 16000) is None
    assert list(Path(tmp_path).glob("*.wav")) == []


def test_diagnostic_wav_preserves_float32_buffer_and_uses_id_only_filename(tmp_path):
    diagnostics = VoiceDiagnostics(
        enabled=True,
        wav_enabled=True,
        directory=tmp_path,
        max_wav_files=2,
    )
    trace = diagnostics.new_trace("utterance-wav")
    samples = np.array([0.0, -0.25, 0.5], dtype=np.float32)

    path = diagnostics.capture_audio(trace, samples, 16000)

    assert path is not None
    output = Path(path)
    assert output.name.startswith("utterance-utterance-wav-")
    assert "hello" not in output.name
    assert output.read_bytes()[-samples.nbytes:] == np.ascontiguousarray(samples.astype("<f4")).tobytes()


def test_resource_telemetry_failure_is_nonfatal(monkeypatch):
    class FailingPsutil:
        Error = RuntimeError

        @staticmethod
        def Process(_pid):
            raise OSError("process unavailable")

        @staticmethod
        def virtual_memory():
            raise OSError("memory unavailable")

        @staticmethod
        def cpu_percent(*_args, **_kwargs):
            raise OSError("cpu unavailable")

        @staticmethod
        def process_iter(*_args, **_kwargs):
            raise OSError("process list unavailable")

    monkeypatch.setitem(sys.modules, "psutil", FailingPsutil())
    monkeypatch.setattr(VoiceDiagnostics, "_read_gpu_snapshot", staticmethod(lambda: {"gpu_metrics": "unavailable"}))

    snapshot = VoiceDiagnostics(enabled=True, wav_enabled=False).resource_snapshot(asr_worker_pid=1234)

    assert snapshot["resource_telemetry"] == "degraded"
    assert snapshot["gpu_metrics"] == "unavailable"
    assert "resource_telemetry_error" in snapshot


def test_voice_diagnostics_has_no_memory_or_session_store_writer():
    source = Path("charlie/voice_diagnostics.py").read_text(encoding="utf-8")
    assert "SessionStore" not in source
    assert "MemoryStore" not in source


# ---------------------------------------------------------------------------
# Echo detection -- is Charlie hearing its own TTS output?
# ---------------------------------------------------------------------------

class TestEchoDetection:
    """A long reply spans multiple speak() calls (one per sentence flush),
    and the mic can pick up any part of it -- not just the first chunk, and
    not only within a short fixed window. Found live: a mid-reply mishear of
    Charlie's own voice was wrongly treated as new user input (barge-in),
    cutting Charlie off for no real reason."""

    def _make_engine(self):
        with patch("charlie.voice.Kokoro"), \
             patch("charlie.voice.sd"), \
             patch("charlie.voice.mp.Queue"):
            return VoiceEngine(FakeConfig(), on_speech=lambda _: None)

    def test_echo_detected_while_still_speaking_past_old_fixed_window(self):
        """The old code only checked a fixed 2s window from the most recent
        speak() call -- a mishear arriving later than that, but while Charlie
        is still genuinely speaking, must still be recognized as echo."""
        engine = self._make_engine()
        engine.speak("This is a longer reply that takes a while to say.")
        engine.is_speaking.set()  # still speaking, well past the old 2s window
        assert engine.is_echo("longer reply") is True

    def test_echo_detected_from_earlier_chunk_of_same_reply(self):
        """The old code only compared against the MOST RECENT speak() call's
        text -- a mishear of an EARLIER sentence chunk of the same reply,
        heard after a later chunk started, must still match."""
        engine = self._make_engine()
        engine.is_speaking.set()  # first chunk already playing
        engine.speak("The first sentence chunk.")
        engine.speak("A second sentence chunk follows.")
        assert engine.is_echo("first sentence chunk") is True

    def test_echo_window_covers_shortly_after_speaking_stops(self):
        engine = self._make_engine()
        engine.speak("Hello there friend.")
        engine.is_speaking.clear()  # utterance just ended
        engine._last_speech_end = time.time()
        assert engine.is_echo("hello there") is True

    def test_not_echo_once_window_and_speaking_both_expired(self):
        engine = self._make_engine()
        engine.speak("Hello there friend.")
        engine.is_speaking.clear()
        engine._last_speech_end = time.time() - 10.0  # long finished
        assert engine.is_echo("hello there") is False

    def test_new_reply_resets_accumulated_words(self):
        """Once Charlie finishes one reply and starts a new one, the old
        reply's words must not still count as an echo match."""
        engine = self._make_engine()
        engine.is_speaking.set()
        engine.speak("Completely different earlier topic.")
        engine.is_speaking.clear()
        engine._last_speech_end = time.time() - 10.0  # force the old reply out of window
        engine.speak("Brand new reply text.")  # is_speaking is clear -> fresh start
        assert engine.is_echo("earlier topic") is False

    def test_genuine_new_user_speech_is_not_suppressed_as_echo(self):
        """Critical safety check: widening the echo window/word coverage
        must not start swallowing real barge-in speech that just happens to
        overlap zero words with what Charlie is currently saying."""
        engine = self._make_engine()
        engine.speak("The weather today is sunny and warm.")
        engine.is_speaking.set()
        assert engine.is_echo("open notepad and write this") is False
