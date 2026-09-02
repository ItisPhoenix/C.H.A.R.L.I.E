import logging
import multiprocessing as mp
import os
import queue
import time
from typing import Any, Optional

import numpy as np
from faster_whisper import WhisperModel

from charlie.voice_diagnostics import VoiceDiagnostics

# Set up logging for the worker process
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("charlie.asr_worker")


# Match openai/whisper CLI's own hallucination-suppression defaults
# (no_speech_threshold=0.6, compression_ratio_threshold=2.4, logprob_threshold=-1.0)
# -- faster-whisper computes all three per segment but never acts on them,
# unlike the CLI. avg_logprob catches confident-sounding-but-wrong text (e.g.
# echoing the hotwords list) that no_speech_prob/compression_ratio miss,
# since that failure mode is neither silence nor a repetition loop.
_NO_SPEECH_PROB_THRESHOLD = 0.6
_COMPRESSION_RATIO_THRESHOLD = 2.4
_AVG_LOGPROB_THRESHOLD = -1.0
# A real "thank you" the user actually said has the model confident it heard
# speech (low no_speech_prob, well under this). A hallucinated one on silence
# still scores noticeably higher, just not high enough to cross the 0.6 cutoff
# above -- so gate the phrase denylist below on this softer secondary threshold
# instead of dropping every instance of these phrases outright.
_HALLUCINATION_PHRASE_NO_SPEECH_THRESHOLD = 0.3

# Whisper's well-documented failure mode: on near-silence/room noise it doesn't
# just say nothing, it confidently hallucinates one of a handful of stock
# phrases from its training data (YouTube-style captions). These pass the
# confidence filters above because the model IS confident -- just wrong. Only
# drop them when no_speech_prob also clears the softer threshold above, so a
# genuinely spoken "thank you" still comes through.
_HALLUCINATION_PHRASES = frozenset({
    "thank you",
    "thanks for watching",
    "thank you for watching",
    "please subscribe",
    "bye",
    "bye bye",
    "goodbye",
    "okay",
    "i'm sorry",
})


def _filter_hallucinated_segments(segments) -> list:
    """Drop segments Whisper itself flags as likely non-speech, a repetition
    loop, or low-confidence -- silence/noise can otherwise decode into a
    plausible-looking sentence (e.g. "thank you for watching", "stop stop
    stop...", or echoing the hotwords list) instead of being dropped."""
    return [
        s
        for s in segments
        if s.no_speech_prob < _NO_SPEECH_PROB_THRESHOLD
        and s.compression_ratio < _COMPRESSION_RATIO_THRESHOLD
        and s.avg_logprob > _AVG_LOGPROB_THRESHOLD
        and not (
            s.no_speech_prob >= _HALLUCINATION_PHRASE_NO_SPEECH_THRESHOLD
            and s.text.strip().lower().rstrip(".") in _HALLUCINATION_PHRASES
        )
    ]


def _numeric_mean(segments: list[Any], attribute: str) -> Optional[float]:
    values = []
    for segment in segments:
        try:
            value = getattr(segment, attribute)
            if value is not None:
                values.append(float(value))
        except (AttributeError, TypeError, ValueError):
            continue
    return sum(values) / len(values) if values else None


def _quality_metadata(
    segments: list[Any],
    info: Any,
    text: str,
    *,
    asr_latency_ms: float,
    audio_duration_ms: float,
) -> dict[str, Any]:
    """Expose truthful aggregate metadata from accepted Faster-Whisper segments."""

    return {
        "avg_logprob": _numeric_mean(segments, "avg_logprob"),
        "no_speech_prob": _numeric_mean(segments, "no_speech_prob"),
        "compression_ratio": _numeric_mean(segments, "compression_ratio"),
        "segment_count": len(segments),
        "decoded_text_length": len(text),
        "language": getattr(info, "language", None),
        "language_probability": getattr(info, "language_probability", None),
        "asr_latency_ms": round(asr_latency_ms, 3),
        "audio_duration_ms": round(audio_duration_ms, 3),
    }


def _worker_stage(
    events: list[dict[str, Any]],
    stage: str,
    timestamp: float,
    *,
    previous_timestamp: Optional[float],
    onset_timestamp: Optional[float],
    utterance_id: Optional[str],
    fields: Optional[dict[str, Any]] = None,
) -> float:
    """Capture worker-side stage time without depending on parent process state."""

    event = {
        "utterance_id": utterance_id,
        "turn_id": None,
        "session_id": None,
        "stage": stage,
        "monotonic_timestamp": timestamp,
        "delta_from_previous_stage_ms": (
            (timestamp - previous_timestamp) * 1000 if previous_timestamp is not None else None
        ),
        "delta_from_onset_ms": (
            (timestamp - onset_timestamp) * 1000 if onset_timestamp is not None else None
        ),
        "fields": fields or {},
    }
    events.append(event)
    return timestamp


