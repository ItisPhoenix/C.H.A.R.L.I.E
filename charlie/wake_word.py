import logging
import threading
import numpy as np
import sounddevice as sd
import time
from typing import Callable, Optional

logger = logging.getLogger("charlie.wake_word")


class WakeWordEngine:
    """Listens for a wake word using openWakeword and triggers a callback.

    Suppresses detection while ``suppress`` is set (avoids self-trigger
    from Charlie's own TTS playback).
    """

    def __init__(
        self,
        model_path: str,
        sensitivity: float = 0.5,
        sample_rate: int = 16000,
        chunk_size: int = 1280,  # ~80 ms at 16 kHz
    ):
        self.model_path = model_path
        self.sensitivity = sensitivity
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size

        self._callback: Optional[Callable[[], None]] = None
        self._running = threading.Event()
        self._stream: Optional[sd.InputStream] = None
        # Suppression flag — set while Charlie's own TTS is playing
        self.suppress = threading.Event()
        self._oww_model = None

        self._load_model()

    def _load_model(self):
        """Load the openwakeword model from ONNX."""
        try:
            from openwakeword import Model as OWWModel

            self._oww_model = OWWModel(
                wakeword_model_paths=[self.model_path],
            )
            logger.info(
                "WakeWordEngine: model loaded from %s (sensitivity=%.2f)",
                self.model_path,
                self.sensitivity,
            )
        except Exception as e:
            logger.warning("WakeWordEngine: failed to load model — %s", e)
            self._oww_model = None

    @property
    def is_available(self) -> bool:
        return self._oww_model is not None

    def listen(self, callback: Callable[[], None]):
        """Start listening in a background thread.

        ``callback`` is invoked on the audio thread when wake word confidence
        exceeds ``sensitivity``.
        """
        if not self.is_available:
            logger.warning("WakeWordEngine: no model loaded, not listening.")
            return

        self._callback = callback
        self._running.set()

        # Use InputStream (not Raw) to get numpy arrays directly
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            blocksize=self.chunk_size,
            callback=self._audio_callback,
        )
        self._stream.start()
        logger.info("WakeWordEngine: listening started.")

    def stop(self):
        self._running.clear()
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        logger.info("WakeWordEngine: stopped.")

    def _audio_callback(self, indata, frames, _time_info, _status):
        if not self._running.is_set():
            return

        # Suppress detection while Charlie is speaking (avoid self-trigger)
        if self.suppress.is_set():
            return

        # indata is already a numpy array because we use sd.InputStream
        # Convert int16 to float32 for openWakeWord
        audio_float = indata.astype(np.float32).flatten() / 32768.0

        prediction = self._oww_model.predict(audio_float)
        # `prediction` is a dict: {model_name: score}
        for model_name, score in prediction.items():
            if score >= self.sensitivity:
                logger.info(
                    "WakeWordEngine: detected (%.3f >= %.2f)",
                    score,
                    self.sensitivity,
                )
                if self._callback:
                    try:
                        self._callback()
                    except Exception as e:
                        logger.error("WakeWordEngine: callback error — %s", e)
                break  # fire once per chunk
