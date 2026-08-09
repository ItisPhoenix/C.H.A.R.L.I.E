"""Tests for charlie/world_model.py: threads + machine events, replacing the markdown memory files."""

import pytest

from charlie.world_model import WorldModel


@pytest.fixture
def wm():
    return WorldModel(db_path=":memory:")


class TestThreads:
    def test_open_thread_appears_in_open_list(self, wm):
        tid = wm.open_thread("fix the bug", "sess1")
        threads = wm.list_open_threads()
        assert (tid, "fix the bug", "") in threads

    def test_update_thread_sets_summary(self, wm):
        tid = wm.open_thread("fix the bug", "sess1")
        wm.update_thread(tid, "found root cause", resolved=False)
        threads = wm.list_open_threads()
        assert threads[0][2] == "found root cause"

    def test_resolve_thread_removes_from_open_list(self, wm):
        tid = wm.open_thread("fix the bug", "sess1")
        wm.update_thread(tid, "done", resolved=True)
        assert wm.list_open_threads() == []

    def test_close_thread_removes_from_open_list(self, wm):
        tid = wm.open_thread("fix the bug", "sess1")
        wm.close_thread(tid)
        assert wm.list_open_threads() == []

    def test_open_threads_ordered_most_recent_first(self, wm):
        wm.open_thread("older", "sess1")
        tid2 = wm.open_thread("newer", "sess1")
        wm.update_thread(tid2, "", resolved=False)
        threads = wm.list_open_threads()
        assert threads[0][1] == "newer"

    def test_list_open_threads_respects_limit(self, wm):
        for i in range(5):
            wm.open_thread(f"thread {i}", "sess1")
        assert len(wm.list_open_threads(limit=2)) == 2


class TestMachineEvents:
    def test_record_and_recall_event(self, wm):
        wm.record_event("tool_error", "shell_execute: boom")
        events = wm.recent_events()
        assert events[0][0] == "tool_error"
        assert events[0][1] == "shell_execute: boom"

    def test_filter_by_event_type(self, wm):
        wm.record_event("app_open", "opened chrome")
        wm.record_event("tool_error", "boom")
        errors = wm.recent_events(event_type="tool_error")
        assert len(errors) == 1
        assert errors[0][0] == "tool_error"

    def test_recent_events_respects_limit(self, wm):
        for i in range(5):
            wm.record_event("app_open", f"app {i}")
        assert len(wm.recent_events(limit=3)) == 3

    def test_recent_events_most_recent_first(self, wm):
        wm.record_event("app_open", "first")
        wm.record_event("app_open", "second")
        events = wm.recent_events()
        assert events[0][1] == "second"


class TestContextSlice:
    def test_empty_store_returns_empty_string(self, wm):
        assert wm.context_slice() == ""

    def test_includes_open_threads(self, wm):
        wm.open_thread("fix the bug", "sess1")
        slice_text = wm.context_slice()
        assert "fix the bug" in slice_text

    def test_excludes_resolved_threads(self, wm):
        tid = wm.open_thread("fix the bug", "sess1")
        wm.update_thread(tid, "done", resolved=True)
        assert "fix the bug" not in wm.context_slice()

    def test_includes_recent_tool_errors_only(self, wm):
        wm.record_event("app_open", "opened chrome")
        wm.record_event("tool_error", "shell_execute: boom")
        slice_text = wm.context_slice()
        assert "shell_execute: boom" in slice_text
        assert "opened chrome" not in slice_text

    def test_respects_char_budget(self, wm):
        wm.open_thread("x" * 2000, "sess1")
        assert len(wm.context_slice(char_budget=100)) <= 100
