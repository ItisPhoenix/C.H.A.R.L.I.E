"""Entry point for Charlie floating pet subprocess."""

import sys
from pathlib import Path

# Ensure charlie package is importable (must precede charlie imports)
sys.path.insert(0, str(Path(__file__).parent.parent))

# Windows event-loop policy (must precede zmq/asyncio imports)
from charlie.runtime import configure as _configure_platform

_configure_platform()


from charlie.pet_window import main

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
