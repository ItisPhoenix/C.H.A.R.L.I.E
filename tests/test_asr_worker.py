"""Tests for charlie.asr_worker's transcribe-kwargs construction.

Regression coverage for a real bug: initial_prompt (meant only to warm up the
Whisper model once at startup) was being applied to every real transcription
too, since non-warmup calls never set flags["warmup_context"] and so fell
back to the same default text. Combined with condition_on_previous_text=True,
Whisper anchored onto that prompt and echoed it back verbatim as a
"transcription" on weak/ambiguous audio instead of transcribing real speech.
"""

from dataclasses import dataclass

from charlie.asr_worker import _build_transcribe_kwargs, _filter_hallucinated_segments


@dataclass
class _FakeSegment:
    text: str
    no_speech_prob: float
    compression_ratio: float
    avg_logprob: float = -0.3  # confident by default; tests override to probe the threshold


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


def test_filter_keeps_real_speech_segment():
    real = _FakeSegment(text=" hello there", no_speech_prob=0.1, compression_ratio=1.2)
    assert _filter_hallucinated_segments([real]) == [real]


def test_filter_drops_high_no_speech_prob_segment():
    """Whisper itself is confident this segment isn't speech -- must be dropped."""
    silence = _FakeSegment(text=" thank you for watching", no_speech_prob=0.9, compression_ratio=1.1)
    assert _filter_hallucinated_segments([silence]) == []


def test_filter_drops_repetitive_hallucination_segment():
    """High compression_ratio flags repetitive decoding loops (e.g. 'stop stop stop...')."""
    loop = _FakeSegment(text=" stop stop stop stop stop stop", no_speech_prob=0.2, compression_ratio=3.0)
    assert _filter_hallucinated_segments([loop]) == []


def test_filter_mixed_segments_keeps_only_valid_ones():
    real = _FakeSegment(text=" open notepad", no_speech_prob=0.1, compression_ratio=1.3)
    hallucinated = _FakeSegment(text=" subscribe now", no_speech_prob=0.95, compression_ratio=1.0)
    assert _filter_hallucinated_segments([real, hallucinated]) == [real]


def test_filter_drops_low_confidence_segment():
    """Confident-sounding-but-wrong text (e.g. echoing the hotwords list on
    weak audio) isn't silence and isn't repetitive, so no_speech_prob and
    compression_ratio both miss it -- avg_logprob catches low-confidence
    decodes that neither of those signals do."""
    low_confidence = _FakeSegment(
        text=" close start stop search weather time date notepad",
        no_speech_prob=0.3,
        compression_ratio=1.5,
        avg_logprob=-1.4,
    )
    assert _filter_hallucinated_segments([low_confidence]) == []
