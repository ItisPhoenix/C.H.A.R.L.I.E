# ruff: noqa: E402
import sys
import io
import os
import logging
import re
import time
import asyncio
from pathlib import Path

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
file_handler.setFormatter(file_formatter)

console_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(console_formatter)

root_logger.handlers = []
root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

# 3. NOW IMPORT CHARLIE MODULES
from charlie.config import config
from charlie.session_store import SessionStore
from charlie.core import Brain
from charlie.voice import VoiceEngine
from charlie.personality import get_emotion_for_context, parse_voice_command

logger = logging.getLogger("charlie.main")

# Sentence/clause splitting for early TTS dispatch
_CLAUSE_BOUNDARY = re.compile(r'(?<=[,;:])\s+(?=[A-Z"])')
_SENTENCE_BOUNDARY = re.compile(r'(?<=[.!?])\s+')
_MAX_FLUSH_CHARS = 200  # Force-flush if no boundary seen within this many chars (~30-40 words)

async def main():
    logger.info("Charlie is waking up...")
    voice = None
    store = None
    speech_echo_cooldown = 0.0
    last_emotion = "neutral"

    try:
        store = SessionStore(config.session_db_path)
    except Exception as e:
        logger.error(f"Failed to initialize SessionStore: {e}")
        return

    def speaking_callback(text):
        if voice:
            voice.speak(text, last_emotion)

    try:
        brain = Brain(config, on_thought_callback=speaking_callback, session_store=store)
    except Exception as e:
        logger.error(f"Failed to initialize Brain: {e}")
        if store:
            store.close()
        return

    loop = asyncio.get_running_loop()

    def on_speech(text: str):
        logger.info(f"Speech detected: {text}")
        asyncio.run_coroutine_threadsafe(_process(text, brain, voice), loop)

    async def _process(text, brain, voice):
        nonlocal speech_echo_cooldown, last_emotion
        if time.time() < speech_echo_cooldown:
            logger.info(f"Echo suppressed: {text}")
            return

        print(f"\rHeard: {text}", flush=True)
        if voice.is_speaking.is_set():
            # Command words that trigger barge-in even during TTS
            _BARGE_COMMANDS = {"stop", "wait", "no", "cancel", "quiet", "shut", "enough"}
            words = set(text.lower().strip().split())
            if words & _BARGE_COMMANDS:
                logger.info("Barge-in: Command word detected. Stopping TTS.")
                voice.stop_tts()
                speech_echo_cooldown = time.time() + 1.5
            else:
                # Everything else during TTS is echo — suppress
                logger.info(f"Echo suppressed (during TTS): {text}")
                return

        # Route !search command
        if text.strip().startswith("!search "):
            query = text.strip()[len("!search "):].strip()
            print("Searching history...", end="\r", flush=True)
            results = store.search(query)
            if not results:
                response_str = "No matching history found."
            else:
                response_str = f"Found {len(results)} result(s):\n"
                for role, content in results:
                    truncated = content[:120] + "..." if len(content) > 120 else content
                    response_str += f"- [{role}]: {truncated}\n"
            print(f"\n{response_str}", flush=True)
            voice.speak(response_str, last_emotion)
            return

        # Store user message
        try:
            store.append("user", text)
        except Exception as e:
            logger.warning(f"Failed to archive user message: {e}")
        # Voice command detection (before LLM call)
        cmd_emotion = parse_voice_command(text)
        if cmd_emotion is not None:
            last_emotion = cmd_emotion
            ack_map = {"energetic": "Got it. Switching to energetic.", "calm": "Got it, calming down."}
            ack = ack_map.get(cmd_emotion, "Got it.")
            voice.speak(ack, cmd_emotion)
            return

        # Detect emotion for this turn
        detected_emotion = get_emotion_for_context(text, [])

        # Sparkle announcements on emotion change
        sparkle = ""
        if detected_emotion != last_emotion:
            sparkle_map = {"energetic": "Oh, exciting! ", "calm": "Got it, calming down. ", "sad": "I hear you. "}
            sparkle = sparkle_map.get(detected_emotion, "")
        last_emotion = detected_emotion

        print("Charlie is thinking...", end="\r", flush=True)

        # Streaming buffer
        sentence_buffer = ""
        full_reply_buffer = ""
        is_first_chunk = True

        async for chunk in brain.chat_stream(text):
            if is_first_chunk:
                print("\r" + " " * 30 + "\r", end="", flush=True) # Clear thinking
                is_first_chunk = False
            print(chunk, end="", flush=True)
            sentence_buffer += chunk
            full_reply_buffer += chunk

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
                    voice.speak(sentence_buffer[:idx], detected_emotion)
                    sentence_buffer = sentence_buffer[idx + 1:]
                else:
                    voice.speak(sentence_buffer[:_MAX_FLUSH_CHARS], detected_emotion)
                    sentence_buffer = sentence_buffer[_MAX_FLUSH_CHARS:]
                continue

            if boundary:
                parts = boundary.split(sentence_buffer)
                if len(parts) > 1:
                    for part in parts[:-1]:
                        if part.strip():
                            voice.speak(part, detected_emotion)
                    sentence_buffer = parts[-1]

        # Final TTS — chunks already printed everything
        if sentence_buffer.strip():
            voice.speak(sparkle + sentence_buffer, detected_emotion)

        # Archive assistant reply
        if full_reply_buffer.strip():
            try:
                store.append("assistant", full_reply_buffer)
            except Exception as e:
                logger.warning(f"Failed to archive assistant message: {e}")

        # Learning loop: extract 0-1 user preferences via fast LLM
        if full_reply_buffer.strip() and text.strip():
            try:
                learning_prompt = (
                    f"User said: {text}\n"
                    f"Charlie replied: {full_reply_buffer}\n"
                    "Extract 0-1 new user preferences (e.g., 'prefers short answers'). "
                    "Output ONLY the preference line, or output nothing if nothing new."
                )
                learning = ""
                async for chunk in brain.chat_stream(learning_prompt):
                    learning += chunk
                learning = learning.strip() if learning.strip() else ""
                if learning:
                    u_path = Path(config.user_file)
                    existing = u_path.read_text(encoding="utf-8") if u_path.exists() else ""
                    if learning not in existing:
                        with open(u_path, "a", encoding="utf-8") as f:
                            f.write(f"\n{learning}")
                        logger.info(f"Learning: {learning}")
            except Exception as e:
                logger.debug(f"Learning loop skipped: {e}")
    logger.info("Loading AI models (Whisper, VAD, Kokoro)...")
    try:
        voice = VoiceEngine(config, on_speech=on_speech)
        voice.start()

        # Connection test & Dynamic Welcome
        logger.debug("Requesting dynamic welcome message from LLM...")
        welcome_msg = ""
        # Wrap the generator in a timeout to avoid hangs if LLM IP is unreachable
        try:
            async with asyncio.timeout(25.0):
                async for chunk in brain.chat_stream("Give me a one-sentence warm, friendly welcome message as you start up. Speak only in English."):
                    welcome_msg += chunk
        except asyncio.TimeoutError:
            logger.warning("Dynamic welcome timed out after 25s. Using fallback.")
            welcome_msg = "Welcome, Sir; I'm online and ready to help."
        except Exception as e:
            logger.warning(f"Dynamic welcome failed: {type(e).__name__}: {e}. Using fallback.")
            welcome_msg = "Welcome, Sir; I'm online and ready to help."

        print("=" * 40, flush=True)
        print("   Charlie is online and listening", flush=True)
        print("=" * 40, flush=True)
        print(f"\rCharlie: {welcome_msg}", flush=True)
        voice.speak(welcome_msg, "neutral")

        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Interrupt received, shutting down...")
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
    finally:
        if 'voice' in locals() and voice is not None:
            voice.stop()
        if 'brain' in locals():
            await brain.close()
        if 'store' in locals() and store is not None:
            store.close()

        logging.shutdown()
        # Force exit to ensure background threads don't hang the process on Windows
        os._exit(0)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        os._exit(0)
