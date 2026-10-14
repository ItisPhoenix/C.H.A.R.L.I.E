"""Process-local stopwatch with monotonic elapsed-time measurement."""

from __future__ import annotations

import time
import threading


class Stopwatch:
    def __init__(self) -> None:
        self._elapsed = 0.0
        self._started_at: float | None = None
        self._lock = threading.Lock()

    def start(self) -> float:
        with self._lock:
            if self._started_at is None:
                self._started_at = time.monotonic()
            return self._elapsed

    def stop(self) -> float:
        with self._lock:
            if self._started_at is not None:
                self._elapsed += time.monotonic() - self._started_at
                self._started_at = None
            return self._elapsed

    def reset(self) -> float:
        with self._lock:
            self._elapsed = 0.0
            self._started_at = time.monotonic() if self._started_at is not None else None
            return 0.0

    def elapsed_seconds(self) -> float:
        with self._lock:
            if self._started_at is None:
                return self._elapsed
            return self._elapsed + time.monotonic() - self._started_at

    @property
    def running(self) -> bool:
        with self._lock:
            return self._started_at is not None
