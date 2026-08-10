"""In-process counters backing GET /api/health and GET /api/metrics.

Not a tracing SDK -- just enough real signal (LLM reachability, rolling
tool/LLM error rates) to answer "is Charlie healthy" without new infra or a
new dependency. A real span-based system is a bigger, separate investment;
this is the minimum that turns those two endpoints from placeholders into
real data.
"""

import time
from collections import deque
from threading import Lock
from typing import Deque, Dict, List, Tuple

# Outcome-feedback signal (Phase 1c): min samples + error rate before a tool earns a rule.
_UNRELIABLE_MIN_CALLS = 5
_UNRELIABLE_ERROR_THRESHOLD = 0.5

_lock = Lock()
_MAX_SAMPLES = 200
_llm_calls: Deque[Tuple[float, bool]] = deque(maxlen=_MAX_SAMPLES)
_tool_calls: Deque[Tuple[float, str, bool]] = deque(maxlen=_MAX_SAMPLES)
_last_llm_success: float = 0.0


def record_llm_call(success: bool) -> None:
    global _last_llm_success
    with _lock:
        now = time.time()
        _llm_calls.append((now, success))
        if success:
            _last_llm_success = now


def record_tool_call(tool_name: str, success: bool) -> None:
    with _lock:
        _tool_calls.append((time.time(), tool_name, success))


def last_llm_success_timestamp() -> float:
    """0.0 if no successful LLM call has been recorded yet this process."""
    return _last_llm_success


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