def _build_transcribe_kwargs(
    is_warmup: bool, flags: dict, default_language: str, asr_config: dict | None
) -> dict:
    """Build faster-whisper transcribe() kwargs.

    initial_prompt is only set for the one-time warm-up call. Passing it on
    every real transcription anchors Whisper onto that fixed text, and on
    weak/ambiguous audio it echoes the prompt back verbatim instead of
    transcribing what was actually said.
    """
    kwargs = dict(
        language=default_language,
        word_timestamps=False,
    )
    if is_warmup:
        kwargs.update(
            initial_prompt=flags.get(
                "warmup_context",
                "This is Charlie, a voice assistant. Short conversational English with real words.",
            ),
            condition_on_previous_text=False,
            beam_size=1,
            best_of=1,
            vad_filter=False,
        )
    else:
        _ac = asr_config or {}
        kwargs.update(
            condition_on_previous_text=True,
            beam_size=_ac.get("beam_size", 6),
            best_of=_ac.get("best_of", 6),
            vad_filter=True,
            vad_parameters=dict(
                threshold=_ac.get("vad_threshold", 0.45),
                min_speech_duration_ms=_ac.get("min_speech_duration_ms", 120),
                max_speech_duration_s=_ac.get("max_speech_duration_s", 60),
                min_silence_duration_ms=_ac.get("min_silence_duration_ms", 480),
                speech_pad_ms=_ac.get("speech_pad_ms", 320),
            ),
            repetition_penalty=_ac.get("repetition_penalty", 1.15),
            no_repeat_ngram_size=3,
            hotwords=(
                "Charlie open close start stop search weather time date "
                "notepad chrome calculator python code youtube"
            ),
        )
    return kwargs


