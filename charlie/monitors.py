"""Proactive CPU/RAM threshold watcher.

evaluate_sample() is a pure function (history in, alert-or-None out) so the
alert-worthiness logic is unit-testable without a real sampler loop or real
psutil calls. The sampler thread (start_monitor_thread) is the only piece
that touches real system state or real time.
"""

import logging
import threading
import time
from typing import Callable, Optional, Tuple

logger = logging.getLogger("charlie.monitors")

_SUSTAINED_SAMPLES = 3  # consecutive over-threshold samples before alerting
_POLL_INTERVAL_S = 60.0
_COOLDOWN_S = 1800.0  # 30 min -- don't re-alert on the same metric sooner than this


class _MetricState:
    """Tracks consecutive-breach count and last-alert time for one metric."""

    def __init__(self) -> None:
        self.consecutive_breaches: int = 0
        # -inf so the first-ever alert is never suppressed by the cooldown
        # check below (now - last_alert_at must start out larger than any
        # cooldown window, regardless of what time base `now` uses).
        self.last_alert_at: float = float("-inf")


def evaluate_sample(
    metric_name: str,
    value_pct: float,
    threshold_pct: float,
    state: _MetricState,
    now: float,
) -> Optional[str]:
    """Return an alert message if this sample should trigger one, else None.

    Mutates `state` in place (consecutive-breach count, last-alert timestamp)
    -- call this once per real sample, in order, for correct behavior.
    """
    if value_pct < threshold_pct:
        state.consecutive_breaches = 0
        return None
    state.consecutive_breaches += 1
    if state.consecutive_breaches < _SUSTAINED_SAMPLES:
        return None
    if now - state.last_alert_at < _COOLDOWN_S:
        return None
    state.last_alert_at = now
    return f"Heads up -- {metric_name} is at {value_pct:.0f} percent."


def start_monitor_thread(
    get_cpu_ram: Callable[[], Tuple[float, float]],
    on_alert: Callable[[str], None],
    cpu_threshold_pct: float,
    ram_threshold_pct: float,
    poll_interval_s: float = _POLL_INTERVAL_S,
    stop_event: Optional[threading.Event] = None,
) -> threading.Thread:
    """Start a daemon thread sampling (cpu_pct, ram_pct) every poll_interval_s
    and calling on_alert(message) when evaluate_sample fires for either metric.
    stop_event, if given, lets a caller (e.g. tests, or graceful shutdown)
    stop the loop instead of it running forever."""
    stop_event = stop_event or threading.Event()
    cpu_state = _MetricState()
    ram_state = _MetricState()

    def _run() -> None:
        while not stop_event.is_set():
            try:
                cpu_pct, ram_pct = get_cpu_ram()
                now = time.monotonic()
                for name, pct, threshold, state in (
                    ("CPU usage", cpu_pct, cpu_threshold_pct, cpu_state),
                    ("memory usage", ram_pct, ram_threshold_pct, ram_state),
                ):
                    msg = evaluate_sample(name, pct, threshold, state, now)
                    if msg:
                        on_alert(msg)
            except Exception:
                logger.warning("Monitor sample failed", exc_info=True)
            stop_event.wait(poll_interval_s)

    thread = threading.Thread(target=_run, daemon=True, name="charlie-monitors")
    thread.start()
    return thread
