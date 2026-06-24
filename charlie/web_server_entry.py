"""Entry point for Charlie web server subprocess."""

import sys
import asyncio

# Windows: Force Selector event loop BEFORE any other imports.
# uvicorn and pyzmq both override/create event loops — this is the
# single authoritative policy set for the web server subprocess.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    import warnings as _warnings
    _warnings.filterwarnings("ignore", message=".*add_reader.*", category=RuntimeWarning)

from pathlib import Path

# Ensure charlie package is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from charlie.web_server import start_server, app

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