def asr_worker_process(
    input_queue: mp.Queue,
    output_queue: mp.Queue,
    model_size: str,
    device: str,
    default_language: str,
    asr_config: dict | None = None,
):
    """
    Worker process that handles Whisper transcription.
    """
    logger.info(f"ASR Worker started. Loading model: {model_size} on {device}")

    try:
        # Load WhisperModel once
        whisper = WhisperModel(
            model_size,
            device=device,
            compute_type="float16" if device == "cuda" else "int8",
            local_files_only=True,
        )
    except Exception as e:
        logger.warning(
            f"ASR Worker: Local load failed for {model_size}, attempting download: {e}"
        )
        try:
            whisper = WhisperModel(
                model_size,
                device=device,
                compute_type="float16" if device == "cuda" else "int8",
            )
        except Exception as e2:
            logger.warning(
                f"ASR Worker: Failed to load {model_size}: {e2}. Falling back to large-v3."
            )
            whisper = WhisperModel(
                "large-v3",
                device=device,
                compute_type="float16" if device == "cuda" else "int8",
            )

    logger.info("ASR Worker: Whisper model loaded and ready.")
    output_queue.put({"type": "ready", "model": model_size})
    worker_diagnostics = None

    while True:
        try:
            # Poll with timeout so KeyboardInterrupt can fire cleanly
            try:
                payload = input_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            except Exception as e:
                logger.warning(f"ASR input queue error: {e}")
                continue
            if payload is None:  # Shutdown signal
                if worker_diagnostics is not None:
                    worker_diagnostics.stop()
                break

            # Robust unpacking: 3-tuple, 2-tuple, or raw numpy array
            sample_rate = 16000
            if isinstance(payload, tuple) and len(payload) == 3:
                audio_data_bytes, sample_rate, flags = payload
            elif isinstance(payload, tuple) and len(payload) == 2:
                audio_data_bytes, sample_rate = payload
                flags = {}
            elif isinstance(payload, np.ndarray):
                audio_data_bytes = payload.tobytes()
                flags = {}
            else:
                logger.error(f"ASR Worker: Invalid payload type: {type(payload)}")
                continue
            if not isinstance(flags, dict):
                flags = {}
            try:
                sample_rate = int(sample_rate)
            except (TypeError, ValueError):
                sample_rate = 16000
            is_warmup = flags.get("is_warmup", False)
            utterance_id = flags.get("utterance_id")
            diagnostics_enabled = bool(flags.get("diagnostic_enabled", False))
            if diagnostics_enabled and worker_diagnostics is None:
                worker_diagnostics = VoiceDiagnostics(enabled=True, wav_enabled=False)
                worker_diagnostics.start_resource_sampler(asr_worker_pid=os.getpid())
            diagnostic_stages: list[dict[str, Any]] = []
            onset_timestamp = None
            previous_timestamp = None
            if diagnostics_enabled:
                try:
                    onset_timestamp = float(flags.get("voice_capture_onset_monotonic"))
                except (TypeError, ValueError):
                    onset_timestamp = None
                try:
                    previous_timestamp = float(flags.get("asr_enqueue_monotonic"))
                except (TypeError, ValueError):
                    previous_timestamp = onset_timestamp
                dequeue_timestamp = time.monotonic()
                queue_depth = None
                try:
                    queue_depth = input_queue.qsize()
                except (AttributeError, NotImplementedError, OSError):
                    pass
                previous_timestamp = _worker_stage(
                    diagnostic_stages,
                    "asr_worker_dequeue",
                    dequeue_timestamp,
                    previous_timestamp=previous_timestamp,
                    onset_timestamp=onset_timestamp,
                    utterance_id=utterance_id,
                    fields={
                        "worker_pid": os.getpid(),
                        "queue_depth_after_dequeue": queue_depth,
                        "dequeue_age_ms": (
                            (dequeue_timestamp - float(flags["asr_enqueue_monotonic"])) * 1000
                            if flags.get("asr_enqueue_monotonic") is not None
                            else None
                        ),
                        "resource_snapshot": (
                            worker_diagnostics.resource_snapshot(asr_worker_pid=os.getpid())
                            if worker_diagnostics is not None
                            else None
                        ),
                    },
                )

            audio_data = np.frombuffer(audio_data_bytes, dtype=np.float32)

            start_time = time.monotonic()

            transcribe_kwargs = _build_transcribe_kwargs(
                is_warmup, flags, default_language, asr_config
            )

            if diagnostics_enabled:
                previous_timestamp = _worker_stage(
                    diagnostic_stages,
                    "asr_start",
                    start_time,
                    previous_timestamp=previous_timestamp,
                    onset_timestamp=onset_timestamp,
                    utterance_id=utterance_id,
                    fields={
                        "worker_pid": os.getpid(),
                        "model": model_size,
                        "sample_rate": sample_rate,
                        "sample_count": int(audio_data.size),
                        "audio_duration_ms": audio_data.size / sample_rate * 1000 if sample_rate else None,
                        "beam_size": transcribe_kwargs.get("beam_size"),
                        "best_of": transcribe_kwargs.get("best_of"),
                        "condition_on_previous_text": transcribe_kwargs.get("condition_on_previous_text"),
                        "vad_filter": transcribe_kwargs.get("vad_filter"),
                        "vad_parameters": transcribe_kwargs.get("vad_parameters"),
                        "resource_snapshot": (
                            worker_diagnostics.resource_snapshot(asr_worker_pid=os.getpid())
                            if worker_diagnostics is not None
                            else None
                        ),
                    },
                )

            segments, info = whisper.transcribe(audio_data, **transcribe_kwargs)
            segments = _filter_hallucinated_segments(list(segments))

            text = "".join([s.text for s in segments]).strip()
            latency_ms = (time.monotonic() - start_time) * 1000
            audio_duration_ms = audio_data.size / sample_rate * 1000 if sample_rate else 0.0
            quality = _quality_metadata(
                segments,
                info,
                text,
                asr_latency_ms=latency_ms,
                audio_duration_ms=audio_duration_ms,
            )
            # Compatibility value retained for existing callers. This is the
            # model's language probability, not transcript confidence.
            legacy_confidence = info.language_probability
            if diagnostics_enabled:
                previous_timestamp = _worker_stage(
                    diagnostic_stages,
                    "asr_complete",
                    time.monotonic(),
                    previous_timestamp=previous_timestamp,
                    onset_timestamp=onset_timestamp,
                    utterance_id=utterance_id,
                    fields={
                        "worker_pid": os.getpid(),
                        "quality": quality,
                        "confidence_semantics": "language_probability",
                        "resource_snapshot": (
                            worker_diagnostics.resource_snapshot(asr_worker_pid=os.getpid())
                            if worker_diagnostics is not None
                            else None
                        ),
                    },
                )
            logger.info(
                f"pipeline_stage | stage=asr | latency_ms={latency_ms:.1f} | warmup={is_warmup}"
            )
            output_flags = {
                "is_warmup": is_warmup,
                "utterance_id": utterance_id,
                "language_probability": quality["language_probability"],
                "confidence": legacy_confidence,
                "confidence_semantics": "language_probability",
                "asr_quality": quality,
                "capture": flags.get("capture", {}),
            }
            if diagnostics_enabled:
                output_flags["diagnostic_stages"] = diagnostic_stages
                output_flags["asr_complete_monotonic"] = previous_timestamp
            output_queue.put((text, legacy_confidence, output_flags))

        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"ASR Worker: Error during transcription: {e}")
            error_flags = {"is_warmup": False, "utterance_id": locals().get("utterance_id")}
            if locals().get("diagnostics_enabled"):
                error_timestamp = time.monotonic()
                _worker_stage(
                    locals().get("diagnostic_stages", []),
                    "asr_error",
                    error_timestamp,
                    previous_timestamp=locals().get("previous_timestamp"),
                    onset_timestamp=locals().get("onset_timestamp"),
                    utterance_id=locals().get("utterance_id"),
                    fields={"error": type(e).__name__, "worker_pid": os.getpid()},
                )
                error_flags["diagnostic_stages"] = locals().get("diagnostic_stages", [])
            error_flags["asr_error"] = type(e).__name__
            output_queue.put(("", 0.0, error_flags))

    logger.info("ASR Worker: Shutting down.")


if __name__ == "__main__":
    # This file isn't meant to be run directly, but if it is, we could add test logic here
    pass
