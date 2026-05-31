import collections
import math
import multiprocessing
import os
import queue
import re
import signal
import threading
import time
from multiprocessing import Queue

import numpy as np
import openwakeword
import pythoncom

# CPU affinity for Wake Word will be set locally during load_models.
import sounddevice as sd
import webrtcvad
from scipy.signal import butter, lfilter, resample_poly

from charlie.config import settings
from charlie.utils.logger import get_logger
from charlie.utils.volume import VolumeController

logger = get_logger(__name__)


def _playback_worker(audio_q: multiprocessing.Queue, device_index: int, sample_rate: int, channels: int, playback_active: multiprocessing.Value):
    """
    Standalone process for audio playback.
    Isolated from main sensory loop to prevent library deadlocks during barge-in.
    """
    import queue

    import sounddevice as sd

    # Block signals to prevent semi-dead states
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    try:
        # Lower process priority to give main engine headroom
        import psutil
        p = psutil.Process(os.getpid())
        if os.name == 'nt':
            p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        else:
            p.nice(10)

        with sd.OutputStream(
            samplerate=sample_rate,
            channels=channels,
            device=device_index,
            blocksize=1024,
            latency="low"
        ) as stream:
            while True:
                try:
                    # Polling get with timeout to allow flag reset
                    audio_data = audio_q.get(timeout=0.1)
                    if audio_data is None:
                        break # Shutdown sentinel

                    if playback_active is not None:
                        playback_active.value = 1
                    stream.write(audio_data)
                except queue.Empty:
                    if playback_active is not None:
                        playback_active.value = 0
                    continue
                except EOFError:
                    break
                except Exception:
                    continue

            if playback_active is not None:
                playback_active.value = 0
    except Exception:
        pass


class LocalTransport:
    def __init__(self, engine: "AudioEngine"):
        self.engine = engine

    def start_local_loop(self):
        """Runs the original AudioEngine.run() logic (blocking)."""
        self.engine._run_original()

    def publish_mic_frame(self, frame: np.ndarray):
        pass  # handled internally by _run_original's stream callback


