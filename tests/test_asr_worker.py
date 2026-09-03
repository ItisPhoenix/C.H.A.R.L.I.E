"""Tests for charlie.asr_worker's transcribe-kwargs construction.

Regression coverage for a real bug: initial_prompt (meant only to warm up the
Whisper model once at startup) was being applied to every real transcription
too, since non-warmup calls never set flags["warmup_context"] and so fell
back to the same default text. Combined with condition_on_previous_text=True,
Whisper anchored onto that prompt and echoed it back verbatim as a
"transcription" on weak/ambiguous audio instead of transcribing real speech.
"""

import time
from argparse import Namespace
from dataclasses import dataclass
from types import SimpleNamespace

import charlie.asr_worker as asr_worker
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


def test_worker_acknowledges_readiness_only_after_model_initializes(monkeypatch):
    calls = []

    class FakeModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, audio, **kwargs):
            calls.append((audio, kwargs))
            return ([], SimpleNamespace(language="en", language_probability=1.0))

    class StopInputQueue:
        def get(self, timeout):
            raise KeyboardInterrupt

    class OutputQueue:
        def __init__(self):
            self.messages = []

        def put(self, message):
            self.messages.append(message)

    monkeypatch.setattr(asr_worker, "WhisperModel", FakeModel)
    output_queue = OutputQueue()

    asr_worker.asr_worker_process(
        StopInputQueue(),
        output_queue,
        "distil-large-v3",
        "cpu",
        "en",
    )

    assert output_queue.messages[0]["type"] == "ready"
    assert output_queue.messages[0]["model"] == "distil-large-v3"
    assert output_queue.messages[0]["metrics"].keys() == {
        "model_load_ms",
        "warmup_inference_ms",
        "asr_ready_ms",
    }
    assert len(calls) == 1
    assert len(calls[0][0]) == 1600
    assert calls[0][1]["vad_filter"] is False
    assert calls[0][1]["beam_size"] == 1


def test_worker_reports_warmup_failure_without_claiming_readiness(monkeypatch):
    class FakeModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *_args, **_kwargs):
            raise RuntimeError("synthetic warm-up failure")

    class StopInputQueue:
        def get(self, timeout):
            raise AssertionError("worker must not enter capture loop after warm-up failure")

    class OutputQueue:
        def __init__(self):
            self.messages = []

        def put(self, message):
            self.messages.append(message)

    monkeypatch.setattr(asr_worker, "WhisperModel", FakeModel)
    output_queue = OutputQueue()

    asr_worker.asr_worker_process(
        StopInputQueue(),
        output_queue,
        "distil-large-v3",
        "cpu",
        "en",
    )

    assert output_queue.messages[0]["type"] == "failed"
    assert output_queue.messages[0]["stage"] == "warmup"
    assert output_queue.messages[0]["metrics"]["asr_ready_ms"] is None


def test_worker_preserves_utterance_id_and_reports_truthful_quality_metadata(monkeypatch):
    class FakeModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, audio, **kwargs):
            assert len(audio) == 1600
            return (
                [_FakeSegment(" hello there", 0.1, 1.2, avg_logprob=-0.2)],
                SimpleNamespace(language="en", language_probability=0.93),
            )

    class InputQueue:
        def __init__(self):
            self.messages = [
                (
                    b"\x00\x00\x00\x00" * 1600,
                    16000,
                    {
                        "utterance_id": "utterance-worker",
                        "diagnostic_enabled": True,
                        "voice_capture_onset_monotonic": 10.0,
                        "asr_enqueue_monotonic": 10.1,
                        "capture": {"submitted_sample_count": 1600},
                    },
                )
            ]

        def get(self, timeout):
            if self.messages:
                return self.messages.pop(0)
            raise KeyboardInterrupt

        def qsize(self):
            return len(self.messages)

    class OutputQueue:
        def __init__(self):
            self.messages = []

        def put(self, message):
            self.messages.append(message)

    monkeypatch.setattr(asr_worker, "WhisperModel", FakeModel)
    monkeypatch.setattr(
        asr_worker.VoiceDiagnostics,
        "resource_snapshot",
        lambda self, **kwargs: {"gpu_metrics": "unavailable"},
    )
    output_queue = OutputQueue()

    asr_worker.asr_worker_process(
        InputQueue(),
        output_queue,
        "distil-large-v3",
        "cpu",
        "en",
        {"beam_size": 6, "best_of": 6},
    )

    text, compatibility_value, flags = next(
        message for message in output_queue.messages if isinstance(message, tuple)
    )
    assert text == "hello there"
    assert compatibility_value == 0.93
    assert flags["utterance_id"] == "utterance-worker"
    assert flags["confidence_semantics"] == "language_probability"
    assert "transcript_confidence" not in flags
    assert flags["asr_quality"] == {
        "avg_logprob": -0.2,
        "no_speech_prob": 0.1,
        "compression_ratio": 1.2,
        "segment_count": 1,
        "decoded_text_length": 11,
        "language": "en",
        "language_probability": 0.93,
        "asr_latency_ms": flags["asr_quality"]["asr_latency_ms"],
        "audio_duration_ms": 100.0,
    }
    assert [event["stage"] for event in flags["diagnostic_stages"]] == [
        "asr_worker_dequeued",
        "asr_worker_transcribe_enter",
        "asr_worker_segments_iteration_begin",
        "asr_worker_segments_iteration_complete",
        "asr_worker_result_built",
    ]


