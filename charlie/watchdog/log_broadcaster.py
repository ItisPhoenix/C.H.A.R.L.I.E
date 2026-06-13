"""
Log broadcaster — pipes structured log records to the dashboard via WebSocket.

A logging.Handler that buffers log records and ships them over a queue
at a controlled rate. The IPCBridge then forwards to all WebSocket clients.
"""

from __future__ import annotations

import logging
from queue import Queue


class DashboardLogHandler(logging.Handler):
    """logging.Handler that ships log records to a queue for dashboard streaming.

    Usage:
        handler = DashboardLogHandler(queue)
        logger.addHandler(handler)
    """

    def __init__(self, queue: Queue, level: int = logging.INFO) -> None:
        super().__init__(level=level)
        self._queue = queue
        self._dropped = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "timestamp": record.created,
                "level": record.levelname,
                "component": record.name,
                "message": self.format(record),
            }
            try:
                self._queue.put_nowait(entry)
            except Exception:
                # Queue full — drop and count (non-blocking)
                self._dropped += 1
        except Exception:
            # Never raise from a logging handler
            self.handleError(record)

    @property
    def dropped_count(self) -> int:
        return self._dropped
