"""Local, owner-scoped file-IPC service for one persistent browser session.

No network listener and no CDP exposure: the CLI writes a request file under
%LOCALAPPDATA%\\Charlie\\browser_ipc and waits for the response file. A detached
service process owns the Playwright context so state survives between CLI calls.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger("charlie.browser.service")

IPC_DIR = Path(os.environ.get("LOCALAPPDATA", ".")) / "Charlie" / "browser_ipc"
TOKEN_FILE = IPC_DIR / "token"
PID_FILE = IPC_DIR / "service.pid"
STOP_FILE = IPC_DIR / "stop"
POLL_S = 0.03
DEFAULT_TIMEOUT_MS = 120_000


def _prepare() -> None:
    IPC_DIR.mkdir(parents=True, exist_ok=True)
    if not TOKEN_FILE.exists():
        TOKEN_FILE.write_text(secrets.token_hex(32), encoding="ascii")


def _token() -> str:
    _prepare()
    return TOKEN_FILE.read_text(encoding="ascii").strip()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_pid() -> int:
    try:
        return int(PID_FILE.read_text(encoding="ascii").strip())
    except Exception:
        return 0


def is_running() -> bool:
    return _pid_alive(_read_pid())


def start() -> bool:
    """Start the detached service if needed. Returns True if running afterwards."""
    if is_running():
        return True
    STOP_FILE.unlink(missing_ok=True)
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(
            [sys.executable, "-m", "charlie.browser.service", "--run"],
            cwd=str(Path(__file__).resolve().parents[2]),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags, close_fds=True,
        )
    except Exception as exc:
        logger.warning("browser service start failed: %s", exc)
        return False
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if is_running():
            return True
        time.sleep(0.1)
    return False


def stop() -> bool:
    pid = _read_pid()
    if not _pid_alive(pid):
        return False
    STOP_FILE.write_text("1", encoding="ascii")
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and _pid_alive(pid):
        time.sleep(0.1)
    return not _pid_alive(pid)


def call(command: str, data: dict[str, Any], *, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> dict[str, Any]:
    """Send one command to the service and wait for its response."""
    if not start():
        return {"ok": False, "error": "browser service could not be started"}
    req_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
    req = IPC_DIR / f"{req_id}.req.json"
    resp = IPC_DIR / f"{req_id}.resp.json"
    payload = {"id": req_id, "token": _token(), "command": command, "data": data}
    try:
        tmp = req.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(req)
    except Exception as exc:
        return {"ok": False, "error": f"request write failed: {type(exc).__name__}: {exc}"}

    deadline = time.monotonic() + max(1, timeout_ms) / 1000
    try:
        while time.monotonic() < deadline:
            if resp.exists():
                try:
                    return json.loads(resp.read_text(encoding="utf-8"))
                except Exception:
                    time.sleep(POLL_S)
                    continue
            if not is_running():
                return {"ok": False, "error": "browser service exited before responding"}
            time.sleep(POLL_S)
    finally:
        req.unlink(missing_ok=True)
        resp.unlink(missing_ok=True)
    return {"ok": False, "error": "browser service timeout"}


def serve() -> int:
    """Service loop: owns the Playwright context until asked to stop."""
    _prepare()
    PID_FILE.write_text(str(os.getpid()), encoding="ascii")
    # Dedicated profile: the in-process gateway path uses config.browser_profile_path.
    from charlie import config
    config.browser_profile_path = str(IPC_DIR / "profile")
    from charlie.browser.gateway import dispatch_command

    logger.info("browser service ready | pid=%s ipc=%s", os.getpid(), IPC_DIR)
    while True:
        try:
            if STOP_FILE.exists():
                STOP_FILE.unlink(missing_ok=True)
                break
            requests = sorted(IPC_DIR.glob("*.req.json"), key=lambda p: p.stat().st_mtime)
            for req in requests:
                resp = req.with_name(req.name.replace(".req.json", ".resp.json"))
                try:
                    payload = json.loads(req.read_text(encoding="utf-8"))
                    if payload.get("token") != _token():
                        result = {"ok": False, "error": "browser service token mismatch"}
                    else:
                        result = dispatch_command(str(payload.get("command", "")),
                                                  dict(payload.get("data", {})))
                except Exception as exc:
                    result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                try:
                    tmp = resp.with_suffix(".tmp")
                    tmp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
                    tmp.replace(resp)
                except Exception:
                    logger.debug("browser response write failed", exc_info=True)
                req.unlink(missing_ok=True)
            time.sleep(POLL_S)
        except Exception:
            logger.warning("browser service loop error", exc_info=True)
            time.sleep(0.2)
    PID_FILE.unlink(missing_ok=True)
    from charlie.browser.controller import shutdown
    shutdown()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="charlie.browser.service")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    if args.stop:
        raise SystemExit(0 if stop() else 1)
    raise SystemExit(serve())
