import asyncio
import logging
import time
from contextlib import suppress
from typing import Optional

from charlie.config import Config
from charlie.voice import VoiceEngine
from charlie.core import Brain

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("charlie.main")

# Flush thresholds for early TTS dispatch
_MAX_SENTENCE_CHARS = 120  # Force-flush if no sentence boundary seen within this many chars
_CLAUSE_MARKERS = (". ", "! ", "? ", ", ", "; ", ": ")  # ordered by sentence then clause


def run() -> None:
    config = Config()
    if not config.llm_url or not config.llm_model:
        logger.error("LLM_URL and LLM_MODEL must be set for voice-only mode.")
        raise SystemExit(1)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    brain = Brain(config)
    voice = None
    current_task: Optional[asyncio.Task] = None

    def on_speech(text: str) -> None:
        nonlocal voice, current_task
        logger.info("Speech detected: %s", text)

        # Handle barge-in/interruption
        if voice and voice.is_speaking.is_set():
            logger.info("Barge-in: User interrupted Charlie. Stopping TTS.")
            voice.stop_tts()
            if current_task and not current_task.done():
                loop.call_soon_threadsafe(current_task.cancel)

        # Schedule the new process task
        def start_task():
            nonlocal current_task
            if current_task and not current_task.done():
                current_task.cancel()
            current_task = asyncio.create_task(_process(text, brain, voice))

        loop.call_soon_threadsafe(start_task)

    voice = VoiceEngine(config, on_speech=on_speech)

    try:
        voice.start()
    except Exception as exc:
        logger.error("Failed to start voice pipeline: %s", exc)
        raise SystemExit(1)

    logger.info("Voice-only mode active. Speak into the mic. Press Ctrl+C to stop.")

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    except SystemExit:
        pass
    finally:
        # Cancel active task on shutdown
        if current_task and not current_task.done():
            current_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                loop.run_until_complete(current_task)

        voice.stop()
        if hasattr(brain, "close"):
            loop.run_until_complete(brain.close())
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.stop()
        loop.close()
        logger.info("Shutdown complete.")


async def _process(text: str, brain: Brain, voice: VoiceEngine) -> None:
    sentence_buffer = ""
    is_first_chunk = True
    first_speak = True
    t_llm_first = 0.0
    t_tts_first = 0.0
    t_start = time.time()

    logger.info("llm_start | user_input=%s", text)
    try:
        async for chunk in brain.chat_stream(text):
            if is_first_chunk:
                voice.timer.mark("llm_first_token")
                ms = voice.timer.log_delta("asr_done", "llm_first_token", "asr_to_llm")
                voice.timer.warn_if_exceeds("asr_to_llm", ms, 1500)
                t_llm_first = time.time()
                is_first_chunk = False

            sentence_buffer += chunk

            flush_at = -1
            for marker in _CLAUSE_MARKERS:
                idx = sentence_buffer.rfind(marker)
                if idx != -1:
                    flush_at = idx + len(marker)
                    break

            if flush_at == -1 and len(sentence_buffer) >= _MAX_SENTENCE_CHARS:
                idx = sentence_buffer.rfind(" ", 0, _MAX_SENTENCE_CHARS)
                flush_at = idx if idx > 0 else _MAX_SENTENCE_CHARS

            if flush_at > 0:
                segment = sentence_buffer[:flush_at].rstrip()
                sentence_buffer = sentence_buffer[flush_at:]
                if segment:
                    logger.info("tts_enqueue | segment=%s", segment)
                    voice.speak(segment, "neutral")
                    if first_speak:
                        voice.timer.mark("tts_first_audio")
                        ms = voice.timer.log_delta("llm_first_token", "tts_first_audio", "llm_to_tts")
                        voice.timer.warn_if_exceeds("llm_to_tts", ms, 1000)
                        t_tts_first = time.time()
                        first_speak = False
    except asyncio.CancelledError:
        logger.debug("LLM stream cancelled.")
        raise
    except Exception as exc:
        logger.warning("LLM stream error: %s", exc)
        with suppress(Exception):
            voice.speak("Sorry, I couldn't reach my brain just now.", "neutral")

    if sentence_buffer.strip():
        logger.info("tts_enqueue | segment=%s", sentence_buffer.strip())
        voice.speak(sentence_buffer.strip(), "neutral")
        if first_speak:
            voice.timer.mark("tts_first_audio")
            ms = voice.timer.log_delta("llm_first_token", "tts_first_audio", "llm_to_tts")
            voice.timer.warn_if_exceeds("llm_to_tts", ms, 1000)
            t_tts_first = time.time()

    t_end = time.time()
    ms = voice.timer.log_delta("speech_end", "tts_first_audio", "total_e2e")
    voice.timer.warn_if_exceeds("total_e2e", ms, 3000)

    asr_to_llm = ((t_llm_first - t_start) * 1000) if t_llm_first > 0 else -1.0
    llm_to_tts = ((t_tts_first - t_llm_first) * 1000) if t_tts_first > 0 and t_llm_first > 0 else -1.0
    total_e2e = (t_end - t_start) * 1000
    logger.info("e2e | asr_to_llm=%.1f ms | llm_to_tts=%.1f ms | total_e2e=%.1f ms", asr_to_llm, llm_to_tts, total_e2e)


if __name__ == "__main__":
    run()
