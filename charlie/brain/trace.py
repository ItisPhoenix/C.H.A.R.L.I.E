"""
Orchestrator trace log.

Writes structured events to charlie/logs/orchestrator_trace.jsonl.
Each line is a JSON object with timestamp, event, and optional fields.
The log is append-only. Errors during write are caught and logged at
WARNING level — never break the calling code.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from charlie.utils.logger import get_logger

logger = get_logger(__name__)

_TRACE_LOG = Path("scratch/orchestrator_trace.jsonl")


def _ensure_log_dir() -> None:
    """Create the logs directory if it doesn't exist."""
    try:
        _TRACE_LOG.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning("trace_log_dir_failed | %s", e)


def trace(
    event: str,
    subtask_id: Optional[str] = None,
    agent: Optional[str] = None,
    duration_ms: Optional[float] = None,
    success: Optional[bool] = None,
    error: Optional[str] = None,
    extra: Optional[dict] = None,
) -> None:
    """Write a single trace event. Never raises."""
    record: dict[str, Any] = {
        "timestamp": time.time(),
        "event": event,
    }
    if subtask_id is not None:
        record["subtask_id"] = subtask_id
    if agent is not None:
        record["agent"] = agent
    if duration_ms is not None:
        record["duration_ms"] = duration_ms
    if success is not None:
        record["success"] = success
    if error is not None:
        record["error"] = error
    if extra is not None:
        record["extra"] = extra

    try:
        _ensure_log_dir()
        with _TRACE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        logger.warning("trace_write_failed | %s", e)


def read_recent(n: int = 50) -> list[dict]:
    """Return the last N trace events (or fewer if log is short)."""
    if not _TRACE_LOG.exists():
        return []
    try:
        with _TRACE_LOG.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        recent = lines[-n:] if len(lines) > n else lines
        return [json.loads(line) for line in recent if line.strip()]
    except Exception as e:
        logger.warning("trace_read_failed | %s", e)
        return []


def clear() -> None:
    """Delete the trace log. For tests only."""
    try:
        if _TRACE_LOG.exists():
            _TRACE_LOG.unlink()
    except Exception as e:
        logger.warning("trace_clear_failed | %s", e)
