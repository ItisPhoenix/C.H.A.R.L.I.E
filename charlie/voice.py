"""Charlie voice engine -- VAD, ASR, TTS (Kokoro), audio I/O.

All text arriving at speak() passes through _humanize_text() before
phonemization. This is the single control point for prosody and pacing.
"""

import asyncio
import inspect
import logging
import multiprocessing as mp
import os
import queue
import re
import threading
import time
import urllib.request
from collections import deque
from typing import Callable, Optional

import numpy as np
import sounddevice as sd
from kokoro_onnx import Kokoro

from charlie.asr_worker import asr_worker_process
from charlie.core import strip_internal_reasoning
from charlie.events import EventMeta, EventSource
from charlie.voice_diagnostics import VoiceDiagnostics, VoiceDiagnosticTrace
from charlie.wake_word import WakeWordDetector

logger = logging.getLogger("charlie.voice")

# --- TTS text humanization constants ---
_MIN_TEXT_LEN = 3
_ECHO_WINDOW_SEC = 2.0
# Sentinel pushed to playback_queue after every chunk of a single TTS run has
# been enqueued. Lets the playback worker distinguish "utterance fully spoken"
# from momentary inter-chunk queue gaps.
_TTS_RUN_END = object()
# Tags a chime item in playback_queue so it skips TTS state bookkeeping.
_CHIME_ITEM = object()
_LONG_SENTENCE_CHARS = 250
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?,;])\s+")
_ASR_WORKER_STAGE_MESSAGE = "asr_worker_stage"
_ASR_WORKER_STALL_THRESHOLD_S = 9.0
_ASR_WORKER_WATCHDOG_INTERVAL_S = 0.5


# Ellipsis patterns
_RE_ELLIPSIS = re.compile(r"\.{4,}")
_RE_DOTS = re.compile(r"(?<!\.)\.(?!\.)(\s*\.)+")  # loose dots -> single period

# Repeated punctuation (keep max 1)
_RE_REPEATED_EXCL = re.compile(r"!{2,}")
_RE_REPEATED_QUES = re.compile(r"\?{2,}")

# Dashes as clause breaks (em dash, en dash, double hyphen)
_RE_EM_DASH = re.compile(r"\s*\u2014\s*")
_RE_EN_DASH = re.compile(r"\s*\u2013\s*")
_RE_DOUBLE_HYPHEN = re.compile(r"\s*--\s*")

# LLM formatting artifacts
_RE_LIST_BULLET = re.compile(r"^[\s]*[-*+]\s+", re.MULTILINE)  # "- item" or "* item"
_RE_NUMBERED_LIST = re.compile(r"^[\s]*\d+[.)]\s+", re.MULTILINE)  # "1. item"
_RE_HASH_HEADER = re.compile(r"^#{1,6}\s+", re.MULTILINE)  # "## Header"
_RE_BOLD_ITALIC = re.compile(r"[*_]{1,3}(\S.*?\S)[*_]{1,3}")  # *bold* or _italic_
_RE_INLINE_CODE = re.compile(r"`([^`]+)`")
_RE_BACKTICK_WRAP = re.compile(r"^`|`$")
_RE_TRAILING_PUNCT_NO_SPACE = re.compile(r"([.!?])([A-Z])")

# Wrapper quotes from LLM output: "Hello world" -> Hello world
_RE_WRAPPER_QUOTES = re.compile(r'^\s*["\u201c\u201d]\s*(.+?)\s*["\u201c\u201d]\s*$')

# Parenthetical: strip short ones entirely, keep content for long ones
_RE_PAREN_SHORT = re.compile(r"\([^)]{1,40}\)")  # short aside -> remove
_RE_PAREN_LONG = re.compile(r"\(([^)]{41,})\)")  # long aside -> keep content

# Contraction fixes for TTS naturalness
_CONTRACTIONS = {
    "i am": "I'm",
    "you are": "you're",
    "we are": "we're",
    "they are": "they're",
    "it is": "it's",
    "that is": "that's",
    "there is": "there's",
    "there are": "there's",
    "what is": "what's",
    "what are": "what's",
    "who is": "who's",
    "who are": "who's",
    "cannot": "can't",
    "do not": "don't",
    "does not": "doesn't",
    "did not": "didn't",
    "will not": "won't",
    "would not": "wouldn't",
    "could not": "couldn't",
    "should not": "shouldn't",
    "is not": "isn't",
    "are not": "aren't",
    "was not": "wasn't",
    "were not": "weren't",
    "have not": "haven't",
    "has not": "hasn't",
    "had not": "hadn't",
}


