"""Voice reply module for Telegram — Kokoro TTS + ffmpeg OGG/OPUS conversion.

Uses the same Kokoro ONNX model and voice settings as the main audio pipeline
for consistent voice across all output channels.
"""

import logging
import os
import subprocess
import tempfile
import time

logger = logging.getLogger("charlie.telegram.voice")

# Telegram voice note requirements: OGG/OPUS format, max 50MB
MAX_TEXT_LENGTH = 4000  # Truncate very long responses


def text_to_telegram_voice(text: str) -> str | None:
    """Convert text to Telegram voice note (OGG/OPUS). Returns file path or None.

    Pipeline: text -> Kokoro ONNX TTS (WAV) -> ffmpeg (OGG/OPUS) -> file path
    Uses the same voice settings as the main audio pipeline for consistency.
    """
    if not text or not text.strip():
        return None

    # Truncate long text
    if len(text) > MAX_TEXT_LENGTH:
        text = text[:MAX_TEXT_LENGTH] + "..."

    try:
        # Step 1: Generate WAV using Kokoro ONNX (same model as audio pipeline)
        wav_path = _generate_kokoro_tts(text)
        if not wav_path:
            return None

        # Step 2: Convert WAV to OGG/OPUS via ffmpeg
        ogg_path = _convert_to_ogg(wav_path)

        # Clean up WAV
        try:
            os.remove(wav_path)
        except OSError:
            pass

        return ogg_path

    except Exception as e:
        logger.error(f"text_to_voice_err | {e}")
        return None


def _generate_kokoro_tts(text: str) -> str | None:
    """Generate WAV audio using Kokoro ONNX TTS. Returns WAV path or None.

    Uses the same model, voice, and settings as the main audio pipeline
    so the Telegram voice sounds identical to the speaker output.
    """
    try:
        from charlie.config import settings

        # Get voice settings from config (same as audio_proc.py)
        voice = getattr(settings.audio, "kokoro_voice", "af_heart")
        speed = getattr(settings.audio, "kokoro_speed", 1.0)
        lang = getattr(settings.audio, "kokoro_lang", "en-us")

        # Load Kokoro ONNX model (same as audio pipeline)
        from kokoro_onnx import Kokoro
        kokoro_model = os.getenv("KOKORO_MODEL_PATH", "charlie/models/kokoro-v1.0.onnx")
        kokoro_voices = os.getenv("KOKORO_VOICES_PATH", "charlie/models/voices-v1.0.bin")

        # Try GPU first, fall back to CPU
        try:
            import onnxruntime as ort
            opts = ort.SessionOptions()
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            sess = ort.InferenceSession(
                kokoro_model, opts,
                providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
            )
            engine = Kokoro.from_session(sess, kokoro_voices)
        except Exception:
            engine = Kokoro(kokoro_model, kokoro_voices)

        # Generate audio
        samples, sr = engine.create(text, voice=voice, speed=speed, lang=lang)

        # Write WAV
        import numpy as np
        import soundfile as sf
        audio = np.array(samples, dtype=np.float32)
        wav_path = os.path.join(tempfile.gettempdir(), f"charlie_tts_{int(time.time())}.wav")
        sf.write(wav_path, audio, sr)

        if os.path.exists(wav_path) and os.path.getsize(wav_path) > 0:
            return wav_path

    except Exception as e:
        logger.error(f"kokoro_tts_err | {e}")

    logger.warning("tts_unavailable | kokoro_onnx could not generate audio")
    return None


def _convert_to_ogg(wav_path: str) -> str | None:
    """Convert WAV to OGG/OPUS using ffmpeg. Returns OGG path or None."""
    ogg_path = wav_path.replace(".wav", ".ogg")

    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", wav_path,
                "-c:a", "libopus",
                "-b:a", "64k",
                "-vbr", "on",
                "-application", "voip",
                ogg_path,
            ],
            capture_output=True,
            timeout=30,
        )

        if result.returncode == 0 and os.path.exists(ogg_path) and os.path.getsize(ogg_path) > 0:
            return ogg_path
        else:
            logger.error(f"ffmpeg_err | rc={result.returncode} | {result.stderr.decode()[:200]}")
            return None

    except FileNotFoundError:
        logger.error("ffmpeg_not_found | install ffmpeg for Telegram voice replies")
        return None
    except subprocess.TimeoutExpired:
        logger.error("ffmpeg_timeout | conversion took >30s")
        return None
    except Exception as e:
        logger.error(f"ffmpeg_err | {e}")
        return None
