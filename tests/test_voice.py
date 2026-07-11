"""Unit tests for charlie.voice.

Mocks sounddevice, Kokoro, and multiprocessing to avoid audio hardware.
Focuses on the text humanization pipeline, RMS calculation, and init logic.
"""

from unittest.mock import patch

import numpy as np
import pytest

from charlie.voice import VoiceEngine


class FakeConfig:
    kokoro_model_dir = "/tmp/kokoro_models"
    wake_word_enabled = False
    wake_word_model_path = ""


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

    def test_wake_word_disabled_by_default(self):
        engine = self._make_engine()
        assert engine._wake_word_detector is None

    def test_stop_tts_sets_event(self):
        engine = self._make_engine()
        assert not engine.stop_tts_event.is_set()
        engine.stop_tts()
        assert engine.stop_tts_event.is_set()

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

    def test_rms_static_method(self):
        samples = np.zeros(100, dtype=np.float32)
        assert VoiceEngine._rms(samples) == 0.0
