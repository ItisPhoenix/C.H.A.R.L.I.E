"""Tests for charlie.asr_worker's transcribe-kwargs construction.

Regression coverage for a real bug: initial_prompt (meant only to warm up the
Whisper model once at startup) was being applied to every real transcription
too, since non-warmup calls never set flags["warmup_context"] and so fell
back to the same default text. Combined with condition_on_previous_text=True,
Whisper anchored onto that prompt and echoed it back verbatim as a
"transcription" on weak/ambiguous audio instead of transcribing real speech.
"""

from charlie.asr_worker import _build_transcribe_kwargs


def test_warmup_uses_default_initial_prompt():
    kwargs = _build_transcribe_kwargs(
        is_warmup=True, flags={}, default_language="en", asr_config=None
    )
    assert kwargs["initial_prompt"] == (
        "This is Charlie, a voice assistant. Short conversational English with real words."
    )


def test_warmup_uses_custom_warmup_context():
    kwargs = _build_transcribe_kwargs(
        is_warmup=True,
        flags={"warmup_context": "custom warmup text"},
        default_language="en",
        asr_config=None,
    )
    assert kwargs["initial_prompt"] == "custom warmup text"


def test_real_transcription_has_no_initial_prompt():
    """The bug: a real (non-warmup) call must never get an initial_prompt --
    passing one biases Whisper toward echoing it back on weak audio."""
    kwargs = _build_transcribe_kwargs(
        is_warmup=False, flags={}, default_language="en", asr_config=None
    )
    assert "initial_prompt" not in kwargs


def test_real_transcription_uses_configured_vad_parameters():
    kwargs = _build_transcribe_kwargs(
        is_warmup=False,
        flags={},
        default_language="en",
        asr_config={"vad_threshold": 0.6, "beam_size": 3},
    )
    assert kwargs["vad_parameters"]["threshold"] == 0.6
    assert kwargs["beam_size"] == 3
    assert kwargs["condition_on_previous_text"] is True


def test_warmup_disables_vad_filter():
    kwargs = _build_transcribe_kwargs(
        is_warmup=True, flags={}, default_language="en", asr_config=None
    )
    assert kwargs["vad_filter"] is False
    assert kwargs["beam_size"] == 1
