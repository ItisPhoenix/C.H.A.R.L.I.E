"""Unified entry point for Charlie's voice and web runtime.

Usage:
    python run.py              Full mode: voice pipeline plus web/API bridge
    python run.py --web-only   Web/API-only mode (no mic/speaker needed)

In full mode, main.py spawns the web server as a subprocess.
In web-only mode, only the FastAPI server starts.
"""

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

# Web-only mode never inherits a launch_id from a parent process (main.py
# generates one for its subprocess) -- set one here, before any charlie.*
# import, since charlie/__init__.py eagerly imports charlie.config and bakes
# in whatever CHARLIE_LAUNCH_ID is set at that moment.
if "--web-only" in sys.argv:
    os.environ.setdefault("CHARLIE_LAUNCH_ID", str(uuid.uuid4()))

# Windows event-loop policy (must precede zmq/asyncio imports)
from charlie.runtime import configure as _configure_platform

_configure_platform()

# Ensure project root is on path
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


def run_full() -> int:
    """Run the voice pipeline and web/API bridge."""

    print("=" * 50)
    print("  Charlie Assistant (Full Mode)")
    print("  - Voice Loop: Starting (microphone readiness pending)")
    print("  - Web/API bridge: Available at the configured local endpoint")
    print("=" * 50)

    from main import main

    try:
        return asyncio.run(main())
    except KeyboardInterrupt:
        return 0


def run_web_only() -> int:
    """Run just the web server -- no voice hardware needed."""

    import uvicorn

    from charlie.config import config
    from charlie.web_server import app

    print("=" * 50)
    print("  Charlie Web/API server (web-only mode)")
    print(f"  - API: Active at http://{config.charlie_host}:{config.charlie_port}/")
    print("=" * 50)

    try:
        server_config = uvicorn.Config(
            app,
            host=config.charlie_host,
            port=config.charlie_port,
            log_level="info",
            loop="none",
        )
        server = uvicorn.Server(server_config)
        server.run()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"Web server failed: {exc}", file=sys.stderr)
        return 1


def cli_main(argv: list[str] | None = None) -> int:
    """Canonical user-facing CLI launcher boundary."""
    parser = argparse.ArgumentParser(description="Charlie: voice assistant and local web/API runtime")
    parser.add_argument(
        "--web-only",
        action="store_true",
        help="Start only the web/API server (no voice pipeline)",
    )
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1

    try:
        if args.web_only:
            return run_web_only()
        return run_full()
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        mode_str = "web-only" if args.web_only else "full"
        print(f"Launcher failed in {mode_str} mode: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(cli_main())
