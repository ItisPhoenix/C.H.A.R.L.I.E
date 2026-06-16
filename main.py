# ruff: noqa: E402
import sys
import io
import os
import logging
import re

# 1. SETUP ENVIRONMENT FIRST
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True, write_through=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True, write_through=True)

os.makedirs("logs", exist_ok=True)
LOG_FILE = "logs/charlie.log"

# 2. CONFIGURE SPLIT LOGGING
root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)

file_formatter = logging.Formatter('%(asctime)s [%(name)s] [%(levelname)s] %(funcName)s:%(lineno)d - %(message)s')
file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8', mode='a')

console_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(console_formatter)

root_logger.handlers = []
root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

# 3. NOW IMPORT CHARLIE MODULES
import asyncio
from charlie.core import Brain
from charlie.config import config
from charlie.voice import VoiceEngine

logger = logging.getLogger("charlie.main")

# Sentence/clause splitting for early TTS dispatch
_CLAUSE_BOUNDARY = re.compile(r'(?<=[,;:])\s+(?=[A-Z"])')
_SENTENCE_BOUNDARY = re.compile(r'(?<=[.!?])\s+')
_MAX_FLUSH_CHARS = 200  # Force-flush if no boundary seen within this many chars (~30-40 words)

async def main():
    logger.info("Charlie is waking up...")

    try:
        brain = Brain(config, on_thought_callback=lambda text: voice.speak(text, brain.persona.emotional_state))
    except Exception as e:
        logger.error(f"Failed to initialize Brain: {e}")
        return

    # Start MCP client (connects to external tools)
    await brain.start_mcp()

    # Start WebSocket bridge for Buddy UI
    if config.enable_buddy_ui:
        brain.bridge_task = asyncio.create_task(brain.bridge.start())


    loop = asyncio.get_running_loop()

    def on_speech(text: str):
        logger.info(f"Speech detected: {text}")
        asyncio.run_coroutine_threadsafe(_process(text, brain, voice), loop)


    async def _process(text, brain, voice):
        print(f"\rHeard: {text}", flush=True)
        if voice.is_speaking.is_set():
            logger.info("Barge-in: User interrupted Charlie. Stopping TTS and cancelling chat.")
            voice.stop_tts()
            brain.cancel_chat()
            brain.persona.response_mode = "concise"

        print("Charlie is thinking...", end="\r", flush=True)

        # Streaming buffer
        sentence_buffer = ""
        is_first_chunk = True


        async for chunk in brain.chat(text):
            if is_first_chunk:
                print("\r" + " " * 30 + "\r", end="", flush=True) # Clear thinking
                is_first_chunk = False
            print(chunk, end="", flush=True)
            sentence_buffer += chunk

            # Try sentence boundary first, then clause boundary, then max-char guard
            boundary = None
            if ". " in sentence_buffer or "! " in sentence_buffer or "? " in sentence_buffer:
                boundary = _SENTENCE_BOUNDARY
            elif ", " in sentence_buffer or "; " in sentence_buffer or ": " in sentence_buffer:
                boundary = _CLAUSE_BOUNDARY
            elif len(sentence_buffer) >= _MAX_FLUSH_CHARS:
                # Force-flush: split ONCE at the last space before limit.
                idx = sentence_buffer.rfind(' ', 0, _MAX_FLUSH_CHARS)
                if idx > 0:
                    voice.speak(sentence_buffer[:idx], brain.persona.emotional_state)
                    sentence_buffer = sentence_buffer[idx + 1:]
                else:
                    voice.speak(sentence_buffer[:_MAX_FLUSH_CHARS], brain.persona.emotional_state)
                    sentence_buffer = sentence_buffer[_MAX_FLUSH_CHARS:]
                continue

            if boundary:
                parts = boundary.split(sentence_buffer)
                if len(parts) > 1:
                    for part in parts[:-1]:
                        if part.strip():
                            voice.speak(part, brain.persona.emotional_state)
                    sentence_buffer = parts[-1]

        # Final TTS — chunks already printed everything
        if sentence_buffer.strip():
            voice.speak(sentence_buffer, brain.persona.emotional_state)
    logger.info("Loading AI models (Whisper, VAD, Kokoro)...")
    try:
        voice = VoiceEngine(config, on_speech=on_speech, bridge=brain.bridge)
        voice.start()

        # Connection test & Dynamic Welcome
        logger.debug("Requesting dynamic welcome message from LLM...")
        welcome_msg = ""
        async for chunk in brain.chat("Give me a one-sentence warm, friendly welcome message as you start up. Speak only in English."):
            welcome_msg += chunk

        print("\n" + "="*40, flush=True)
        print("   Charlie is online and listening", flush=True)
        print("="*40 + "\n", flush=True)
        print(f"\rCharlie: {welcome_msg}", flush=True)
        voice.speak(welcome_msg, brain.persona.emotional_state)

        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
    finally:
        if 'brain' in locals():
            await brain.close()
        if 'voice' in locals():
            voice.stop()
        if 'brain' in locals():
            await brain.mcp_client.close()
        logging.shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
