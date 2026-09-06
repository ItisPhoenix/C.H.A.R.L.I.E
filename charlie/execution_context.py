"""Private cancellation context for runtime-owned synchronous operations."""

from __future__ import annotations

import contextvars
import os
import sys
import threading
from dataclasses import dataclass
from typing import Any, Optional

import psutil

_CURRENT_CONTEXT: contextvars.ContextVar[Optional["ExecutionContext"]] = contextvars.ContextVar(
    "charlie_execution_context", default=None
)


def get_current_execution_context() -> Optional["ExecutionContext"]:
    return _CURRENT_CONTEXT.get()


def activate_execution_context(context: "ExecutionContext") -> contextvars.Token:
    return _CURRENT_CONTEXT.set(context)


def reset_execution_context(token: contextvars.Token) -> None:
    _CURRENT_CONTEXT.reset(token)


@dataclass(frozen=True)
class OwnedProcess:
    """Identity captured when Charlie creates one owned process."""

    popen: Any
    identity: psutil.Process
    pid: int
    creation_time: float
    process_group_id: Optional[int]


def _capture_owned_process(process: Any) -> OwnedProcess:
    pid = int(process.pid)
    identity = psutil.Process(pid)
    creation_time = float(identity.create_time())
    process_group_id = None
    if sys.platform != "win32":
        process_group_id = os.getpgid(pid)
    return OwnedProcess(process, identity, pid, creation_time, process_group_id)


def _identity_state(owned: OwnedProcess) -> str:
    """Return owned, gone, or uncertain without trusting a reused PID."""
    try:
        if not owned.identity.is_running():
            return "gone"
        if float(owned.identity.create_time()) != owned.creation_time:
            return "uncertain"
        return "owned"
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return "gone"
    except (psutil.AccessDenied, OSError, ValueError):
        return "uncertain"


def _wait_owned(owned: list[OwnedProcess], timeout: float) -> set[int]:
    if not owned:
        return set()
    try:
        _, alive = psutil.wait_procs([item.identity for item in owned], timeout=timeout)
    except (psutil.Error, OSError):
        alive = [item.identity for item in owned if _identity_state(item) == "owned"]
    return {int(item.pid) for item in alive}


def terminate_process_tree(owned_root: OwnedProcess, *, timeout: float = 0.25) -> bool:
    """Terminate only the identity captured when Charlie created the process."""
    root_state = _identity_state(owned_root)
    if root_state == "gone":
        return True
    if root_state != "owned":
        return False

    descendants: list[OwnedProcess] = []
    try:
        descendants = [
            OwnedProcess(None, child, int(child.pid), float(child.create_time()), None)
            for child in owned_root.identity.children(recursive=True)
        ]
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        descendants = []
    except (psutil.AccessDenied, OSError, ValueError):
        return False

    all_owned = [*reversed(descendants), owned_root]
    for owned in all_owned:
        state = _identity_state(owned)
        if state == "gone":
            continue
        if state != "owned":
            return False
        try:
            owned.identity.terminate()
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        except (psutil.AccessDenied, OSError):
            return False

    alive = _wait_owned(all_owned, timeout)
    if alive:
        for owned in all_owned:
            if owned.pid not in alive:
                continue
            state = _identity_state(owned)
            if state == "gone":
                continue
            if state != "owned":
                return False
            try:
                owned.identity.kill()
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except (psutil.AccessDenied, OSError):
                return False
        alive = _wait_owned(all_owned, timeout)
    return not alive


class ExecutionContext:
    """Internal cancellation and process ownership scope for one worker call."""

    def __init__(self) -> None:
        self._cancel_event = threading.Event()
        self._lock = threading.Lock()
        self._process: Optional[OwnedProcess] = None

    @property
    def cancellation_requested(self) -> bool:
        return self._cancel_event.is_set()

    def request_cancel(self) -> None:
        self._cancel_event.set()

    @property
    def owned_process(self) -> Optional[OwnedProcess]:
        with self._lock:
            return self._process

    def register_process(self, process: Any) -> OwnedProcess:
        owned = _capture_owned_process(process)
        with self._lock:
            self._process = owned
            cancelled = self._cancel_event.is_set()
        if cancelled:
            terminate_process_tree(owned)
        return owned

    def unregister_process(self, process: OwnedProcess) -> None:
        with self._lock:
            if self._process is process:
                self._process = None


__all__ = [
    "ExecutionContext",
    "OwnedProcess",
    "activate_execution_context",
    "get_current_execution_context",
    "reset_execution_context",
    "terminate_process_tree",
]
