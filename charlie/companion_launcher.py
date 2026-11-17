"""Repo-owned manual lifecycle control for the Windows companion and gateway.

No autostart, no supervisor: start/status/stop only. The runtime entry point is
still ``run.py``; this module owns process identity and the status file so those
operations no longer depend on a temporary diagnostic script outside the repo.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"
RUNTIME = ROOT / "run.py"
LOG_DIR = Path(os.environ["LOCALAPPDATA"]) / "Charlie" / "logs"
STATUS = Path(os.environ["LOCALAPPDATA"]) / "Charlie" / "device-gateway.status.json"
STOP_FILE = Path(os.environ["LOCALAPPDATA"]) / "Charlie" / "device-gateway.stop"
# A deliberate stop is distinct from a crash: the watchdog must never undo it.
NO_RESTART = {
    "gateway": Path(os.environ["LOCALAPPDATA"]) / "Charlie" / "device-gateway.no-restart",
    "companion": Path(os.environ["LOCALAPPDATA"]) / "Charlie" / "companion.no-restart",
}


def _live_pid(marker: str) -> int | None:
    try:
        import psutil
    except ImportError:
        return None
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmd = " ".join(proc.info.get("cmdline") or [])
            if str(RUNTIME) in cmd and marker in cmd:
                return int(proc.info["pid"])
        except (OSError, ValueError, psutil.Error):
            continue
    return None


def _spawn(marker: str, log_name: str) -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = LOG_DIR / log_name
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    with log.open("ab") as handle:
        child = subprocess.Popen(
            [str(PY), "-B", str(RUNTIME), marker],
            cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=handle,
            stderr=subprocess.STDOUT, creationflags=flags, close_fds=True,
        )
    return int(child.pid)


def start(kind: str) -> int:
    marker = "--companion" if kind == "companion" else "--device-gateway"
    existing = _live_pid(marker)
    if existing:
        print(f"{kind}: already running pid={existing}")
        return 0
    NO_RESTART[kind].unlink(missing_ok=True)
    pid = _spawn(marker, f"{kind}.log")
    for _ in range(40):
        live = _live_pid(marker)
        if live:
            print(f"{kind}: started pid={live} log={LOG_DIR / (kind + '.log')}")
            return 0
        time.sleep(0.25)
    print(f"{kind}: launch exited early (launcher pid={pid})")
    return 1


def status(kind: str) -> int:
    marker = "--companion" if kind == "companion" else "--device-gateway"
    pid = _live_pid(marker)
    print(f"{kind}: pid={pid or 'none'} no_restart={NO_RESTART[kind].exists()}")
    if kind == "gateway":
        from charlie.device_gateway import read_gateway_status

        print(dict(read_gateway_status()))
    return 0


def stop(kind: str) -> int:
    STOP_FILE.parent.mkdir(parents=True, exist_ok=True)
    NO_RESTART[kind].write_text("deliberate stop", encoding="ascii")
    if kind == "gateway":
        STOP_FILE.write_text("manual stop", encoding="ascii")
        for _ in range(20):
            if not _live_pid("--device-gateway"):
                print("gateway: stopped via emergency-stop file")
                return 0
            time.sleep(0.25)
        print("gateway: stop requested but process still alive")
        return 1
    pid = _live_pid("--companion")
    if pid is None:
        print("companion: not running")
        return 0
    result = subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=20)
    print(f"companion: stop requested pid={pid} rc={result.returncode}")
    return 0 if result.returncode == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "status", "stop"))
    parser.add_argument("kind", choices=("companion", "gateway"))
    args = parser.parse_args(argv)
    return {"start": start, "status": status, "stop": stop}[args.action](args.kind)


if __name__ == "__main__":
    raise SystemExit(main())
