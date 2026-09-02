"""Replay existing WAV files through controlled Faster-Whisper variants.

This tool is diagnostic-only. It never records hardware, writes Charlie memory,
or reports WER without a caller-supplied reference transcript.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

# Allow `python tools/voice_asr_benchmark.py ...` from any working directory.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from charlie.asr_worker import _filter_hallucinated_segments, _quality_metadata
from charlie.config import config as runtime_config

VARIANT_SETTINGS: dict[str, dict[str, Any]] = {
    # Empty override means: use the effective Config/CLI invocation exactly.
    "current": {},
    "beam6_best6": {
        "beam_size": 6,
        "best_of": 6,
        "condition_on_previous_text": True,
        "vad_filter": True,
    },
    "beam1_best1": {
        "beam_size": 1,
        "best_of": 1,
        "condition_on_previous_text": True,
        "vad_filter": True,
    },
    "vad_disabled": {
        "beam_size": 6,
        "best_of": 6,
        "condition_on_previous_text": True,
        "vad_filter": False,
    },
    "condition_off": {
        "beam_size": 6,
        "best_of": 6,
        "condition_on_previous_text": False,
        "vad_filter": True,
    },
    "beam1_vad_disabled": {
        "beam_size": 1,
        "best_of": 1,
        "condition_on_previous_text": True,
        "vad_filter": False,
    },
    "beam1_condition_off": {
        "beam_size": 1,
        "best_of": 1,
        "condition_on_previous_text": False,
        "vad_filter": True,
    },
    "vad_disabled_condition_off": {
        "beam_size": 6,
        "best_of": 6,
        "condition_on_previous_text": False,
        "vad_filter": False,
    },
    "beam1_vad_disabled_condition_off": {
        "beam_size": 1,
        "best_of": 1,
        "condition_on_previous_text": False,
        "vad_filter": False,
    },
}


def _resolve_runtime_defaults(args: argparse.Namespace) -> argparse.Namespace:
    """Resolve omitted benchmark values from Charlie's effective Config."""

    args.model = args.model or runtime_config.whisper_model
    args.device = args.device or runtime_config.gpu_device
    args.language = args.language or runtime_config.default_language
    args.beam_size = args.beam_size if args.beam_size is not None else runtime_config.asr_beam_size
    args.best_of = args.best_of if args.best_of is not None else runtime_config.asr_best_of
    args.condition_on_previous_text = (
        args.condition_on_previous_text if args.condition_on_previous_text is not None else True
    )
    args.vad_filter = args.vad_filter if args.vad_filter is not None else True
    args.repetition_penalty = (
        args.repetition_penalty
        if args.repetition_penalty is not None
        else runtime_config.asr_repetition_penalty
    )
    args.vad_threshold = args.vad_threshold if args.vad_threshold is not None else runtime_config.vad_threshold
    args.min_speech_duration_ms = (
        args.min_speech_duration_ms
        if args.min_speech_duration_ms is not None
        else runtime_config.vad_min_speech_duration_ms
    )
    args.max_speech_duration_s = (
        args.max_speech_duration_s
        if args.max_speech_duration_s is not None
        else runtime_config.vad_max_speech_duration_s
    )
    args.min_silence_duration_ms = (
        args.min_silence_duration_ms
        if args.min_silence_duration_ms is not None
        else runtime_config.vad_min_silence_duration_ms
    )
    args.speech_pad_ms = (
        args.speech_pad_ms
        if args.speech_pad_ms is not None
        else runtime_config.vad_speech_pad_ms
    )
    args.compute_type = args.compute_type or ("float16" if args.device == "cuda" else "int8")
    return args


def _variant_kwargs(settings: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "language": args.language,
        "word_timestamps": False,
        "beam_size": args.beam_size,
        "best_of": args.best_of,
        "condition_on_previous_text": args.condition_on_previous_text,
        "vad_filter": args.vad_filter,
        "repetition_penalty": args.repetition_penalty,
        "no_repeat_ngram_size": 3,
        "hotwords": (
            "Charlie open close start stop search weather time date "
            "notepad chrome calculator python code youtube"
        ),
    }
    for key, value in settings.items():
        kwargs[key] = value
    if kwargs["vad_filter"]:
        kwargs["vad_parameters"] = {
            "threshold": args.vad_threshold,
            "min_speech_duration_ms": args.min_speech_duration_ms,
            "max_speech_duration_s": args.max_speech_duration_s,
            "min_silence_duration_ms": args.min_silence_duration_ms,
            "speech_pad_ms": args.speech_pad_ms,
        }
    return kwargs


