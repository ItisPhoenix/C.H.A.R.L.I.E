import asyncio
from charlie.core import Brain
from charlie.config import config

async def test():
    print("--- Initializing Charlie ---")
    brain = Brain(config)
    print("--- Testing Chat Logic ---")
    # We mock the chat result for this environment to show flow
    response = await brain.chat("Test message")
    print(f"Response: {response}")

if __name__ == "__main__":
    asyncio.run(test())
