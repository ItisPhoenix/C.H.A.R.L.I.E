import logging
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
from charlie.wake_word import WakeWordEngine
import onnxruntime as ort
ort.set_default_logger_severity(3)

logger = logging.getLogger("charlie.voice")

class VoiceEngine:
    def __init__(self, config, on_speech: Callable[[str], None], on_wake_word: Optional[Callable[[], None]] = None):
        self.config = config
        self.on_speech = on_speech
        self.on_wake_word = on_wake_word
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

        # Wake Word state
        self._wake_word_engine: Optional[WakeWordEngine] = None
        self._wake_listener_thread: Optional[threading.Thread] = None
        self._listening_active = threading.Event()  # gated by wake word
        self._smart_mode_timer: Optional[threading.Timer] = None
        self._smart_mode_timeout: float = config.smart_mode_timeout

        # Initialize Models
        self._ensure_models()

        # 1. LOCAL VAD (Silero)
        try:
            self.vad_model, self.vad_utils = torch.hub.load(
                repo_or_dir='snakers4/silero-vad:v3.1',
                model='silero_vad',
                force_reload=False,
                onnx=True,
                trust_repo=True
            )
            logger.info("VAD loaded from local cache.")
        except Exception as e:
            logger.warning(f"VAD local load failed: {e}")
            self.vad_model, self.vad_utils = torch.hub.load('snakers4/silero-vad:v3.1', 'silero_vad', onnx=True)

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
            import time
            time.sleep(1)  # Wait for worker to load model
            silent_audio = np.zeros(16000, dtype=np.float32).tobytes()
            self.asr_input_queue.put((silent_audio, 16000))
            logger.info("ASR warm-up: sent silent audio.")
        except Exception as e:
            logger.warning(f"ASR warm-up failed: {e}")
        # 3. LOCAL TTS (Kokoro) — CUDA GPU accelerated
        os.environ.setdefault("ONNX_PROVIDER", "CUDAExecutionProvider")
        model_path = os.path.join(config.kokoro_model_dir, "kokoro-v1.0.onnx")
        voices_path = os.path.join(config.kokoro_model_dir, "voices-v1.0.bin")
        self.kokoro = Kokoro(model_path, voices_path)
        logger.info("Kokoro TTS initialized locally.")
        # Warm up ONNX graph
        try:
            self.kokoro.create("Warm up", voice=self.config.kokoro_voice, speed=1.0, lang=self.config.kokoro_lang)
            logger.info("Kokoro warm-up: ONNX graph compiled.")
        except Exception as e:
            logger.warning(f"Kokoro warm-up failed: {e}")
        # Use thread-safe deque for audio chunks
        self.audio_buffer = deque(maxlen=200)
        self.processing_thread = None

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

        # Wake Word engine (if enabled and model exists)
        if self.config.enable_wake_word:
            self._start_wake_word_listener()
        else:
            # Without wake word, listen continuously (push-to-talk / always-on)
            self._listening_active.set()
            logger.info("Wake word disabled — continuous listening mode.")

    def _start_wake_word_listener(self):
        model_path = self.config.wake_word_model_path
        if not os.path.exists(model_path):
            logger.warning(
                "Wake word model not found at %s — falling back to continuous listening.",
                model_path,
            )
            self._listening_active.set()
            return

        self._wake_word_engine = WakeWordEngine(
            model_path=model_path,
            sensitivity=self.config.wake_word_sensitivity,
        )

        if not self._wake_word_engine.is_available:
            logger.warning("Wake word engine unavailable — continuous listening.")
            self._listening_active.set()
            return


        self._wake_word_engine.listen(self._on_wake_word_detected)
        logger.info("Wake word listener active.")

    def stop(self):
        self.stop_event.set()
        self.stop_tts()
        if self.processing_thread and self.processing_thread.is_alive():
            self.processing_thread.join(timeout=5.0)

        if self._wake_word_engine:
            self._wake_word_engine.stop()
        self._cancel_smart_mode_timer()

        if self.asr_process:
            self.asr_input_queue.put(None) # Signal shutdown
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

    # ── Procedural Audio ───────────────────────────────────────────────────
    @staticmethod
    def _gen_chime(freq_start: float = 440, freq_end: float = 880,
                   duration: float = 0.25, sample_rate: int = 24000,
                   decay: float = 3.0) -> np.ndarray:
        """Generate a short procedural sine-wave chime (no external files needed)."""
        t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
        freq = np.linspace(freq_start, freq_end, len(t))
        envelope = np.exp(-decay * t / duration)  # exponential decay
        tone = np.sin(2 * np.pi * freq * t) * envelope
        return (tone * 0.3).astype(np.float32)

    def play_wake_chime(self, tone: str = "gentle"):
        """Play a short wake-chime asynchronously."""
        if tone == "gentle":
            samples = self._gen_chime(440, 880, 0.25, decay=3.0)
        else:
            samples = self._gen_chime(523, 1047, 0.3, decay=2.0)
        threading.Thread(target=lambda: sd.play(samples, samplerate=24000), daemon=True).start()

    def play_smart_mode_exit_chime(self):
        """Play a descending 'power down' chime when smart mode ends."""
        samples = self._gen_chime(660, 330, 0.3, decay=2.0)
        threading.Thread(target=lambda: sd.play(samples, samplerate=24000), daemon=True).start()

    # ── Wake Word Handler ───────────────────────────────────────────────────
    def _on_wake_word_detected(self):
        """Called by WakeWordEngine when wake word is detected (audio thread)."""
        if self.stop_event.is_set():
            return

        # Barge-in: if speaking, stop TTS immediately
        if self.is_speaking.is_set():
            logger.info("Barge-in detected. Stopping TTS. Starting ASR...")
            self.stop_tts()

        # Activate listening
        self._listening_active.set()

        # Play wake chime
        self.play_wake_chime()

        # Cancel any pending smart mode timeout
        self._cancel_smart_mode_timer()

        if self.on_wake_word:
            try:
                self.on_wake_word()
            except Exception as e:
                logger.error(f"on_wake_word callback error: {e}")

    # ── Smart Mode ───────────────────────────────────────────────────────────
    def _cancel_smart_mode_timer(self):
        if self._smart_mode_timer:
            self._smart_mode_timer.cancel()
            self._smart_mode_timer = None

    def _start_smart_mode_timer(self):
        """After TTS finishes, start a timer to exit smart mode after silence."""
        self._cancel_smart_mode_timer()
        self._smart_mode_timer = threading.Timer(
            self._smart_mode_timeout, self._on_smart_mode_timeout
        )
        self._smart_mode_timer.daemon = True
        self._smart_mode_timer.start()

    def _on_smart_mode_timeout(self):
        """Called when smart mode idle timer expires."""
        logger.info("Smart mode timeout — no follow-up detected.")
        self._listening_active.clear()
        self.play_smart_mode_exit_chime()

    def speak(self, text: str, emotional_state: str = "neutral"):
        # Strip tool calls and thinking tags
        text = re.sub(r'TOOL:\s*\w+\(".*?"\)', '', text)
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
        text = re.sub(r'<(thought|thinking)>.*?</\1>', '', text, flags=re.DOTALL | re.IGNORECASE)
        # Strip URLs
        text = re.sub(r'\(https?://.*?\)', '', text)
        text = re.sub(r'https?://\S+', '', text)
        # Strip markdown characters
        text = re.sub(r'[*_#`~]', '', text)
        # Convert numbers→words BEFORE symbol mapping (catches $2,000 patterns)
        text = self._numbers_to_words(text)
        # Convert symbols → spoken words BEFORE ASCII/regex strip
        text = self._symbols_to_words(text)
        # Remove non-ASCII (em-dashes etc → nearest equivalent)
        text = text.replace('\u2014', ' -- ').replace('\u2013', ' - ')
        text = text.replace('\u2018', "'").replace('\u2019', "'")
        text = text.replace('\u201c', '"').replace('\u201d', '"')
        text = text.encode("ascii", "ignore").decode("ascii")
        # Keep letters, digits, spaces, and basic punctuation
        text = re.sub(r'[^a-zA-Z0-9\s.,!?;:\'\-"]', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        
        if not text or len(text) < 3:
            return
            
        self.stop_tts_event.clear()
        self.tts_queue.put((text, emotional_state))


    def _synth(self, text: str, speed: float):
        """Synthesize text to audio samples. Returns (samples, sample_rate) or None."""
        text = self._sanitize_for_tts(text)
        if not text:
            return None
        # Suppress noisy phonemizer fork warnings
        phon_logger = logging.getLogger("phonemizer")
        old_level = phon_logger.level
        phon_logger.setLevel(logging.ERROR)
        with self.tts_lock:
            try:
                tts_start = time.time()
                samples, sample_rate = self.kokoro.create(
                    text, voice=self.config.kokoro_voice, speed=speed, lang=self.config.kokoro_lang
                )
                tts_ms = (time.time() - tts_start) * 1000
                logger.debug(f"pipeline_stage | stage=tts | latency_ms={tts_ms:.1f}")
                return (samples, sample_rate)
            except Exception as e:
                logger.error(f"synth_error | {e}")
                return None
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

                item = self.playback_queue.get(timeout=0.1)
                samples, sample_rate = item
                self.is_speaking.set()
                sd.play(samples, samplerate=sample_rate)
                while sd.get_stream() and sd.get_stream().active:
                    if self.stop_tts_event.is_set():
                        sd.stop()
                        break
                    time.sleep(0.01)
                sd.wait()
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"playback_error | {e}")
            finally:
                self.is_speaking.clear()
                # After TTS completes, start smart mode timer (for wake-word mode)
                if self.config.enable_wake_word and self._listening_active.is_set():
                    self._start_smart_mode_timer()
    
    def _tts_worker_loop(self):
        """TTS synthesis worker. Overlaps with playback worker: as soon as
        one sentence is synthesized it's queued for playback and the next
        sentence begins synthesizing immediately."""
        while not self.stop_event.is_set():
            try:
                if self.stop_tts_event.is_set():
                    while not self.tts_queue.empty():
                        self.tts_queue.get_nowait()
                    self.stop_tts_event.clear()
                
                item = self.tts_queue.get(timeout=0.1)
                text, emotional_state = item
                
                speed = 1.0
                if emotional_state == "energetic": speed = 1.05
                elif emotional_state in ["sad", "calm"]: speed = 0.95
                
                audio = self._synth(text, speed)
                if audio is not None:
                    self.playback_queue.put(audio)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"tts_worker_error | {e}")
    
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
        def _replace_currency(m):
            raw = m.group(1).replace(",", "")
            try:
                n = int(float(raw))
                words = self._number_to_words(n) if n else "zero"
                return words + " dollars" if n != 1 else words + " dollar"
            except ValueError:
                return m.group(0)
        def _replace_number(m):
            raw = m.group(0).replace(",", "")
            try:
                n = int(float(raw))
                return self._number_to_words(n) if n else "zero"
            except ValueError:
                return m.group(0)
        def _replace_decimal(m):
            integer = m.group(1).replace(",", "")
            fraction = m.group(2)
            try:
                int_words = self._number_to_words(int(integer)) if int(integer) else "zero"
            except ValueError:
                int_words = integer
            # Each fraction digit → word ("14" → "one four")
            frac_digits = " ".join(self._number_to_words(int(d)) if d != "0" else "zero" for d in fraction)
            return f"{int_words} point {frac_digits}"
        text = re.sub(r'\$(\d[\d,]*\.?\d*)', _replace_currency, text)
        text = re.sub(r'(?<!\w)\d{1,3}(?:,\d{3})+(?!\w)', _replace_number, text)
        text = re.sub(r'(?<!\d\.)(?<!\w)(\d{1,3}(?:,\d{3})*)\.(\d+)(?!\.\d)', _replace_decimal, text)
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
    def _sanitize_for_tts(self, text: str) -> str:
        """Final pass before phonemizer — convert symbols → words, keep punctuation for prosody."""
        text = self._numbers_to_words(text)
        text = self._symbols_to_words(text)

        text = re.sub(r'[*_#`~]', '', text)
        text = re.sub(r'[^a-zA-Z0-9\s.,!?;:\'\-"]', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text


    def _run(self):
        samplerate = 16000
        block_size = 512
        phrase_buffer = []
        silence_start = None
        phrase_start_time = None
        
        # Resolve input device: -1 → system default
        input_device = None if self.config.mic_index < 0 else self.config.mic_index
        
        
        def callback(indata, frames, time_info, status):
            if status:
                logger.warning(f"Audio status: {status}")
            self.audio_buffer.append(bytes(indata))

        try:
            with sd.RawInputStream(samplerate=samplerate, blocksize=block_size,
                                   dtype='int16', channels=1, callback=callback,
                                   device=input_device):
                while not self.stop_event.is_set():
                    if not self.audio_buffer:
                        time.sleep(0.01)
                        continue
                    
                    try:
                        chunk = self.audio_buffer.popleft()
                    except IndexError:
                        continue

                    audio_int16 = np.frombuffer(chunk, dtype=np.int16)
                    audio_float32 = audio_int16.astype(np.float32) / 32768.0

                    # Skip VAD phrase capture if not actively listening (wake word not triggered)
                    if not self._listening_active.is_set():
                        continue

                    # Ignore VAD if Charlie is speaking (prevents self-interruption from echo)
                    if self.is_speaking.is_set():
                        continue

                    with torch.no_grad():
                        confidence = self.vad_model(torch.from_numpy(audio_float32), samplerate).item()

                    if confidence > 0.5:
                        if not phrase_buffer:
                            phrase_start_time = time.time()
                            if self.is_speaking.is_set():
                                self.stop_tts()
                        phrase_buffer.append(audio_int16)
                        silence_start = None
                    else:
                        if phrase_buffer:
                            if silence_start is None:
                                silence_start = time.time()

                            duration = time.time() - phrase_start_time
                            silence_duration = time.time() - silence_start

                            if silence_duration > self.config.silence_timeout or duration > self.config.phrase_max_duration:
                                full_phrase = np.concatenate(phrase_buffer)
                                phrase_buffer = []
                                silence_start = None

                                if duration >= self.config.phrase_min_duration:
                                    # Process in a separate thread to avoid blocking VAD loop
                                    threading.Thread(target=self._process_phrase, args=(full_phrase,), daemon=True).start()
                                    # Reset smart mode timer on new user speech
                                    self._cancel_smart_mode_timer()
                                    self._start_smart_mode_timer()

                    time.sleep(0.001)
        except Exception as e:
            logger.error(f"InputStream error: {e}")
    def _asr_poller_loop(self):
        while not self.stop_event.is_set():
            try:
                # Check if process is still alive
                if not self.asr_process.is_alive():
                    logger.warning("ASR worker process died. Respawning...")
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

                # Poll for results with a timeout
                try:
                    text, confidence = self.asr_output_queue.get(timeout=0.05)
                    if text:
                        logger.info(f"stt_result | {text} ({confidence:.2f})")
                        self.on_speech(text)
                except queue.Empty:
                    continue
            except Exception as e:
                logger.error(f"asr_poller_loop_error | {e}")
                time.sleep(1)


    def _process_phrase(self, audio_data):
        try:
            # Serialize audio data and put on queue
            # Ensure it's float32 for Whisper
            audio_data_f32 = audio_data.astype(np.float32)
            self.asr_input_queue.put((audio_data_f32.tobytes(), 16000))
        except Exception as e:
            logger.error(f"process_phrase_error | {e}")
