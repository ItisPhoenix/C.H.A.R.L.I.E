"""Minimal interactive stdin bridge into the main runtime loop."""

from __future__ import annotations

import sys
import threading
from typing import Callable, TextIO


class ConsoleTextIngress:
    def __init__(self, loop, on_text: Callable[[str], None], stream: TextIO | None = None) -> None:
        self._loop = loop
        self._on_text = on_text
        self._stream = sys.stdin if stream is None else stream
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        if self._thread is not None:
            return True
        try:
            if self._stream is None or not self._stream.isatty():
                return False
        except (OSError, ValueError, AttributeError):
            return False
        self._thread = threading.Thread(target=self._read, name="charlie-console-input", daemon=True)
        self._thread.start()
        return True

    def stop(self, *, timeout: float = 0.25) -> bool:
        self._stopped.set()
        if self._thread is None:
            return True
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def _read(self) -> None:
        # ponytail: blocking stdin cannot be interrupted portably; daemon plus stop gate prevents late turns.
        while not self._stopped.is_set():
            try:
                line = self._stream.readline()
            except (OSError, ValueError, UnicodeError, KeyboardInterrupt):
                return
            if not line:
                return
            text = line.strip()
            if not text or self._stopped.is_set():
                continue
            try:
                self._loop.call_soon_threadsafe(self._deliver, text)
            except RuntimeError:
                return

    def _deliver(self, text: str) -> None:
        if not self._stopped.is_set():
            self._on_text(text)
