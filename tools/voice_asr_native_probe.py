"""Compare one real ASR worker before and after one real local vision request.

This is a native diagnostic probe, not a second ASR implementation. It reuses
``charlie.asr_worker.asr_worker_process`` and the existing WAV reader, sends
the same mono WAV through one worker three times before vision and once after,
then prints every worker boundary event as JSON. The vision step uses Charlie's
existing screenshot tool and ``Brain`` vision stream path.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import multiprocessing as mp
import queue
import sys
import time
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

# Allow ``python tools/voice_asr_native_probe.py`` from the repository root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from charlie.asr_worker import asr_worker_process
from charlie.config import config as runtime_config
from charlie.voice_diagnostics import VoiceDiagnostics
from tools.voice_asr_benchmark import _read_wav

logger = logging.getLogger("charlie.voice.native_probe")

_ASR_RESULT_TIMEOUT_S = 30.0
_VISION_TIMEOUT_S = 9.0


def _emit(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False, sort_keys=True, default=str), flush=True)


def _asr_config() -> dict[str, Any]:
    """Match VoiceEngine's production worker configuration wiring."""

    return {
        "beam_size": runtime_config.asr_beam_size,
        "best_of": runtime_config.asr_best_of,
        "repetition_penalty": runtime_config.asr_repetition_penalty,
        "vad_threshold": runtime_config.vad_threshold,
        "min_speech_duration_ms": runtime_config.vad_min_speech_duration_ms,
        "max_speech_duration_s": runtime_config.vad_max_speech_duration_s,
        "min_silence_duration_ms": runtime_config.vad_min_silence_duration_ms,
        "speech_pad_ms": runtime_config.vad_speech_pad_ms,
    }


class NativeAsrWorkerProbe:
    """Own one production ASR worker and preserve its stage/result order."""

    def __init__(self, diagnostics: VoiceDiagnostics) -> None:
        context = mp.get_context("spawn")
        self.input_queue = context.Queue(maxsize=8)
        self.output_queue = context.Queue(maxsize=8)
        self.process = context.Process(
            target=asr_worker_process,
            args=(
                self.input_queue,
                self.output_queue,
                runtime_config.whisper_model,
                runtime_config.gpu_device,
                runtime_config.default_language,
                _asr_config(),
            ),
            daemon=True,
        )
        self.process.start()
        diagnostics.start_resource_sampler(asr_worker_pid=self.process.pid)

    def _get(self, deadline: float) -> object:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("ASR worker probe deadline exceeded")
        try:
            return self.output_queue.get(timeout=remaining)
        except queue.Empty as exc:
            raise TimeoutError("ASR worker probe deadline exceeded") from exc

    def wait_ready(self) -> None:
        deadline = time.monotonic() + _ASR_RESULT_TIMEOUT_S
        while True:
            message = self._get(deadline)
            if isinstance(message, dict) and message.get("type") == "ready":
                _emit("asr_ready", metrics=message.get("metrics"), worker_pid=self.process.pid)
                return
            if isinstance(message, dict) and message.get("type") == "failed":
                raise RuntimeError(message.get("error") or "ASR worker failed during startup")

    def transcribe(self, audio: Any, sample_rate: int, phase: str, ordinal: int) -> dict[str, Any]:
        utterance_id = f"{phase}-{ordinal}-{uuid4().hex[:8]}"
        self.input_queue.put(
            (
                audio.tobytes(),
                sample_rate,
                {
                    "utterance_id": utterance_id,
                    "diagnostic_enabled": True,
                    "voice_capture_onset_monotonic": time.monotonic(),
                    "asr_enqueue_monotonic": time.monotonic(),
                    "capture": {
                        "capture_mode": "fixed_wav_probe",
                        "sample_count": int(len(audio)),
                        "sample_rate": sample_rate,
                    },
                },
            )
        )
        deadline = time.monotonic() + _ASR_RESULT_TIMEOUT_S
        stages: list[dict[str, Any]] = []
        result: Optional[tuple[Any, ...]] = None
        terminal_stage = False
        while result is None or not terminal_stage:
            message = self._get(deadline)
            if isinstance(message, dict) and message.get("type") == "asr_worker_stage":
                if message.get("utterance_id") != utterance_id:
                    _emit("asr_unexpected_stage", expected=utterance_id, message=message)
                    continue
                stages.append(message)
                _emit("asr_worker_stage", phase=phase, ordinal=ordinal, **message)
                terminal_stage = message.get("stage") in {
                    "asr_worker_result_enqueued",
                    "asr_worker_exception",
                }
                continue
            if isinstance(message, tuple) and len(message) >= 3:
                flags = message[2] if isinstance(message[2], dict) else {}
                if flags.get("utterance_id") != utterance_id:
                    _emit("asr_unexpected_result", expected=utterance_id, message=message)
                    continue
                result = message
                _emit(
                    "asr_result",
                    phase=phase,
                    ordinal=ordinal,
                    utterance_id=utterance_id,
                    text=message[0],
                    flags=flags,
                )

        return {
            "phase": phase,
            "ordinal": ordinal,
            "utterance_id": utterance_id,
            "stages": [message.get("stage") for message in stages],
            "result": result[0] if result is not None else None,
            "flags": result[2] if result is not None and isinstance(result[2], dict) else {},
        }

    def close(self) -> None:
        try:
            self.input_queue.put(None, timeout=1.0)
        except Exception:
            pass
        self.process.join(timeout=2.0)
        if self.process.is_alive():
            _emit("asr_worker_probe_cleanup", action="terminate_owned_worker", worker_pid=self.process.pid)
            self.process.terminate()
            self.process.join(timeout=2.0)
        _emit("asr_worker_probe_cleanup", action="worker_stopped", worker_pid=self.process.pid)


