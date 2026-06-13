# ruff: noqa: E402
import sys
import io
import os
import logging

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

async def main():
    logger.info("Charlie is waking up...")
    
    try:
        brain = Brain(config)
    except Exception as e:
        logger.error(f"Failed to initialize Brain: {e}")
        return

    loop = asyncio.get_running_loop()

    def on_speech(text: str):
        logger.info(f"Speech detected: {text}")
        asyncio.run_coroutine_threadsafe(_process(text, brain, voice), loop)

    async def _process(text, brain, voice):
        print(f"\rHeard: {text}", flush=True)
        if voice.is_speaking.is_set():
            logger.debug("Barge-in event: Stopping ongoing TTS")
            voice.stop_tts()
            
        print("Charlie is thinking...", end="\r", flush=True)
        
        response = await brain.chat(text)
        # Brain.chat handles the tool loop internally and returns the final synthesized answer.
        # Clear the "thinking" indicator
        print("\r" + " " * 30 + "\r", end="", flush=True)
        
        if response and not response.strip().startswith("TOOL:"):
            print(f"Charlie: {response}", flush=True)
            voice.speak(response)
        elif response:
            msg = "I've gathered some data, but I'm still trying to make sense of it. Could you ask me in a different way?"
            print(f"Charlie: {msg}", flush=True)
            voice.speak(msg)
        else:
            logger.warning("Brain returned empty response.")
    logger.info("Loading AI models (Whisper, VAD, Kokoro)...")
    try:
        voice = VoiceEngine(config, on_speech=on_speech)
        voice.start()
        
        # Connection test & Dynamic Welcome
        logger.debug("Requesting dynamic welcome message from LLM...")
        welcome_msg = await brain.chat("Give me a one-sentence warm, friendly welcome message as you start up. Speak only in English.")
        
        print("\n" + "="*40, flush=True)
        print("   Charlie is online and listening", flush=True)
        print("="*40 + "\n", flush=True)
        
        print(f"Charlie: {welcome_msg}", flush=True)
        voice.speak(welcome_msg)

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
        logging.shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
