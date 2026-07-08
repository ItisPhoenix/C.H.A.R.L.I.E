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
from charlie.runtime import configure as _configure_platform

_configure_platform()

# Ensure project root is on path
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


def check_and_build_frontend():
    """Ensure the frontend is built, compiling it if necessary."""
    root = Path(__file__).parent
    frontend_dir = root / "frontend"
    dist_dir = frontend_dir / "out"

    if dist_dir.exists() and (dist_dir / "index.html").exists():
        return

    if not frontend_dir.exists():
        print("Warning: frontend directory not found. Web UI cannot be built.")
        return

    print("Frontend build not found. Compiling frontend...")
    import shutil
    import subprocess

    npm_path = shutil.which("npm")
    if not npm_path:
        print("Warning: npm not found. Please install node/npm and run 'npm run build' inside 'frontend' manually.")
        return

    try:
        print("Running 'npm install' in frontend...")
        subprocess.run(
            [npm_path, "install"],
            cwd=str(frontend_dir),
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print("Running 'npm run build' in frontend...")
        subprocess.run(
            [npm_path, "run", "build"],
            cwd=str(frontend_dir),
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print("Frontend built successfully!")
    except subprocess.CalledProcessError as e:
        print(f"Warning: Failed to compile frontend automatically: {e}")


def run_full():
    """Run voice pipeline + web dashboard (the default)."""
    check_and_build_frontend()

    print("=" * 50)
    print("  Charlie Assistant & Web Dashboard (Full Mode)")
    print("  - Voice Loop: Active (listening to mic)")
    print("  - Web Dashboard: Active (http://localhost:8000)")
    print("=" * 50)

    from main import main

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    finally:
        os._exit(0)


def run_web_only():
    """Run just the web server -- no voice hardware needed."""
    check_and_build_frontend()

    import signal
    import threading

    import uvicorn

    from charlie.web_server import app

    print("=" * 50)
    print("  Charlie Web Dashboard (web-only mode)")
    print("  - Web Dashboard: Active (http://localhost:8000)")
    print("=" * 50)

    # Force-exit safety net: if graceful shutdown hangs >5s, kill immediately.
    _force_exit_timer: threading.Timer | None = None
    _server_ref: list = []  # mutable cell so signal handler can access server

    def _schedule_force_exit():
        nonlocal _force_exit_timer
        if _force_exit_timer is not None:
            return
        _force_exit_timer = threading.Timer(5.0, os._exit, args=[1])
        _force_exit_timer.daemon = True
        _force_exit_timer.start()

    def _cancel_force_exit():
        nonlocal _force_exit_timer
        if _force_exit_timer is not None:
            _force_exit_timer.cancel()
            _force_exit_timer = None

    def _sigint_handler(signum, frame):
        _schedule_force_exit()  # 5s safety net
        # Tell uvicorn to shut down gracefully.
        if _server_ref:
            _server_ref[0].should_exit = True
        # Second Ctrl+C = immediate kill.
        signal.signal(signal.SIGINT, lambda _s, _f: os._exit(1))

    signal.signal(signal.SIGINT, _sigint_handler)

    try:
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=8000,
            log_level="info",
            loop="asyncio",
        )
        server = uvicorn.Server(config)
        _server_ref.append(server)
        server.run()
    finally:
        _cancel_force_exit()
        os._exit(0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Charlie: voice assistant + web dashboard")
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
