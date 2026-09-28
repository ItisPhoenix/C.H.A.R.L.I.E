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


def test_path_change_watcher_reports_metadata_without_contents_and_dedupes(tmp_path):
    f = tmp_path / "watched.txt"
    f.write_text("private first contents")
    watcher = path_change_watcher([str(f)])
    assert watcher.check() is None

    f.write_text("different private contents")
    import os
    import time
    os.utime(f, (time.time() + 5, time.time() + 5))
    event = watcher.check()

    payload = event["payload"]
    assert payload["signal"] == {"kind": "path_change", "paths": [str(f)]}
    diagnosis = payload["diagnosis"]
    assert diagnosis["changed_count"] == 1
    assert diagnosis["paths"][0]["previous"]["size_bytes"] == len("private first contents")
    assert diagnosis["paths"][0]["current"]["size_bytes"] == len("different private contents")
    assert diagnosis["paths"][0]["delta"]["size_bytes"] > 0
    assert "private first contents" not in repr(event)
    assert "different private contents" not in repr(event)
    assert watcher.check() is None


def test_path_change_watcher_reports_disappearance_once_without_contents(tmp_path):
    f = tmp_path / "watched.txt"
    f.write_text("private contents")
    watcher = path_change_watcher([str(f)])
    assert watcher.check() is None

    f.unlink()
    event = watcher.check()
    payload = event["payload"]
    assert payload["signal"] == {"kind": "path_change", "paths": [str(f)]}
    diagnosis = payload["diagnosis"]["paths"][0]
    assert diagnosis["previous"]["size_bytes"] == len("private contents")
    assert diagnosis["current"] is None
    assert diagnosis["delta"] == {"exists": False}
    assert "disappeared" in payload["message"]
    assert "private contents" not in repr(event)
    assert watcher.check() is None


def test_path_change_watcher_reports_reappearance_once(tmp_path):
    f = tmp_path / "watched.txt"
    f.write_text("first")
    initially_missing = tmp_path / "created-later.txt"
    watcher = path_change_watcher([str(f), str(initially_missing)])
    assert watcher.check() is None

    f.unlink()
    disappeared = watcher.check()
    assert disappeared["payload"]["diagnosis"]["paths"][0]["change"] == "disappeared"
    assert watcher.check() is None

    f.write_text("recreated")
    initially_missing.write_text("new file")
    appeared = watcher.check()
    diagnoses = appeared["payload"]["diagnosis"]["paths"]
    assert [item["change"] for item in diagnoses] == ["appeared", "appeared"]
    assert diagnoses[0]["previous"] is None
    assert diagnoses[1]["previous"] is None
    assert watcher.check() is None


def test_stalled_task_watcher_uses_canonical_diagnosis_and_dedupes(tmp_path):
    from charlie.task_journal import TaskJournal, TaskOrigin, TaskStatus

    journal = TaskJournal(tmp_path / "task-journal.json")
    task = journal.create_task(
        "Investigate report",
        task_id="task-42",
        origin=TaskOrigin.BACKGROUND,
        status=TaskStatus.RUNNING,
        total_steps=4,
    )
    journal.update_progress(
        task.id,
        progress=0.5,
        current_step=2,
        total_steps=4,
        current_action="Inspect source metadata",
        waiting_reason="waiting for a read-only check",
    )
    before = journal.get(task.id).to_dict()
    watcher = stalled_task_watcher(lambda: journal.list(include_terminal=False), sustained_polls=2)

    assert watcher.check() is None
    event = watcher.check()
    assert event["payload"]["signal"] == {"kind": "stalled_task", "task_id": "task-42"}
    diagnosis = event["payload"]["diagnosis"]
    assert diagnosis == {
        "task_id": "task-42",
        "status": "running",
        "current_step": 2,
        "total_steps": 4,
        "progress": 0.5,
        "current_action": "Inspect source metadata",
        "waiting_reason": "waiting for a read-only check",
        "updated_at": before["updated_at"],
    }
    assert "50% complete" in event["payload"]["message"]
    assert watcher.check() is None
    assert journal.get(task.id).to_dict() == before