def test_worker_emits_lazy_iterator_boundary_stages(monkeypatch):
    calls = []

    class FakeModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, audio, **kwargs):
            if len(audio) == 1600:
                return ([], SimpleNamespace(language="en", language_probability=1.0))
            calls.append("transcribe_returned")

            def lazy_segments():
                calls.append("iterator_started")
                yield _FakeSegment(" fixed phrase", 0.1, 1.2)

            return lazy_segments(), SimpleNamespace(language="en", language_probability=0.91)

    class InputQueue:
        def __init__(self):
            self.messages = [
                (
                    b"\x00\x00\x00\x00" * 3200,
                    16000,
                    {
                        "utterance_id": "utterance-lazy",
                        "diagnostic_enabled": True,
                        "voice_capture_onset_monotonic": 10.0,
                        "asr_enqueue_monotonic": 10.1,
                    },
                )
            ]

        def get(self, timeout):
            if self.messages:
                return self.messages.pop(0)
            raise KeyboardInterrupt

        def qsize(self):
            return len(self.messages)

    class OutputQueue:
        def __init__(self):
            self.messages = []

        def put(self, message):
            self.messages.append(message)

        def qsize(self):
            return len(self.messages)

    monkeypatch.setattr(asr_worker, "WhisperModel", FakeModel)
    monkeypatch.setattr(
        asr_worker.VoiceDiagnostics,
        "resource_snapshot",
        lambda self, **kwargs: {"asr_worker_rss_bytes": 123},
    )
    output_queue = OutputQueue()

    asr_worker.asr_worker_process(
        InputQueue(),
        output_queue,
        "distil-large-v3",
        "cpu",
        "en",
        {"beam_size": 6, "best_of": 6},
    )

    stages = [
        message["stage"]
        for message in output_queue.messages
        if isinstance(message, dict) and message.get("type") == "asr_worker_stage"
    ]
    assert stages == [
        "asr_worker_dequeued",
        "asr_worker_transcribe_enter",
        "asr_worker_segments_iteration_begin",
        "asr_worker_segments_iteration_complete",
        "asr_worker_result_built",
        "asr_worker_result_enqueued",
    ]
    result_index = next(
        index for index, message in enumerate(output_queue.messages) if isinstance(message, tuple)
    )
    result_enqueued_index = next(
        index
        for index, message in enumerate(output_queue.messages)
        if isinstance(message, dict) and message.get("stage") == "asr_worker_result_enqueued"
    )
    assert result_index < result_enqueued_index
    assert calls == ["transcribe_returned", "iterator_started"]
    first_stage = next(
        message
        for message in output_queue.messages
        if isinstance(message, dict) and message.get("type") == "asr_worker_stage"
    )
    stage_fields = first_stage["fields"]
    assert first_stage["utterance_id"] == "utterance-lazy"
    assert stage_fields["worker_pid"] > 0
    assert stage_fields["audio_sample_count"] == 3200
    assert stage_fields["audio_duration_ms"] == 200.0
    assert stage_fields["model"] == "distil-large-v3"
    assert stage_fields["device"] == "cpu"
    assert stage_fields["compute_type"] == "int8"
    assert stage_fields["beam_size"] == 6
    assert stage_fields["best_of"] == 6
    assert stage_fields["vad_filter"] is True
    assert stage_fields["asr_worker_rss_bytes"] == 123


