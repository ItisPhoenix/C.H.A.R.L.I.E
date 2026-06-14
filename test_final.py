
import asyncio
from charlie.core import Brain
from charlie.config import config
import logging

logging.basicConfig(level=logging.INFO)

async def test():
    def thought_logger(msg):
        print(f"[THOUGHT] {msg}")

    brain = Brain(config, on_thought_callback=thought_logger)
    
    # 1. Test Persona (Anti-AI claim)
    print("\n--- Testing Persona Hardening ---")
    print("USER: Are you an AI?")
    response = []
    async for chunk in brain.chat("Are you an AI?"):
        response.append(chunk)
    print(f"CHARLIE: {''.join(response)}")
    
    # 2. Test Async Research (Non-blocking)
    print("\n--- Testing Async Research ---")
    async for chunk in brain.chat("research the current status of the rust programming language"):
        print(chunk, end="", flush=True)
    print()
    
    await asyncio.sleep(5)
    await brain.close()

if __name__ == "__main__":
    asyncio.run(test())