async def _run_real_vision_request(diagnostics: VoiceDiagnostics) -> dict[str, Any]:
    """Run Charlie's real screenshot -> local vision stream path once."""

    from charlie.core import Brain
    from charlie.streaming import FollowupStreamState
    from charlie.tools import desktop_screenshot, pop_pending_vision_image

    brain = Brain(runtime_config, register_panic_hotkey=False)
    trace = diagnostics.new_trace("native-vision-probe")
    generator = None
    status = "completed"
    chunks: list[str] = []
    screenshot_text = ""
    try:
        screenshot_text = await asyncio.to_thread(desktop_screenshot)
        image_url = pop_pending_vision_image()
        if not image_url:
            raise RuntimeError(
                "desktop_screenshot did not produce a vision image; enable desktop control and vision dependencies"
            )
        if brain._vision_client is None:
            raise RuntimeError("Charlie local vision client is not configured")
        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe exactly what is visible on this screen."},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ],
            "stream": True,
            "temperature": 0.0,
        }
        state = FollowupStreamState()
        generator = brain._stream_followup_once(
            brain._vision_client,
            brain._vision_model,
            payload,
            brain._chat_generation,
            state,
            trace,
        )
        try:
            async with asyncio.timeout(_VISION_TIMEOUT_S):
                async for chunk in generator:
                    chunks.append(chunk)
        except asyncio.TimeoutError:
            status = "timeout"
            brain.cancel_chat()
        except asyncio.CancelledError:
            status = "cancelled"
            raise
    except Exception as exc:
        status = "error"
        _emit("vision_probe_error", error_type=type(exc).__name__, error=str(exc))
    finally:
        if generator is not None:
            try:
                await generator.aclose()
            except Exception as exc:
                _emit("vision_probe_cleanup_error", error_type=type(exc).__name__, error=str(exc))
        await brain.close()

    events = trace.events()
    for event in events:
        _emit("vision_stage", **event)
    return {
        "status": status,
        "screenshot_text": screenshot_text,
        "answer": "".join(chunks),
        "stages": [event["stage"] for event in events],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wav", required=True, type=Path, help="Existing mono 16 kHz WAV to replay")
    parser.add_argument(
        "--skip-vision",
        action="store_true",
        help="Control variant: replay the same WAV without making a vision request",
    )
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] [%(levelname)s] %(message)s")
    args = build_parser().parse_args()
    if not args.wav.is_file():
        raise SystemExit(f"WAV does not exist: {args.wav}")

    try:
        audio, sample_rate = _read_wav(args.wav)
    except Exception as exc:
        raise SystemExit(f"could not read {args.wav}: {type(exc).__name__}: {exc}") from exc
    if sample_rate != 16000:
        raise SystemExit(f"fixed-WAV probe requires 16000 Hz audio; got {sample_rate} Hz")

    diagnostics = VoiceDiagnostics(enabled=True, wav_enabled=False)
    worker = NativeAsrWorkerProbe(diagnostics)
    try:
        worker.wait_ready()
        for ordinal in range(1, 4):
            worker.transcribe(audio, sample_rate, "pre-vision", ordinal)
        if args.skip_vision:
            _emit("vision_probe_skipped", worker_pid=worker.process.pid)
            worker.transcribe(audio, sample_rate, "post-no-vision", 1)
        else:
            _emit("vision_probe_begin", worker_pid=worker.process.pid)
            vision_result = asyncio.run(_run_real_vision_request(diagnostics))
            _emit("vision_probe_end", **vision_result)
            worker.transcribe(audio, sample_rate, "post-vision", 1)
        return 0
    except Exception as exc:
        _emit("native_probe_failed", error_type=type(exc).__name__, error=str(exc))
        return 1
    finally:
        worker.close()
        diagnostics.stop()


if __name__ == "__main__":
    raise SystemExit(main())
