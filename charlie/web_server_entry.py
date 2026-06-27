"""Entry point for Charlie web server subprocess."""

import asyncio
import sys

# Windows event-loop policy (must precede zmq/asyncio imports)
from charlie.platform import configure as _configure_platform

_configure_platform()

from pathlib import Path

# Ensure charlie package is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from charlie.web_server import app, start_server


# Suppress pyzmq CancelledError traceback on Windows shutdown.
# When uvicorn cancels the event loop on SIGINT, pyzmq's _chain callback
# calls Future.exception() on a cancelled future, raising CancelledError
# inside asyncio's callback dispatch. A custom exception handler on the
# running loop suppresses this cosmetic error.
@app.on_event("startup")
async def _install_zmq_guard():
    loop = asyncio.get_event_loop()
    _orig_call = loop.call_exception_handler

    def _guarded_call(context):
        exc = context.get("exception")
        if isinstance(exc, asyncio.CancelledError):
            return
        _orig_call(context)

    loop.call_exception_handler = _guarded_call


if __name__ == "__main__":
    try:
        start_server()
    except KeyboardInterrupt:
        pass
