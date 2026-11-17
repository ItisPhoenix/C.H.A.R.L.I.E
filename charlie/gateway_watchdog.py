"""User-level watchdog for the Windows companion and device gateway.

Started by a current-user scheduled task, not by OpenCode. It restarts a service
only after an unexpected exit. Deliberate stops write a no-restart marker and are
never undone until the owner starts the service again manually.
"""
from __future__ import annotations

import time
from pathlib import Path

from charlie.companion_launcher import (
    LOG_DIR,
    NO_RESTART,
    ROOT,
    STOP_FILE,
    _live_pid,
    start,
)

LOCK_PATH = ROOT / "data" / "charlie-watchdog.lock"
INTERVAL_S = 15.0
LOG = LOG_DIR / "watchdog.log"
_MARKERS = {"gateway": "--device-gateway", "companion": "--companion"}


def log(message: str) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        pass


def run() -> int:
    from charlie.single_instance import SingleInstanceError, SingleInstanceLock

    try:
        lock = SingleInstanceLock(str(LOCK_PATH)).acquire()
    except SingleInstanceError:
        log("watchdog already running; exiting")
        return 1
    log("watchdog started")
    last: dict[str, str] = {}
    try:
        while True:
            try:
                for kind, marker in _MARKERS.items():
                    if NO_RESTART[kind].exists():
                        state = "suppressed"
                    elif kind == "gateway" and STOP_FILE.exists():
                        # Emergency stop file means a person/owner requested the stop.
                        NO_RESTART[kind].write_text("emergency stop", encoding="ascii")
                        state = "suppressed"
                    elif _live_pid(marker) is None:
                        log(f"{kind}: unexpected exit detected; restarting")
                        if start(kind) == 0:
                            state = "running"
                        else:
                            state = "start_failed"
                    else:
                        state = "running"
                    if last.get(kind) != state:
                        log(f"{kind}: state={state}")
                        last[kind] = state
            except Exception as exc:
                log(f"watchdog poll error: {type(exc).__name__}: {exc}")
            time.sleep(INTERVAL_S)
    except KeyboardInterrupt:
        log("watchdog stopped by keyboard interrupt")
    finally:
        lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
