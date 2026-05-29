"""Kokoro TTS engine wrapper — generates WAV audio from text."""

import logging
import os
import tempfile
import time

logger = logging.getLogger("charlie.tts.kokoro")


class KokoroTTS:
    """Wrapper around kokoro_onnx for text-to-speech synthesis."""

    def __init__(self, model_path: str | None = None, voices_path: str | None = None):
        self.model_path = model_path or os.getenv(
            "KOKORO_MODEL_PATH", "charlie/models/kokoro-v1.0.onnx"
        )
        self.voices_path = voices_path or os.getenv(
            "KOKORO_VOICES_PATH", "charlie/models/voices-v1.0.bin"
        )
        self._engine = None

    def _load_engine(self):
        """Lazy-load the Kokoro ONNX engine."""
        if self._engine is not None:
            return True
        try:
            from kokoro_onnx import Kokoro
            self._engine = Kokoro(self.model_path, self.voices_path)
            logger.info("kokoro_loaded | model=%s", self.model_path)
            return True
        except Exception as e:
            logger.error("kokoro_load_failed | %s", e)
            return False

    def synthesize(
        self,
        text: str,
        output_path: str | None = None,
        voice: str = "af_sarah",
        speed: float = 1.0,
        lang: str = "en-us",
    ) -> str | None:
        """Generate WAV audio from text. Returns file path or None."""
        if not self._load_engine():
            return None

        if output_path is None:
            output_path = os.path.join(
                tempfile.gettempdir(), f"charlie_tts_{int(time.time())}.wav"
            )

        try:
            audio = self._engine.create(text, voice=voice, speed=speed, lang=lang)
            import soundfile as sf
            sf.write(output_path, audio, 24000)
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                return output_path
        except Exception as e:
            logger.error("kokoro_synthesize_failed | %s", e)
        return None
