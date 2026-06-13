import logging
import threading
import sys
import os
import time
import tempfile
import urllib.request
import numpy as np
import sounddevice as sd
import soundfile as sf
import torch
import re
from typing import Callable
from faster_whisper import WhisperModel
from kokoro_onnx import Kokoro
from collections import deque

logger = logging.getLogger("charlie.voice")

class VoiceEngine:
    def __init__(self, config, on_speech: Callable[[str], None]):
        self.config = config
        self.on_speech = on_speech
        
        has_cuda = torch.cuda.is_available()
        if not has_cuda:
            logger.warning("Torch CUDA not available. Checking ONNX...")
            import onnxruntime
            if 'CUDAExecutionProvider' not in onnxruntime.get_available_providers():
                logger.error("No CUDA found (Torch or ONNX). GPU required for local voice.")
                sys.exit(1)
        
        self.device = config.gpu_device
        self.stop_event = threading.Event()
        self.stop_tts_event = threading.Event()
        self.is_speaking = threading.Event()
        self.tts_lock = threading.Lock() # Protect TTS shared resources
        
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

        # 2. LOCAL STT (Whisper)
        logger.info(f"Loading Whisper {config.whisper_model} (OFFLINE MODE)...")
        try:
            self.whisper = WhisperModel(
                config.whisper_model,
                device=self.device,
                compute_type="float16",
                local_files_only=True
            )
        except Exception as e:
            logger.warning(f"Offline Whisper load failed. Downloading once... | {e}")
            self.whisper = WhisperModel(config.whisper_model, device=self.device, compute_type="float16", local_files_only=False)
        
        # 3. LOCAL TTS (Kokoro)
        model_path = os.path.join(config.kokoro_model_dir, "kokoro-v1.0.onnx")
        voices_path = os.path.join(config.kokoro_model_dir, "voices-v1.0.bin")
        self.kokoro = Kokoro(model_path, voices_path)
        logger.info("Kokoro TTS initialized locally.")
        
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
        logger.info("Starting voice engine loop")
        self.processing_thread = threading.Thread(target=self._run, daemon=True, name="VoiceLoop")
        self.processing_thread.start()

    def stop(self):
        self.stop_event.set()
        self.stop_tts()
        if self.processing_thread and self.processing_thread.is_alive():
            self.processing_thread.join(timeout=5.0)

    def stop_tts(self):
        self.stop_tts_event.set()
        sd.stop()

    def speak(self, text: str):
        # Extremely aggressive cleaning to stop Phonemizer "words count mismatch" warnings
        text = re.sub(r'TOOL:\s*\w+\(".*?"\)', '', text)
        text = re.sub(r'<(thought|thinking)>.*?</\1>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'\[.*?\]', '', text)
        text = re.sub(r'\(https?://.*?\)', '', text)
        text = text.encode("ascii", "ignore").decode("ascii")
        text = re.sub(r'[^a-zA-Z0-9\s.,!?\']', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        
        if not text:
            return
            
        self.stop_tts_event.clear()
        threading.Thread(target=self._synth_and_play, args=(text,), daemon=True, name="TTSThread").start()

    def _synth_and_play(self, text: str):
        with self.tts_lock: # Ensure only one synthesis happens at a time
            self.is_speaking.set()
            try:
                samples, sample_rate = self.kokoro.create(
                    text, 
                    voice=self.config.kokoro_voice, 
                    speed=1.0, 
                    lang=self.config.kokoro_lang
                )
                
                # Check for stop event before playing
                if self.stop_tts_event.is_set():
                    return

                sd.play(samples, samplerate=sample_rate)
                
                while sd.get_stream().active:
                    if self.stop_tts_event.is_set():
                        sd.stop()
                        break
                    time.sleep(0.05)
                sd.wait()
            except Exception as e:
                logger.error(f"tts_error | {e}")
            finally:
                self.is_speaking.clear()

    def _run(self):
        samplerate = 16000
        block_size = 512
        phrase_buffer = []
        silence_start = None
        phrase_start_time = None
        
        def callback(indata, frames, time_info, status):
            if status:
                logger.warning(f"Audio status: {status}")
            self.audio_buffer.append(bytes(indata))

        try:
            with sd.RawInputStream(samplerate=samplerate, blocksize=block_size, 
                                   dtype='int16', channels=1, callback=callback):
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
                    
                    time.sleep(0.001)
        except Exception as e:
            logger.error(f"InputStream error: {e}")

    def _process_phrase(self, audio_data):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            temp_path = f.name
        
        try:
            sf.write(temp_path, audio_data, 16000)
            segments, _ = self.whisper.transcribe(
                temp_path, 
                language=self.config.default_language,
                initial_prompt="I am speaking to my witty and intelligent AI assistant, Charlie.",
                beam_size=5,
                word_timestamps=False
            )
            text = "".join([s.text for s in segments]).strip()
            if text:
                self.on_speech(text)
        except Exception as e:
            logger.error(f"stt_error | {e}")
        finally:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except Exception as e:
                logger.warning(f"Failed to remove temp file {temp_path}: {e}")
