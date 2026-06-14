import asyncio
from charlie.core import Brain
from charlie.config import config

async def test():
    print("--- Initializing Charlie ---")
    brain = Brain(config)
    print("--- Testing Chat Logic ---")
    print("CHARLIE: ", end="", flush=True)
    async for chunk in brain.chat("What's the weather like?"):
        print(chunk, end="", flush=True)
    print()

    print("\n--- Testing Research Command ---")
    # This will trigger deep_research
    print("CHARLIE: ", end="", flush=True)
    async for chunk in brain.chat("research local AI agents"):
        print(chunk, end="", flush=True)
    print()
if __name__ == "__main__":
    asyncio.run(test())
