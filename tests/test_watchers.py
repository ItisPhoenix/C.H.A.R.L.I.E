from charlie.attention import AttentionLevel
from charlie.watchers import Watcher, WatcherRegistry


def test_run_once_skips_watcher_before_its_interval_elapses():
    calls = []
    watcher = Watcher(name="w1", interval_s=10.0, check=lambda: calls.append(1) or None)
    registry = WatcherRegistry()
    registry.register(watcher)

    registry.run_once(now=0.0)
    registry.run_once(now=5.0)

    assert len(calls) == 1


def test_run_once_runs_watcher_again_after_interval_elapses():
    calls = []
    watcher = Watcher(name="w1", interval_s=10.0, check=lambda: calls.append(1) or None)
    registry = WatcherRegistry()
    registry.register(watcher)

    registry.run_once(now=0.0)
    registry.run_once(now=11.0)

    assert len(calls) == 2


def test_run_once_routes_event_through_attention_and_returns_non_silent_signals():
    event = {"type": "alert", "payload": {"severity": "error", "message": "disk full"}}
    watcher = Watcher(name="w1", interval_s=0.0, check=lambda: event)
    registry = WatcherRegistry()
    registry.register(watcher)

    signals = registry.run_once(now=0.0)

    assert len(signals) == 1
    got_event, level, reason = signals[0]
    assert got_event == event
    assert level == AttentionLevel.ATTENTION
    assert reason == "disk full"


def test_run_once_drops_silent_events():
    event = {"type": "background_task", "payload": {"status": "running"}}
    watcher = Watcher(name="w1", interval_s=0.0, check=lambda: event)
    registry = WatcherRegistry()
    registry.register(watcher)

    assert registry.run_once(now=0.0) == []


def test_run_once_dedupes_repeated_signals_within_cooldown():
    event = {"type": "alert", "payload": {"severity": "warning", "message": "cpu high"}}
    watcher = Watcher(name="w1", interval_s=0.0, check=lambda: event)
    registry = WatcherRegistry()
    registry.register(watcher)

    first = registry.run_once(now=0.0)
    second = registry.run_once(now=1.0)

    assert len(first) == 1
    assert second == []


def test_run_once_continues_past_a_watcher_that_raises():
    def _boom():
        raise RuntimeError("boom")

    event = {"type": "alert", "payload": {"severity": "error", "message": "ok watcher"}}
    bad = Watcher(name="bad", interval_s=0.0, check=_boom)
    good = Watcher(name="good", interval_s=0.0, check=lambda: event)
    registry = WatcherRegistry()
    registry.register(bad)
    registry.register(good)

    signals = registry.run_once(now=0.0)

    assert len(signals) == 1
    assert signals[0][0] == event


# --- built-in watcher factories ---

from charlie.watchers import (
    cpu_ram_watcher,
    mcp_health_watcher,
    path_change_watcher,
    repeated_tool_failure_watcher,
    stalled_task_watcher,
)


def test_cpu_ram_watcher_fires_after_sustained_breaches():
    values = iter([(96.0, 10.0), (96.0, 10.0), (96.0, 10.0)])
    watcher = cpu_ram_watcher(lambda: next(values), cpu_threshold_pct=95.0, ram_threshold_pct=92.0)

    assert watcher.check() is None
    assert watcher.check() is None
    event = watcher.check()
    assert event["payload"]["severity"] == "warning"
    assert "CPU" in event["payload"]["message"]


def test_mcp_health_watcher_edge_triggers_on_newly_down_server():
    statuses = iter([{"a": True}, {"a": False}, {"a": False}])
    watcher = mcp_health_watcher(lambda: next(statuses))

    assert watcher.check() is None
    event = watcher.check()
    assert "a" in event["payload"]["message"]
    assert watcher.check() is None  # already known-down, no re-alert


class _Task:
    def __init__(self, id, status, current_step):
        self.id = id
        self.status = status
        self.current_step = current_step


def test_stalled_task_watcher_fires_after_no_progress():
    tasks = [_Task("t1", "running", 2)]
    watcher = stalled_task_watcher(lambda: tasks, sustained_polls=3)

    assert watcher.check() is None
    assert watcher.check() is None
    event = watcher.check()
    assert "t1" in event["payload"]["message"]
    assert watcher.check() is None  # already alerted, no repeat while still stalled


def test_stalled_task_watcher_resets_on_progress():
    tasks = [_Task("t1", "running", 0)]
    watcher = stalled_task_watcher(lambda: tasks, sustained_polls=2)

    watcher.check()
    tasks[0].current_step = 1  # progressed before the 2nd poll
    assert watcher.check() is None


def test_repeated_tool_failure_watcher_edge_triggers():
    calls = iter([[], [("shell_execute", 0.8, 6)], [("shell_execute", 0.8, 6)]])
    watcher = repeated_tool_failure_watcher(lambda: next(calls))

    assert watcher.check() is None
    event = watcher.check()
    assert "shell_execute" in event["payload"]["message"]
    assert watcher.check() is None


def test_repeated_tool_failure_watcher_reports_each_newly_failed_tool():
    calls = iter([
        [("file_read", 0.8, 6), ("shell_execute", 0.8, 6)],
        [("file_read", 0.8, 6), ("shell_execute", 0.8, 6)],
        [("file_read", 0.8, 6), ("shell_execute", 0.8, 6)],
    ])
    watcher = repeated_tool_failure_watcher(lambda: next(calls))

    first = watcher.check()
    second = watcher.check()

    assert "file_read" in first["payload"]["message"]
    assert "shell_execute" in second["payload"]["message"]


def test_path_change_watcher_detects_mtime_change(tmp_path):
    f = tmp_path / "watched.txt"
    f.write_text("a")
    watcher = path_change_watcher([str(f)])

    assert watcher.check() is None  # first poll just establishes the baseline
    f.write_text("bb")
    import os
    import time
    os.utime(f, (time.time() + 5, time.time() + 5))
    event = watcher.check()
    assert str(f) in event["payload"]["message"]