class AudioEngine:
    """
    Lean, high-fidelity sensory core.
    Optimized for zero-latency communication and hardware-matched resampling.
    """

    def __init__(
        self,
        brain_task_q: Queue,
        tts_q: Queue,
        status_q: Queue,
        audio_cmd_q: Queue,
        heartbeat=None,
        interrupt_event=None,
    ):
        self.brain_task_q = brain_task_q
        self.tts_q = tts_q
        self.status_q = status_q
        self.audio_cmd_q = audio_cmd_q
        self.heartbeat = heartbeat
        self.interrupt_event = interrupt_event

        self.buffer_lock = threading.Lock()
        self.stt_lock = threading.Lock()
        self.input_rate = settings.audio.sample_rate
        self.target_rate = settings.audio.target_rate
        self.current_gain = 1.0  # UNITY: Fixes distortion. STT handles its own boost.
        self.frame_duration = 80
        self.hw_output_rate = 24000

        self.device_index = settings.audio.mic_index
        self.output_index = settings.audio.output_index
        self.channels = 1
        self.block_size = int(self.input_rate * self.frame_duration / 1000)

        self.is_listening = False
        self.audio_buffer = []
        self.verif_buffer = collections.deque(maxlen=40)
        self.pending_wake = False
        self.silence_limit = settings.audio.silence_limit
        self.silence_frames = 0
        self.active_idle_frames = 0
        self.max_silence = int(self.silence_limit * 1000 / self.frame_duration)

        self.vad = webrtcvad.Vad(settings.audio.vad_mode)
        nyq = 0.5 * self.target_rate
        self.b_hp, self.a_hp = butter(4, 100 / nyq, btype="high")

        self.last_wake_time = 0
        self.last_diagnostic_time = 0
        self.is_speaking = False
        self.is_thinking = False
        self._speech_hysteresis = 0
        self.conversation_active = False
        self.stt_ready = False
        self.stt_in_progress = False
        self.awaiting_brain = False
        self.running = True
        self.standby_mode = False

        self.last_output_peak = 0.0
        self._active_stream = None  # Live OutputStream ref for interrupt watcher
        self.stt_model = None
        self.ww_model = None
        self.tts_model = None
        self.input_q = queue.Queue()
        self._ww_inference_chunks = 3  # Increase chunks for better cadence capture
        self._stt_task_q = queue.Queue()  # Persistent STT worker queue (replaces thread-per-call)
        # SPEED: Pre-compute resample decision at init time — not on every frame
        self._needs_resample = self.input_rate != self.target_rate
        if self._needs_resample:
            _common = math.gcd(self.target_rate, self.input_rate)
            self._resample_up = self.target_rate // _common
            self._resample_down = self.input_rate // _common
            logger.info(
                f"audio_resample_init | up={self._resample_up} | down={self._resample_down}"
            )

        self.volume = VolumeController(
            steps=settings.audio.duck_steps
            if hasattr(settings.audio, "duck_steps")
            else 8
        )

        # New: Multiprocessing Audio Playback
        self._playback_q = multiprocessing.Queue(maxsize=50)
        self._playback_active = multiprocessing.Value('i', 0)
        # Attach to queue for easy passing to worker
        self._playback_q.playback_active = self._playback_active
        self._playback_proc = None

    def _normalize_text(self, text: str) -> str:
        """Cleans text for TTS synthesis (removes markdown, RSS metadata, URLs)."""
        if not text:
            return ""

        # 1. Remove code blocks entirely
        text = re.sub(r'```[\s\S]*?```', '[code block]', text)

        # 2. Remove RSS/news metadata patterns
        text = re.sub(r'Source:\s*link\s+to\s+\S+[\s,]*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\bTopic:\s*[A-Z_]+\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\bSummary:\s*', '', text, flags=re.IGNORECASE)

        # 3. Remove markdown headers (###, ##, #)
        text = re.sub(r'#{1,6}\s+', '', text)

        # 4. Handle URLs - replace with domain name only
        def url_repl(match):
            from urllib.parse import urlparse
            url = match.group(0)
            try:
                domain = urlparse(url).netloc
                return domain
            except Exception:
                return ""
        text = re.sub(r'https?://\S+', url_repl, text)

        # 5. Clean markdown symbols
        text = text.replace("*", "").replace("_", "").replace("`", "")

        # 6. Clean bullet points for better flow
        text = re.sub(r'^\s*[-*]\s+', ' ', text, flags=re.MULTILINE)

        # 7. Remove "link to" leftovers from previous URL processing
        text = re.sub(r'\blink\s+to\s+\S+', '', text, flags=re.IGNORECASE)

        # 8. Handle decimals in numbers (e.g., 3.14 -> 3 point 14)
        text = re.sub(r'(\d+)\.(\d+)', r'\1 point \2', text)

        # 9. Final cleanup of excessive whitespace and dashes
        text = re.sub(r'\s*[-–—]\s*', '. ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        text = re.sub(r'\.\s*\.', '.', text)  # Remove double dots
        text = re.sub(r'^\.\s*', '', text)  # Remove leading dot

        return text

    def _ensure_playback_proc(self):
        """Starts the playback process if dead/stopped."""
        if self._playback_proc is None or not self._playback_proc.is_alive():
            self._playback_proc = multiprocessing.Process(
                target=_playback_worker,
                args=(
                    self._playback_q,
                    self.output_index,
                    24000,
                    2 if self.channels > 1 else 1,
                    self._playback_active,
                ),
                daemon=True,
            )
            self._playback_proc.start()
            logger.info(f"playback_process_started | pid={self._playback_proc.pid}")

    def run(self):
        pythoncom.CoInitialize()
        self.load_models()
        logger.info("audio_engine_ignition")

        self.transport = LocalTransport(self)
        self.transport.start_local_loop()

    def load_models(self):
        logger.info("audio_models_loading")
        # Initialize CUDA/cuDNN paths for Windows DLL resolution before libraries load
        from charlie.utils.cuda_helper import setup_cuda_paths

        setup_cuda_paths()

        # Defer heavy imports until DLL paths are registered
        from faster_whisper import WhisperModel

        try:
            # 1. Perception (Whisper)
            stt_model_name = getattr(settings.audio, "stt_model", "tiny.en")
            device = getattr(settings.audio, "stt_device", "cpu")
            try:
                self.stt_model = WhisperModel(
                    stt_model_name,
                    device=device,
                    compute_type="int8_float16" if device == "cuda" else "int8",
                )
            except Exception as e:
                logger.warning(
                    f"stt_load_fail | model={stt_model_name} | device={device} | error={e} | falling_back_to_tiny_cpu"
                )
                self.stt_model = WhisperModel(
                    "tiny.en", device="cpu", compute_type="float32"
                )

            # 2. Wake-Word (openWakeWord) - Forced to CPU locally for stability
            try:
                import onnxruntime as ort

                ort.set_default_logger_severity(3)

                # STRICT: Use only the model Sir provided
                ww_paths = [os.path.abspath(p) for p in settings.audio.wakeword_models]
                for p in ww_paths:
                    if not os.path.isfile(p):
                        logger.error(f"wakeword_model_missing | path={p}")
                        raise FileNotFoundError(f"Wakeword model not found: {p}")

                self.ww_model = openwakeword.Model(wakeword_model_paths=ww_paths)
                logger.info(f"wake_model_loaded | model=Charlie | paths={ww_paths}")

            except Exception as e:
                logger.error(f"ww_cuda_init_failed | {e}")
                if "cudnn" in str(e).lower() or "cuda" in str(e).lower():
                    logger.warning("attempting_cpu_fallback_after_cuda_error")
                    # Disable CUDA for this process and retry once as last resort
                    # Fallback handled via re-init without cuda params if necessary
                    ww_paths = [
                        os.path.abspath(p) for p in settings.audio.wakeword_models
                    ]
                    self.ww_model = openwakeword.Model(wakeword_model_paths=ww_paths)
                else:
                    raise

            # 3. Vocal Synthesis (Kokoro-82M ONNX) — GPU-accelerated
            import onnxruntime as ort
            from kokoro_onnx import Kokoro
            kokoro_model = os.getenv("KOKORO_MODEL_PATH", "charlie/models/kokoro-v1.0.onnx")
            kokoro_voices = os.getenv("KOKORO_VOICES_PATH", "charlie/models/voices-v1.0.bin")

            # Try CUDA first, fall back to CPU
            try:
                opts = ort.SessionOptions()
                opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                sess = ort.InferenceSession(
                    kokoro_model, opts,
                    providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
                )
                self.tts_model = Kokoro.from_session(sess, kokoro_voices)
                logger.info("kokoro_gpu_ready | provider=CUDAExecutionProvider")
            except Exception as e:
                logger.warning(f"kokoro_gpu_fail | error={e} | falling_back_to_cpu")
                self.tts_model = Kokoro(kokoro_model, kokoro_voices)

            self.kokoro_voice = getattr(settings.audio, "kokoro_voice", "af_heart")
            self.kokoro_speed = getattr(settings.audio, "kokoro_speed", 1.0)
            self.kokoro_lang = getattr(settings.audio, "kokoro_lang", "en-us")
            logger.info(f"audio_kokoro_ready | voice={self.kokoro_voice} speed={self.kokoro_speed}")

            # 4. Warm-up inferences (first call is slow due to CUDA kernel compilation)
            logger.info("audio_warmup_starting")
            _warmup_audio, _ = self.tts_model.create("test", voice=self.kokoro_voice, speed=1.0, lang=self.kokoro_lang)
            dummy = np.zeros(16000, dtype=np.float32)
            _segs, _ = self.stt_model.transcribe(dummy, beam_size=1, best_of=1, language="en")
            list(_segs)  # exhaust generator
            logger.info("audio_warmup_complete")

            logger.info("audio_models_ready | all_systems_go")

        except Exception as e:
            err_msg = (
                f"Sir, my audio sensory systems failed to initialize. Error: {str(e)}"
            )
            logger.error("audio_model_load_failed", error=str(e))
            self.tts_q.put({"type": "SPEAK", "content": err_msg})
            self.running = False

    def audio_callback(self, indata, frames, time_info, status):
        # Immediate interrupt check
        if self.interrupt_event and self.interrupt_event.is_set():
            self.audio_buffer = []
            self.is_listening = False
            self.is_speaking = False  # FIXED: Stops TTS playback on interrupt
            self.silence_frames = 0
            return

        if status:
            logger.warning(f"audio_io_warn | {status}")
        self.input_q.put(indata.copy())

    def _sensory_processor(self):
        """Dedicated thread for signal processing, VAD, and Wake-Word detection."""
        pythoncom.CoInitialize()
        logger.info(f"sensory_processor_online | mic_index={self.device_index}")
        while self.running:
            try:
                indata = self.input_q.get(timeout=0.5)
            except queue.Empty:
                continue

            if indata.ndim > 1:
                indata_mono = indata[:, 0]
            else:
                indata_mono = indata.flatten()

            if self._needs_resample:
                audio_f32 = resample_poly(
                    indata_mono.astype(np.float32),
                    self._resample_up,
                    self._resample_down,
                )
            else:
                audio_f32 = indata_mono.astype(np.float32)

            # Robust normalization: Peak-normalize if signal is out of bounds or in int16 range
            peak_raw = np.max(np.abs(audio_f32))
            if peak_raw > 1.0:
                audio_f32 /= 32768.0 if peak_raw > 100.0 else peak_raw

            audio_16k = audio_f32

            # Use a clean linear gain. Avoid tanh/filters on small chunks without state
            # as they introduce artifacts that ruin wake-word recognition.
            audio_boosted = np.clip(audio_16k * self.current_gain, -1.0, 1.0)

            audio_ww = audio_boosted
            # Apply Butterworth high-pass filter to remove DC offset and low-frequency noise
            if len(audio_ww) > 0:
                audio_filtered = lfilter(self.b_hp, self.a_hp, audio_ww)
            else:
                audio_filtered = audio_ww

            rms = np.sqrt(np.mean(audio_16k**2))
            now = time.time()

            # IPC Telemetry (20Hz for smooth UI animation)
            if not hasattr(self, "_last_telemetry_time"):
                self._last_telemetry_time = 0.0
                self._last_config_reload = now

            # Periodic config reload from JSON (every 60s)
            if now - self._last_config_reload > 60:
                from charlie.config import load_json_overrides

                load_json_overrides()
                self._last_config_reload = now

            peak = np.max(np.abs(audio_boosted))

            if now - self._last_telemetry_time > 0.05:
                # Digital clipping warning
                if peak >= 0.99 and not self.is_speaking:
                    logger.warning(
                        f"audio_signal_clipping | peak={peak:.4f} | rms={rms:.4f}"
                    )
                    # Auto-attenuate to prevent distortion
                    self.current_gain = max(0.5, self.current_gain * 0.85)
                    logger.info(f"gain_adjusted | new_gain={self.current_gain:.2f}")

                # Use peak for snappier animations
                val = float(peak * 1.2)

                # WAVEFORM: 64 bins for radial ring spikes
                # Optimized: simple slice/step downsampling for performance
                step = max(1, len(audio_boosted) // 64)
                waveform = (np.abs(audio_boosted[::step][:64])).tolist()
                # Fill zeros if chunk was too small
                if len(waveform) < 64:
                    waveform.extend([0.0] * (64 - len(waveform)))

                if (val > 0.005 or self.conversation_active) and not self.standby_mode:
                    self.status_q.put(
                        {"type": "VOICE_ACTIVITY", "peak": val, "waveform": waveform}, block=False
                    )
                self._last_telemetry_time = now

            audio_i16 = (np.clip(audio_boosted, -1.0, 1.0) * 32767).astype(np.int16)
            self.verif_buffer.append(audio_i16)

            # Use 20ms chunks for VAD compatibility
            chunk_size_vad = int(self.target_rate * 20 / 1000)

            is_speech = False
            try:
                # Optimized sensitivity: floor lowered to 0.01 to capture soft speech
                if rms > 0.01:
                    for i in range(0, len(audio_i16), chunk_size_vad):
                        chunk = audio_i16[i : i + chunk_size_vad]
                        if len(chunk) == chunk_size_vad:
                            if self.vad.is_speech(chunk.tobytes(), self.target_rate):
                                is_speech = True
                                break
            except Exception as e:
                logger.error(f"vad_error | {e}")

            # VAD HYSTERESIS: Keep 'is_speech' true for 8 frames (~640ms) after it ends to bridge gaps.
            if is_speech:
                self._speech_hysteresis = 8
            elif hasattr(self, "_speech_hysteresis") and self._speech_hysteresis > 0:
                is_speech = True
                self._speech_hysteresis -= 1

            # EFFECTIVE SPEAKING STATE: Logical speaking OR physical playback
            effective_speaking = self.is_speaking or (self._playback_active.value == 1)

            if effective_speaking:
                # TOUGH INTERRUPT: Configurable sensitivity for barge-in
                sensitivity = getattr(settings.audio, "barge_in_sensitivity", 0.6)
                ref_threshold = sensitivity
                peak = np.max(np.abs(audio_filtered))

                # Must be clear speech AND above high threshold to stop her
                # Added cooldown to prevent rapid-fire interrupts
                now = time.time()
                if not hasattr(self, '_last_barge_in'):
                    self._last_barge_in = 0
                if ((is_speech and peak > ref_threshold) or (peak > 0.90)) and (now - self._last_barge_in > 1.0):
                    self._last_barge_in = now
                    logger.info("interrupt_triggered | barge_in")
                    self.is_speaking = False
                    sd.stop()
                    if self.interrupt_event:
                        self.interrupt_event.set()
                    self.audio_cmd_q.put({"type": "INTERRUPT"})
                    self.brain_task_q.put({"type": "INTERRUPT"}, block=False)
                    self.status_q.put(
                        {"type": "PHASE", "content": "LISTENING", "source": "audio"},
                        block=False,
                    )
                    # Clear internal buffer immediately
                    with self.buffer_lock:
                        self.audio_buffer = []
                    continue
                # No 'else continue' here - allow WW check to proceed even if barge-in not triggered

            peak = np.max(np.abs(audio_filtered))

            if effective_speaking:
                # Clear buffer during playback to prevent echo-triggers for STT
                with self.buffer_lock:
                    if self.audio_buffer:
                        self.audio_buffer = []
                self.is_listening = False
                self.silence_frames = 0

            elif self.conversation_active:
                if self.awaiting_brain:
                    # Just monitor for speech while brain thinking, but don't record yet
                    if not is_speech:
                        self.silence_frames += 1
                else:
                    if is_speech and not self.is_listening:
                        self.is_listening = True
                        # PRE-ROLL: Include last 20 frames (~1.6s) to ensure word starts aren't cut off
                        with self.buffer_lock:
                            preroll = list(self.verif_buffer)[-20:]
                            self.audio_buffer.extend(preroll)

                        if not self.standby_mode:
                            self.status_q.put(
                                {
                                    "type": "PHASE",
                                    "content": "LISTENING",
                                    "source": "audio",
                                },
                                block=False,
                            )
                        self.silence_frames = 0
                        self.active_idle_frames = 0 # Reset inactivity on speech start

                    if self.is_listening:
                        with self.buffer_lock:
                            self.audio_buffer.append(audio_i16)

                        if not is_speech:
                            self.silence_frames += 1
                        else:
                            self.silence_frames = 0

                        max_buffer_frames = int(
                            settings.audio.conversation_timeout
                            * 1000
                            / self.frame_duration
                        )
                        max_speech_frames = int(
                            10000 / self.frame_duration
                        )  # 10s max speech duration

                        max_silence = int(
                            settings.audio.silence_limit * 1000 / self.frame_duration
                        )
                        if (
                            (self.silence_frames >= max_silence)
                            or (len(self.audio_buffer) > max_buffer_frames)
                            or (len(self.audio_buffer) > max_speech_frames)
                        ):
                            if len(self.audio_buffer) > max_speech_frames:
                                logger.info("max_speech_duration_reached")

                            self.is_listening = False
                            if not self.standby_mode:
                                self.stt_ready = True
                                self.awaiting_brain = True
                                self.status_q.put(
                                    {
                                        "type": "PHASE",
                                        "content": "THINKING",
                                        "source": "audio",
                                    },
                                    block=False,
                                )
                                self.trigger_stt()  # Proactively trigger STT
                            self.silence_frames = 0
                    else:
                        if not is_speech:
                            self.active_idle_frames += 1
                        else:
                            self.active_idle_frames = 0

                        # 120s inactivity timeout (approx 120 / 0.08 = 1500 frames)
                        if self.active_idle_frames > 1500:
                            logger.info("conversation_timeout | returning_to_idle")
                            self.conversation_active = False
                            self.active_idle_frames = 0
                            self.status_q.put(
                                {"type": "PHASE", "content": "IDLE", "source": "audio"},
                                block=False,
                            )

                        self.silence_frames = 0

            # ALWAYS check for wake word if model is loaded (enables interruption by name)
            if self.ww_model:
                try:
                    # WW model requires exactly 1280 samples at 16kHz.
                    # Guard: if block is larger (e.g. fallback rate mismatch), slice correctly.
                    ww_samples = 1280
                    if len(audio_filtered) < ww_samples:
                        continue

                    # Use boosted signal scaled to int16 range (expected by openwakeword)
                    ww_frame = (audio_filtered[:ww_samples] * 32767).astype(np.int16)

                    prediction = self.ww_model.predict(ww_frame)
                    score = max(prediction.values()) if prediction else 0.0

                    if score > 0.05:
                        logger.debug(
                            f"ww_score_debug | score={score:.6f} | rms={rms:.4f} | peak={peak:.4f} | keys={list(prediction.keys())}"
                        )

                    # WAKE_SCORE is high-frequency telemetry used only for
                    # local threshold comparison — do NOT push to status_q
                    # (the dashboard uses VOICE_ACTIVITY for the voice orb).

                    # Read the actual wake_threshold (not wake_word_sensitivity which is the user-facing 0-1 scale)
                    threshold = getattr(settings.audio, "wake_threshold", 0.02)

                    # WAKE WORD DETECTION:
                    # 1. Model score > threshold
                    # 2. RMS bypass (0.10) GATED by VAD (is_speaking) to filter thumps
                    # 3. WAKE LOCK: Prevent re-triggering for 5s to allow for greeting/stt
                    rms_bypass = (
                        rms > 0.10 and effective_speaking and not self.is_listening
                    )
                    now = time.time()

                    # POST-WAKE GUARD: If we just woke up, be more strict (filter echos)
                    time_since_wake = now - getattr(self, "_last_standby_wake_time", 0)
                    dynamic_threshold = threshold

                    if effective_speaking:
                        # GHOST WAKE PROTECTION: Massive threshold bump while Charlie speaks
                        dynamic_threshold = max(0.45, threshold * 15.0)
                    elif time_since_wake < 5.0:
                        dynamic_threshold = max(
                            0.25, threshold * 5.0
                        )  # Significant bump for echo window

                    wake_lock = now - getattr(self, "_last_wake_trigger_time", 0) < 5.0

                    if ((score > dynamic_threshold) or rms_bypass) and not wake_lock:
                        self._last_wake_trigger_time = now
                        logger.info(
                            f"wake_triggered | score={score:.4f} | is_speaking={effective_speaking} | threshold={dynamic_threshold:.3f}"
                        )
                        self.pending_wake = True
                        self.last_wake_time = time.time()

                        # AGGRESSIVE INTERRUPT: Stop EVERYTHING if Charlie is heard
                        if self.interrupt_event:
                            self.interrupt_event.set()

                        # FORCE ABORT: Immediately stop any active stream globally
                        try:
                            sd.stop()
                        except Exception as e:
                            logger.debug(f"audio_stop_failed_or_no_stream | {e}")

                        self.audio_cmd_q.put({"type": "INTERRUPT"})
                        self.brain_task_q.put({"type": "WAKE"}, block=False)

                        # In standby mode: pre-buffer the wake audio and trigger STT immediately
                        # so the transcribed text ('charlie', 'hey charlie' etc.) reaches brain.
                        if self.standby_mode:
                            with self.buffer_lock:
                                preroll = list(self.verif_buffer)[
                                    -20:
                                ]  # ~1.6s pre-roll
                                self.audio_buffer = list(preroll)
                            self._last_standby_wake_time = (
                                time.time()
                            )  # WINDOW: suppress wake-word STT for 5s
                            self.conversation_active = True
                            self.awaiting_brain = False
                            self.stt_ready = True
                            self.trigger_stt()  # FORCE STT on wake-word during standby
                        else:
                            # Non-standby wake: Ensure conversation is active immediately
                            self.conversation_active = True
                            self.awaiting_brain = False
                            # Flush buffers normally (non-standby WW handling)
                            with self.buffer_lock:
                                self.audio_buffer = []

                        if not self.standby_mode:
                            self.status_q.put(
                                {
                                    "type": "PHASE",
                                    "content": "LISTENING",
                                    "source": "audio",
                                },
                                block=False,
                            )
                    elif (
                        rms > 0.20
                        and score < 0.01
                        and effective_speaking
                        and not self.standby_mode
                    ):
                        # Signal is loud but WW didn't fire - Rate limit diagnostic to 10s
                        if now - self.last_diagnostic_time > 10:
                            logger.warning(
                                f"audio_signal_detected_no_wake | rms={rms:.4f} | score={score:.4f} | check_mic_clarity"
                            )
                            self.last_diagnostic_time = now
                except Exception as e:
                    logger.error(f"ww_predict_error | {e}")
                    # Vocal report if it persists
                    if not hasattr(self, "_ww_err_count"):
                        self._ww_err_count = 0

                    self._ww_err_count += 1
                    if self._ww_err_count == 50:  # Report every ~10s of failure
                        self.tts_q.put(
                            {
                                "type": "SPEAK",
                                "content": "Sir, my wake-word detection is encountering internal errors.",
                            }
                        )

    def trigger_stt(self):
        with self.stt_lock:
            if self.stt_in_progress:
                return
            if not self.audio_buffer:
                return
            self.stt_in_progress = True
            self.stt_ready = False

        def _stt_task():
            try:
                with self.buffer_lock:
                    if not self.audio_buffer:
                        self.stt_in_progress = False
                        return
                    audio = np.concatenate(self.audio_buffer)
                    self.audio_buffer = []
                # Normalization: audio is already float32 [-1, 1] from sensory loop
                audio_f32 = audio.astype(np.float32)
                # Normalization: Use peak-normalization to ensure Whisper gets a clear signal
                peak = np.max(np.abs(audio_f32))
                if peak > 1.0:
                    audio_f32 /= 32768.0
                elif 0.005 < peak < 0.5:
                    # Gentle normalization: lower threshold to catch quiet voices
                    audio_f32 = audio_f32 / peak * 0.95

                logger.info("STT_START | Sensory pipeline processing audio...")
                logger.info(
                    f"stt_transcribing | samples={len(audio_f32)} | peak={peak:.4f}"
                )

                initial_prompt = getattr(settings.audio, "stt_initial_prompt", "Sir,")
                segments, info = self.stt_model.transcribe(
                    audio_f32,
                    beam_size=1,  # SPEED: Minimal beams for lowest latency
                    best_of=1,
                    language="en",
                    vad_filter=True,
                    initial_prompt=initial_prompt,
                    condition_on_previous_text=True,  # Better conversational context
                )

                junk_patterns = [
                    "thank you for watching",
                    "subtitles by",
                    "amara.org",
                    "thanks for watching",
                    "shining moon",
                    "the end",
                    "subscribe",
                    "translated by",
                    "i hope you enjoyed",
                ]
                text_segments = []
                for s in segments:
                    if any(p in s.text.lower() for p in junk_patterns):
                        continue
                    text_segments.append(s.text)
                    logger.debug(f"stt_segment | {s.text}")

                text = " ".join(text_segments).strip()

                if not text:
                    # Silence empty result spam
                    self.awaiting_brain = False
                    # Stay LISTENING — don't emit IDLE. Only standby stops listening.
                    return
                logger.info(f"stt_result | SIR: {text}")
                logger.info(f"STT_COMPLETE | Transcribed: {text}")

                # STANDBY WAKE FILTER: If we just woke from standby, the first 5 seconds of STT
                # results are likely just the wake word ("Charlie") or a repeat of the
                # standby command. Filter these out to prevent re-entering standby.
                now = time.time()
                time_since_wake = now - getattr(self, "_last_standby_wake_time", 0)
                if time_since_wake < 5.0:
                    text_clean_check = text.lower().strip().rstrip(".,!?")
                    # Strip leading "charlie" and check if anything meaningful remains
                    import re as _re

                    residual = _re.sub(
                        r"^(charlie|hey charlie)[,.\s]*", "", text_clean_check
                    ).strip()
                    standby_triggers = [
                        "stand by",
                        "standby",
                        "sleep",
                        "dismissed",
                        "go to sleep",
                        "",
                    ]
                    if residual in standby_triggers:
                        logger.info(
                            f"standby_wake_filter | suppressed='{text}' | time={time_since_wake:.1f}s"
                        )
                        self.stt_in_progress = False
                        # REFRESH LOCK: Prevent immediate re-trigger from same echo
                        self._last_wake_trigger_time = time.time()
                        # CRITICAL: Re-trigger listen so she doesn't stay 'paused'
                        self._start_stt()
                        return

                # Broadcast transcript to dashboard (persistent via WS → frontend)
                self.status_q.put(
                    {"type": "CHAT_MSG", "speaker": "SIR", "content": text}, block=False
                )
                self.status_q.put(
                    {"type": "USER_TRANSCRIPT", "content": text}, block=False
                )
                if hasattr(self.brain_task_q, "put"):
                    self.brain_task_q.put(
                        {"type": "TEXT", "content": text, "source": "local"}
                    )
            except Exception as e:
                logger.error(f"stt_error | {e}")
                if hasattr(self.tts_q, "put"):
                    self.tts_q.put(
                        {
                            "type": "SPEAK",
                            "content": f"Sir, my speech-to-text system encountered an error: {str(e)}",
                        }
                    )
                self.awaiting_brain = False

            finally:
                self.stt_in_progress = False

        # SPEED: Queue to persistent worker instead of spawning a new thread per call.
        # Eliminates thread creation overhead (~1-2ms) on every STT invocation.
        self._stt_task_q.put(_stt_task)

    def _start_stt(self):
        """Resets the sensory state to begin listening for a fresh interaction."""
        self.is_listening = False
        self.awaiting_brain = False
        self.silence_frames = 0
        logger.debug("sensory_retrigger | ready_for_new_input")

    def _speaker_worker(self):
        import time
        pythoncom.CoInitialize()
        logger.info("speaker_worker_online | voice=eve")
        self._ensure_playback_proc()

        def _interrupt_watcher():
            """Dedicated thread: kills active playback process the instant interrupt_event fires."""
            last_interrupt = 0
            while self.running:
                if (
                    self.interrupt_event
                    and self.interrupt_event.is_set()
                    and self._playback_proc
                ):
                    now = time.time()
                    if now - last_interrupt < 0.5:  # 500ms cooldown between interrupts
                        self.interrupt_event.clear()
                        time.sleep(0.02)
                        continue
                    last_interrupt = now
                    logger.info("interrupt_watcher_fired | aborting_playback_process")
                    self.is_speaking = False
                    self.volume.unduck()
                    # Forceful termination of playback process
                    try:
                        self._playback_proc.terminate()
                        self._playback_proc.join(timeout=1.0)
                        self._ensure_playback_proc() # Respawn for next time
                    except Exception as e:
                        logger.debug(f"proc_abort_failed | {e}")
                    # Flush pending TTS chunks
                    while not self.tts_q.empty():
                        try:
                            self.tts_q.get_nowait()
                        except Exception as e:
                            logger.debug(f"tts_q_drain_interrupted | {e}")
                            break
                    # Clear interrupt event after handling
                    self.interrupt_event.clear()
                time.sleep(0.02)

        threading.Thread(
            target=_interrupt_watcher, daemon=True, name="InterruptWatcher"
        ).start()

        # Start ambient feedback threads


        while self.running:
            try:
                # Optimized poll for instant response
                task = self.tts_q.get(timeout=0.05)
            except queue.Empty:
                continue
            except (EOFError, ConnectionResetError, BrokenPipeError):
                logger.error("audio_tts_ipc_disconnected")
                self.running = False
                break
            except Exception as e:
                logger.error(f"audio_tts_loop_err | {e}")
                if "closed" in str(e).lower() or "pipe" in str(e).lower():
                    self.running = False
                    break
                continue

            self.is_speaking = True
            try:
                current_task = task
                if isinstance(current_task, str):
                    current_task = {"type": "SPEAK", "content": current_task}
                while True:
                    if current_task["type"] in ["CONVERSATION_END", "TURN_END"]:
                        self.awaiting_brain = False
                        self.is_speaking = False  # Force clear

                        if current_task["type"] == "CONVERSATION_END":
                            was_active = self.conversation_active
                            self.conversation_active = False
                            self.allow_barge_in = False

                            if was_active and not self.standby_mode:
                                self.conversation_active = True # Resume listening
                                self.status_q.put(
                                    {"type": "PHASE", "content": "LISTENING", "source": "audio"},
                                    block=False,
                                )
                            elif self.standby_mode:
                                self.status_q.put(
                                    {"type": "PHASE", "content": "STANDBY", "source": "audio"},
                                    block=False,
                                )
                        else:
                            # TURN_END: Ensure we stay in conversational mode
                            self.conversation_active = True
                            if not self.standby_mode:
                                self.status_q.put(
                                    {"type": "PHASE", "content": "LISTENING", "source": "audio"},
                                    block=False,
                                )

                        self.volume.unduck()
                        break
                    if current_task["type"] == "SPEAK":
                        raw_text = current_task["content"]
                        text = self._normalize_text(raw_text)
                        logger.info(f"speak_vocal | CHARLIE: {text}")

                        # 1. Split by sentences
                        chunks = re.split(r"(?<=[.!?])\s+", text)
                        try:
                            self._ensure_playback_proc()
                            self.volume.duck()
                            self.status_q.put(
                                {"type": "PHASE", "content": "SPEAKING", "source": "audio"},
                                block=False,
                            )
                            # CRITICAL: Set speaking state before loop
                            self.is_speaking = True

                            # LOW-LATENCY TTS: Synthesize each sub-chunk and stream
                            # immediately. With GPU (~300ms/chunk), the gap between
                            # sub-chunks is imperceptible.
                            for chunk_text in chunks:
                                if not self.is_speaking:
                                    break
                                words = chunk_text.split()
                                sub_chunks = (
                                    [" ".join(words[i : i + 50]) for i in range(0, len(words), 50)]
                                    if len(words) > 55
                                    else [chunk_text]
                                )

                                for final_text in sub_chunks:
                                    if not self.is_speaking:
                                        break
                                    final_text = final_text.strip()
                                    try:
                                        samples, sr = self.tts_model.create(
                                            final_text,
                                            voice=self.kokoro_voice,
                                            speed=self.kokoro_speed,
                                            lang=self.kokoro_lang,
                                        )
                                        audio_block = np.array(samples, dtype=np.float32)
                                    except Exception as e:
                                        logger.error(f"kokoro_tts_error | {e}")
                                        continue

                                    # Stream this block immediately
                                    if not self.is_speaking:
                                        break
                                    block_size = int(sr * 0.1)  # 100ms blocks
                                    for i in range(0, len(audio_block), block_size):
                                        if not self.is_speaking or (
                                            self.interrupt_event and self.interrupt_event.is_set()
                                        ):
                                            self.is_speaking = False
                                            self._playback_proc.terminate()
                                            self._ensure_playback_proc()
                                            while not self.tts_q.empty():
                                                try:
                                                    self.tts_q.get_nowait()
                                                except Exception:
                                                    break
                                            break

                                        sub_block = audio_block[i:i + block_size]

                                        # WAVEFORM: Generate for speaker output
                                        step = max(1, len(sub_block) // 64)
                                        waveform = (np.abs(sub_block[::step][:64])).tolist()
                                        if len(waveform) < 64:
                                            waveform.extend([0.0] * (64 - len(waveform)))

                                        self.status_q.put({
                                            "type": "VOICE_ACTIVITY",
                                            "peak": float(np.max(np.abs(sub_block))),
                                            "waveform": waveform,
                                            "source": "speaker"
                                        }, block=False)

                                        # Send to playback process
                                        self._playback_q.put(sub_block)

                        except Exception as e:
                            if self.running:
                                logger.error(f"speaker_stream_error | {e}")
                            else:
                                logger.debug(f"speaker_stream_shutdown_cleanup | {e}")
                        finally:
                            if self.interrupt_event:
                                self.interrupt_event.clear()

                    if not self.is_speaking:
                        break

                    try:
                        # Bridge stream gaps without unducking (reduced to 2s for faster recovery)
                        start_wait = time.time()
                        current_task = None
                        while time.time() - start_wait < 2.0:
                            if self.interrupt_event and self.interrupt_event.is_set():
                                break
                            try:
                                current_task = self.tts_q.get(timeout=0.1)
                                break
                            except queue.Empty:
                                continue

                        if current_task is None:
                            break
                    except Exception:
                        break
                self.is_speaking = False
                self.volume.unduck()
            except Exception as e:
                logger.error(f"speaker_worker_fatal | {e}")
                self.awaiting_brain = False # Fail-safe: release sensory loop
                self.is_speaking = False
                self.volume.unduck()

    def _status_listener(self):
        """Monitors status_q to sync neural hum with brain activity."""
        while self.running:
            try:
                msg = self.status_q.get(timeout=0.1)
                if msg.get("type") == "PHASE":
                    content = msg.get("content", "")
                    if content == "THINKING":
                        self.is_thinking = True
                    elif content in ["SPEAKING", "LISTENING", "IDLE"]:
                        self.is_thinking = False
            except queue.Empty:
                continue
            except Exception:
                break

    def _thinking_hum_worker(self):
        """Generates a subtle, modulated 'data hum' during thinking states."""
        sample_rate = 44100
        t = 0
        base_freq = 220.0

        while self.running:
            if getattr(self, "is_thinking", False) and not self.is_speaking:
                # Modulate frequency slightly for 'organic' feel
                mod = math.sin(2 * math.pi * 0.5 * t) * 5.0
                freq = base_freq + mod

                # Simple sine wave chunk
                chunk_size = 1024
                samples = (np.sin(2 * math.pi * freq * (np.arange(t, t + chunk_size) / sample_rate))).astype(np.float32)
                samples *= 0.03 # Very quiet

                try:
                    self._playback_q.put(samples)
                except Exception:
                    pass

                t += chunk_size
                time.sleep(chunk_size / sample_rate)
            else:
                t = 0
                time.sleep(0.1)

    def _run_original(self):
        import time
        pythoncom.CoInitialize()
        # ── PRIORITY ELEVATION ──
        # Ensure sensory loop gets priority over heavy background tasks (Vision/RAG)
        try:
            import psutil
            p = psutil.Process(os.getpid())
            if os.name == 'nt':
                p.nice(psutil.HIGH_PRIORITY_CLASS)
            else:
                p.nice(-10)
            logger.info("audio_priority_elevated | status=high")
        except Exception as e:
            logger.debug(f"audio_priority_failed | {e}")

        logger.info("audio_engine_ignition")

        try:
            stream = sd.InputStream(
                device=self.device_index,
                channels=1,
                samplerate=self.input_rate,
                blocksize=self.block_size,
                dtype="float32",
                latency="high",
                callback=self.audio_callback,
            )
        except Exception as e:
            logger.warning(
                f"audio_init_failed | rate={self.input_rate} | error={e} | falling_back_to_16k"
            )
            self.input_rate = 16000
            self.block_size = int(16000 * self.frame_duration / 1000)
            stream = sd.InputStream(
                device=self.device_index,
                channels=1,
                samplerate=16000,
                blocksize=self.block_size,
                dtype="float32",
                latency="high",
                callback=self.audio_callback,
            )
        with stream:
            # Hardware rate handshake: ensure we use the actual rate the device opened with
            actual_rate = getattr(stream, "samplerate", self.input_rate)
            if actual_rate != self.input_rate:
                logger.info(
                    f"audio_hardware_handshake | requested={self.input_rate} | actual={actual_rate}"
                )
                self.input_rate = int(actual_rate)

            # Start workers and handshake only AFTER stream is confirmed
            threading.Thread(
                target=self._speaker_worker, daemon=True, name="SpeakerWorker"
            ).start()
            threading.Thread(
                target=self._sensory_processor, daemon=True, name="SensoryProcessor"
            ).start()

            # Persistent STT worker thread — drains tasks from _stt_task_q.
            # Replaces the old polling loop + thread-per-call pattern.
            def _stt_worker():
                while self.running:
                    try:
                        task = self._stt_task_q.get(timeout=0.5)
                        if task is None:  # Shutdown sentinel
                            break
                        task()
                    except queue.Empty:
                        continue

            threading.Thread(
                target=_stt_worker, daemon=True, name="STTWorker"
            ).start()

            # Push-to-talk listener: Right Ctrl activates listening
            def _push_to_talk():
                try:
                    from pynput import keyboard

                    def on_press(key):
                        if key == keyboard.Key.ctrl_r:
                            if not self.conversation_active:
                                logger.info("push_to_talk_activated")
                                self.pending_wake = True
                                self.last_wake_time = time.time()

                    listener = keyboard.Listener(on_press=on_press)
                    listener.daemon = True
                    listener.start()
                    logger.info("push_to_talk_listener_started")
                    while self.running:
                        time.sleep(1)
                except ImportError:
                    logger.debug("push_to_talk_unavailable | pynnot not installed")
                except Exception as e:
                    logger.debug(f"push_to_talk_error | {e}")

            threading.Thread(
                target=_push_to_talk, daemon=True, name="PushToTalk"
            ).start()

            self.brain_task_q.put({"type": "SENSORY_READY"})
            logger.info("audio_handshake_complete | sensory_ready_emitted")

            # Main Control & Heartbeat Loop (Prioritized for Liveness)
            while self.running:
                now = time.time()
                # 1. Update Heartbeat
                if self.heartbeat:
                    self.heartbeat.value = now
                # 2. Check interrupt event
                if self.interrupt_event and self.interrupt_event.is_set():
                    sd.stop()
                    self.is_speaking = False
                    self.interrupt_event.clear()
                # 3. Process Wake Signal
                if self.pending_wake:
                    self.pending_wake = False
                    self.conversation_active = True
                    if not self.standby_mode:
                        self.status_q.put(
                            {"type": "PHASE", "content": "LISTENING", "source": "audio"},
                            block=False,
                        )
                    self.brain_task_q.put({"type": "WAKE"})
                # 4. Handle Commands (Truncated loop for brevity - same logic as before)
                try:
                    msg = self.audio_cmd_q.get(timeout=0.01)
                    cmd_type = msg.get("type")
                    if cmd_type in ["STANDBY", "SET_STANDBY"]:
                        is_active = msg.get("value", True)
                        logger.info(f"audio_cmd | standby_mode | value={is_active}")
                        self.standby_mode = is_active
                        self.conversation_active = not is_active
                        self.stt_ready = False
                        self.awaiting_brain = False
                        phase = "STANDBY" if is_active else "LISTENING"
                        self.status_q.put({"type": "PHASE", "content": phase, "source": "audio"}, block=False)

                    elif cmd_type == "WAKE":
                        logger.info("audio_cmd | wake_command_received")
                        self.standby_mode = False
                        self.conversation_active = True
                        self.awaiting_brain = False
                        self.status_q.put({"type": "PHASE", "content": "LISTENING", "source": "audio"}, block=False)

                    elif cmd_type == "LISTENING":
                        logger.debug("audio_cmd | set_listening_state")
                        self.conversation_active = True
                        self.awaiting_brain = False

                    elif cmd_type in ["STOP", "SHUTDOWN"]:
                        logger.info(f"audio_cmd | {cmd_type} | killing_audio_engine")
                        self.running = False
                        # Send sentinels to workers for clean shutdown
                        self._playback_q.put(None)
                        self._stt_task_q.put(None)
                except queue.Empty:
                    pass
                time.sleep(0.01)
