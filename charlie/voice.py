import logging
import asyncio
import threading
import os
os.environ["ORT_LOG_LEVEL"] = "3"

import time
import urllib.request
import numpy as np
import sounddevice as sd
import torch
import re
import queue
import multiprocessing as mp
from typing import Callable, Optional
from kokoro_onnx import Kokoro
from collections import deque
from charlie.asr_worker import asr_worker_process

from charlie.core import strip_internal_reasoning

logger = logging.getLogger("charlie.voice")

class VoiceEngine:
    def __init__(self, config, on_speech: Callable[[str], None]):
        self.config = config
        self.on_speech = on_speech
        self.is_speaking = threading.Event()
        self.stop_event = threading.Event()
        self.stop_tts_event = threading.Event()
        self.tts_queue = queue.Queue()
        self.tts_lock = threading.Lock()
        self.playback_queue = queue.Queue()
        self.vad_model = None
        self.asr_input_queue = mp.Queue()
        self.asr_output_queue = mp.Queue()
        self.asr_process = None
        self.speech_start_time = 0.0
        # Initialize Models
        self._ensure_models()
        # 1. LOCAL VAD (Silero)
        try:
            self.vad_model, _ = torch.hub.load(
                repo_or_dir='snakers4/silero-vad:v3.1',
                model='silero_vad',
                force_reload=False,
                onnx=True,
                trust_repo=True
            )
            logger.info("VAD loaded from local cache.")
        except Exception as e:
            logger.warning(f"VAD local load failed: {e}")
            self.vad_model, _ = torch.hub.load('snakers4/silero-vad:v3.1', 'silero_vad', onnx=True)

        # 2. LOCAL STT (Whisper) - Spawned in separate process
        logger.info(f"Spawning ASR worker process (Whisper {config.whisper_model})...")
        self.asr_process = mp.Process(
            target=asr_worker_process,
            args=(
                self.asr_input_queue,
                self.asr_output_queue,
                config.whisper_model,
                config.gpu_device,
                config.default_language
            ),
            daemon=True
        )
        self.asr_process.start()
        # Warm up ASR with silent audio
        try:
            time.sleep(1)  # Wait for worker to load model
            silent_audio = np.zeros(16000, dtype=np.float32).tobytes()
            self.asr_input_queue.put((silent_audio, 16000))
            logger.info("ASR warm-up: sent silent audio.")
        except Exception as e:
            logger.warning(f"ASR warm-up failed: {e}")
        # 3. LOCAL TTS (Kokoro) — detect CUDA availability
        os.environ.pop("ONNX_PROVIDER", None)  # clear stale value
        import ctypes
        _CUDA_DLLS = (
            "cublasLt64_14.dll", "cublasLt64_13.dll", "cublasLt64_12.dll",
            "cublas64_14.dll", "cublas64_13.dll", "cublas64_12.dll",
            "cudart64_12.dll", "cudart64_11_0.dll",
        )
        cuda_found = False
        for dll in _CUDA_DLLS:
            try:
                ctypes.WinDLL(dll)
                cuda_found = True
                break
            except OSError:
                continue
        if cuda_found:
            os.environ["ONNX_PROVIDER"] = "CUDAExecutionProvider"
            logger.info(f"Kokoro TTS: CUDA detected ({dll}) — using GPU.")
        else:
            os.environ["ONNX_PROVIDER"] = "CPUExecutionProvider"
            logger.info("Kokoro TTS: No CUDA runtime found — using CPU.")

        model_path = os.path.join(config.kokoro_model_dir, "kokoro-v1.0.onnx")
        voices_path = os.path.join(config.kokoro_model_dir, "voices-v1.0.bin")
        try:
            self.kokoro = Kokoro(model_path, voices_path)
            logger.info("Kokoro TTS initialized locally.")
        except Exception as e:
            if cuda_found:
                logger.warning(f"Kokoro TTS: CUDA init failed ({e}), retrying on CPU.")
                os.environ["ONNX_PROVIDER"] = "CPUExecutionProvider"
                self.kokoro = Kokoro(model_path, voices_path)
                logger.info("Kokoro TTS: initialized on CPU after CUDA failure.")
            else:
                raise
        # Warm up ONNX graph
        try:
            self.kokoro.create("Warm up", voice=self.config.kokoro_voice, speed=1.0, lang=self.config.kokoro_lang)
            logger.info("Kokoro warm-up: ONNX graph compiled.")
        except Exception as e:
            logger.warning(f"Kokoro warm-up failed: {e}")
        # Use thread-safe deque for audio chunks
        self.audio_buffer = deque(maxlen=200)
        self.tts_active = threading.Event()
        self._pending_warmup_text = ""
        self._last_warmup_time = 0.0
        self._warmup_lock = threading.Lock()
        self._widget_callback = None
        self._rms_callback = None
        self.barge_in_enabled: bool = True

    def set_widget_callback(self, cb: Callable[[str], None]) -> None:
        """Register callback for mode changes (listening/speaking/idle)."""
        self._widget_callback = cb

    def _ensure_models(self):
        os.makedirs(self.config.kokoro_model_dir, exist_ok=True)
        base_url = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
        files = {
            "kokoro-v1.0.onnx": f"{base_url}/kokoro-v1.0.onnx",
            "voices-v1.0.bin": f"{base_url}/voices-v1.0.bin"
        }
        for name, url in files.items():
            path = os.path.join(self.config.kokoro_model_dir, name)
            if not os.path.exists(path):
                logger.info(f"Downloading {name} for local use...")
                urllib.request.urlretrieve(url, path)
    def start(self):
        logger.info("Starting voice engine loops")
        # Audio input thread
        self.input_thread = threading.Thread(target=self._run, daemon=True, name="VoiceInputLoop")
        self.input_thread.start()
        # TTS synthesis worker thread
        self.tts_worker = threading.Thread(target=self._tts_worker_loop, daemon=True, name="TTSWorker")
        self.tts_worker.start()
        # TTS playback worker (separate audio output thread)  
        self.playback_worker = threading.Thread(target=self._playback_worker, daemon=True, name="TTSPlayback")
        self.playback_worker.start()
        # Start ASR result polling thread
        self.asr_poller_thread = threading.Thread(target=self._asr_poller_loop, daemon=True)
        self.asr_poller_thread.start()
        logger.info("Continuous listening mode active.")


    def stop(self):
        self.stop_event.set()
        self.stop_tts()
        # Wait for all worker threads
        for thread in (self.input_thread, self.tts_worker, self.playback_worker, self.asr_poller_thread):
            if thread and thread.is_alive():
                thread.join(timeout=5.0)

        if self.asr_process:
            self.asr_input_queue.put(None)  # Signal shutdown
            self.asr_process.join(timeout=2.0)
            if self.asr_process.is_alive():
                self.asr_process.terminate()
    def stop_tts(self):
        self.stop_tts_event.set()
        # Drain pending TTS tasks
        while not self.tts_queue.empty():
            try: self.tts_queue.get_nowait()
            except queue.Empty: break
        # Drain pending playback
        while not self.playback_queue.empty():
            try: self.playback_queue.get_nowait()
            except queue.Empty: break
        sd.stop()


    def speak(self, text: str, emotional_state: str = "neutral"):
        """Sanitize text for TTS and enqueue. Non-blocking."""
        # Strip reasoning tags using shared helper
        text = strip_internal_reasoning(text)
        # Strip URLs
        text = re.sub(r'\(https?://.*?\)', '', text)
        text = re.sub(r'https?://\S+', '', text)
        # Normalize Unicode to ASCII equivalents
        text = text.replace('\u2014', ' -- ').replace('\u2013', ' - ')
        text = text.replace('\u2018', "'").replace('\u2019', "'")
        text = text.replace('\u201c', '"').replace('\u201d', '"')
        text = text.encode("ascii", "ignore").decode("ascii")
        text = self._numbers_to_words(text)
        text = self._symbols_to_words(text)
        text = re.sub(r'[*_#`~]', '', text)
        text = re.sub(r'[^a-zA-Z0-9\s.,!?;:\'\-"]', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()

        if not text or len(text) < 3:
            return

        self.stop_tts_event.clear()
        self.tts_queue.put((text, emotional_state))


    def _synth(self, text: str, speed: float):
        """Synthesize text to audio samples. Returns (samples, sample_rate) or None."""
        if not text:
            return None
        # Suppress noisy phonemizer fork warnings
        phon_logger = logging.getLogger("phonemizer")
        old_level = phon_logger.level
        phon_logger.setLevel(logging.ERROR)
        try:
            with self.tts_lock:
                try:
                    tts_start = time.time()
                    samples, sample_rate = self.kokoro.create(
                        text, voice=self.config.kokoro_voice, speed=speed, lang=self.config.kokoro_lang
                    )
                    tts_ms = (time.time() - tts_start) * 1000
                    logger.debug(f"pipeline_stage | stage=tts | latency_ms={tts_ms:.1f}")
                    mouth_values = []
                    return (samples, sample_rate, mouth_values)
                except Exception as e:
                    logger.error(f"synth_error | {e}")
                    return None
        finally:
            phon_logger.setLevel(old_level)
    async def _synth_stream(self, text: str, speed: float):
        """Yield (samples, sample_rate) chunks from kokoro.create_stream().

        Falls back to batch _synth() as a single-chunk generator if the
        installed kokoro-onnx version lacks create_stream.
        """
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
                text, voice=self.config.kokoro_voice, speed=speed, lang=self.config.kokoro_lang
            )
            async for samples, sr in stream:
                yield (samples, sr)
        finally:
            phon_logger.setLevel(old_level)
    def _playback_worker(self):
        """Dedicated playback thread. Plays audio sequentially; blocks on each."""
        while not self.stop_event.is_set():
            try:
                if self.stop_tts_event.is_set():
                    while not self.playback_queue.empty():
                        try: self.playback_queue.get_nowait()
                        except queue.Empty: break
                    self.stop_tts_event.clear()
                    self.tts_active.clear()

                item = self.playback_queue.get(timeout=0.1)
                samples, sample_rate, mouth_values = item
                self.is_speaking.set()
                self.tts_active.set()
                self.speech_start_time = time.time()
                # Start lip-sync thread
                def sync_mouth():
                    for mv in mouth_values:
                        if self.stop_tts_event.is_set():
                            break
                        time.sleep(0.05) # 50ms chunks

                mouth_thread = threading.Thread(target=sync_mouth, daemon=True)
                mouth_thread.start()

                sd.play(samples, samplerate=sample_rate)
                while sd.get_stream() and sd.get_stream().active:
                    if self.stop_tts_event.is_set():
                        sd.stop()
                        break
                    time.sleep(0.01)
                sd.wait()
                mouth_thread.join(timeout=0.1)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"playback_error | {e}")
            finally:
                self.is_speaking.clear()
                self.tts_active.clear()
    
    def _tts_worker_loop(self):
        """TTS synthesis worker. Streams audio chunks to the playback queue
        as they become available, enabling low-latency first-audio."""
        while not self.stop_event.is_set():
            try:
                if self.stop_tts_event.is_set():
                    while not self.tts_queue.empty():
                        self.tts_queue.get_nowait()
                    self.stop_tts_event.clear()

                item = self.tts_queue.get(timeout=0.1)
                text, emotional_state = item

                speed = 1.0
                if emotional_state == "energetic":
                    speed = 1.05
                elif emotional_state in ("sad", "calm"):
                    speed = 0.95

                # Drive async streaming synth in a fresh event loop (thread context)
                asyncio.run(self._tts_stream_and_queue(text, speed))
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"tts_worker_error | {e}")

    async def _tts_stream_and_queue(self, text: str, speed: float):
        """Consume _synth_stream and push each chunk to playback_queue."""
        async for samples, sr in self._synth_stream(text, speed):
            if self.stop_tts_event.is_set():
                break
            # playback_worker expects (samples, sr, mouth_values); use [] for streaming
            self.playback_queue.put((samples, sr, []))
    
    # ── number-to-word conversion for TTS ──────────────────────────────────
    @staticmethod
    def _number_to_words(n: int) -> str:
        """Convert integer to English words (0—999 billion)."""
        if n == 0: return "zero"
        ones = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
                "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
                "seventeen", "eighteen", "nineteen"]
        tens = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
        def _h(n: int) -> str:
            if n == 0: return ""
            if n < 20: return ones[n]
            if n < 100:
                t = tens[n // 10]
                r = n % 10
                return t + (" " + ones[r] if r else "")
            h = ones[n // 100] + " hundred"
            r = n % 100
            return h + (" " + _h(r) if r else "")
        parts = []
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
        """Convert numeric patterns (commas, currency, decimals) to English words.
        Called BEFORE _symbols_to_words so $2,000 → number→words → then $→symbol.
        """
        def _get_suffix_word(s):
            s = s.lower()
            if s == "k": return " thousand"
            if s == "m": return " million"
            if s == "b": return " billion"
            if s == "t": return " trillion"
            return ""

        def _replace_currency(m):
            raw = m.group(1).replace(",", "")
            suffix = m.group(2) if len(m.groups()) >= 2 and m.group(2) else ""
            suffix_word = _get_suffix_word(suffix)
            try:
                if "." in raw:
                    integer, fraction = raw.split(".", 1)
                    int_words = self._number_to_words(int(integer)) if integer and integer != "0" else "zero"
                    frac_digits = " ".join(self._number_to_words(int(d)) for d in fraction)
                    words = f"{int_words} point {frac_digits}"
                else:
                    n = int(float(raw))
                    words = self._number_to_words(n)
                
                return f"{words}{suffix_word} dollars" if words != "one" or suffix_word else f"{words} dollar"
            except (ValueError, IndexError):
                return m.group(0)

        def _replace_number(m):
            raw = m.group(1).replace(",", "")
            suffix = m.group(2) if len(m.groups()) >= 2 and m.group(2) else ""
            suffix_word = _get_suffix_word(suffix)
            try:
                if "." in raw:
                    integer, fraction = raw.split(".", 1)
                    int_words = self._number_to_words(int(integer)) if integer and integer != "0" else "zero"
                    frac_digits = " ".join(self._number_to_words(int(d)) for d in fraction)
                    words = f"{int_words} point {frac_digits}"
                else:
                    n = int(float(raw))
                    words = self._number_to_words(n)
                return f"{words}{suffix_word}"
            except (ValueError, IndexError):
                return m.group(0)

        # 1. Currency with optional suffixes: $965B, $1.5M, $100
        text = re.sub(r'\$(\d[\d,]*\.?\d*)\s*([BbMmTtKk])?(?!\w)', _replace_currency, text)
        
        # 2. Large numbers with suffixes: 965B, 1.5M
        text = re.sub(r'(?<!\w)(\d[\d,]*\.?\d*)\s*([BbMmTtKk])(?!\w)', _replace_number, text)

        # 3. Comma-separated numbers: 1,000,000
        text = re.sub(r'(?<!\w)(\d{1,3}(?:,\d{3})+)(?!\w)', _replace_number, text)
        
        # 4. Standalone decimals: 1.23 (if not already handled by currency/suffix)
        def _replace_decimal_simple(m):
            integer, fraction = m.group(1).replace(",", ""), m.group(2)
            try:
                int_words = self._number_to_words(int(integer)) if integer and integer != "0" else "zero"
                frac_digits = " ".join(self._number_to_words(int(d)) for d in fraction)
                return f"{int_words} point {frac_digits}"
            except ValueError: return m.group(0)
        text = re.sub(r'(?<!\d\.)(?<!\w)(\d{1,3}(?:,\d{3})*)\.(\d+)(?!\.\d)', _replace_decimal_simple, text)
        
        # 5. Long standalone numbers: 12345
        text = re.sub(r'(?<!\w)(\d{5,})(?!\.\d)(?!\w)', _replace_number, text)
        return text
    
    # Symbol-to-word mappings for {str}.translate() — maps ordinal → word
    _SYMBOL_MAP = str.maketrans({
        "%": " percent",
        "&": " and",
        "@": " at",
        "+": " plus",
        "=": " equals",
        "/": " slash ",
        "\\": " backslash ",
        ">": " greater than ",
        "<": " less than ",
    })
    def _symbols_to_words(self, text: str) -> str:
        text = text.translate(self._SYMBOL_MAP)
        # Handle degree at start of word (e.g. "25°C" → "25 degrees C")
        text = re.sub(r'(\d)\s+degrees\s+', r'\1 degrees ', text)
        return text


    def _run(self):
        samplerate = 16000
        block_size = 512
        phrase_buffer = []
        pre_roll = deque(maxlen=25) # ~0.8s pre-roll buffer
        silence_start = None
        phrase_start_time = None
        speech_frame_count = 0
        
        # Resolve input device: -1 → system default
        input_device = None if self.config.mic_index == -1 else self.config.mic_index

        def callback(indata, frames, time_info, status):
            if status:
                logger.warning(f"Audio status: {status}")
            self.audio_buffer.append(bytes(indata))

        try:
            try:
                stream = sd.RawInputStream(samplerate=samplerate, blocksize=block_size,
                                          dtype='int16', channels=1, callback=callback,
                                          device=input_device)
            except sd.PortAudioError:
                if input_device is not None and input_device != -1:
                    logger.warning(f"Mic device {input_device} invalid. Falling back to default.")
                    stream = sd.RawInputStream(samplerate=samplerate, blocksize=block_size,
                                              dtype='int16', channels=1, callback=callback)
                else:
                    raise
            with stream:
                while not self.stop_event.is_set():
                    if not self.audio_buffer:
                        time.sleep(0.01)
                        continue
                    try:
                        chunk = self.audio_buffer.popleft()
                    except IndexError:
                        continue

                    audio_int16 = np.frombuffer(chunk, dtype=np.int16)
                    pre_roll.append(audio_int16)
                    audio_float32 = audio_int16.astype(np.float32) / 32768.0
                    # Mic Health Check: detect static/dead signal
                    rms = np.sqrt(np.mean(audio_float32**2))
                    if self._rms_callback:
                        try:
                            self._rms_callback(rms)
                        except Exception:
                            pass
                    if not hasattr(self, '_rms_history'):
                        self._rms_history = deque(maxlen=50)
                    self._rms_history.append(rms)
                    if len(self._rms_history) == 50:
                        std_rms = np.std(self._rms_history)
                        if std_rms < 0.0001 and rms > 0.05:
                            if not getattr(self, '_static_warned', False):
                                logger.error("MIC ALERT: Signal is STATIC/DEAD (hum detected). Speech detection will fail.")
                                self._static_warned = True
                        else:
                            self._static_warned = False

                    
                    # --- Silero VAD ---
                    with torch.no_grad():
                        vad_confidence = self.vad_model(torch.from_numpy(audio_float32), samplerate).item()

                    # Contextual VAD threshold: higher during TTS to avoid echo
                    if self.tts_active.is_set():
                        vad_threshold = 0.60
                        required_vad_frames = 10  # ~300 ms sustained speech
                    else:
                        vad_threshold = 0.40
                        required_vad_frames = 3   # ~90 ms

                    if vad_confidence > vad_threshold:
                        speech_frame_count += 1
                    else:
                        speech_frame_count = 0

                    # Require sustained VAD frames to start a phrase
                    if speech_frame_count >= required_vad_frames:
                        if not phrase_buffer:
                            phrase_start_time = time.time()
                            # Add pre-roll to the start of the phrase to catch initial words
                            phrase_buffer.extend(list(pre_roll))
                            pre_roll.clear()
                        phrase_buffer.append(audio_int16)
                        silence_start = None
                    else:
                        if phrase_buffer:
                            phrase_buffer.append(audio_int16) # Don't drop audio during gaps!
                            if silence_start is None:
                                silence_start = time.time()
                            duration = time.time() - phrase_start_time
                            silence_duration = time.time() - silence_start

                            base = self.config.vad_silence_timeout
                            if duration < 2.0:
                                dynamic_silence_timeout = base
                            elif duration < 5.0:
                                dynamic_silence_timeout = base * 0.67
                            else:
                                dynamic_silence_timeout = base * 0.50

                            if silence_duration > dynamic_silence_timeout or duration > self.config.phrase_max_duration:

                                full_phrase = np.concatenate(phrase_buffer)
                                phrase_buffer = []
                                silence_start = None

                                if duration >= self.config.phrase_min_duration:
                                    threading.Thread(target=self._process_phrase, args=(full_phrase,), daemon=True).start()

                            elif silence_duration > 0.4 and duration > 2.0:
                                # Snapshot current buffer for warm-up ASR, keep collecting
                                # Debounce: max 1 warmup submission per second
                                if time.time() - self._last_warmup_time >= 1.0 and phrase_buffer:
                                    self._last_warmup_time = time.time()
                                    warmup_phrase = np.concatenate(phrase_buffer)
                                    threading.Thread(
                                        target=self._process_phrase,
                                        args=(warmup_phrase, {"is_warmup": True}),
                                        daemon=True
                                    ).start()

                    time.sleep(0.001)
        except sd.PortAudioError as e:
            logger.error(f"Mic fatal error: {e}")
            return
        except Exception as e:
            logger.error(f"InputStream error: {e}")
    def _asr_poller_loop(self):
        respawn_count = 0
        max_respawns = 10
        while not self.stop_event.is_set():
            try:
                # Check if process is still alive
                if not self.asr_process.is_alive():
                    if respawn_count >= max_respawns:
                        logger.error(f"ASR worker died {max_respawns} times. Giving up.")
                        break
                    respawn_count += 1
                    backoff = min(respawn_count * 2, 30)
                    logger.warning(f"ASR worker died. Respawning in {backoff}s... (attempt {respawn_count}/{max_respawns})")
                    time.sleep(backoff)
                    # Drain stale items from input queue to prevent immediate shutdown
                    drained = 0
                    while not self.asr_input_queue.empty():
                        try:
                            self.asr_input_queue.get_nowait()
                            drained += 1
                        except Exception:
                            break
                    if drained:
                        logger.info(f"Drained {drained} stale items from ASR input queue")
                    # Drain stale output
                    while not self.asr_output_queue.empty():
                        try:
                            self.asr_output_queue.get_nowait()
                        except Exception:
                            break
                    self.asr_process = mp.Process(
                        target=asr_worker_process,
                        args=(
                            self.asr_input_queue,
                            self.asr_output_queue,
                            self.config.whisper_model,
                            self.config.gpu_device,
                            self.config.default_language
                        ),
                        daemon=True
                    )
                    self.asr_process.start()
                    respawn_count = 0  # Reset on successful restart

                # Poll for results with a timeout
                try:
                    result = self.asr_output_queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                if len(result) == 3:
                    text, confidence, flags = result
                else:
                    text, confidence = result
                    flags = {}
                is_final = not flags.get("is_warmup", False)
                if is_final:
                    if text:
                        logger.info(f"stt_result | {text} ({confidence:.2f})")
                        with self._warmup_lock:
                            self._pending_warmup_text = ""
                        self.on_speech(text)
                else:
                    if text:
                        logger.info(f"warmup_asr | {text} ({confidence:.2f})")
                        with self._warmup_lock:
                            self._pending_warmup_text = text
            except Exception as e:
                logger.error(f"asr_poller_loop_error | {e}")
                time.sleep(1)


    def _process_phrase(self, audio_data, flags=None):
        try:
            flags = flags or {}
            # For final (non-warmup) calls, seed with warm-up text
            if not flags.get("is_warmup") and self._pending_warmup_text:
                with self._warmup_lock:
                    flags["warmup_context"] = self._pending_warmup_text
            # Serialize audio data and put on queue
            # Ensure it's float32 for Whisper
            audio_data_f32 = audio_data.astype(np.float32) / 32768.0
            self.asr_input_queue.put((audio_data_f32.tobytes(), 16000, flags))
        except Exception as e:
            logger.error(f"process_phrase_error | {e}")
