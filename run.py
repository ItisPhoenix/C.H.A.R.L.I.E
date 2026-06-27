"""Unified entry point for Charlie -- voice + web dashboard in one process.

Usage:
    python run.py              Full mode: voice pipeline + web dashboard
    python run.py --web-only   Web-only mode: just the web UI (no mic/speaker needed)

In full mode, main.py spawns the web server as a subprocess.
In web-only mode, only the FastAPI server starts (useful for testing the UI).
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Windows event-loop policy (must precede zmq/asyncio imports)
from charlie.platform import configure as _configure_platform

_configure_platform()

# Ensure project root is on path
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


def run_full():
    """Run voice pipeline + web dashboard (the default)."""
    from main import main

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    finally:
        os._exit(0)


def run_web_only():
    """Run just the web server -- no voice hardware needed."""
    import uvicorn

    from charlie.web_server import app

    print("=" * 50)
    print("  Charlie Web Dashboard (web-only mode)")
    print("  Open http://localhost:8000 in your browser")
    print("=" * 50)

    try:
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=8000,
            log_level="info",
            loop="asyncio",
        )
        server = uvicorn.Server(config)
        server.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Charlie: voice assistant + web dashboard"
    )
    parser.add_argument(
        "--web-only",
        action="store_true",
        help="Start only the web dashboard (no voice pipeline)",
    )
    args = parser.parse_args()

    if args.web_only:
        run_web_only()
    else:
        run_full()
