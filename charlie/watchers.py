"""Watcher registry: polled check() functions, output always routed through
charlie.attention.decide before a caller responds -- watchers never perform effects directly.
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


def _bounded_text(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


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
            status = getattr(getattr(task, "status", None), "value", getattr(task, "status", None))
            if task_id is None or status != "running":
                continue
            task_id = _bounded_text(task_id, 128)
            seen_ids.add(task_id)
            step = getattr(task, "current_step", 0)
            same = task_id in last_step and last_step[task_id] == step
            stall_count[task_id] = stall_count.get(task_id, 0) + 1 if same else 1
            last_step[task_id] = step
            if not same:
                already_alerted.discard(task_id)
            if result is None and stall_count[task_id] >= sustained_polls and task_id not in already_alerted:
                already_alerted.add(task_id)
                total_steps = getattr(task, "total_steps", None)
                progress = getattr(task, "progress", None)
                progress = round(float(progress), 3) if isinstance(progress, (int, float)) else None
                current_action = _bounded_text(getattr(task, "current_action", None)) or None
                waiting_reason = _bounded_text(getattr(task, "waiting_reason", None), 120) or None
                updated_at = _bounded_text(getattr(task, "updated_at", None), 64) or None
                diagnosis = {
                    "task_id": task_id,
                    "status": status,
                    "current_step": step,
                    "total_steps": total_steps,
                    "progress": progress,
                    "current_action": current_action,
                    "waiting_reason": waiting_reason,
                    "updated_at": updated_at,
                }
                details = [f"step {step}" + (f"/{total_steps}" if total_steps is not None else "")]
                if progress is not None:
                    details.append(f"{progress:.0%} complete")
                if current_action:
                    details.append(f"action: {current_action}")
                if waiting_reason:
                    details.append(f"waiting: {waiting_reason}")
                if updated_at:
                    details.append(f"updated: {updated_at}")
                result = _alert_event(f"Task '{task_id}' appears stalled ({'; '.join(details)})")
                result["payload"]["signal"] = {"kind": "stalled_task", "task_id": task_id}
                result["payload"]["diagnosis"] = diagnosis
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
        already_alerted.intersection_update(flagged_names)
        if newly_flagged:
            name = sorted(newly_flagged)[0]
            already_alerted.add(name)
            rate = next(r for n, r, _c in flagged if n == name)
            return _alert_event(f"Tool '{name}' is failing {rate:.0%} of calls")
        return None

    return Watcher(name="repeated_tool_failure", interval_s=interval_s, check=_check)


def path_change_watcher(paths: List[str], interval_s: float = 30.0) -> Watcher:
    """Report metadata-only changes to fixed paths after establishing a baseline."""
    last_stats: Dict[str, Optional[tuple[int, int, int]]] = {}

    def _check() -> Optional[dict]:
        changed = []
        for path in paths:
            try:
                stat = os.stat(path)
            except FileNotFoundError:
                if path not in last_stats:
                    last_stats[path] = None
                    continue
                previous = last_stats[path]
                last_stats[path] = None
                if previous is not None:
                    path_label = _bounded_text(path, 256)
                    changed.append({
                        "change": "disappeared",
                        "path": path_label,
                        "previous": {
                            "size_bytes": previous[0],
                            "mtime_ns": previous[1],
                            "mode": previous[2],
                        },
                        "current": None,
                        "delta": {"exists": False},
                    })
                continue
            except OSError:
                continue
            current = (stat.st_size, stat.st_mtime_ns, stat.st_mode)
            if path not in last_stats:
                last_stats[path] = current
                continue
            previous = last_stats[path]
            if previous is None:
                changed.append({
                    "change": "appeared",
                    "path": _bounded_text(path, 256),
                    "previous": None,
                    "current": {
                        "size_bytes": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                        "mode": stat.st_mode,
                    },
                    "delta": {"exists": True},
                })
            elif current != previous:
                previous_size, previous_mtime, previous_mode = previous
                path_label = _bounded_text(path, 256)
                changed.append({
                    "change": "changed",
                    "path": path_label,
                    "previous": {
                        "size_bytes": previous_size,
                        "mtime_ns": previous_mtime,
                        "mode": previous_mode,
                    },
                    "current": {
                        "size_bytes": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                        "mode": stat.st_mode,
                    },
                    "delta": {
                        "size_bytes": stat.st_size - previous_size,
                        "mtime_ms": round((stat.st_mtime_ns - previous_mtime) / 1_000_000, 3),
                    },
                })
            last_stats[path] = current
        if not changed:
            return None
        shown = changed[:10]
        message_parts = []
        for item in shown:
            if item["change"] == "disappeared":
                message_parts.append(f"{item['path']}: disappeared (last size {item['previous']['size_bytes']} B)")
            elif item["change"] == "appeared":
                message_parts.append(f"{item['path']}: appeared (size {item['current']['size_bytes']} B)")
            else:
                message_parts.append(
                    f"{item['path']}: size {item['previous']['size_bytes']}→{item['current']['size_bytes']} B "
                    f"(Δ{item['delta']['size_bytes']:+} B), mtime Δ{item['delta']['mtime_ms']:+.3f} ms"
                )
        if len(changed) > len(shown):
            message_parts.append(f"and {len(changed) - len(shown)} more path(s)")
        event = _alert_event("Watched path changed: " + "; ".join(message_parts), severity="warning")
        event["payload"]["signal"] = {"kind": "path_change", "paths": [item["path"] for item in shown]}
        event["payload"]["diagnosis"] = {"paths": shown, "changed_count": len(changed)}
        return event

    return Watcher(name="path_change", interval_s=interval_s, check=_check)