def _read_wav(path: Path):
    try:
        import soundfile as sf
    except ImportError as exc:  # pragma: no cover - depends on local optional install
        raise RuntimeError("soundfile is required to replay diagnostic WAV files") from exc
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=False)
    if getattr(audio, "ndim", 1) != 1:
        raise ValueError(f"expected mono WAV, got shape {getattr(audio, 'shape', None)}")
    return audio, int(sample_rate)


def _run_variant(model: Any, audio: Any, sample_rate: int, variant: str, args: argparse.Namespace) -> dict[str, Any]:
    settings = VARIANT_SETTINGS[variant]
    kwargs = _variant_kwargs(settings, args)
    started = time.monotonic()
    segments, info = model.transcribe(audio, **kwargs)
    accepted_segments = _filter_hallucinated_segments(list(segments))
    text = "".join(segment.text for segment in accepted_segments).strip()
    latency_ms = (time.monotonic() - started) * 1000
    audio_duration_ms = len(audio) / sample_rate * 1000 if sample_rate else 0.0
    quality = _quality_metadata(
        accepted_segments,
        info,
        text,
        asr_latency_ms=latency_ms,
        audio_duration_ms=audio_duration_ms,
    )
    return {
        "variant": variant,
        "effective_parameters": {
            "model": args.model,
            "device": args.device,
            "compute_type": args.compute_type,
            "language": kwargs["language"],
            "beam_size": kwargs["beam_size"],
            "best_of": kwargs["best_of"],
            "condition_on_previous_text": kwargs["condition_on_previous_text"],
            "vad_filter": kwargs["vad_filter"],
            "repetition_penalty": kwargs["repetition_penalty"],
            "vad_parameters": kwargs.get("vad_parameters"),
        },
        "transcript": text,
        "asr_latency_ms": round(latency_ms, 3),
        "audio_duration_ms": round(audio_duration_ms, 3),
        "real_time_factor": round(latency_ms / audio_duration_ms, 5) if audio_duration_ms else None,
        "quality": quality,
        "wer": None,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wav",
        action="append",
        required=True,
        type=Path,
        help="Existing mono WAV to replay; repeatable",
    )
    parser.add_argument("--model", default=None, help="Override Config.whisper_model")
    parser.add_argument("--device", default=None, help="Override Config.gpu_device")
    parser.add_argument("--compute-type", default=None)
    parser.add_argument("--language", default=None, help="Override Config.default_language")
    parser.add_argument("--beam-size", type=int, default=None, help="Override Config.asr_beam_size")
    parser.add_argument("--best-of", type=int, default=None, help="Override Config.asr_best_of")
    parser.add_argument(
        "--condition-on-previous-text",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override runtime condition_on_previous_text (default: true)",
    )
    parser.add_argument(
        "--vad-filter",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override runtime vad_filter (default: true)",
    )
    parser.add_argument("--variants", default=",".join(VARIANT_SETTINGS), help="Comma-separated variant names")
    parser.add_argument("--allow-download", action="store_true", help="Allow Faster-Whisper model download")
    parser.add_argument("--vad-threshold", type=float, default=None)
    parser.add_argument("--min-speech-duration-ms", type=int, default=None)
    parser.add_argument("--max-speech-duration-s", type=int, default=None)
    parser.add_argument("--min-silence-duration-ms", type=int, default=None)
    parser.add_argument("--speech-pad-ms", type=int, default=None)
    parser.add_argument("--repetition-penalty", type=float, default=None)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args = _resolve_runtime_defaults(args)
    variants = [item.strip() for item in args.variants.split(",") if item.strip()]
    unknown = [item for item in variants if item not in VARIANT_SETTINGS]
    if unknown:
        parser.error(f"unknown variant(s): {', '.join(unknown)}")

    for path in args.wav:
        if not path.is_file():
            parser.error(f"WAV does not exist: {path}")

    try:
        from faster_whisper import WhisperModel

        model_kwargs = {"device": args.device, "compute_type": args.compute_type}
        if not args.allow_download:
            model_kwargs["local_files_only"] = True
        model = WhisperModel(args.model, **model_kwargs)
    except Exception as exc:
        parser.error(f"could not load local Faster-Whisper model: {type(exc).__name__}: {exc}")

    for path in args.wav:
        try:
            audio, sample_rate = _read_wav(path)
        except Exception as exc:
            parser.error(f"could not read {path}: {type(exc).__name__}: {exc}")
        for variant in variants:
            result = _run_variant(model, audio, sample_rate, variant, args)
            print(
                json.dumps(
                    {
                        "wav": str(path),
                        "sample_rate": sample_rate,
                        "sample_count": len(audio),
                        **result,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
