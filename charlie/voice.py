"""Charlie voice engine -- VAD, ASR, TTS (Kokoro), audio I/O.

All text arriving at speak() passes through _humanize_text() before
phonemization. This is the single control point for prosody and pacing.
"""

import asyncio
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
from charlie.wake_word import WakeWordDetector

logger = logging.getLogger("charlie.voice")

# --- TTS text humanization constants ---
_MIN_TEXT_LEN = 3
_ECHO_WINDOW_SEC = 2.0
_ECHO_MAX_WORDS = 4
# Sentinel pushed to playback_queue after every chunk of a single TTS run has
# been enqueued. Lets the playback worker distinguish "utterance fully spoken"
# from momentary inter-chunk queue gaps.
_TTS_RUN_END = object()
_LONG_SENTENCE_CHARS = 250
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?,;])\s+")


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
        on_speech: Callable[[str], None],
        on_tts_start: Optional[Callable[[], None]] = None,
        on_tts_stop: Optional[Callable[[], None]] = None,
    ):
        self.config = config
        self.on_speech = on_speech
        self._on_tts_start = on_tts_start
        self._on_tts_stop = on_tts_stop
        self.is_speaking = threading.Event()
        self.tts_active = threading.Event()
        self.stop_event = threading.Event()
        self.stop_tts_event = threading.Event()
        self.tts_queue: queue.Queue = queue.Queue()
        self.playback_queue: queue.Queue = queue.Queue()
        self.tts_lock = threading.Lock()
        self._last_speech_time = 0.0
        self._last_speech_text = ""
        self._last_speech_end = 0.0
        self.speech_echo_window = _ECHO_WINDOW_SEC
        self.speech_echo_max_words = _ECHO_MAX_WORDS
        self._widget_callback = None

        # Speaker output state (driven by the dashboard audio controls).
        # `muted` silences TTS playback; `volume` is a 0.0-1.0 linear gain
        # applied to the audio samples before they reach the output device.
        self.muted: bool = False
        self.volume: float = 1.0

        # Microphone input state. `mic_muted` drops captured frames before
        # they reach ASR so the assistant stops listening without killing the
        # audio device. Distinct from `muted`, which only affects speakers.
        self.mic_muted: bool = False

        # ASR state
        self.asr_input_queue: mp.Queue = mp.Queue(maxsize=8)
        self.asr_output_queue: mp.Queue = mp.Queue(maxsize=8)
        self.asr_process = None

        # Load Kokoro TTS
        self._ensure_models()
        self.kokoro = Kokoro(
            os.path.join(config.kokoro_model_dir, "kokoro-v1.0.onnx"),
            os.path.join(config.kokoro_model_dir, "voices-v1.0.bin"),
        )
        self.barge_in_enabled: bool = True

        # Wake word state
        self._wake_word_detector: Optional[WakeWordDetector] = None
        self._wake_word_active: bool = False  # True = in active session after wake word
        self._last_activity_time: float = 0.0
        self._on_wake_word: Optional[Callable[[], None]] = None

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

    def _emit_audio_level(self, level: float) -> None:
        """Publish a normalized 0.0-1.0 audio amplitude on the event bus.

        Throttled to ~50ms so a fast playback loop doesn't flood subscribers.
        Runs from audio threads; the bus lives on the async loop, so we
        schedule the emit there.
        """
        bus = getattr(self, "_event_bus", None)
        loop = getattr(self, "_event_loop", None)
        if bus is None or loop is None:
            return
        now = time.monotonic()
        last = getattr(self, "_last_level_emit", 0.0)
        if now - last < 0.05:
            return
        self._last_level_emit = now
        try:
            asyncio.run_coroutine_threadsafe(
                bus.emit("audio_level", {"level": level}), loop
            )
        except Exception:
            logger.debug("audio_level emit failed", exc_info=True)

    @staticmethod
    def _rms(samples: "np.ndarray") -> float:
        """Root-mean-square amplitude of a float32 audio buffer, 0.0-1.0."""
        arr = np.asarray(samples, dtype=np.float32)
        if arr.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(arr))))

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
        self.input_thread = threading.Thread(
            target=self._run, daemon=True, name="VoiceInputLoop"
        )
        self.input_thread.start()
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

        logger.info("Continuous listening mode active.")

    def stop(self):
        """Shut down voice engine. Called from main.py finally block."""
        logger.info("Voice engine stopping...")
        self.stop_event.set()
        self.stop_tts()
        for thread in (
            self.input_thread,
            self.tts_worker,
            self.playback_worker,
            self.asr_poller_thread,
        ):
            if thread and thread.is_alive():
                thread.join(timeout=1.0)
        if self.audio_stream is not None:
            try:
                self.audio_stream.close()
            except Exception as e:
                logger.debug(f"audio_stream close error: {e}")
        if self.asr_process:
            self.asr_input_queue.put(None)
            self.asr_process.join(timeout=1.0)
            if self.asr_process.is_alive():
                self.asr_process.terminate()
                self.asr_process.join(timeout=1.0)
        logger.info("Voice engine stopped.")

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
        sd.stop()

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
        """True if `text` is a short subset of what Charlie just spoke.

        Used both here (to skip re-speaking a near-duplicate) and by main.py
        barge-in (to suppress the assistant hearing its own TTS output).
        """
        now = time.time()
        if now - getattr(self, "_last_speech_time", 0.0) >= self.speech_echo_window:
            return False
        new_words = set(text.lower().split())
        old_words = set(getattr(self, "_last_speech_text", "").lower().split())
        return bool(
            new_words
            and len(new_words) <= self.speech_echo_max_words
            and new_words.issubset(old_words)
        )

    def speak(self, text: str, emotional_state: str = "neutral"):
        """Sanitize text for TTS and enqueue. Non-blocking."""
        # Strip reasoning tags using shared helper
        text = strip_internal_reasoning(text)

        # Echo detection
        if self.is_echo(text):
            return ""
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
            for chunk in chunks:
                chunk = chunk.strip()
                if chunk and len(chunk) >= _MIN_TEXT_LEN:
                    self.tts_queue.put((chunk, emotional_state))
            return

        self.stop_tts_event.clear()
        self.tts_queue.put((text, emotional_state))

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
                text, emotional_state = item

                speed = 1.0
                if emotional_state == "energetic":
                    speed = 1.05
                elif emotional_state in ("sad", "calm"):
                    speed = 0.95

                asyncio.run(self._tts_stream_and_queue(text, speed))
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"tts_worker_error | {e}")

    async def _tts_stream_and_queue(self, text: str, speed: float):
        """Consume _synth_stream and push each chunk to playback_queue.

        A _TTS_RUN_END sentinel is pushed after all chunks of a single TTS
        run so the playback worker knows when the entire utterance (which may
        span multiple chunks) is fully drained, rather than clearing
        is_speaking on the momentary gaps between chunks.
        """
        async for samples, sr in self._synth_stream(text, speed):
            if self.stop_tts_event.is_set():
                break
            self.playback_queue.put((samples, sr, []))
        if not self.stop_tts_event.is_set():
            self.playback_queue.put(_TTS_RUN_END)

    def _playback_worker(self):
        """Dedicated playback thread."""
        tts_started_fired = False
        while not self.stop_event.is_set():
            try:
                if self.stop_tts_event.is_set():
                    # Drain pending chunks
                    while not self.playback_queue.empty():
                        try:
                            self.playback_queue.get_nowait()
                        except queue.Empty:
                            break
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
                    continue

                try:
                    item = self.playback_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                # Sentinel marking the true end of a TTS run.
                if item is _TTS_RUN_END:
                    if tts_started_fired and self.stop_tts_event.is_set() is False:
                        if self._on_tts_stop:
                            try:
                                self._on_tts_stop()
                            except Exception:
                                pass
                        self.is_speaking.clear()
                        self.tts_active.clear()
                        tts_started_fired = False
                    continue

                samples, sample_rate, mouth_values = item

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
                    self.is_speaking.set()
                    self.tts_active.set()
                    if self._on_tts_start:
                        try:
                            self._on_tts_start()
                        except Exception:
                            pass

                sd.play(samples, samplerate=sample_rate)
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
                # Play in a thread so we don't block the audio loop
                threading.Thread(
                    target=sd.play, args=(samples, sr), daemon=True
                ).start()
            else:
                # Synthesize a short sine-wave chime (440Hz, 200ms)
                sr = 16000
                duration = 0.2
                t = np.linspace(0, duration, int(sr * duration), dtype=np.float32)
                chime = 0.3 * np.sin(2 * np.pi * 440 * t) * gain
                # Quick fade-in/out to avoid clicks
                fade = min(int(sr * 0.02), len(chime))
                chime[:fade] *= np.linspace(0, 1, fade)
                chime[-fade:] *= np.linspace(1, 0, fade)
                threading.Thread(
                    target=sd.play, args=(chime, sr), daemon=True
                ).start()
        except Exception as e:
            logger.debug(f"Wake chime error: {e}")

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

        try:
            self.audio_stream = sd.InputStream(
                samplerate=samplerate,
                channels=1,
                dtype="float32",
                blocksize=block_size,
                device=input_device,
                callback=_callback,
            )
            self.audio_stream.start()
        except Exception as e:
            logger.error(f"Failed to open audio stream: {e}")
            return

        try:
            dev_info = sd.query_devices(input_device)
            dev_name = (
                dev_info.get("name", str(input_device))
                if isinstance(dev_info, dict)
                else str(input_device)
            )
        except Exception:
            dev_name = str(input_device)
        logger.info(
            f"Audio stream opened: device={dev_name} rate={samplerate} block={block_size}"
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

        # Wake word sliding buffer (~2s at 16kHz for inference)
        _ww_buffer_samples = samplerate * 2  # 32000 samples = 2s
        _ww_buffer: deque = deque(maxlen=_ww_buffer_samples // block_size + 1)
        _ww_check_interval = max(1, block_size // 512)  # scale with block_size
        _ww_check_counter = 0

        while not self.stop_event.is_set():
            try:
                data = self._audio_queue.get(timeout=0.1)
            except queue.Empty:
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
                    is_speech = True
                    speech_start_time = time.time()
                    last_speech_time = time.time()
                    logger.info(
                        f"vad_speech_onset | rms={rms:.4f} threshold={_vad_threshold}"
                    )
                    # Prepend pre-roll buffer to prevent clipping first words
                    speech_buffer = list(_pre_roll_buffer)
                    speech_buffer.append(data.copy())
                continue

            # During speech
            speech_buffer.append(data.copy())
            now = time.time()

            if rms > _vad_threshold:
                last_speech_time = now

            duration = now - speech_start_time
            silence_duration = now - last_speech_time

            should_end = False
            if (
                silence_duration >= _silence_timeout
                and duration >= _phrase_min_duration
            ):
                should_end = True
            if duration >= _phrase_max_duration:
                should_end = True

            if should_end:
                is_speech = False
                audio = np.concatenate(speech_buffer)
                speech_buffer = []
                duration_ms = duration * 1000
                logger.info(
                    f"vad_speech_offset | duration_ms={duration_ms:.0f} samples={len(audio)}"
                )

                # Send to ASR (must be tuple: bytes, sample_rate). Use
                # put_nowait with drop-oldest backpressure so the capture
                # thread never blocks on a full ASR queue.
                try:
                    self.asr_input_queue.put_nowait((audio.tobytes(), samplerate))
                except queue.Full:
                    try:
                        self.asr_input_queue.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        self.asr_input_queue.put_nowait((audio.tobytes(), samplerate))
                    except queue.Full:
                        pass  # drop oldest phrase on overflow

    def _asr_poller_loop(self):
        """Poll ASR results and forward to on_speech callback."""
        while not self.stop_event.is_set():
            try:
                result = self.asr_output_queue.get(timeout=0.1)
                if result and self.on_speech:
                    # Worker sends (text, confidence, flags_dict) tuples
                    text = (
                        result[0].strip()
                        if isinstance(result, tuple)
                        else str(result).strip()
                    )
                    if text:
                        # Reset wake word activity timer on user speech
                        if self._wake_word_detector is not None:
                            self._last_activity_time = time.time()
                        self.on_speech(text)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"asr_poller_error | {e}")
