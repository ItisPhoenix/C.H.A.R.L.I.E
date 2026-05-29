"""Voice reply module for Telegram — Kokoro TTS + ffmpeg OGG/OPUS conversion."""

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

    Pipeline: text → Kokoro TTS (WAV) → ffmpeg (OGG/OPUS) → file path
    """
    if not text or not text.strip():
        return None

    # Truncate long text
    if len(text) > MAX_TEXT_LENGTH:
        text = text[:MAX_TEXT_LENGTH] + "..."

    try:
        # Step 1: Generate WAV using Kokoro TTS
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
    """Generate WAV audio using Kokoro TTS. Returns WAV path or None."""
    try:
        # Try importing kokoro
        from charlie.audio.tts_engine import TTSEngine
        engine = TTSEngine()
        if hasattr(engine, 'synthesize'):
            wav_path = os.path.join(tempfile.gettempdir(), f"charlie_tts_{int(time.time())}.wav")
            engine.synthesize(text, wav_path)
            if os.path.exists(wav_path) and os.path.getsize(wav_path) > 0:
                return wav_path
    except Exception as e:
        logger.debug(f"kokoro_tts_err | {e}")

    # Fallback: try direct kokoro import
    try:
        import kokoro
        wav_path = os.path.join(tempfile.gettempdir(), f"charlie_tts_{int(time.time())}.wav")
        # kokoro.generate returns audio data
        audio = kokoro.generate(text)
        if audio is not None:
            import soundfile as sf
            sf.write(wav_path, audio, 24000)
            if os.path.exists(wav_path) and os.path.getsize(wav_path) > 0:
                return wav_path
    except Exception as e:
        logger.debug(f"kokoro_direct_err | {e}")

    logger.warning("tts_unavailable | no TTS engine could generate audio")
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
