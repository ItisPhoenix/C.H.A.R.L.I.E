import concurrent.futures
import logging
import multiprocessing as mp
import queue
import time

import numpy as np
from faster_whisper import WhisperModel

# Set up logging for the worker process
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("charlie.asr_worker")

# A stuck transcribe() call previously wedged the whole worker forever -- see _run_transcribe.
_TRANSCRIBE_TIMEOUT_S = 30.0
# int8_float16 on CUDA: ~2x faster and ~35% less VRAM than float16, <0.5% WER regression on Ampere+.


def _compute_type(device: str) -> str:
    return "int8_float16" if device == "cuda" else "int8"


_file_model: "WhisperModel | None" = None


def _get_file_model(model_size: str, device: str) -> WhisperModel:
    """Lazily-loaded singleton for one-off file transcription (e.g. Telegram voice notes)."""
    global _file_model
    if _file_model is None:
        _file_model = WhisperModel(
            model_size, device=device, compute_type=_compute_type(device), local_files_only=True
        )
    return _file_model


def transcribe_file(
    path: str, model_size: str, device: str, default_language: str, asr_config: dict | None = None
) -> str:
    """Transcribe an arbitrary audio file (faster-whisper decodes via PyAV, no ffmpeg install needed)."""
    whisper = _get_file_model(model_size, device)
    kwargs = _build_transcribe_kwargs(
        is_warmup=False, flags={}, default_language=default_language, asr_config=asr_config
    )
    segments, _info = whisper.transcribe(path, **kwargs)
    segments = _filter_hallucinated_segments(list(segments))
    return "".join(s.text for s in segments).strip()


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
    stop...", or echoing the hotwords list) instead of being dropped.

    Logs every dropped segment with the reason -- without this, a segment
    that was real speech but tripped a threshold vanishes with zero trace,
    indistinguishable from Charlie simply not hearing anything."""
    kept = []
    for s in segments:
        if s.no_speech_prob >= _NO_SPEECH_PROB_THRESHOLD:
            logger.info(f"asr_segment_dropped | reason=no_speech_prob | value={s.no_speech_prob:.3f} | text={s.text!r}")
            continue
        if s.compression_ratio >= _COMPRESSION_RATIO_THRESHOLD:
            logger.info(
                f"asr_segment_dropped | reason=compression_ratio | value={s.compression_ratio:.3f} | text={s.text!r}"
            )
            continue
        if s.avg_logprob <= _AVG_LOGPROB_THRESHOLD:
            logger.info(f"asr_segment_dropped | reason=avg_logprob | value={s.avg_logprob:.3f} | text={s.text!r}")
            continue
        if (
            s.no_speech_prob >= _HALLUCINATION_PHRASE_NO_SPEECH_THRESHOLD
            and s.text.strip().lower().rstrip(".") in _HALLUCINATION_PHRASES
        ):
            logger.info(
                f"asr_segment_dropped | reason=hallucination_phrase | "
                f"no_speech_prob={s.no_speech_prob:.3f} | text={s.text!r}"
            )
            continue
        kept.append(s)
    return kept


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
            compute_type=_compute_type(device),
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
                compute_type=_compute_type(device),
            )
        except Exception as e2:
            logger.warning(
                f"ASR Worker: Failed to load {model_size}: {e2}. Falling back to large-v3."
            )
            whisper = WhisperModel(
                "large-v3",
                device=device,
                compute_type=_compute_type(device),
            )

    logger.info("ASR Worker: Whisper model loaded and ready.")

    def _run_transcribe(audio_data: np.ndarray, kwargs: dict) -> tuple:
        segments, info = whisper.transcribe(audio_data, **kwargs)
        segments = _filter_hallucinated_segments(list(segments))
        text = "".join([s.text for s in segments]).strip()
        return text, info.language_probability

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
                break

            # Robust unpacking: 3-tuple, 2-tuple, or raw numpy array
            if isinstance(payload, tuple) and len(payload) == 3:
                audio_data_bytes, _, flags = payload
            elif isinstance(payload, tuple) and len(payload) == 2:
                audio_data_bytes, _ = payload
                flags = {}
            elif isinstance(payload, np.ndarray):
                audio_data_bytes = payload.tobytes()
                flags = {}
            else:
                logger.error(f"ASR Worker: Invalid payload type: {type(payload)}")
                continue
            is_warmup = flags.get("is_warmup", False)

            audio_data = np.frombuffer(audio_data_bytes, dtype=np.float32)

            start_time = time.time()

            transcribe_kwargs = _build_transcribe_kwargs(
                is_warmup, flags, default_language, asr_config
            )

            # Own throwaway executor per call, not shared -- a timed-out call's
            # thread is abandoned (never joined), and reusing one pool would
            # let an abandoned call block every future submission behind it.
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            try:
                future = executor.submit(_run_transcribe, audio_data, transcribe_kwargs)
                text, confidence = future.result(timeout=_TRANSCRIBE_TIMEOUT_S)
            except concurrent.futures.TimeoutError:
                logger.error(
                    f"ASR Worker: transcribe() exceeded {_TRANSCRIBE_TIMEOUT_S}s, dropping this utterance"
                )
                output_queue.put(("", 0.0, {"is_warmup": is_warmup, "timed_out": True}))
                continue
            finally:
                executor.shutdown(wait=False)

            latency_ms = (time.time() - start_time) * 1000
            logger.info(
                f"pipeline_stage | stage=asr | latency_ms={latency_ms:.1f} | warmup={is_warmup}"
            )
            output_queue.put((text, confidence, {"is_warmup": is_warmup}))

        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"ASR Worker: Error during transcription: {e}", exc_info=True)
            output_queue.put(("", 0.0, {"is_warmup": False}))

    logger.info("ASR Worker: Shutting down.")


if __name__ == "__main__":
    # This file isn't meant to be run directly, but if it is, we could add test logic here
    pass
