"""Opt-in diagnostics for one native Charlie voice phrase.

The diagnostic trace is deliberately a sidecar to ``TurnRequest``.  Voice and
ASR code can correlate one captured phrase without changing the existing turn
identity contract or persisting audio/transcripts into Charlie memory stores.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import struct
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional
from uuid import uuid4

import numpy as np

logger = logging.getLogger("charlie.voice.diagnostics")

VOICE_DIAGNOSTIC_STAGES = (
    "voice_capture_onset",
    "voice_capture_endpoint",
    "asr_enqueue",
    "asr_worker_dequeue",
    "asr_start",
    "asr_complete",
    "asr_poller_receive",
    "speech_callback",
    "turn_dispatch",
    "intent_decision",
    "brain_start",
    "first_llm_token",
    "tool_start",
    "tool_complete",
    "tts_enqueue",
    "tts_synthesis_start",
    "tts_synthesis_complete",
    "playback_first_sample",
    "response_text_complete",
    "playback_complete",
)

_DIAGNOSTIC_FLAG = "CHARLIE_VOICE_DIAGNOSTICS"
_WAV_FLAG = "CHARLIE_VOICE_DIAGNOSTIC_WAV"
_DIAGNOSTIC_DIR = "CHARLIE_VOICE_DIAGNOSTIC_DIR"
_MAX_WAV_FILES = "CHARLIE_VOICE_DIAGNOSTIC_WAV_MAX_FILES"
_DEFAULT_MAX_WAV_FILES = 32
_DEFAULT_DIAGNOSTIC_DIR_NAME = "charlie-voice-diagnostics"
_RESOURCE_SAMPLE_INTERVAL_S = 1.0
_VISION_PROCESS_MARKERS = (
    "lm studio",
    "lmstudio",
    "ollama",
    "llama-server",
    "llama server",
    "vllm",
)


def _flag_enabled(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _test_mode() -> bool:
    return os.getenv("CHARLIE_TEST_MODE", "").strip().casefold() == "true"


def _safe_json(value: Any) -> str:
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        encoded = json.dumps(str(value))
    return encoded.replace("|", "/")


def _log_fields(fields: Mapping[str, Any]) -> str:
    return " | ".join(
        f"{key}={value.replace('|', '/') if isinstance(value, str) else _safe_json(value)}"
        for key, value in fields.items()
    )


def _as_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


class VoiceDiagnostics:
    """Owns opt-in voice diagnostics, resource snapshots, and WAV capture."""

    def __init__(
        self,
        *,
        enabled: Optional[bool] = None,
        wav_enabled: Optional[bool] = None,
        directory: Optional[str | Path] = None,
        max_wav_files: Optional[int] = None,
    ) -> None:
        if enabled is None:
            enabled = _flag_enabled(_DIAGNOSTIC_FLAG) and not _test_mode()
        self.enabled = bool(enabled)

        if wav_enabled is None:
            wav_enabled = _flag_enabled(_WAV_FLAG) and not _test_mode()
        self.wav_enabled = bool(wav_enabled)

        configured_directory = directory is not None or bool(os.getenv(_DIAGNOSTIC_DIR))
        requested_directory = directory or os.getenv(_DIAGNOSTIC_DIR)
        if requested_directory:
            resolved_directory = Path(requested_directory).expanduser()
        else:
            resolved_directory = Path(tempfile.gettempdir()) / _DEFAULT_DIAGNOSTIC_DIR_NAME

        # Production capture must not accidentally land in the repository. Tests
        # may inject a temporary directory directly through the constructor.
        try:
            repo_root = Path.cwd().resolve()
            if configured_directory and resolved_directory.resolve().is_relative_to(repo_root):
                logger.warning(
                    "Voice diagnostic directory is inside repository; using %%TEMP%% instead: %s",
                    resolved_directory,
                )
                resolved_directory = Path(tempfile.gettempdir()) / _DEFAULT_DIAGNOSTIC_DIR_NAME
        except (OSError, RuntimeError):
            resolved_directory = Path(tempfile.gettempdir()) / _DEFAULT_DIAGNOSTIC_DIR_NAME
        self.directory = resolved_directory

        if max_wav_files is None:
            try:
                max_wav_files = int(os.getenv(_MAX_WAV_FILES, str(_DEFAULT_MAX_WAV_FILES)))
            except ValueError:
                max_wav_files = _DEFAULT_MAX_WAV_FILES
        self.max_wav_files = max(1, int(max_wav_files))

        self._gpu_lock = threading.Lock()
        self._gpu_cache_timestamp = 0.0
        self._gpu_cache: dict[str, Any] = {"gpu_metrics": "unavailable"}
        self._resource_lock = threading.Lock()
        self._resource_stop = threading.Event()
        self._resource_thread: Optional[threading.Thread] = None
        self._resource_asr_worker_pid: Optional[int] = None
        self._resource_cache: dict[str, Any] = {
            "resource_telemetry": "unavailable",
            "gpu_metrics": "unavailable",
            "asr_worker_pid": None,
        }

    @classmethod
    def from_env(cls) -> "VoiceDiagnostics":
        """Build production diagnostics from explicit environment flags."""

        return cls()

    def new_trace(self, utterance_id: Optional[str] = None) -> "VoiceDiagnosticTrace":
        return VoiceDiagnosticTrace(self, utterance_id=utterance_id)

    def prime_gpu_async(self) -> None:
        """Start bounded background telemetry without blocking voice threads."""

        self.start_resource_sampler()

    def start_resource_sampler(self, *, asr_worker_pid: Optional[int] = None) -> None:
        """Refresh expensive telemetry in one bounded background sampler."""

        if not self.enabled:
            return
        with self._resource_lock:
            if asr_worker_pid is not None:
                self._resource_asr_worker_pid = int(asr_worker_pid)
            if self._resource_thread is not None and self._resource_thread.is_alive():
                return
            self._resource_stop.clear()
            thread = threading.Thread(
                target=self._resource_sampler_loop,
                daemon=True,
                name="VoiceResourceDiagnostics",
            )
            self._resource_thread = thread
        thread.start()

    def stop(self) -> None:
        """Stop the sampler cleanly; diagnostics remain non-fatal."""

        self._resource_stop.set()
        thread = self._resource_thread
        if thread is not None and thread is not threading.current_thread() and thread.is_alive():
            thread.join(timeout=1.0)
        with self._resource_lock:
            if self._resource_thread is thread:
                self._resource_thread = None

    def capture_audio(self, trace: "VoiceDiagnosticTrace", audio: Any, sample_rate: int) -> Optional[str]:
        """Write exact float32 samples to a bounded-retention IEEE-float WAV."""

        if not self.wav_enabled:
            return None
        try:
            samples = np.asarray(audio, dtype=np.float32).reshape(-1)
            samples = np.ascontiguousarray(samples.astype("<f4", copy=False))
            self.directory.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            path = self.directory / f"utterance-{trace.utterance_id}-{timestamp}.wav"
            payload = samples.tobytes(order="C")
            sample_rate = int(sample_rate)
            fmt_payload = struct.pack(
                "<HHIIHH",
                3,  # WAVE_FORMAT_IEEE_FLOAT; preserves the exact float32 buffer.
                1,
                sample_rate,
                sample_rate * 4,
                4,
                32,
            )
            riff_size = 4 + (8 + len(fmt_payload)) + (8 + len(payload))
            with path.open("wb") as wav_file:
                wav_file.write(b"RIFF")
                wav_file.write(struct.pack("<I", riff_size))
                wav_file.write(b"WAVE")
                wav_file.write(b"fmt ")
                wav_file.write(struct.pack("<I", len(fmt_payload)))
                wav_file.write(fmt_payload)
                wav_file.write(b"data")
                wav_file.write(struct.pack("<I", len(payload)))
                wav_file.write(payload)
            self._prune_wav_files()
            logger.info(
                "voice_diagnostic_wav | %s",
                _log_fields(
                    {
                        "utterance_id": trace.utterance_id,
                        "path": str(path),
                        "sample_rate": sample_rate,
                        "sample_count": int(samples.size),
                        "duration_ms": round(samples.size / sample_rate * 1000, 3) if sample_rate else None,
                    }
                ),
            )
            return str(path)
        except Exception as exc:
            # Diagnostics cannot make the voice path fail.
            logger.warning("voice_diagnostic_wav_failed | error=%s", type(exc).__name__)
            return None

    def _prune_wav_files(self) -> None:
        try:
            files = sorted(
                self.directory.glob("utterance-*.wav"),
                key=lambda item: item.stat().st_mtime_ns,
            )
            for path in files[:-self.max_wav_files]:
                try:
                    path.unlink()
                except OSError:
                    logger.debug("Could not prune diagnostic WAV: %s", path, exc_info=True)
        except OSError:
            logger.debug("Could not inspect diagnostic WAV retention directory", exc_info=True)

    def resource_snapshot(self, *, asr_worker_pid: Optional[int] = None) -> dict[str, Any]:
        """Return cached telemetry in realtime; direct sampling is test/compat fallback."""

        if asr_worker_pid is not None:
            with self._resource_lock:
                self._resource_asr_worker_pid = int(asr_worker_pid)
        with self._resource_lock:
            thread = self._resource_thread
            cached = dict(self._resource_cache)
            cached["asr_worker_pid"] = self._resource_asr_worker_pid
        if thread is not None and thread.is_alive():
            return cached
        if self.enabled:
            self.start_resource_sampler(asr_worker_pid=asr_worker_pid)
            with self._resource_lock:
                return dict(self._resource_cache)
        return cached

    def _resource_sampler_loop(self) -> None:
        while not self._resource_stop.is_set():
            self._refresh_resource_snapshot()
            self._resource_stop.wait(_RESOURCE_SAMPLE_INTERVAL_S)

    def _refresh_resource_snapshot(self) -> None:
        with self._resource_lock:
            asr_worker_pid = self._resource_asr_worker_pid
        try:
            snapshot = self._collect_resource_snapshot(asr_worker_pid=asr_worker_pid)
        except Exception as exc:  # pragma: no cover - defensive sampler boundary
            snapshot = {
                "resource_telemetry": "unavailable",
                "asr_worker_pid": asr_worker_pid,
                "resource_telemetry_error": type(exc).__name__,
                "gpu_metrics": "unavailable",
            }
        with self._resource_lock:
            self._resource_cache = dict(snapshot)

    def _collect_resource_snapshot(self, *, asr_worker_pid: Optional[int] = None) -> dict[str, Any]:
        """Collect expensive telemetry from the sampler thread."""

        snapshot: dict[str, Any] = {
            "resource_telemetry": "available",
            "asr_worker_pid": asr_worker_pid,
        }
        try:
            import psutil

            current_pid = os.getpid()
            try:
                process_rss = int(psutil.Process(current_pid).memory_info().rss)
                if asr_worker_pid is not None and int(asr_worker_pid) == current_pid:
                    snapshot["asr_worker_rss_bytes"] = process_rss
                else:
                    snapshot["process_rss_bytes"] = process_rss
                    snapshot["charlie_process_rss_bytes"] = process_rss
            except (OSError, psutil.Error) as exc:
                if asr_worker_pid is not None and int(asr_worker_pid) == current_pid:
                    snapshot["asr_worker_rss_bytes"] = None
                else:
                    snapshot["charlie_process_rss_bytes"] = None
                snapshot["resource_telemetry_error"] = type(exc).__name__

            try:
                virtual_memory = psutil.virtual_memory()
                snapshot.update(
                    {
                        "system_ram_used_bytes": int(virtual_memory.used),
                        "system_ram_available_bytes": int(virtual_memory.available),
                        "system_ram_percent": float(virtual_memory.percent),
                    }
                )
            except (OSError, psutil.Error) as exc:
                snapshot["resource_telemetry_error"] = type(exc).__name__

            try:
                snapshot["cpu_utilization_percent"] = float(psutil.cpu_percent(interval=None))
            except (OSError, psutil.Error) as exc:
                snapshot["resource_telemetry_error"] = type(exc).__name__

            if asr_worker_pid is not None and int(asr_worker_pid) != current_pid:
                try:
                    snapshot["asr_worker_rss_bytes"] = int(psutil.Process(asr_worker_pid).memory_info().rss)
                except (OSError, psutil.Error, ValueError) as exc:
                    snapshot["asr_worker_rss_bytes"] = None
                    snapshot["resource_telemetry_error"] = type(exc).__name__

            try:
                visible_processes = []
                for process in psutil.process_iter(["pid", "name"]):
                    name = str(process.info.get("name") or "")
                    if any(marker in name.casefold() for marker in _VISION_PROCESS_MARKERS):
                        try:
                            rss = int(process.memory_info().rss)
                        except (OSError, psutil.Error):
                            rss = None
                        visible_processes.append({"pid": int(process.info["pid"]), "name": name, "rss_bytes": rss})
                snapshot["vision_process_visibility"] = "available"
                snapshot["vision_processes"] = visible_processes
            except (OSError, psutil.Error) as exc:
                snapshot["vision_process_visibility"] = "unavailable"
                snapshot["vision_processes"] = None
                snapshot["resource_telemetry_error"] = type(exc).__name__
        except Exception as exc:
            snapshot["resource_telemetry"] = "unavailable"
            snapshot["resource_telemetry_error"] = type(exc).__name__

        try:
            snapshot.update(self._read_gpu_snapshot())
        except Exception as exc:
            snapshot["gpu_metrics"] = "unavailable"
            snapshot["resource_telemetry_error"] = type(exc).__name__
        if snapshot.get("resource_telemetry") == "available" and "resource_telemetry_error" in snapshot:
            snapshot["resource_telemetry"] = "degraded"
        return snapshot

    def _gpu_snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        if now - self._gpu_cache_timestamp < 1.0:
            return dict(self._gpu_cache)
        if not self._gpu_lock.acquire(blocking=False):
            return dict(self._gpu_cache)
        try:
            now = time.monotonic()
            if now - self._gpu_cache_timestamp < 1.0:
                return dict(self._gpu_cache)
            self._gpu_cache_timestamp = now
            try:
                self._gpu_cache = self._read_gpu_snapshot()
            except Exception:
                self._gpu_cache = {"gpu_metrics": "unavailable"}
            return dict(self._gpu_cache)
        finally:
            self._gpu_lock.release()

    @staticmethod
    def _read_gpu_snapshot() -> dict[str, Any]:
        executable = shutil.which("nvidia-smi")
        if not executable:
            return {"gpu_metrics": "unavailable"}
        try:
            result = subprocess.run(
                [
                    executable,
                    "--query-gpu=utilization.gpu,memory.total,memory.used,memory.free",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
            row = next((line.strip() for line in result.stdout.splitlines() if line.strip()), "")
            values = [float(value.strip()) for value in row.split(",")]
            if result.returncode != 0 or len(values) != 4:
                return {"gpu_metrics": "unavailable"}
            snapshot: dict[str, Any] = {
                "gpu_metrics": "available",
                "gpu_utilization_percent": values[0],
                "gpu_total_vram_mb": values[1],
                "gpu_used_vram_mb": values[2],
                "gpu_free_vram_mb": values[3],
            }
            try:
                processes = subprocess.run(
                    [
                        executable,
                        "--query-compute-apps=pid,used_memory",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                    check=False,
                )
                compute_processes = []
                for line in processes.stdout.splitlines():
                    parts = [part.strip() for part in line.split(",")]
                    if len(parts) == 2:
                        compute_processes.append({"pid": parts[0], "used_vram_mb": parts[1]})
                snapshot["gpu_compute_processes"] = compute_processes
            except (OSError, subprocess.SubprocessError):
                snapshot["gpu_compute_processes"] = None
            return snapshot
        except (OSError, subprocess.SubprocessError, ValueError):
            return {"gpu_metrics": "unavailable"}


class VoiceDiagnosticTrace:
    """Thread-safe sidecar trace for one captured utterance."""

    def __init__(self, diagnostics: VoiceDiagnostics, *, utterance_id: Optional[str] = None) -> None:
        self.diagnostics = diagnostics
        self.utterance_id = utterance_id or uuid4().hex
        self._lock = threading.Lock()
        self._last_timestamp: Optional[float] = None
        self._onset_timestamp: Optional[float] = None
        self._turn_id: Optional[str] = None
        self._session_id: Optional[str] = None
        self._events: list[dict[str, Any]] = []
        self._once_stages: set[str] = set()

    def bind(self, *, turn_id: Optional[str] = None, session_id: Optional[str] = None) -> None:
        with self._lock:
            if turn_id:
                self._turn_id = turn_id
            if session_id:
                self._session_id = session_id

    def onset_timestamp(self) -> Optional[float]:
        with self._lock:
            return self._onset_timestamp

    def mark_once(
        self,
        stage: str,
        *,
        fields: Optional[Mapping[str, Any]] = None,
        timestamp: Optional[float] = None,
        include_resource: bool = False,
        asr_worker_pid: Optional[int] = None,
    ) -> Optional[dict[str, Any]]:
        with self._lock:
            if stage in self._once_stages:
                return None
            self._once_stages.add(stage)
        return self.mark(
            stage,
            fields=fields,
            timestamp=timestamp,
            include_resource=include_resource,
            asr_worker_pid=asr_worker_pid,
        )

    def mark(
        self,
        stage: str,
        *,
        fields: Optional[Mapping[str, Any]] = None,
        timestamp: Optional[float] = None,
        turn_id: Optional[str] = None,
        session_id: Optional[str] = None,
        include_resource: bool = False,
        asr_worker_pid: Optional[int] = None,
    ) -> dict[str, Any]:
        timestamp = time.monotonic() if timestamp is None else float(timestamp)
        if include_resource and self.diagnostics.enabled:
            merged_fields = dict(fields or {})
            merged_fields["resource_snapshot"] = self.diagnostics.resource_snapshot(
                asr_worker_pid=asr_worker_pid,
            )
            fields = merged_fields

        with self._lock:
            if turn_id:
                self._turn_id = turn_id
            if session_id:
                self._session_id = session_id
            previous_timestamp = self._last_timestamp
            if stage == "voice_capture_onset" and self._onset_timestamp is None:
                self._onset_timestamp = timestamp
            onset_timestamp = self._onset_timestamp
            event = {
                "utterance_id": self.utterance_id,
                "turn_id": self._turn_id,
                "session_id": self._session_id,
                "stage": stage,
                "monotonic_timestamp": timestamp,
                "delta_from_previous_stage_ms": (
                    (timestamp - previous_timestamp) * 1000 if previous_timestamp is not None else None
                ),
                "delta_from_onset_ms": (
                    (timestamp - onset_timestamp) * 1000 if onset_timestamp is not None else None
                ),
                "fields": dict(fields or {}),
            }
            self._last_timestamp = timestamp
            self._events.append(event)

        if self.diagnostics.enabled:
            logger.info("voice_diagnostic_stage | %s", _log_fields(event))
        return event

    def import_stage_events(self, events: Any) -> None:
        """Import worker-captured timestamps without inventing new timings."""

        if not isinstance(events, (list, tuple)):
            return
        for event in events:
            if not isinstance(event, Mapping):
                continue
            stage = event.get("stage")
            timestamp = _as_float(event.get("monotonic_timestamp"))
            if not isinstance(stage, str) or timestamp is None:
                continue
            self.mark(
                stage,
                timestamp=timestamp,
                turn_id=event.get("turn_id"),
                session_id=event.get("session_id"),
                fields=event.get("fields") if isinstance(event.get("fields"), Mapping) else {},
            )

    def events(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(event, fields=dict(event.get("fields") or {})) for event in self._events]


__all__ = ["VOICE_DIAGNOSTIC_STAGES", "VoiceDiagnosticTrace", "VoiceDiagnostics"]