class VoiceEngine:
    def __init__(
        self,
        config,
        on_speech: Callable[..., None],
        on_tts_start: Optional[Callable[[], None]] = None,
        on_tts_stop: Optional[Callable[[], None]] = None,
        on_speech_onset: Optional[Callable[..., None]] = None,
    ):
        self.config = config
        self.on_speech = on_speech
        self._speech_callback_mode = self._resolve_speech_callback_mode(on_speech)
        self._on_tts_start = on_tts_start
        self._on_tts_stop = on_tts_stop
        self._on_speech_onset = on_speech_onset
        self.is_speaking = threading.Event()
        self.tts_active = threading.Event()
        self.stop_event = threading.Event()
        self.stop_tts_event = threading.Event()
        self._stopped = False
        self.tts_queue: queue.Queue = queue.Queue()
        self.playback_queue: queue.Queue = queue.Queue()
        self.tts_lock = threading.Lock()
        # Set for real in _run(); stop() checks this before start() has run
        # (or if opening the audio device failed), so it must exist here too.
        self.audio_stream = None
        self.input_thread = None
        self.tts_worker = None
        self.playback_worker = None
        self.asr_poller_thread = None
        self._readiness_event = threading.Event()
        self._readiness_lock = threading.Lock()
        self._readiness_status = "starting"
        self._readiness_error: Optional[str] = None
        self._audio_device_info: dict = {}
        self._last_speech_time = 0.0
        self._last_speech_text = ""
        self._last_speech_end = 0.0
        # Union of words spoken across every speak() chunk of the current
        # reply, not just the most recent chunk -- a long reply spans many
        # speak() calls (one per sentence flush), and the mic can pick up
        # any part of it, not only the last one.
        self._recent_spoken_words: set = set()
        self.speech_echo_window = _ECHO_WINDOW_SEC
        self._widget_callback = None
        self._event_emit_lock = threading.Lock()
        self._event_emit_futures: set = set()

        # Speaker output state (driven by the dashboard audio controls).
        # `muted` silences TTS playback; `volume` is a 0.0-1.0 linear gain
        # applied to the audio samples before they reach the output device.
        self.muted: bool = False
        self.volume: float = 1.0

        # Microphone input state. `mic_muted` drops captured frames before
        # they reach ASR so the assistant stops listening without killing the
        # audio device. Distinct from `muted`, which only affects speakers.
        self.mic_muted: bool = False
        self._ptt_lock = threading.Lock()
        self._ptt_active = False
        self._ptt_stop_requested = False
        self._ptt_chunks: list[np.ndarray] = []
        self._ptt_trace: Optional[VoiceDiagnosticTrace] = None

        # Voice diagnostics are a sidecar. They never write to session/memory
        # stores and remain inactive unless explicitly enabled at runtime.
        self.voice_diagnostics = VoiceDiagnostics.from_env()
        self._utterance_traces: dict[str, VoiceDiagnosticTrace] = {}
        self._diagnostic_context_lock = threading.Lock()
        self._diagnostic_context: Optional[VoiceDiagnosticTrace] = None

        # ASR state
        self.asr_input_queue: mp.Queue = mp.Queue(maxsize=8)
        self.asr_output_queue: mp.Queue = mp.Queue(maxsize=8)
        self.asr_process = None
        self._asr_readiness_lock = threading.Lock()
        self._asr_readiness_status = "starting"
        self._asr_readiness_error: Optional[str] = None
        self.asr_startup_metrics: dict = {}
        self._asr_worker_state_lock = threading.Lock()
        self._asr_worker_inflight: Optional[dict] = None
        self._asr_worker_latest_dequeued_utterance_id: Optional[str] = None
        self._asr_worker_last_output_activity_monotonic: Optional[float] = None
        self._asr_worker_last_watchdog_monotonic = 0.0
        self._asr_worker_stall_warning_key = None

        # Load Kokoro TTS
        self._ensure_models()
        self.kokoro = Kokoro(
            os.path.join(config.kokoro_model_dir, "kokoro-v1.0.onnx"),
            os.path.join(config.kokoro_model_dir, "voices-v1.0.bin"),
        )
        self.barge_in_enabled: bool = bool(getattr(config, "enable_barge_in", True))

        # Wake word state
        self._wake_word_detector: Optional[WakeWordDetector] = None
        self._wake_word_active: bool = False  # True = in active session after wake word
        self._last_activity_time: float = 0.0
        self._on_wake_word: Optional[Callable[[], None]] = None

    @staticmethod
    def _resolve_speech_callback_mode(callback: Callable[..., None]) -> str:
        """Select compatibility-preserving metadata delivery for on_speech."""

        try:
            signature = inspect.signature(callback)
        except (TypeError, ValueError):
            return "text"
        try:
            signature.bind("text", {})
            return "positional"
        except TypeError:
            try:
                signature.bind("text", diagnostic_metadata={})
                return "keyword"
            except TypeError:
                return "text"

    def set_diagnostic_context(self, trace: Optional[VoiceDiagnosticTrace]) -> None:
        """Associate future TTS enqueues with the currently active voice turn."""

        with self._diagnostic_context_lock:
            self._diagnostic_context = trace

    def _current_diagnostic_context(self) -> Optional[VoiceDiagnosticTrace]:
        with self._diagnostic_context_lock:
            return self._diagnostic_context

    def set_widget_callback(self, cb: Callable[[str], None]) -> None:
        """Register callback for mode changes (listening/speaking/idle)."""
        self._widget_callback = cb

    def set_wake_word_callback(self, cb: Callable[[], None]) -> None:
        """Register callback for wake-word detection events."""
        self._on_wake_word = cb

    def set_event_bus(self, bus: object) -> None:
        """Hand the voice engine a reference to the event bus so it can
        publish real-time audio levels from the playback/mic threads. Also
        captures the running event loop so audio threads can schedule emits."""
        self._event_bus = bus
        try:
            self._event_loop = asyncio.get_event_loop()
        except RuntimeError:
            self._event_loop = None

    @property
    def is_ready(self) -> bool:
        with self._readiness_lock:
            return self._readiness_status == "ready"

    def readiness_snapshot(self) -> dict:
        with self._readiness_lock:
            return {
                **self._audio_device_info,
                "status": self._readiness_status,
                "stream_open": self._readiness_status == "ready",
                "error": self._readiness_error,
            }

    def readiness_detail(self) -> str:
        snapshot = self.readiness_snapshot()
        if snapshot["status"] == "ready":
            return "Microphone ready"
        error = snapshot.get("error") or "device initialization failed"
        return f"Microphone unavailable: {error}"

    @property
    def asr_readiness_status(self) -> str:
        with self._asr_readiness_lock:
            return self._asr_readiness_status

    @property
    def asr_ready(self) -> bool:
        return self.asr_readiness_status == "ready"

    def asr_readiness_detail(self) -> str:
        with self._asr_readiness_lock:
            status = self._asr_readiness_status
            error = self._asr_readiness_error
        if status == "ready":
            return "ASR worker ready"
        if status == "failed":
            return f"ASR unavailable: {error or 'worker initialization failed'}"
        return "ASR worker starting"

    def _set_asr_readiness(self, status: str, error: Optional[str] = None) -> None:
        with self._asr_readiness_lock:
            self._asr_readiness_status = status
            self._asr_readiness_error = error

    def _set_readiness(self, status: str, *, error: Optional[str] = None, device_info: Optional[dict] = None) -> None:
        with self._readiness_lock:
            self._readiness_status = status
            self._readiness_error = error
            if device_info:
                self._audio_device_info = {**self._audio_device_info, **device_info}
        self._readiness_event.set()

    def _emit_audio_level(self, level: float) -> None:
        """Publish a normalized 0.0-1.0 audio amplitude on the event bus.

        Throttled to ~50ms so a fast playback loop doesn't flood subscribers.
        Runs from audio threads; the bus lives on the async loop, so we
        schedule the emit there.
        """
        now = time.monotonic()
        last = getattr(self, "_last_level_emit", 0.0)
        if now - last < 0.05:
            return
        self._last_level_emit = now
        self._schedule_event_emit("audio_level", {"level": level})

    def _schedule_event_emit(self, event_type: str, payload: dict) -> None:
        bus = getattr(self, "_event_bus", None)
        loop = getattr(self, "_event_loop", None)
        if bus is None or loop is None or self._stopped:
            return
        coroutine = bus.emit(event_type, payload, meta=EventMeta(source=EventSource.VOICE))
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        except Exception:
            coroutine.close()
            logger.debug("%s emit failed", event_type, exc_info=True)
            return
        with self._event_emit_lock:
            self._event_emit_futures.add(future)

        def _discard(completed) -> None:
            with self._event_emit_lock:
                self._event_emit_futures.discard(completed)

        future.add_done_callback(_discard)

    def _cancel_pending_event_emits(self) -> None:
        with self._event_emit_lock:
            futures = tuple(self._event_emit_futures)
            self._event_emit_futures.clear()
        for future in futures:
            future.cancel()

    def _emit_vad_start(self) -> None:
        """Publish speech-onset so the dashboard can show a listening state.

        Without wake-word mode, nothing else ever emits "vad_start" -- the
        dashboard's listening animation would otherwise never trigger.
        """
        self._schedule_event_emit("vad_start", {})

    @staticmethod
    def _rms(samples: "np.ndarray") -> float:
        """Root-mean-square amplitude of a float32 audio buffer, 0.0-1.0."""
        arr = np.asarray(samples, dtype=np.float32)
        if arr.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(arr))))

    @staticmethod
    def _speech_onset_confirmed(consecutive_loud_frames: int, required_frames: int = 2) -> bool:
        return consecutive_loud_frames >= required_frames

    @staticmethod
    def _speech_buffer_from_pre_roll(pre_roll_buffer: deque) -> list[np.ndarray]:
        """Start phrase with each buffered physical frame exactly once."""
        return list(pre_roll_buffer)

    @staticmethod
    def _should_end_speech(
        silence_duration: float,
        speech_duration: float,
        silence_timeout: float,
        phrase_min_duration: float,
        phrase_max_duration: float,
    ) -> bool:
        return (
            silence_duration >= silence_timeout and speech_duration >= phrase_min_duration
        ) or speech_duration >= phrase_max_duration

    def _ensure_models(self):
        os.makedirs(self.config.kokoro_model_dir, exist_ok=True)
        base_url = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
        files = {
            "kokoro-v1.0.onnx": f"{base_url}/kokoro-v1.0.onnx",
            "voices-v1.0.bin": f"{base_url}/voices-v1.0.bin",
        }
        for name, url in files.items():
            path = os.path.join(self.config.kokoro_model_dir, name)
            if not os.path.exists(path):
                logger.info(f"Downloading {name} for local use...")
                urllib.request.urlretrieve(url, path)

    def start(self):
        logger.info("Starting voice engine loops")
        self.voice_diagnostics.prime_gpu_async()
        self.input_thread = threading.Thread(
            target=self._run, daemon=True, name="VoiceInputLoop"
        )
        self.input_thread.start()
        # Voice start is not readiness. Wait for the input worker to acknowledge
        # physical stream open/failure before reporting subsystem health.
        if not self._readiness_event.wait(timeout=10.0):
            self._set_readiness("failed", error="microphone initialization timed out")
        self.tts_worker = threading.Thread(
            target=self._tts_worker_loop, daemon=True, name="TTSWorker"
        )
        self.tts_worker.start()
        self.playback_worker = threading.Thread(
            target=self._playback_worker, daemon=True, name="TTSPlayback"
        )
        self.playback_worker.start()
        self.asr_poller_thread = threading.Thread(
            target=self._asr_poller_loop, daemon=True
        )
        self.asr_poller_thread.start()

        # Initialize wake word detector if enabled
        if self.config.wake_word_enabled:
            try:
                self._wake_word_detector = WakeWordDetector(
                    classifier_path=self.config.wake_word_model_path,
                    threshold=self.config.wake_word_threshold,
                )
                if self._wake_word_detector.is_available:
                    self._wake_word_active = False  # start in waiting state
                    self._last_activity_time = time.time()
                    logger.info("Wake word detection enabled.")
                else:
                    self._wake_word_detector = None
                    logger.warning("Wake word detector unavailable; disabling.")
            except Exception as e:
                self._wake_word_detector = None
                logger.warning(f"Wake word init failed: {e}; disabling.")
        else:
            logger.info("Wake word detection disabled.")

        if self.is_ready:
            logger.info("Continuous listening mode active.")
        else:
            logger.warning("Continuous listening unavailable: %s", self.readiness_detail())

    def stop(self):
        """Shut down voice engine. Called from main.py finally block."""
        if self._stopped:
            logger.info("voice_shutdown_complete | already_stopped=true")
            return
        self._stopped = True
        logger.info("voice_shutdown_begin")
        self.stop_event.set()
        self.stop_tts()
        self._event_bus = None
        self._event_loop = None
        self._cancel_pending_event_emits()
        if self.audio_stream is not None:
            try:
                self.audio_stream.close()
            except Exception as e:
                logger.debug(f"audio_stream close error: {e}")
            self.audio_stream = None
        for name, thread in (
            ("voice_capture_thread", self.input_thread),
            ("tts_thread", self.tts_worker),
            ("playback_thread", self.playback_worker),
            ("asr_poller_thread", self.asr_poller_thread),
        ):
            if thread and thread.is_alive():
                thread.join(timeout=1.0)
            logger.info(
                "voice_shutdown_thread | name=%s | stopped=%s",
                name,
                not (thread and thread.is_alive()),
            )
        with self._ptt_lock:
            self._ptt_active = False
            self._ptt_stop_requested = False
            self._ptt_chunks.clear()
            self._ptt_trace = None
        pending_trace_count = len(self._utterance_traces)
        self._utterance_traces.clear()
        if pending_trace_count:
            logger.info("voice_shutdown_asr_traces_cleared | count=%s", pending_trace_count)
        if self.asr_process:
            drained = 0
            while True:
                try:
                    payload = self.asr_input_queue.get_nowait()
                except (queue.Empty, OSError, ValueError):
                    break
                drained += 1
                if isinstance(payload, tuple) and len(payload) >= 3 and isinstance(payload[2], dict):
                    self._utterance_traces.pop(payload[2].get("utterance_id"), None)
            if drained:
                logger.info("voice_shutdown_asr_queue_drained | count=%s", drained)
            try:
                self.asr_input_queue.put(None, timeout=1.0)
            except Exception:
                logger.warning("ASR worker shutdown signal could not be queued", exc_info=True)
            logger.info("ASR worker shutdown requested | pid=%s", getattr(self.asr_process, "pid", None))
            self.asr_process.join(timeout=1.0)
            if self.asr_process.is_alive():
                logger.warning("ASR worker did not exit gracefully; terminating owned worker")
                self.asr_process.terminate()
                self.asr_process.join(timeout=1.0)
            logger.info(
                "ASR worker exited | pid=%s | stopped=%s",
                getattr(self.asr_process, "pid", None),
                not self.asr_process.is_alive(),
            )
        self.voice_diagnostics.stop()
        sampler = getattr(self.voice_diagnostics, "_resource_thread", None)
        sampler_stopped = sampler is None or not sampler.is_alive()
        logger.info("voice_diagnostics_sampler_stopped | stopped=%s", sampler_stopped)
        logger.info("voice_shutdown_complete")

    def stop_tts(self):
        self.stop_tts_event.set()
        while not self.tts_queue.empty():
            try:
                self.tts_queue.get_nowait()
            except queue.Empty:
                break
        while not self.playback_queue.empty():
            try:
                self.playback_queue.get_nowait()
            except queue.Empty:
                break
        # Do NOT call sd.stop() here -- this runs on the caller's thread
        # (e.g. the barge-in path), while _playback_worker's own thread
        # concurrently drives sd.play()/sd.stop()/sd.wait() on the same
        # sounddevice global stream. Two threads touching PortAudio's
        # stream lifecycle unsynchronized is a native-crash hazard, worse
        # under rapid repeated barge-in. _playback_worker already polls
        # stop_tts_event every 10ms during playback and calls sd.stop()
        # itself from its own thread -- that's the only safe caller.

    def _notify_speech_onset(self, trace: VoiceDiagnosticTrace, rms: float, sample_rate: int) -> None:
        """Interrupt active playback after VAD confirms two loud frames."""

        tts_was_active = self.is_speaking.is_set()
        if tts_was_active and self.barge_in_enabled:
            self.stop_tts()
        if self._on_speech_onset is None:
            return
        try:
            self._on_speech_onset(
                {
                    "trace": trace,
                    "speech_onset_rms": rms,
                    "sample_rate": sample_rate,
                    "tts_was_active": tts_was_active,
                }
            )
        except TypeError:
            self._on_speech_onset()
        except Exception:
            logger.debug("speech onset callback failed", exc_info=True)

    def set_audio_state(self, muted: Optional[bool] = None, volume: Optional[float] = None) -> dict:
        """Apply dashboard speaker controls. Returns the resulting state.

        `muted` toggles silence; `volume` is a 0.0-1.0 linear gain. Either may
        be omitted to leave the existing value unchanged.
        """
        if muted is not None:
            self.muted = bool(muted)
        if volume is not None:
            self.volume = self._clamp_volume(volume)
        return {"muted": self.muted, "volume": self.volume}

    @staticmethod
    def _clamp_volume(v: float) -> float:
        return max(0.0, min(1.0, float(v)))

    def _apply_gain(self) -> float:
        """Effective output gain: silence when muted, else clamped volume."""
        return 0.0 if self.muted else self._clamp_volume(self.volume)

    def get_audio_state(self) -> dict:
        return {"muted": self.muted, "volume": self.volume}

    def set_mic_state(self, mic_muted: bool) -> dict:
        """Toggle the microphone input gate. When muted, captured audio is
        dropped before ASR so the assistant stops hearing the user without
        tearing down the audio device. Returns the resulting mic state.
        """
        self.mic_muted = bool(mic_muted)
        return {"mic_muted": self.mic_muted}

    def get_mic_state(self) -> dict:
        return {"mic_muted": self.mic_muted}

    def start_ptt(self) -> None:
        """Start a real hold-to-talk capture using the existing mic/ASR path."""
        with self._ptt_lock:
            self._ptt_chunks.clear()
            self._ptt_stop_requested = False
            self._ptt_active = True
            self._ptt_trace = self.voice_diagnostics.new_trace()
            self._ptt_trace.mark_once(
                "voice_capture_onset",
                fields={"capture_mode": "ptt", "onset_rms": None},
                include_resource=self.voice_diagnostics.enabled,
            )

    def stop_ptt(self) -> None:
        """Stop PTT; the capture loop submits the collected audio to existing ASR."""
        with self._ptt_lock:
            if self._ptt_active:
                self._ptt_active = False
                self._ptt_stop_requested = True

    def cancel_ptt(self) -> None:
        with self._ptt_lock:
            self._ptt_active = False
            self._ptt_stop_requested = False
            self._ptt_chunks.clear()
            self._ptt_trace = None

    # -----------------------------------------------------------------------
    # Text humanization -- the single control point for TTS prosody
    # -----------------------------------------------------------------------

    @staticmethod
    def _humanize_text(text: str) -> str:
        """Transform LLM output into natural spoken text for Kokoro TTS.

        Kokoro's phonemizer (espeak-ng) uses punctuation to control prosody:
        - Period -> falling intonation + pause (sentence end)
        - Comma -> short pause (clause break)
        - Question mark -> rising intonation
        - Exclamation -> emphasis + falling intonation

        This function normalizes LLM quirks into clean text with correct
        punctuation so Kokoro produces natural speech, not robotic reading.
        """
        if not text:
            return ""

        # 1. Ellipsis handling: "..." -> ".", "wait..." -> "wait."
        text = _RE_ELLIPSIS.sub(".", text)
        text = _RE_DOTS.sub(".", text)

        # 2. Repeated punctuation: "!!" -> "!", "??" -> "?"
        text = _RE_REPEATED_EXCL.sub("!", text)
        text = _RE_REPEATED_QUES.sub("?", text)

        # 3. Dashes -> commas (clause breaks sound natural; dashes sound robotic)
        text = _RE_EM_DASH.sub(", ", text)
        text = _RE_EN_DASH.sub(", ", text)
        text = _RE_DOUBLE_HYPHEN.sub(", ", text)

        # 4. Strip LLM formatting artifacts
        text = _RE_LIST_BULLET.sub("", text)
        text = _RE_NUMBERED_LIST.sub("", text)
        text = _RE_HASH_HEADER.sub("", text)
        text = _RE_BOLD_ITALIC.sub(r"\1", text)  # *bold* -> bold
        text = _RE_INLINE_CODE.sub(r"\1", text)  # `code` -> code
        # Strip remaining bold asterisks to prevent TTS reading them.
        # Paired emphasis (_italic_ / *bold*) is handled by _RE_BOLD_ITALIC above.
        # Lone underscores (snake_case, IDs, handles) are intentionally preserved.
        text = text.replace("**", "").replace("*", "")

        # 5. Wrapper quotes: "Hello world" -> Hello world
        m = _RE_WRAPPER_QUOTES.match(text)
        if m:
            text = m.group(1)

        # 6. Parenthetical aside handling
        text = _RE_PAREN_SHORT.sub("", text)  # remove short asides entirely
        text = _RE_PAREN_LONG.sub(r"\1", text)  # keep content of long asides

        # 7. Expand contractions for natural speech
        for full, contracted in _CONTRACTIONS.items():
            # Case-insensitive word-boundary replacement
            text = re.sub(
                r"\b" + re.escape(full) + r"\b",
                contracted,
                text,
                flags=re.IGNORECASE,
            )

        # 8. Ensure sentence ends with punctuation (Kokoro needs this for prosody)
        text = text.rstrip()
        if text and text[-1] not in ".!?":
            # Check if it looks like a question
            lower = text.lower()
            if any(
                lower.endswith(q)
                for q in (
                    "what",
                    "why",
                    "how",
                    "when",
                    "where",
                    "who",
                    "which",
                    "is it",
                    "do you",
                    "can you",
                    "could you",
                    "would you",
                    "shall we",
                )
            ):
                text += "?"
            else:
                text += "."

        # 9. Fix missing space after sentence-ending punctuation
        text = _RE_TRAILING_PUNCT_NO_SPACE.sub(r"\1 \2", text)

        # 10. Collapse multiple spaces/newlines
        text = re.sub(r"\s+", " ", text).strip()

        return text

    def is_echo(self, text: str) -> bool:
        """True if `text` is a subset of words Charlie is currently speaking
        (or just finished speaking).

        Used both here (to skip re-speaking a near-duplicate) and by main.py
        barge-in (to suppress the assistant hearing its own TTS output).
        Covers the whole reply, not just a fixed window after the most
        recently flushed sentence chunk -- a long reply spans multiple
        speak() calls, and an open mic can pick up any part of it, well
        past a short fixed window from the first chunk.
        """
        now = time.time()
        still_speaking = self.is_speaking.is_set()
        recently_finished = now - getattr(self, "_last_speech_end", 0.0) < self.speech_echo_window
        if not (still_speaking or recently_finished):
            return False
        new_words = set(text.lower().split())
        old_words = getattr(self, "_recent_spoken_words", set())
        return bool(new_words and new_words.issubset(old_words))

    def speak(self, text: str, emotional_state: str = "neutral"):
        """Sanitize text for TTS and enqueue. Non-blocking."""
        # Strip reasoning tags using shared helper
        text = strip_internal_reasoning(text)

        # Echo detection
        if self.is_echo(text):
            return ""
        # A new reply (not a continuation chunk of one already being spoken)
        # starts a fresh word set instead of carrying over the last reply's.
        if not self.is_speaking.is_set():
            self._recent_spoken_words = set()
        self._last_speech_time = time.time()

        # Strip URLs
        text = re.sub(r"\(https?://.*?\)", "", text)
        text = re.sub(r"https?://\S+", "", text)

        # Humanize for natural TTS prosody
        text = self._humanize_text(text)

        # Store the humanized string actually spoken (used by echo detection
        # in both speak() and main.py barge-in). Do this before ASCII cleanup
        # so comparisons match what Kokoro phonemizes.
        self._last_speech_text = text
        self._recent_spoken_words |= set(text.lower().split())

        # Number and symbol conversion
        text = self._numbers_to_words(text)
        text = self._symbols_to_words(text)
        # Final ASCII cleanup
        text = text.encode("ascii", "ignore").decode("ascii")
        text = re.sub(r"\s+", " ", text).strip()

        if not text or len(text) < _MIN_TEXT_LEN:
            return ""

        if len(text) > _LONG_SENTENCE_CHARS:
            self.stop_tts_event.clear()
            chunks = _SENTENCE_SPLIT_RE.split(text)
            trace = self._current_diagnostic_context()
            if trace is not None:
                trace.mark_once(
                    "tts_enqueue",
                    fields={"text_length": len(text), "emotional_state": emotional_state},
                )
            for chunk in chunks:
                chunk = chunk.strip()
                if chunk and len(chunk) >= _MIN_TEXT_LEN:
                    item = (chunk, emotional_state, trace) if trace is not None else (chunk, emotional_state)
                    self.tts_queue.put(item)
            return

        self.stop_tts_event.clear()
        trace = self._current_diagnostic_context()
        if trace is not None:
            trace.mark_once(
                "tts_enqueue",
                fields={"text_length": len(text), "emotional_state": emotional_state},
            )
        item = (text, emotional_state, trace) if trace is not None else (text, emotional_state)
        self.tts_queue.put(item)

    # -----------------------------------------------------------------------
    # TTS synthesis
    # -----------------------------------------------------------------------

    def _synth(self, text: str, speed: float):
        """Synthesize text to audio samples. Returns (samples, sample_rate, mouth_values) or None."""
        if not text:
            return None
        phon_logger = logging.getLogger("phonemizer")
        old_level = phon_logger.level
        phon_logger.setLevel(logging.ERROR)
        try:
            with self.tts_lock:
                try:
                    tts_start = time.time()
                    samples, sample_rate = self.kokoro.create(
                        text,
                        voice=self.config.kokoro_voice,
                        speed=speed,
                        lang=self.config.kokoro_lang,
                    )
                    tts_ms = (time.time() - tts_start) * 1000
                    logger.debug(
                        f"pipeline_stage | stage=tts | latency_ms={tts_ms:.1f}"
                    )
                    mouth_values = []
                    return (samples, sample_rate, mouth_values)
                except Exception as e:
                    logger.error(f"synth_error | {e}")
                    return None
        finally:
            phon_logger.setLevel(old_level)

    async def _synth_stream(self, text: str, speed: float):
        """Yield (samples, sample_rate) chunks from kokoro.create_stream()."""
        if not text:
            return
        phon_logger = logging.getLogger("phonemizer")
        old_level = phon_logger.level
        phon_logger.setLevel(logging.ERROR)
        try:
            if not hasattr(self.kokoro, "create_stream"):
                logger.debug("kokoro has no create_stream; falling back to batch")
                result = self._synth(text, speed)
                if result is not None:
                    samples, sr, _mouth = result
                    yield (samples, sr)
                return
            stream = self.kokoro.create_stream(
                text,
                voice=self.config.kokoro_voice,
                speed=speed,
                lang=self.config.kokoro_lang,
            )
            async for samples, sr in stream:
                yield (samples, sr)
        finally:
            phon_logger.setLevel(old_level)

    def _tts_worker_loop(self):
        """TTS synthesis worker."""
        while not self.stop_event.is_set():
            try:
                if self.stop_tts_event.is_set():
                    while not self.tts_queue.empty():
                        self.tts_queue.get_nowait()
                    self.stop_tts_event.clear()

                item = self.tts_queue.get(timeout=0.01)
                trace = item[2] if isinstance(item, tuple) and len(item) >= 3 else None
                text, emotional_state = item[:2]

                speed = 1.0
                if emotional_state == "energetic":
                    speed = 1.05
                elif emotional_state in ("sad", "calm"):
                    speed = 0.95

                asyncio.run(self._tts_stream_and_queue(text, speed, trace=trace))
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"tts_worker_error | {e}")

    async def _tts_stream_and_queue(
        self,
        text: str,
        speed: float,
        *,
        trace: Optional[VoiceDiagnosticTrace] = None,
    ):
        """Consume _synth_stream and push each chunk to playback_queue.

        A _TTS_RUN_END sentinel is pushed after all chunks of a single TTS
        run so the playback worker knows when the entire utterance (which may
        span multiple chunks) is fully drained, rather than clearing
        is_speaking on the momentary gaps between chunks.
        """
        if trace is not None:
            trace.mark_once(
                "tts_synthesis_start",
                fields={"text_length": len(text), "speed": speed},
            )
        synthesis_completed = False
        try:
            async for samples, sr in self._synth_stream(text, speed):
                if self.stop_tts_event.is_set():
                    break
                item = (samples, sr, [], trace) if trace is not None else (samples, sr, [])
                self.playback_queue.put(item)
            synthesis_completed = not self.stop_tts_event.is_set()
            if synthesis_completed:
                self.playback_queue.put(_TTS_RUN_END)
        finally:
            if trace is not None and synthesis_completed:
                trace.mark_once(
                    "tts_synthesis_complete",
                    fields={"text_length": len(text), "speed": speed},
                )

    def _playback_worker(self):
        """Dedicated playback thread."""
        tts_started_fired = False
        active_trace: Optional[VoiceDiagnosticTrace] = None
        while not self.stop_event.is_set():
            try:
                if self.stop_tts_event.is_set():
                    # Drain pending chunks
                    while not self.playback_queue.empty():
                        try:
                            self.playback_queue.get_nowait()
                        except queue.Empty:
                            break
                    if active_trace is not None:
                        active_trace.mark_once(
                            "playback_complete",
                            fields={"status": "stopped"},
                        )
                    self.stop_tts_event.clear()
                    # Fire stop callback if we were speaking
                    if tts_started_fired and self._on_tts_stop:
                        try:
                            self._on_tts_stop()
                        except Exception:
                            pass
                    self.is_speaking.clear()
                    self.tts_active.clear()
                    tts_started_fired = False
                    active_trace = None
                    continue

                try:
                    item = self.playback_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                # Sentinel marking the true end of a TTS run.
                if item is _TTS_RUN_END:
                    if tts_started_fired and self.stop_tts_event.is_set() is False:
                        if active_trace is not None:
                            active_trace.mark_once(
                                "playback_complete",
                                fields={"status": "completed"},
                            )
                        if self._on_tts_stop:
                            try:
                                self._on_tts_stop()
                            except Exception:
                                pass
                        self.is_speaking.clear()
                        self.tts_active.clear()
                        tts_started_fired = False
                        active_trace = None
                    continue

                trace = item[3] if isinstance(item, tuple) and len(item) >= 4 else None
                samples, sample_rate, mouth_values = item[:3]

                # Chime: gain already applied by caller, skip TTS state.
                if mouth_values is _CHIME_ITEM:
                    sd.play(samples, samplerate=sample_rate)
                    sd.wait()
                    continue

                # Apply dashboard volume gain; a muted device still drives the
                # speaking callbacks (so the UI reflects state) but emits silence.
                gain = self._apply_gain()
                if gain != 1.0:
                    samples = np.asarray(samples, dtype=np.float32) * gain

                # Publish real TTS amplitude (drops to ~0.0 when muted).
                self._emit_audio_level(self._rms(samples))

                # First chunk of a new TTS run
                if not tts_started_fired:
                    tts_started_fired = True
                    active_trace = trace
                    self.is_speaking.set()
                    self.tts_active.set()
                    if self._on_tts_start:
                        try:
                            self._on_tts_start()
                        except Exception:
                            pass

                sd.play(samples, samplerate=sample_rate)
                if trace is not None:
                    trace.mark_once(
                        "playback_first_sample",
                        fields={
                            "sample_rate": sample_rate,
                            "sample_count": len(samples),
                        },
                    )
                while sd.get_stream() and sd.get_stream().active:
                    if self.stop_tts_event.is_set():
                        sd.stop()
                        break
                    time.sleep(0.01)
                sd.wait()
                self._last_speech_end = time.time()

                # Do NOT clear is_speaking here. A long utterance spans
                # multiple chunks with momentary queue-empty gaps; clearing
                # on a gap defeats barge-in. The _TTS_RUN_END sentinel (pushed
                # after all chunks) signals the real end of the utterance.

            except Exception as e:
                logger.error(f"playback_error | {e}")
                if tts_started_fired and self._on_tts_stop:
                    try:
                        self._on_tts_stop()
                    except Exception:
                        pass
                self.is_speaking.clear()
                self.tts_active.clear()
                tts_started_fired = False
                if active_trace is not None:
                    active_trace.mark_once(
                        "playback_complete",
                        fields={"status": "error"},
                    )
                active_trace = None

        if active_trace is not None:
            active_trace.mark_once(
                "playback_complete",
                fields={"status": "stopped"},
            )

    # -----------------------------------------------------------------------
    # Number and symbol -> word conversion
    # -----------------------------------------------------------------------

    @staticmethod
    def _number_to_words(n: int) -> str:
        """Convert integer to English words (0 to 999 billion)."""
        if n == 0:
            return "zero"
        prefix = ""
        if n < 0:
            prefix = "minus "
            n = -n
        ones = [
            "",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
            "eighteen",
            "nineteen",
        ]
        tens = [
            "",
            "",
            "twenty",
            "thirty",
            "forty",
            "fifty",
            "sixty",
            "seventy",
            "eighty",
            "ninety",
        ]

        def _h(n: int) -> str:
            if n == 0:
                return ""
            if n < 20:
                return ones[n]
            if n < 100:
                t = tens[n // 10]
                r = n % 10
                return t + (" " + ones[r] if r else "")
            h = ones[n // 100] + " hundred"
            r = n % 100
            return h + (" " + _h(r) if r else "")

        parts = []
        if prefix:
            parts.append(prefix.rstrip())
        if n >= 1_000_000_000:
            b = n // 1_000_000_000
            parts.append(_h(b) + " billion")
            n %= 1_000_000_000
        if n >= 1_000_000:
            m = n // 1_000_000
            parts.append(_h(m) + " million")
            n %= 1_000_000
        if n >= 1000:
            parts.append(_h(n // 1000) + " thousand")
            n %= 1000
        if n > 0:
            parts.append(_h(n))
        return " ".join(parts) if parts else "zero"

    def _numbers_to_words(self, text: str) -> str:
        """Convert numeric patterns to English words."""

        def _get_suffix_word(s):
            s = s.lower()
            if s == "k":
                return " thousand"
            if s == "m":
                return " million"
            if s == "b":
                return " billion"
            if s == "t":
                return " trillion"
            return ""
        # Pre-pass: normalize full-word suffixes to single-letter ($2 billion -> $2B)
        text = re.sub(
            r"\$(\d[\d,]*\.?\d*)\s+(thousand|million|billion|trillion)",
            lambda m: f"${m.group(1)}{m.group(2)[0].upper()}",
            text, flags=re.IGNORECASE,
        )
        text = re.sub(
            r"(?<!\w)(\d[\d,]*\.?\d*)\s+(thousand|million|billion|trillion)",
            lambda m: f"{m.group(1)}{m.group(2)[0].upper()}",
            text, flags=re.IGNORECASE,
        )

        def _replace_currency(m):
            raw = m.group(1).replace(",", "")
            suffix = m.group(2) if len(m.groups()) >= 2 and m.group(2) else ""
            suffix_word = _get_suffix_word(suffix)
            try:
                if "." in raw:
                    integer, fraction = raw.split(".", 1)
                    int_words = (
                        self._number_to_words(int(integer))
                        if integer and integer != "0"
                        else "zero"
                    )
                    frac_digits = " ".join(
                        self._number_to_words(int(d)) for d in fraction
                    )
                    words = f"{int_words} point {frac_digits}"
                else:
                    n = int(float(raw))
                    words = self._number_to_words(n)
                return (
                    f"{words}{suffix_word} dollars"
                    if words != "one" or suffix_word
                    else f"{words} dollar"
                )
            except (ValueError, IndexError):
                return m.group(0)

        def _replace_number(m):
            raw = m.group(1).replace(",", "")
            suffix = m.group(2) if len(m.groups()) >= 2 and m.group(2) else ""
            suffix_word = _get_suffix_word(suffix)
            try:
                if "." in raw:
                    integer, fraction = raw.split(".", 1)
                    int_words = (
                        self._number_to_words(int(integer))
                        if integer and integer != "0"
                        else "zero"
                    )
                    frac_digits = " ".join(
                        self._number_to_words(int(d)) for d in fraction
                    )
                    words = f"{int_words} point {frac_digits}"
                else:
                    n = int(float(raw))
                    words = self._number_to_words(n)
                return f"{words}{suffix_word}"
            except (ValueError, IndexError):
                return m.group(0)

        text = re.sub(
            r"\$(\d[\d,]*\.?\d*)\s*([BbMmTtKk])?(?!\w)", _replace_currency, text
        )
        text = re.sub(
            r"(?<!\w)(\d[\d,]*\.?\d*)\s*([BbMmTtKk])(?!\w)", _replace_number, text
        )
        text = re.sub(r"(?<!\w)(\d{1,3}(?:,\d{3})+)(?!\w)", _replace_number, text)

        def _replace_decimal_simple(m):
            integer, fraction = m.group(1).replace(",", ""), m.group(2)
            try:
                int_words = (
                    self._number_to_words(int(integer))
                    if integer and integer != "0"
                    else "zero"
                )
                frac_digits = " ".join(self._number_to_words(int(d)) for d in fraction)
                return f"{int_words} point {frac_digits}"
            except ValueError:
                return m.group(0)

        text = re.sub(
            r"(?<!\d\.)(?<!\w)(\d{1,3}(?:,\d{3})*)\.(\d+)(?!\.\d)",
            _replace_decimal_simple,
            text,
        )
        text = re.sub(r"(?<!\w)(\d{5,})(?!\.\d)(?!\w)", _replace_number, text)
        return text

    # Symbol-to-word mappings for {str}.translate() -- maps ordinal -> word
    _SYMBOL_MAP = str.maketrans(
        {
            "%": " percent",
            "&": " and",
            "@": " at",
            "+": " plus",
            "=": " equals",
            "/": " slash ",
            "\\": " backslash ",
            ">": " greater than ",
            "<": " less than ",
        }
    )

    def _symbols_to_words(self, text: str) -> str:
        text = text.translate(self._SYMBOL_MAP)
        text = re.sub(r"(\d)\s+degrees\s+", r"\1 degrees ", text)
        return text

    # -----------------------------------------------------------------------
    # Audio input / ASR
    # -----------------------------------------------------------------------

    def _play_wake_chime(self) -> None:
        """Play a short chime on wake-word detection. Non-blocking."""
        # Respect the dashboard speaker controls.
        gain = self._apply_gain()
        if gain == 0.0:
            return
        chime_path = self.config.wake_word_audio_chime_path
        try:
            if os.path.exists(chime_path):
                import soundfile as sf

                samples, sr = sf.read(chime_path, dtype="float32")
                samples = np.asarray(samples, dtype=np.float32) * gain
            else:
                # Synthesize a short sine-wave chime (440Hz, 200ms)
                sr = 16000
                duration = 0.2
                t = np.linspace(0, duration, int(sr * duration), dtype=np.float32)
                samples = 0.3 * np.sin(2 * np.pi * 440 * t) * gain
                # Quick fade-in/out to avoid clicks
                fade = min(int(sr * 0.02), len(samples))
                samples[:fade] *= np.linspace(0, 1, fade)
                samples[-fade:] *= np.linspace(1, 0, fade)
            # Route through playback_queue so sd.play only ever runs on
            # _playback_worker's thread -- a raw thread here raced it.
            self.playback_queue.put((samples, sr, _CHIME_ITEM))
        except Exception as e:
            logger.debug(f"Wake chime error: {e}")

    @staticmethod
    def _safe_audio_error(error: Exception) -> str:
        text = re.sub(
            r"(?i)(api[-_ ]?key|token|password|secret)\s*[:=]\s*[^\s,;]+",
            r"\1=redacted",
            str(error),
        ).strip()
        return text[:240] or "audio device initialization failed"

    @staticmethod
    def _input_device_candidates(configured_index: int) -> list[int | None]:
        """Return configured input first, then same device via other host APIs."""
        selected = None if configured_index == -1 else configured_index
        selected_index = int(sd.default.device[0]) if selected is None else int(selected)
        selected_info = sd.query_devices(selected_index)
        selected_name = str(selected_info.get("name", "")).strip()
        candidates: list[int | None] = [selected_index]
        if not selected_name:
            return candidates
        try:
            devices = sd.query_devices()
        except Exception:
            return candidates
        for index, info in enumerate(devices):
            if index == selected_index or int(info.get("max_input_channels", 0)) < 1:
                continue
            if str(info.get("name", "")).strip() == selected_name:
                candidates.append(index)
        return candidates

    def _open_input_stream(self, samplerate: int, block_size: int, callback, configured_index: int):
        """Open capture with bounded retry and same-device host API fallback."""
        errors: list[str] = []
        for candidate_index in self._input_device_candidates(configured_index):
            for attempt in range(2):
                stream = None
                try:
                    stream = sd.InputStream(
                        samplerate=samplerate, channels=1, dtype="float32",
                        blocksize=block_size, device=candidate_index, callback=callback,
                    )
                    stream.start()
                    return stream, candidate_index
                except Exception as error:
                    if stream is not None:
                        try:
                            stream.close()
                        except Exception:
                            pass
                    errors.append(
                        f"{candidate_index if candidate_index is not None else 'default'}: "
                        f"{self._safe_audio_error(error)}"
                    )
                    if attempt == 0:
                        time.sleep(0.25)
        detail = "; ".join(errors[-4:]) or "no input devices available"
        raise RuntimeError(f"all microphone open attempts failed ({detail})")

    @staticmethod
    def _queue_depth(audio_queue) -> Optional[int]:
        try:
            return int(audio_queue.qsize())
        except (AttributeError, NotImplementedError, OSError):
            return None

    def _submit_asr(
        self,
        audio: np.ndarray,
        sample_rate: int,
        trace: VoiceDiagnosticTrace,
        capture_fields: dict,
    ) -> None:
        """Submit one phrase while recording queue/backpressure evidence."""

        audio_bytes = np.asarray(audio, dtype=np.float32).reshape(-1).tobytes()
        enqueue_started = time.monotonic()
        depth_before = self._queue_depth(self.asr_input_queue)
        dropped_utterance_id = None
        outcome = "enqueued"
        try:
            self._utterance_traces[trace.utterance_id] = trace
            self.asr_input_queue.put_nowait(
                (
                    audio_bytes,
                    sample_rate,
                    {
                        "utterance_id": trace.utterance_id,
                        "diagnostic_enabled": self.voice_diagnostics.enabled,
                        "voice_capture_onset_monotonic": trace.onset_timestamp(),
                        "asr_enqueue_monotonic": enqueue_started,
                        "capture": dict(capture_fields),
                    },
                )
            )
        except queue.Full:
            outcome = "drop_oldest"
            try:
                dropped_payload = self.asr_input_queue.get_nowait()
                if isinstance(dropped_payload, tuple) and len(dropped_payload) >= 3:
                    dropped_flags = dropped_payload[2]
                    if isinstance(dropped_flags, dict):
                        dropped_utterance_id = dropped_flags.get("utterance_id")
            except queue.Empty:
                pass
            if dropped_utterance_id:
                dropped_trace = self._utterance_traces.pop(dropped_utterance_id, None)
                if dropped_trace is not None:
                    dropped_trace.mark(
                        "asr_input_dropped",
                        fields={"reason": "drop_oldest", "replaced_by": trace.utterance_id},
                    )
            try:
                self.asr_input_queue.put_nowait(
                    (
                        audio_bytes,
                        sample_rate,
                        {
                            "utterance_id": trace.utterance_id,
                            "diagnostic_enabled": self.voice_diagnostics.enabled,
                            "voice_capture_onset_monotonic": trace.onset_timestamp(),
                            "asr_enqueue_monotonic": enqueue_started,
                            "capture": dict(capture_fields),
                        },
                    )
                )
            except queue.Full:
                outcome = "drop_new"
                self._utterance_traces.pop(trace.utterance_id, None)

        enqueue_finished = time.monotonic()
        depth_after = self._queue_depth(self.asr_input_queue)
        trace.mark_once(
            "asr_enqueue",
            fields={
                **capture_fields,
                "queue_depth_before": depth_before,
                "queue_depth_after": depth_after,
                "enqueue_wait_ms": (enqueue_finished - enqueue_started) * 1000,
                "enqueue_outcome": outcome,
                "dropped_utterance_id": dropped_utterance_id,
            },
            timestamp=enqueue_finished,
            include_resource=self.voice_diagnostics.enabled,
            asr_worker_pid=self.asr_process.pid if self.asr_process is not None else None,
        )
        if outcome != "drop_new":
            self.voice_diagnostics.capture_audio(trace, audio, sample_rate)

    def _record_asr_worker_stage(self, status: dict, receive_timestamp: float) -> None:
        """Record child-process progress and expose it in the parent log."""

        if status.get("type") != _ASR_WORKER_STAGE_MESSAGE:
            return
        stage = status.get("stage")
        utterance_id = status.get("utterance_id")
        if not isinstance(stage, str):
            return
        try:
            worker_timestamp = float(status.get("monotonic_timestamp", receive_timestamp))
        except (TypeError, ValueError):
            worker_timestamp = receive_timestamp
        fields = status.get("fields") if isinstance(status.get("fields"), dict) else {}
        worker_pid = status.get("worker_pid") or fields.get("worker_pid")

        with self._asr_worker_state_lock:
            self._asr_worker_last_output_activity_monotonic = receive_timestamp
            if stage == "asr_worker_dequeued" and utterance_id is not None:
                self._asr_worker_latest_dequeued_utterance_id = str(utterance_id)
                self._asr_worker_inflight = {
                    "utterance_id": str(utterance_id),
                    "worker_pid": worker_pid,
                    "dequeued_monotonic": worker_timestamp,
                    "processing_started_monotonic": None,
                    "last_stage": stage,
                    "last_stage_monotonic": worker_timestamp,
                    "last_stage_fields": dict(fields),
                }
                self._asr_worker_stall_warning_key = None
            elif (
                self._asr_worker_inflight is not None
                and str(utterance_id) == self._asr_worker_inflight.get("utterance_id")
            ):
                self._asr_worker_inflight.update(
                    {
                        "worker_pid": worker_pid or self._asr_worker_inflight.get("worker_pid"),
                        "last_stage": stage,
                        "last_stage_monotonic": worker_timestamp,
                        "last_stage_fields": dict(fields),
                    }
                )
                if stage == "asr_worker_transcribe_enter":
                    self._asr_worker_inflight["processing_started_monotonic"] = worker_timestamp
                self._asr_worker_stall_warning_key = None

        resource_snapshot = None
        if stage in {
            "asr_worker_dequeued",
            "asr_worker_transcribe_enter",
            "asr_worker_segments_iteration_begin",
            "asr_worker_exception",
        }:
            resource_snapshot = self.voice_diagnostics.resource_snapshot(asr_worker_pid=worker_pid)
        logger.info(
            "asr_worker_stage | stage=%s | utterance_id=%s | worker_pid=%s | "
            "audio_sample_count=%s | audio_duration_ms=%s | queue_age_ms=%s | "
            "model=%s | device=%s | compute_type=%s | beam_size=%s | best_of=%s | "
            "vad_filter=%s | fields=%s | resource_snapshot=%s",
            stage,
            utterance_id,
            worker_pid,
            fields.get("audio_sample_count"),
            fields.get("audio_duration_ms"),
            fields.get("queue_age_ms"),
            fields.get("model"),
            fields.get("device"),
            fields.get("compute_type"),
            fields.get("beam_size"),
            fields.get("best_of"),
            fields.get("vad_filter"),
            fields,
            resource_snapshot,
        )

    def _record_asr_worker_output(self, result: object, receive_timestamp: float) -> None:
        """Mark result-queue activity and retire its matching in-flight item."""

        flags = result[2] if isinstance(result, tuple) and len(result) >= 3 else {}
        if not isinstance(flags, dict):
            flags = {}
        utterance_id = flags.get("utterance_id")
        text = result[0].strip() if isinstance(result, tuple) and result and isinstance(result[0], str) else ""
        asr_error = flags.get("asr_error")
        with self._asr_worker_state_lock:
            self._asr_worker_last_output_activity_monotonic = receive_timestamp
            if (
                utterance_id is not None
                and self._asr_worker_inflight is not None
                and str(utterance_id) == self._asr_worker_inflight.get("utterance_id")
            ):
                self._asr_worker_inflight = None
                self._asr_worker_stall_warning_key = None
        logger.info(
            "asr_worker_output_received | utterance_id=%s | text_length=%s | "
            "asr_error=%s | input_queue_depth=%s | output_queue_depth=%s",
            utterance_id,
            len(text),
            asr_error,
            self._queue_depth(self.asr_input_queue),
            self._queue_depth(self.asr_output_queue),
        )

    def _asr_worker_alive(self) -> Optional[bool]:
        process = self.asr_process
        if process is None:
            return None
        try:
            return bool(process.is_alive())
        except (OSError, ValueError):
            return None

    def _check_asr_worker_stall(self) -> None:
        """Warn about an aged child job plus backlog; never restart it."""

        now = time.monotonic()
        with self._asr_worker_state_lock:
            if now - self._asr_worker_last_watchdog_monotonic < _ASR_WORKER_WATCHDOG_INTERVAL_S:
                return
            self._asr_worker_last_watchdog_monotonic = now
            state = dict(self._asr_worker_inflight) if self._asr_worker_inflight is not None else None
            last_output = self._asr_worker_last_output_activity_monotonic
        if state is None:
            return

        started = state.get("processing_started_monotonic") or state.get("dequeued_monotonic")
        if started is None:
            return
        age_ms = max(0.0, (now - float(started)) * 1000)
        if age_ms < _ASR_WORKER_STALL_THRESHOLD_S * 1000:
            return

        input_queue_depth = self._queue_depth(self.asr_input_queue)
        worker_alive = self._asr_worker_alive()
        # ponytail: require backlog or dead worker; avoids false alarms for one legitimate long decode.
        if worker_alive is True and input_queue_depth == 0:
            return
        warning_key = (state.get("utterance_id"), state.get("last_stage"))
        with self._asr_worker_state_lock:
            if warning_key == self._asr_worker_stall_warning_key:
                return
            self._asr_worker_stall_warning_key = warning_key

        worker_pid = state.get("worker_pid") or getattr(self.asr_process, "pid", None)
        try:
            poller_alive = bool(self.asr_poller_thread and self.asr_poller_thread.is_alive())
        except (OSError, ValueError):
            poller_alive = None
        output_queue_depth = self._queue_depth(self.asr_output_queue)
        output_age_ms = (
            max(0.0, (now - last_output) * 1000) if last_output is not None else None
        )
        resource_snapshot = self.voice_diagnostics.resource_snapshot(asr_worker_pid=worker_pid)
        logger.warning(
            "asr_worker_stall_suspected | utterance_id=%s | last_stage=%s | "
            "age_ms=%.1f | worker_alive=%s | asr_poller_alive=%s | "
            "input_queue_depth=%s | output_queue_depth=%s | "
            "output_queue_last_activity_age_ms=%s | latest_dequeued_utterance_id=%s | "
            "resource_snapshot=%s",
            state.get("utterance_id"),
            state.get("last_stage"),
            age_ms,
            worker_alive,
            poller_alive,
            input_queue_depth,
            output_queue_depth,
            output_age_ms,
            self._asr_worker_latest_dequeued_utterance_id,
            resource_snapshot,
        )

    def _run(self):
        samplerate = 16000
        block_size = 1024

        def _callback(indata, frames, time_info, status):
            # Mic muted: drop the frame before ASR and stop publishing its
            # level so the VU meter reads flat instead of faking live audio.
            if self.mic_muted:
                return
            # Avoid logging on the audio thread; check status flag silently or log on debug
            try:
                self._audio_queue.put_nowait(indata.copy())
            except queue.Full:
                try:
                    self._audio_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._audio_queue.put_nowait(indata.copy())
                except queue.Full:
                    pass  # drop oldest frame on overflow
            # Publish live mic amplitude from every captured frame (throttled
            # in _emit_audio_level). Near-zero when quiet, rises with speech.
            self._emit_audio_level(self._rms(indata))

        self._audio_queue: queue.Queue = queue.Queue(maxsize=32)

        # Resolve input device: -1 -> system default
        input_device = None if self.config.mic_index == -1 else self.config.mic_index

        selected_device_index = input_device
        device_info: dict = {
            "configured_device_index": self.config.mic_index,
            "configured_sample_rate": samplerate,
            "channels": 1,
            "block_size": block_size,
        }
        try:
            if selected_device_index is None:
                selected_device_index = int(sd.default.device[0])
            dev_info = sd.query_devices(selected_device_index)
            hostapi_index = int(dev_info.get("hostapi", -1))
            hostapi_info = sd.query_hostapis(hostapi_index) if hostapi_index >= 0 else {}
            device_info.update(
                {
                    "selected_device_index": selected_device_index,
                    "device_name": str(dev_info.get("name", selected_device_index)),
                    "host_api": str(hostapi_info.get("name", "unknown")),
                    "max_input_channels": int(dev_info.get("max_input_channels", 0)),
                    "native_sample_rate": float(dev_info.get("default_samplerate", 0.0)),
                }
            )
        except Exception as error:
            device_info["selected_device_index"] = selected_device_index
            self._set_readiness("failed", error=self._safe_audio_error(error), device_info=device_info)
            self._set_asr_readiness("failed", "microphone unavailable; ASR worker was not started")
            logger.error("Failed to inspect audio input device: %s", error)
            return

        try:
            self.audio_stream, selected_device_index = self._open_input_stream(
                samplerate, block_size, _callback, self.config.mic_index
            )
            opened_info = sd.query_devices(selected_device_index)
            opened_hostapi_index = int(opened_info.get("hostapi", -1))
            opened_hostapi = sd.query_hostapis(opened_hostapi_index) if opened_hostapi_index >= 0 else {}
            device_info.update(
                {
                    "selected_device_index": selected_device_index,
                    "device_name": str(opened_info.get("name", selected_device_index)),
                    "host_api": str(opened_hostapi.get("name", "unknown")),
                    "max_input_channels": int(opened_info.get("max_input_channels", 0)),
                    "native_sample_rate": float(opened_info.get("default_samplerate", 0.0)),
                    "fallback_host_api": selected_device_index != (
                        int(sd.default.device[0]) if input_device is None else input_device
                    ),
                }
            )
        except Exception as e:
            if self.audio_stream is not None:
                try:
                    self.audio_stream.close()
                except Exception:
                    pass
                self.audio_stream = None
            self._set_readiness("failed", error=self._safe_audio_error(e), device_info=device_info)
            self._set_asr_readiness("failed", "microphone unavailable; ASR worker was not started")
            logger.error(
                "Audio input readiness failed: device=%s index=%s host_api=%s "
                "max_input_channels=%s configured_rate=%s native_rate=%s "
                "channels=%s block=%s stream_open=False error=%s",
                device_info.get("device_name"),
                device_info.get("selected_device_index"),
                device_info.get("host_api"),
                device_info.get("max_input_channels"),
                device_info.get("configured_sample_rate"),
                device_info.get("native_sample_rate"),
                device_info.get("channels"),
                device_info.get("block_size"),
                self._safe_audio_error(e),
            )
            return

        self._set_readiness("ready", device_info={**device_info, "stream_open": True})
        logger.info(
            "Audio stream opened: device=%s index=%s host_api=%s rate=%s native_rate=%s channels=%s block=%s",
            device_info.get("device_name"),
            device_info.get("selected_device_index"),
            device_info.get("host_api"),
            samplerate,
            device_info.get("native_sample_rate"),
            1,
            block_size,
        )

        # Start ASR worker process
        _asr_config = {
            "beam_size": self.config.asr_beam_size,
            "best_of": self.config.asr_best_of,
            "repetition_penalty": self.config.asr_repetition_penalty,
            "vad_threshold": self.config.vad_threshold,
            "min_speech_duration_ms": self.config.vad_min_speech_duration_ms,
            "max_speech_duration_s": self.config.vad_max_speech_duration_s,
            "min_silence_duration_ms": self.config.vad_min_silence_duration_ms,
            "speech_pad_ms": self.config.vad_speech_pad_ms,
        }
        self.asr_process = mp.Process(
            target=asr_worker_process,
            args=(
                self.asr_input_queue,
                self.asr_output_queue,
                self.config.whisper_model,
                self.config.gpu_device,
                self.config.default_language,
                _asr_config,
            ),
            daemon=True,
        )
        self.asr_process.start()
        self.voice_diagnostics.start_resource_sampler(asr_worker_pid=self.asr_process.pid)
        self._set_asr_readiness("starting")
        logger.info("ASR worker process started.")

        # VAD state
        _vad_threshold = self.config.vad_threshold
        _silence_timeout = self.config.vad_silence_timeout
        _phrase_min_duration = self.config.phrase_min_duration
        _phrase_max_duration = self.config.phrase_max_duration
        _pre_roll_samples = int(samplerate * 0.8)  # 800ms pre-roll buffer
        _pre_roll_buffer: deque = deque(maxlen=_pre_roll_samples // block_size)

        is_speech = False
        speech_start_time = 0.0
        last_speech_time = 0.0
        speech_buffer = []
        _frame_count = 0
        _rms_log_interval = int(3.0 * samplerate / block_size)  # log every ~3s
        # Require 2 consecutive loud frames (~128ms) before confirming onset, so a single
        # ~64ms keyboard click transient can't trigger it -- real speech sustains across frames.
        _onset_debounce_frames = 2
        _consecutive_loud_frames = 0
        speech_trace: Optional[VoiceDiagnosticTrace] = None
        speech_onset_rms: Optional[float] = None
        speech_start_monotonic: Optional[float] = None
        last_speech_monotonic: Optional[float] = None
        speech_pre_roll_samples = 0

        # Wake word sliding buffer (~2s at 16kHz for inference)
        _ww_buffer_samples = samplerate * 2  # 32000 samples = 2s
        _ww_buffer: deque = deque(maxlen=_ww_buffer_samples // block_size + 1)
        _ww_check_interval = max(1, block_size // 512)  # scale with block_size
        _ww_check_counter = 0

        while not self.stop_event.is_set():
            self._check_asr_worker_stall()
            try:
                data = self._audio_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            # PTT bypasses VAD timing but still uses the same capture device,
            # ASR worker, and on_speech callback as ordinary voice input.
            with self._ptt_lock:
                ptt_active = self._ptt_active
                ptt_stop = self._ptt_stop_requested
                ptt_trace = self._ptt_trace
                if ptt_active:
                    self._ptt_chunks.append(data.copy())
                ptt_audio = np.concatenate(self._ptt_chunks) if ptt_stop and self._ptt_chunks else None
                if ptt_stop:
                    self._ptt_chunks.clear()
                    self._ptt_stop_requested = False
                    self._ptt_trace = None
            if ptt_active or ptt_stop:
                # Do not carry a VAD phrase across a PTT turn boundary.
                is_speech = False
                speech_buffer = []
                _consecutive_loud_frames = 0
                if ptt_audio is not None and len(ptt_audio) >= block_size:
                    if ptt_trace is None:
                        ptt_trace = self.voice_diagnostics.new_trace()
                        ptt_trace.mark_once(
                            "voice_capture_onset",
                            fields={"capture_mode": "ptt", "onset_rms": None},
                        )
                    ptt_trace.mark_once(
                        "voice_capture_endpoint",
                        fields={
                            "capture_mode": "ptt",
                            "configured_sample_rate": samplerate,
                            "submitted_sample_count": int(len(ptt_audio)),
                            "submitted_duration_ms": len(ptt_audio) / samplerate * 1000,
                            "vad_measured_speech_duration_ms": None,
                            "effective_pre_roll_ms": 0.0,
                            "speech_onset_rms": None,
                            "vad_threshold": _vad_threshold,
                            "vad_min_speech_duration_ms": self.config.vad_min_speech_duration_ms,
                            "vad_min_silence_duration_ms": self.config.vad_min_silence_duration_ms,
                            "vad_speech_pad_ms": self.config.vad_speech_pad_ms,
                            "endpoint_silence_duration_ms": None,
                            "phrase_min_duration_ms": _phrase_min_duration * 1000,
                            "phrase_max_duration_s": _phrase_max_duration,
                        },
                        include_resource=self.voice_diagnostics.enabled,
                        asr_worker_pid=self.asr_process.pid if self.asr_process is not None else None,
                    )
                    self._submit_asr(
                        ptt_audio,
                        samplerate,
                        ptt_trace,
                        {
                            "capture_mode": "ptt",
                            "configured_sample_rate": samplerate,
                            "submitted_sample_count": int(len(ptt_audio)),
                            "submitted_duration_ms": len(ptt_audio) / samplerate * 1000,
                            "vad_measured_speech_duration_ms": None,
                            "effective_pre_roll_ms": 0.0,
                            "speech_onset_rms": None,
                            "vad_threshold": _vad_threshold,
                            "vad_min_speech_duration_ms": self.config.vad_min_speech_duration_ms,
                            "vad_min_silence_duration_ms": self.config.vad_min_silence_duration_ms,
                            "vad_speech_pad_ms": self.config.vad_speech_pad_ms,
                            "endpoint_silence_duration_ms": None,
                            "phrase_min_duration_ms": _phrase_min_duration * 1000,
                            "phrase_max_duration_s": _phrase_max_duration,
                        },
                    )
                continue

            # -- Wake word gating --
            # When wake word is enabled and we're NOT in an active session,
            # feed audio to the wake word detector instead of running VAD.
            if self._wake_word_detector is not None and not self._wake_word_active:
                _ww_buffer.append(data.copy())
                _ww_check_counter += 1
                if _ww_check_counter >= _ww_check_interval:
                    _ww_check_counter = 0
                    if len(_ww_buffer) >= _ww_buffer_samples // block_size:
                        ww_audio = np.concatenate(list(_ww_buffer)).flatten()
                        if self._wake_word_detector.is_triggered(ww_audio):
                            logger.info("wake_word_detected")
                            self._wake_word_active = True
                            self._last_activity_time = time.time()
                            # Play chime (non-blocking)
                            self._play_wake_chime()
                            # Notify frontend
                            if self._on_wake_word:
                                try:
                                    self._on_wake_word()
                                except Exception as e:
                                    logger.debug(f"wake_word_callback error: {e}")
                continue  # skip VAD when not in active session

            # -- Activity timeout check --
            if (
                self._wake_word_detector is not None
                and self._wake_word_active
                and not is_speech  # only check when not mid-speech
            ):
                elapsed = time.time() - self._last_activity_time
                if elapsed > self.config.wake_word_activity_timeout_seconds:
                    logger.info("wake_word_inactive | timeout reached")
                    self._wake_word_active = False
                    _ww_buffer.clear()
                    _ww_check_counter = 0


            rms = float(np.sqrt(np.mean(data**2) + 1e-10))
            _frame_count += 1

            # Periodic RMS logging for mic level diagnostics
            if _frame_count % _rms_log_interval == 0:
                logger.debug(
                    f"vad_rms | rms={rms:.4f} threshold={_vad_threshold} speech={is_speech}"
                )

            # Pre-roll: always keep a sliding window of recent audio
            _pre_roll_buffer.append(data.copy())

            if not is_speech:
                if rms > _vad_threshold:
                    _consecutive_loud_frames += 1
                else:
                    _consecutive_loud_frames = 0
                if self._speech_onset_confirmed(_consecutive_loud_frames, _onset_debounce_frames):
                    is_speech = True
                    _consecutive_loud_frames = 0
                    speech_start_time = time.time()
                    last_speech_time = time.time()
                    speech_start_monotonic = time.monotonic()
                    last_speech_monotonic = speech_start_monotonic
                    speech_onset_rms = rms
                    speech_trace = self.voice_diagnostics.new_trace()
                    logger.info(
                        f"vad_speech_onset | rms={rms:.4f} threshold={_vad_threshold}"
                    )
                    speech_trace.mark_once(
                        "voice_capture_onset",
                        fields={
                            "capture_mode": "vad",
                            "configured_sample_rate": samplerate,
                            "speech_onset_rms": rms,
                            "vad_threshold": _vad_threshold,
                            "onset_debounce_frames": _onset_debounce_frames,
                            "pre_roll_configured_ms": 800.0,
                            "pre_roll_buffer_samples": len(_pre_roll_buffer) * block_size,
                            "effective_pre_roll_ms": len(_pre_roll_buffer) * block_size / samplerate * 1000,
                            "vad_min_speech_duration_ms": self.config.vad_min_speech_duration_ms,
                            "vad_min_silence_duration_ms": self.config.vad_min_silence_duration_ms,
                            "vad_speech_pad_ms": self.config.vad_speech_pad_ms,
                        },
                        include_resource=self.voice_diagnostics.enabled,
                        asr_worker_pid=self.asr_process.pid if self.asr_process is not None else None,
                    )
                    speech_pre_roll_samples = len(_pre_roll_buffer) * block_size
                    self._notify_speech_onset(speech_trace, rms, samplerate)
                    self._emit_vad_start()
                    # Prepend pre-roll buffer to prevent clipping first words
                    speech_buffer = self._speech_buffer_from_pre_roll(_pre_roll_buffer)
                continue

            # During speech
            speech_buffer.append(data.copy())
            now = time.time()

            if rms > _vad_threshold:
                last_speech_time = now
                last_speech_monotonic = time.monotonic()

            duration = now - speech_start_time
            silence_duration = now - last_speech_time

            should_end = self._should_end_speech(
                silence_duration,
                duration,
                _silence_timeout,
                _phrase_min_duration,
                _phrase_max_duration,
            )

            if should_end:
                is_speech = False
                audio = np.concatenate(speech_buffer)
                speech_buffer = []
                duration_ms = duration * 1000
                endpoint_monotonic = time.monotonic()
                logger.info(
                    f"vad_speech_offset | duration_ms={duration_ms:.0f} samples={len(audio)}"
                )
                if speech_trace is not None:
                    capture_fields = {
                        "capture_mode": "vad",
                        "configured_sample_rate": samplerate,
                        "submitted_sample_count": int(len(audio)),
                        "submitted_duration_ms": len(audio) / samplerate * 1000,
                        "vad_measured_speech_duration_ms": (
                            (endpoint_monotonic - speech_start_monotonic) * 1000
                            if speech_start_monotonic is not None
                            else duration_ms
                        ),
                        "effective_pre_roll_ms": speech_pre_roll_samples / samplerate * 1000,
                        "pre_roll_configured_ms": 800.0,
                        "pre_roll_buffer_samples": speech_pre_roll_samples,
                        "speech_onset_rms": speech_onset_rms,
                        "vad_threshold": _vad_threshold,
                        "vad_min_speech_duration_ms": self.config.vad_min_speech_duration_ms,
                        "vad_min_silence_duration_ms": self.config.vad_min_silence_duration_ms,
                        "vad_speech_pad_ms": self.config.vad_speech_pad_ms,
                        "endpoint_silence_duration_ms": (
                            (endpoint_monotonic - last_speech_monotonic) * 1000
                            if last_speech_monotonic is not None
                            else silence_duration * 1000
                        ),
                        "phrase_min_duration_ms": _phrase_min_duration * 1000,
                        "phrase_max_duration_s": _phrase_max_duration,
                        "onset_frame_duplicated_in_buffer": False,
                        "onset_frame_counted_once": True,
                    }
                    speech_trace.mark_once(
                        "voice_capture_endpoint",
                        fields=capture_fields,
                        timestamp=endpoint_monotonic,
                        include_resource=self.voice_diagnostics.enabled,
                        asr_worker_pid=self.asr_process.pid if self.asr_process is not None else None,
                    )
                    self._submit_asr(audio, samplerate, speech_trace, capture_fields)
                speech_trace = None
                speech_onset_rms = None
                speech_start_monotonic = None
                last_speech_monotonic = None
                speech_pre_roll_samples = 0

    def _asr_poller_loop(self):
        """Poll ASR results and forward to on_speech callback."""
        while not self.stop_event.is_set():
            try:
                result = self.asr_output_queue.get(timeout=0.1)
                receive_timestamp = time.monotonic()
                if isinstance(result, dict):
                    if result.get("type") == _ASR_WORKER_STAGE_MESSAGE:
                        self._record_asr_worker_stage(result, receive_timestamp)
                        continue
                    with self._asr_worker_state_lock:
                        self._asr_worker_last_output_activity_monotonic = receive_timestamp
                    if result.get("type") == "ready":
                        metrics = result.get("metrics")
                        self.asr_startup_metrics = dict(metrics) if isinstance(metrics, dict) else {}
                        self._set_asr_readiness("ready")
                        logger.info(
                            "ASR readiness acknowledged by worker | model_load_ms=%s | "
                            "warmup_inference_ms=%s | asr_ready_ms=%s",
                            self.asr_startup_metrics.get("model_load_ms"),
                            self.asr_startup_metrics.get("warmup_inference_ms"),
                            self.asr_startup_metrics.get("asr_ready_ms"),
                        )
                    elif result.get("type") == "failed":
                        metrics = result.get("metrics")
                        self.asr_startup_metrics = dict(metrics) if isinstance(metrics, dict) else {}
                        self._set_asr_readiness("failed", str(result.get("error") or "worker initialization failed"))
                    continue
                flags = result[2] if isinstance(result, tuple) and len(result) >= 3 else {}
                if not isinstance(flags, dict):
                    flags = {}
                self._record_asr_worker_output(result, receive_timestamp)
                if flags.get("is_warmup"):
                    continue
                utterance_id = flags.get("utterance_id")
                trace = self._utterance_traces.pop(utterance_id, None) if utterance_id else None
                if trace is not None:
                    trace.import_stage_events(flags.get("diagnostic_stages"))
                    complete_timestamp = flags.get("asr_complete_monotonic")
                    try:
                        result_age_ms = (receive_timestamp - float(complete_timestamp)) * 1000
                    except (TypeError, ValueError):
                        result_age_ms = None
                    trace.mark(
                        "asr_poller_receive",
                        fields={
                            "result_age_ms": result_age_ms,
                            "output_queue_depth": self._queue_depth(self.asr_output_queue),
                            "worker_pid": self.asr_process.pid if self.asr_process is not None else None,
                            "confidence_semantics": flags.get("confidence_semantics"),
                        },
                        timestamp=receive_timestamp,
                        include_resource=self.voice_diagnostics.enabled,
                        asr_worker_pid=self.asr_process.pid if self.asr_process is not None else None,
                    )
                if result and self.on_speech:
                    # Worker sends (text, legacy_language_probability, flags_dict) tuples.
                    text = result[0].strip() if isinstance(result, tuple) else str(result).strip()
                    if text:
                        # Reset wake word activity timer on user speech
                        if self._wake_word_detector is not None:
                            self._last_activity_time = time.time()
                        if trace is not None:
                            trace.mark_once(
                                "speech_callback",
                                fields={
                                    "transcript_length": len(text),
                                    "language_probability": flags.get("language_probability"),
                                    "confidence_semantics": flags.get("confidence_semantics"),
                                },
                                timestamp=time.monotonic(),
                            )
                        metadata = {
                            "utterance_id": utterance_id,
                            "trace": trace,
                            "asr_quality": flags.get("asr_quality", {}),
                            "audio": flags.get("capture", {}),
                            "confidence": result[1] if isinstance(result, tuple) and len(result) >= 2 else None,
                            "language_probability": flags.get("language_probability"),
                            "confidence_semantics": flags.get("confidence_semantics"),
                        }
                        if trace is not None and self._speech_callback_mode == "positional":
                            self.on_speech(text, metadata)
                        elif trace is not None and self._speech_callback_mode == "keyword":
                            self.on_speech(text, diagnostic_metadata=metadata)
                        else:
                            self.on_speech(text)
            except queue.Empty:
                self._check_asr_worker_stall()
                if self.asr_process is not None and not self.asr_process.is_alive() and not self.asr_ready:
                    self._set_asr_readiness("failed", "ASR worker exited before readiness")
                continue
            except Exception as e:
                logger.error(f"asr_poller_error | {e}")
