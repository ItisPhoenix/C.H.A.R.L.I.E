"""Watcher registry: polled check() functions, output always routed through
charlie.attention.decide before a caller reacts -- watchers never spawn a surface directly.
"""

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from charlie.attention import AttentionLevel
from charlie.attention import decide as _attention_decide
from charlie.events import EventType

logger = logging.getLogger("charlie.watchers")

_DEFAULT_POLL_INTERVAL_S = 30.0
_STALL_SUSTAINED_POLLS = 3


@dataclass
class Watcher:
    name: str
    interval_s: float
    check: Callable[[], Optional[dict]]


class WatcherRegistry:
    """run_once() is pure enough to unit test: no real time or I/O of its own, just dispatch."""

    def __init__(self) -> None:
        self._watchers: List[Watcher] = []
        self._last_run: Dict[str, float] = {}
        self._cooldowns: Dict[str, float] = {}

    def register(self, watcher: Watcher) -> None:
        self._watchers.append(watcher)

    def run_once(self, now: Optional[float] = None) -> List[Tuple[dict, AttentionLevel, str]]:
        now = now if now is not None else time.monotonic()
        signals: List[Tuple[dict, AttentionLevel, str]] = []
        for watcher in self._watchers:
            last = self._last_run.get(watcher.name, float("-inf"))
            if now - last < watcher.interval_s:
                continue
            self._last_run[watcher.name] = now
            try:
                event = watcher.check()
            except Exception:
                logger.warning("Watcher '%s' check failed", watcher.name, exc_info=True)
                continue
            if event is None:
                continue
            level, reason = _attention_decide(event, cooldowns=self._cooldowns, now=now)
            if level > AttentionLevel.SILENT:
                signals.append((event, level, reason))
        return signals


def start_watcher_thread(
    registry: WatcherRegistry,
    on_signal: Callable[[dict, AttentionLevel, str], None],
    poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
    stop_event: Optional[threading.Event] = None,
) -> threading.Thread:
    """Mirrors monitors.py's start_monitor_thread shape: daemon thread, injectable stop_event."""
    stop_event = stop_event or threading.Event()

    def _run() -> None:
        while not stop_event.is_set():
            try:
                for event, level, reason in registry.run_once():
                    on_signal(event, level, reason)
            except Exception:
                logger.warning("Watcher poll failed", exc_info=True)
            stop_event.wait(poll_interval_s)

    thread = threading.Thread(target=_run, daemon=True, name="charlie-watchers")
    thread.start()
    return thread


def _alert_event(message: str, severity: str = "error") -> dict:
    return {"type": EventType.ALERT, "payload": {"severity": severity, "message": message}}


def cpu_ram_watcher(
    get_cpu_ram: Callable[[], Tuple[float, float]],
    cpu_threshold_pct: float,
    ram_threshold_pct: float,
    interval_s: float = 60.0,
) -> Watcher:
    """Thin adapter over monitors.evaluate_sample -- no rewrite, proves the Watcher interface."""
    from charlie.monitors import _MetricState, evaluate_sample

    cpu_state = _MetricState()
    ram_state = _MetricState()

    def _check() -> Optional[dict]:
        cpu_pct, ram_pct = get_cpu_ram()
        now = time.monotonic()
        for name, pct, threshold, state in (
            ("CPU usage", cpu_pct, cpu_threshold_pct, cpu_state),
            ("memory usage", ram_pct, ram_threshold_pct, ram_state),
        ):
            msg = evaluate_sample(name, pct, threshold, state, now)
            if msg:
                return _alert_event(msg, severity="warning")
        return None

    return Watcher(name="cpu_ram", interval_s=interval_s, check=_check)


def mcp_health_watcher(get_status: Callable[[], Dict[str, bool]], interval_s: float = 60.0) -> Watcher:
    """get_status() maps server name -> is_running. Edge-triggered: alerts once per newly-down server."""
    known_down: set = set()

    def _check() -> Optional[dict]:
        down_now = {name for name, up in get_status().items() if not up}
        newly_down = down_now - known_down
        known_down.clear()
        known_down.update(down_now)
        if newly_down:
            return _alert_event(f"MCP server(s) down: {', '.join(sorted(newly_down))}")
        return None

    return Watcher(name="mcp_health", interval_s=interval_s, check=_check)


def stalled_task_watcher(
    get_tasks: Callable[[], List[Any]],
    sustained_polls: int = _STALL_SUSTAINED_POLLS,
    interval_s: float = 60.0,
) -> Watcher:
    """A 'running' task whose current_step hasn't advanced for sustained_polls consecutive polls stalls."""
    last_step: Dict[str, Any] = {}
    stall_count: Dict[str, int] = {}
    already_alerted: set = set()

    def _check() -> Optional[dict]:
        seen_ids = set()
        result = None
        for task in get_tasks():
            task_id = getattr(task, "id", None)
            if task_id is None or getattr(task, "status", None) != "running":
                continue
            seen_ids.add(task_id)
            step = getattr(task, "current_step", None)
            same = task_id in last_step and last_step[task_id] == step
            stall_count[task_id] = stall_count.get(task_id, 0) + 1 if same else 1
            last_step[task_id] = step
            if result is None and stall_count[task_id] >= sustained_polls and task_id not in already_alerted:
                already_alerted.add(task_id)
                result = _alert_event(f"Task '{task_id}' appears stalled at step {step}")
        for stale_id in set(already_alerted) - seen_ids:
            already_alerted.discard(stale_id)
            stall_count.pop(stale_id, None)
            last_step.pop(stale_id, None)
        return result

    return Watcher(name="stalled_task", interval_s=interval_s, check=_check)


def repeated_tool_failure_watcher(
    get_unreliable_tools: Callable[[], List[Tuple[str, float, int]]], interval_s: float = 60.0
) -> Watcher:
    """Edge-triggered on charlie.telemetry.unreliable_tools(): alerts once per newly-flagged tool."""
    already_alerted: set = set()

    def _check() -> Optional[dict]:
        flagged = get_unreliable_tools()
        flagged_names = {name for name, _rate, _calls in flagged}
        newly_flagged = flagged_names - already_alerted
        already_alerted.clear()
        already_alerted.update(flagged_names)
        if newly_flagged:
            name = sorted(newly_flagged)[0]
            rate = next(r for n, r, _c in flagged if n == name)
            return _alert_event(f"Tool '{name}' is failing {rate:.0%} of calls")
        return None

    return Watcher(name="repeated_tool_failure", interval_s=interval_s, check=_check)


def path_change_watcher(paths: List[str], interval_s: float = 30.0) -> Watcher:
    """Generic mtime-diff watcher over a fixed path list; silent until a second poll has a baseline."""
    last_mtimes: Dict[str, float] = {}

    def _check() -> Optional[dict]:
        changed = []
        for path in paths:
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if path in last_mtimes and mtime != last_mtimes[path]:
                changed.append(path)
            last_mtimes[path] = mtime
        return _alert_event(f"Changed: {', '.join(changed)}", severity="warning") if changed else None

    return Watcher(name="path_change", interval_s=interval_s, check=_check)