def test_worker_emits_exception_boundary_before_empty_error_result(monkeypatch):
    class FakeModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *_args, **kwargs):
            if kwargs.get("vad_filter") is False:
                return ([], SimpleNamespace(language="en", language_probability=1.0))
            raise RuntimeError("synthetic iterator failure")

    class InputQueue:
        def __init__(self):
            self.messages = [
                (
                    b"\x00\x00\x00\x00" * 1600,
                    16000,
                    {
                        "utterance_id": "utterance-error",
                        "diagnostic_enabled": False,
                        "asr_enqueue_monotonic": time.monotonic(),
                    },
                )
            ]

        def get(self, timeout):
            if self.messages:
                return self.messages.pop(0)
            raise KeyboardInterrupt

        def qsize(self):
            return len(self.messages)

    class OutputQueue:
        def __init__(self):
            self.messages = []

        def put(self, message):
            self.messages.append(message)

    monkeypatch.setattr(asr_worker, "WhisperModel", FakeModel)
    output_queue = OutputQueue()

    asr_worker.asr_worker_process(InputQueue(), output_queue, "distil-large-v3", "cpu", "en")

    statuses = [
        message
        for message in output_queue.messages
        if isinstance(message, dict) and message.get("type") == "asr_worker_stage"
    ]
    assert [message["stage"] for message in statuses] == [
        "asr_worker_dequeued",
        "asr_worker_transcribe_enter",
        "asr_worker_exception",
    ]
    exception = statuses[-1]
    assert exception["fields"]["last_worker_stage"] == "asr_worker_transcribe_enter"
    assert exception["fields"]["exception_type"] == "RuntimeError"
    assert exception["fields"]["exception_message"] == "synthetic iterator failure"
    error_result = next(message for message in output_queue.messages if isinstance(message, tuple))
    assert error_result[2]["asr_error"] == "RuntimeError"
    assert error_result[2]["asr_worker_last_stage"] == "asr_worker_transcribe_enter"


def test_worker_keeps_legacy_two_tuple_payload_compatible(monkeypatch):
    class FakeModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, audio, **kwargs):
            return ([_FakeSegment(" compatible", 0.1, 1.2)], SimpleNamespace(language="en", language_probability=0.8))

    class InputQueue:
        def __init__(self):
            self.first = True

        def get(self, timeout):
            if self.first:
                self.first = False
                return (b"\x00\x00\x00\x00" * 1600, 16000)
            raise KeyboardInterrupt

    class OutputQueue:
        def __init__(self):
            self.messages = []

        def put(self, message):
            self.messages.append(message)

    monkeypatch.setattr(asr_worker, "WhisperModel", FakeModel)
    output_queue = OutputQueue()

    asr_worker.asr_worker_process(InputQueue(), output_queue, "distil-large-v3", "cpu", "en")

    assert output_queue.messages[1][0] == "compatible"
    assert output_queue.messages[1][2]["utterance_id"] is None


