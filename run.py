"""Unified entry point for Charlie -- voice runtime plus React HUD bridge.

Usage:
    python run.py              Full mode: voice pipeline + React HUD bridge
    python run.py --web-only   Web-only mode: just the React HUD (no mic/speaker needed)

In full mode, main.py spawns the web server as a subprocess.
In web-only mode, only the FastAPI server starts (useful for testing the UI).
"""

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
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


_FRONTEND_SHARED_INPUTS = (
    Path("shared/event_contract.json"),
    Path("shared/presentation_contract.json"),
)
_FRONTEND_CONFIG_GLOBS = (
    "vite.config.*",
    "tsconfig*.json",
    "package.json",
    "package-lock.json",
    "index.html",
)
_FRONTEND_DIST_ENV = "CHARLIE_FRONTEND_DIST"


def _frontend_build_inputs(frontend_dir: Path) -> list[Path]:
    """Return files that can affect Vite's production bundle.

    Keep this list rooted in actual Vite inputs. Backend-only files and generated
    output are intentionally excluded. Shared JSON files are listed because
    frontend runtime modules import them directly.
    """
    root = frontend_dir.parent
    inputs: set[Path] = set()
    for directory in (frontend_dir / "src", frontend_dir / "public"):
        if directory.is_dir():
            inputs.update(path for path in directory.rglob("*") if path.is_file())
    for pattern in _FRONTEND_CONFIG_GLOBS:
        inputs.update(path for path in frontend_dir.glob(pattern) if path.is_file())
    inputs.update(path for path in (root / relative for relative in _FRONTEND_SHARED_INPUTS) if path.is_file())
    return sorted(inputs, key=lambda path: path.relative_to(root).as_posix())


def _frontend_inputs_fingerprint(frontend_dir: Path) -> str:
    digest = hashlib.sha256()
    root = frontend_dir.parent
    for path in _frontend_build_inputs(frontend_dir):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _read_frontend_manifest(dist_dir: Path) -> dict | None:
    manifest_path = dist_dir / "charlie-build.json"
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _git_build_identity(root: Path) -> tuple[str | None, bool | None]:
    try:
        git_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain", "--untracked-files=no"],
                cwd=root,
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        )
        return git_sha, dirty
    except (OSError, subprocess.CalledProcessError):
        return None, None


def _frontend_build_is_stale(frontend_dir: Path, dist_dir: Path) -> bool:
    """True when required build inputs differ from the served frontend build."""
    if not (dist_dir / "index.html").is_file():
        return True
    manifest = _read_frontend_manifest(dist_dir)
    if not manifest or manifest.get("input_fingerprint") != _frontend_inputs_fingerprint(frontend_dir):
        return True
    git_sha, dirty = _git_build_identity(frontend_dir.parent)
    if git_sha is not None and manifest.get("git_sha") != git_sha:
        return True
    if dirty is not None and manifest.get("dirty") != dirty:
        return True
    return False


def _frontend_dist_is_user_accessible(dist_dir: Path) -> bool:
    """Return whether the current user can read and replace the served build.

    Windows sandboxes and stale elevated builds can leave ``frontend/dist`` with
    an ACL that excludes the normal user.  Detect that state before attempting
    the rename transaction so a fresh build can be served from an owned path.
    """
    if not dist_dir.exists():
        return True
    try:
        next(dist_dir.iterdir(), None)
        return os.access(dist_dir, os.R_OK | os.W_OK | os.X_OK)
    except OSError:
        return False


def _publish_frontend_build(staging_dir: Path, dist_dir: Path) -> None:
    """Publish a verified build while retaining old output on build failure."""
    backup_dir = dist_dir.with_name(f"{dist_dir.name}.previous")
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    moved_old = False
    try:
        if dist_dir.exists():
            dist_dir.rename(backup_dir)
            moved_old = True
        staging_dir.rename(dist_dir)
    except Exception:
        if dist_dir.exists():
            shutil.rmtree(dist_dir)
        if moved_old and backup_dir.exists():
            backup_dir.rename(dist_dir)
        raise
    finally:
        if backup_dir.exists():
            shutil.rmtree(backup_dir)


def check_and_build_frontend(project_root: Path | None = None) -> None:
    """Ensure a current frontend exists; refuse to serve stale output after a failed build."""
    root = project_root or Path(__file__).parent
    frontend_dir = root / "frontend"
    dist_dir = frontend_dir / "dist"
    os.environ.pop(_FRONTEND_DIST_ENV, None)

    if not frontend_dir.exists():
        raise RuntimeError("Frontend directory not found; refusing to start without the React HUD build.")

    if not _frontend_build_is_stale(frontend_dir, dist_dir):
        return

    print("Frontend build missing or stale. Compiling frontend...")
    npm_path = shutil.which("npm")
    if not npm_path:
        raise RuntimeError("npm was not found; install Node.js/npm and run 'npm run build' in frontend/.")

    use_runtime_dist = not _frontend_dist_is_user_accessible(dist_dir)
    staging_dir = Path(
        tempfile.mkdtemp(
            prefix="charlie-runtime-build-" if use_runtime_dist else ".charlie-build-",
            dir=None if use_runtime_dist else frontend_dir,
        )
    )
    build_env = os.environ.copy()
    build_env["CHARLIE_FRONTEND_OUT_DIR"] = str(staging_dir)
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
            env=build_env,
            check=True,
        )
        manifest = _read_frontend_manifest(staging_dir)
        if not (staging_dir / "index.html").is_file() or not manifest:
            raise RuntimeError("Frontend build completed without a valid charlie-build.json identity.")
        manifest["input_fingerprint"] = _frontend_inputs_fingerprint(frontend_dir)
        (staging_dir / "charlie-build.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        if not use_runtime_dist:
            _publish_frontend_build(staging_dir, dist_dir)
            os.environ[_FRONTEND_DIST_ENV] = str(dist_dir)
        else:
            os.environ[_FRONTEND_DIST_ENV] = str(staging_dir)
            print(
                "Canonical frontend/dist is not accessible to this user; "
                f"serving the verified build from {staging_dir}."
            )
        print("Frontend built successfully!")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "Frontend build failed; refusing to start with stale React HUD assets. "
            "See the npm output above and fix the build before restarting."
        ) from e
    finally:
        if staging_dir.exists() and not use_runtime_dist:
            shutil.rmtree(staging_dir)


def run_full():
    """Run voice pipeline + React HUD bridge (the default)."""
    check_and_build_frontend()

    print("=" * 50)
    print("  Charlie Assistant + React HUD (Full Mode)")
    print("  - Voice Loop: Starting (microphone readiness pending)")
    print("  - React HUD: Available at http://localhost:8000/")
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
    print("  Charlie React HUD (web-only mode)")
    print(f"  - React HUD: Active at http://{config.charlie_host}:{config.charlie_port}/")
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
    parser = argparse.ArgumentParser(description="Charlie: voice assistant + React HUD")
    parser.add_argument(
        "--web-only",
        action="store_true",
        help="Start only the React HUD bridge (no voice pipeline)",
    )
    args = parser.parse_args()

    if args.web_only:
        run_web_only()
    else:
        run_full()
