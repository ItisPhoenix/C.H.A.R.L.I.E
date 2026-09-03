"""Entry point for Charlie web server subprocess."""

import sys
from pathlib import Path

# Ensure charlie package is importable (must precede charlie imports)
sys.path.insert(0, str(Path(__file__).parent.parent))

# Windows event-loop policy (must precede zmq/asyncio imports)
from charlie.runtime import configure as _configure_platform

_configure_platform()


from run import check_and_build_frontend

check_and_build_frontend(Path(__file__).resolve().parent.parent)

from charlie.web_server import start_server

# ZMQ guard and EventBus lifecycle are handled via FastAPI lifespan
# in web_server.py (lifespan context manager).


if __name__ == "__main__":
    try:
        start_server()
    except KeyboardInterrupt:
        pass