def test_replay_benchmark_exposes_current_and_controlled_variants():
    from tools.voice_asr_benchmark import VARIANT_SETTINGS, _run_variant, _variant_kwargs

    args = Namespace(
        model="distil-large-v3",
        device="cuda",
        compute_type="float16",
        language="en",
        beam_size=1,
        best_of=1,
        condition_on_previous_text=True,
        vad_filter=True,
        repetition_penalty=1.15,
        vad_threshold=0.05,
        min_speech_duration_ms=120,
        max_speech_duration_s=60,
        min_silence_duration_ms=480,
        speech_pad_ms=500,
    )

    assert VARIANT_SETTINGS["current"] == {}
    assert _variant_kwargs(VARIANT_SETTINGS["current"], args)["beam_size"] == 1
    assert VARIANT_SETTINGS["beam6_best6"] == {
        "beam_size": 6,
        "best_of": 6,
        "condition_on_previous_text": True,
        "vad_filter": True,
    }
    assert VARIANT_SETTINGS["beam1_best1"]["beam_size"] == 1
    assert VARIANT_SETTINGS["beam1_best1"]["best_of"] == 1
    assert VARIANT_SETTINGS["vad_disabled"]["vad_filter"] is False
    assert VARIANT_SETTINGS["condition_off"]["condition_on_previous_text"] is False
    assert VARIANT_SETTINGS["beam1_vad_disabled_condition_off"] == {
        "beam_size": 1,
        "best_of": 1,
        "condition_on_previous_text": False,
        "vad_filter": False,
    }

    class FakeModel:
        def transcribe(self, _audio, **kwargs):
            self.kwargs = kwargs
            return ([_FakeSegment(" hello", 0.1, 1.2)], SimpleNamespace(language="en", language_probability=0.9))

    result = _run_variant(FakeModel(), [0.0] * 1600, 16000, "current", args)
    assert result["effective_parameters"] == {
        "model": "distil-large-v3",
        "device": "cuda",
        "compute_type": "float16",
        "language": "en",
        "beam_size": 1,
        "best_of": 1,
        "condition_on_previous_text": True,
        "vad_filter": True,
        "repetition_penalty": 1.15,
        "vad_parameters": {
            "threshold": 0.05,
            "min_speech_duration_ms": 120,
            "max_speech_duration_s": 60,
            "min_silence_duration_ms": 480,
            "speech_pad_ms": 500,
        },
    }


def test_replay_current_resolves_effective_config_defaults(monkeypatch):
    import tools.voice_asr_benchmark as benchmark

    monkeypatch.setattr(benchmark.runtime_config, "whisper_model", "configured-model")
    monkeypatch.setattr(benchmark.runtime_config, "gpu_device", "cpu")
    monkeypatch.setattr(benchmark.runtime_config, "default_language", "en")
    monkeypatch.setattr(benchmark.runtime_config, "asr_beam_size", 1)
    monkeypatch.setattr(benchmark.runtime_config, "asr_best_of", 1)
    monkeypatch.setattr(benchmark.runtime_config, "asr_repetition_penalty", 1.15)
    monkeypatch.setattr(benchmark.runtime_config, "vad_threshold", 0.05)
    monkeypatch.setattr(benchmark.runtime_config, "vad_min_speech_duration_ms", 120)
    monkeypatch.setattr(benchmark.runtime_config, "vad_max_speech_duration_s", 60)
    monkeypatch.setattr(benchmark.runtime_config, "vad_min_silence_duration_ms", 480)
    monkeypatch.setattr(benchmark.runtime_config, "vad_speech_pad_ms", 500)

    args = Namespace(
        model=None,
        device=None,
        compute_type=None,
        language=None,
        beam_size=None,
        best_of=None,
        condition_on_previous_text=None,
        vad_filter=None,
        repetition_penalty=None,
        vad_threshold=None,
        min_speech_duration_ms=None,
        max_speech_duration_s=None,
        min_silence_duration_ms=None,
        speech_pad_ms=None,
    )

    resolved = benchmark._resolve_runtime_defaults(args)

    assert resolved.model == "configured-model"
    assert resolved.device == "cpu"
    assert resolved.beam_size == 1
    assert resolved.best_of == 1
    assert resolved.condition_on_previous_text is True
    assert resolved.vad_filter is True
    assert resolved.vad_threshold == 0.05
    assert resolved.speech_pad_ms == 500


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


def test_filter_drops_thank_you_hallucination_on_borderline_silence():
    """Real bug: Whisper hallucinates 'Thank you.' on room noise/silence with
    no_speech_prob just under the hard 0.6 cutoff -- confident enough to pass
    the other filters, but still elevated compared to real speech."""
    hallucinated = _FakeSegment(text=" Thank you.", no_speech_prob=0.45, compression_ratio=1.0)
    assert _filter_hallucinated_segments([hallucinated]) == []


def test_filter_keeps_genuine_thank_you():
    """A real, deliberately spoken 'thank you' has the model confident it's
    speech (low no_speech_prob) -- must not be swept up by the phrase denylist."""
    genuine = _FakeSegment(text=" Thank you.", no_speech_prob=0.05, compression_ratio=1.0)
    assert _filter_hallucinated_segments([genuine]) == [genuine]
