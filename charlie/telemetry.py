"""In-process counters backing GET /api/health and GET /api/metrics.

Not a tracing SDK -- just enough real signal (LLM reachability, rolling
tool/LLM error rates) to answer "is Charlie healthy" without new infra or a
new dependency. A real span-based system is a bigger, separate investment;
this is the minimum that turns those two endpoints from placeholders into
real data.
"""

from __future__ import annotations

import time
from collections import deque
from threading import RLock
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

# Outcome-feedback signal: min samples + error rate before a tool earns a rule.
_UNRELIABLE_MIN_CALLS = 5
_UNRELIABLE_ERROR_THRESHOLD = 0.5

_lock = RLock()
_MAX_SAMPLES = 200
_llm_calls: Deque[Tuple[float, bool]] = deque(maxlen=_MAX_SAMPLES)
_tool_calls: Deque[Tuple[float, str, bool]] = deque(maxlen=_MAX_SAMPLES)
_last_llm_success: float = 0.0
_last_llm_attempt_timestamp: float = 0.0
_last_llm_attempt_status: str = "never_attempted"
_on_telemetry_updated: Optional[Callable[[], Any]] = None


def set_telemetry_listener(callback: Optional[Callable[[], Any]]) -> None:
    """Register a callback invoked when LLM or tool telemetry is recorded."""
    global _on_telemetry_updated
    with _lock:
        _on_telemetry_updated = callback


def reset_telemetry() -> None:
    """Reset in-process telemetry counters (primarily for tests)."""
    global _last_llm_success, _last_llm_attempt_timestamp, _last_llm_attempt_status
    with _lock:
        _llm_calls.clear()
        _tool_calls.clear()
        _last_llm_success = 0.0
        _last_llm_attempt_timestamp = 0.0
        _last_llm_attempt_status = "never_attempted"


def record_llm_call(success: bool) -> None:
    global _last_llm_success, _last_llm_attempt_timestamp, _last_llm_attempt_status
    callback = None
    with _lock:
        now = time.time()
        _llm_calls.append((now, success))
        _last_llm_attempt_timestamp = now
        if success:
            _last_llm_success = now
            _last_llm_attempt_status = "success"
        else:
            _last_llm_attempt_status = "failed"
        callback = _on_telemetry_updated
    if callback is not None:
        try:
            callback()
        except Exception:
            pass


def record_tool_call(tool_name: str, success: bool) -> None:
    callback = None
    with _lock:
        _tool_calls.append((time.time(), tool_name, success))
        callback = _on_telemetry_updated
    if callback is not None:
        try:
            callback()
        except Exception:
            pass


def last_llm_success_timestamp() -> float:
    """0.0 if no successful LLM call has been recorded yet this process."""
    return _last_llm_success


def last_llm_attempt_timestamp() -> float:
    """0.0 if no LLM call attempt has been recorded yet this process."""
    return _last_llm_attempt_timestamp


def last_llm_attempt_status() -> str:
    """'never_attempted', 'success', or 'failed'."""
    return _last_llm_attempt_status


def llm_error_rate() -> float:
    with _lock:
        if not _llm_calls:
            return 0.0
        errors = sum(1 for _, ok in _llm_calls if not ok)
        return errors / len(_llm_calls)


def tool_error_rate() -> float:
    with _lock:
        if not _tool_calls:
            return 0.0
        errors = sum(1 for _, _, ok in _tool_calls if not ok)
        return errors / len(_tool_calls)


def tool_error_rate_by_name() -> Dict[str, Dict[str, float]]:
    with _lock:
        by_name: Dict[str, list] = {}
        for _, name, ok in _tool_calls:
            stats = by_name.setdefault(name, [0, 0])
            stats[0] += 1
            if not ok:
                stats[1] += 1
    return {
        name: {"calls": calls, "errors": errors, "error_rate": errors / calls}
        for name, (calls, errors) in by_name.items()
    }


def unreliable_tools() -> List[Tuple[str, float, int]]:
    """Tools with enough samples and a high enough error rate to be worth a
    learned rule: (tool_name, error_rate, call_count), highest error first."""
    stats = tool_error_rate_by_name()
    flagged = [
        (name, s["error_rate"], s["calls"])
        for name, s in stats.items()
        if s["calls"] >= _UNRELIABLE_MIN_CALLS and s["error_rate"] >= _UNRELIABLE_ERROR_THRESHOLD
    ]
    return sorted(flagged, key=lambda t: t[1], reverse=True)


def snapshot() -> Dict[str, Any]:
    """Produce authoritative JSON-safe and secret-safe runtime telemetry snapshot."""
    with _lock:
        tool_by_name = tool_error_rate_by_name()
        unreliable = unreliable_tools()
        llm_err = (
            sum(1 for _, ok in _llm_calls if not ok) / len(_llm_calls)
            if _llm_calls
            else 0.0
        )
        tool_err = (
            sum(1 for _, _, ok in _tool_calls if not ok) / len(_tool_calls)
            if _tool_calls
            else 0.0
        )
        return {
            "authority": "main_runtime",
            "timestamp": time.time(),
            "llm_last_success_timestamp": _last_llm_success,
            "llm_last_attempt_timestamp": _last_llm_attempt_timestamp,
            "llm_last_attempt_status": _last_llm_attempt_status,
            "llm_error_rate": llm_err,
            "tool_error_rate": tool_err,
            "tool_error_rate_by_tool": tool_by_name,
            "unreliable_tools": unreliable,
            "llm": {
                "last_success_timestamp": _last_llm_success,
                "last_attempt_timestamp": _last_llm_attempt_timestamp,
                "last_attempt_status": _last_llm_attempt_status,
                "error_rate": llm_err,
                "total_calls": len(_llm_calls),
            },
            "tools": {
                "error_rate": tool_err,
                "total_calls": len(_tool_calls),
                "by_tool": tool_by_name,
                "unreliable_tools": unreliable,
            },
        }
