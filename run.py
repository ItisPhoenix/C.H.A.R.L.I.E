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


def _frontend_build_is_stale(frontend_dir: Path, dist_dir: Path) -> bool:
    """True if any frontend source file is newer than the last build output."""
    index_html = dist_dir / "index.html"
    if not index_html.exists():
        return True
    build_time = index_html.stat().st_mtime
    src_root = frontend_dir / "src"
    if not src_root.exists():
        return False
    return any(
        f.stat().st_mtime > build_time for f in src_root.rglob("*") if f.is_file()
    )


def check_and_build_frontend() -> None:
    """Ensure a current frontend exists; refuse to serve stale output after a failed build."""
    root = Path(__file__).parent
    frontend_dir = root / "frontend"
    dist_dir = frontend_dir / "dist"

    if not frontend_dir.exists():
        raise RuntimeError("Frontend directory not found; refusing to start without the dashboard build.")

    if not _frontend_build_is_stale(frontend_dir, dist_dir):
        return

    print("Frontend build missing or stale. Compiling frontend...")
    import shutil
    import subprocess

    npm_path = shutil.which("npm")
    if not npm_path:
        raise RuntimeError("npm was not found; install Node.js/npm and run 'npm run build' in frontend/.")

    try:
        if not (frontend_dir / "node_modules").exists():
            print("Running 'npm install' in frontend...")
            subprocess.run(
                [npm_path, "install"],
                cwd=str(frontend_dir),
                check=True,
            )
        print("Running 'npm run build' in frontend...")
        subprocess.run(
            [npm_path, "run", "build"],
            cwd=str(frontend_dir),
            check=True,
        )
        print("Frontend built successfully!")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "Frontend build failed; refusing to start with stale dashboard assets. "
            "See the npm output above and fix the build before restarting."
        ) from e


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

    from charlie.config import config
    from charlie.web_server import app

    print("=" * 50)
    print("  Charlie Web Dashboard (web-only mode)")
    print(f"  - Web Dashboard: Active (http://{config.charlie_host}:{config.charlie_port})")
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
        # loop="asyncio" hardcodes ProactorEventLoop on win32 regardless of the
        # process-wide policy, which breaks pyzmq (needs add_reader, Proactor doesn't
        # have it -- see charlie/web_server.py:start_server for the same fix).
        # "none" defers loop creation to _configure_platform()'s WindowsSelectorEventLoopPolicy.
        server_config = uvicorn.Config(
            app,
            host=config.charlie_host,
            port=config.charlie_port,
            log_level="info",
            loop="none",
        )
        server = uvicorn.Server(server_config)
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
