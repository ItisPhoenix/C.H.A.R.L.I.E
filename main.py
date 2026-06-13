import sys
import io
import os

# Fix encoding before any other imports
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import asyncio
import logging
from charlie.core import Brain
from charlie.config import config
from charlie.voice import VoiceEngine

# Ensure log directory exists
os.makedirs("logs", exist_ok=True)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.log_file, encoding='utf-8')
    ]
)
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
        print(f"\rHeard: {text}")
        if voice.is_speaking.is_set():
            logger.info("Barge-in detected, stopping TTS")
            voice.stop_tts()
            
        response = await brain.chat(text)
        print(f"Charlie: {response}")
        voice.speak(response)

    logger.info("Loading AI models (Whisper, VAD, Kokoro)...")
    try:
        voice = VoiceEngine(config, on_speech=on_speech)
        voice.start()
        
        # Connection test & Dynamic Welcome
        logger.info("Connecting to brain for welcome message...")
        welcome_msg = await brain.chat("Give me a one-sentence warm, friendly welcome message as you start up. Speak only in English.")
        
        print("\n" + "="*40)
        print("   Charlie is online and listening")
        print("="*40 + "\n")
        
        print(f"Charlie: {welcome_msg}")
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

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
