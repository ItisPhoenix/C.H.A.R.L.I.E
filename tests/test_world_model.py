"""Tests for charlie/world_model.py: threads + machine events, replacing the markdown memory files."""

import pytest

from charlie.world_model import _RULE_DECAY_FLOOR, _RULE_INITIAL_CONFIDENCE, WorldModel


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


class TestRules:
    def test_add_rule_appears_in_active_rules(self, wm):
        rid = wm.add_rule("always reply short on Telegram", "teaching")
        active = wm.active_rules()
        assert (rid, "always reply short on Telegram") in active

    def test_add_rule_starts_at_initial_confidence(self, wm):
        rid = wm.add_rule("be terse", "teaching")
        rules = wm.list_rules()
        row = next(r for r in rules if r[0] == rid)
        assert row[2] == _RULE_INITIAL_CONFIDENCE

    def test_reinforce_raises_confidence(self, wm):
        rid = wm.add_rule("be terse", "teaching")
        wm.reinforce_rule(rid)
        row = next(r for r in wm.list_rules() if r[0] == rid)
        assert row[2] > _RULE_INITIAL_CONFIDENCE

    def test_reinforce_caps_at_one(self, wm):
        rid = wm.add_rule("be terse", "teaching")
        for _ in range(10):
            wm.reinforce_rule(rid)
        row = next(r for r in wm.list_rules() if r[0] == rid)
        assert row[2] == 1.0

    def test_decay_lowers_confidence(self, wm):
        rid = wm.add_rule("be terse", "teaching")
        wm.decay_rule(rid)
        row = next(r for r in wm.list_rules(include_decayed=True) if r[0] == rid)
        assert row[2] < _RULE_INITIAL_CONFIDENCE

    def test_decay_below_floor_retires_rule(self, wm):
        rid = wm.add_rule("be terse", "teaching")
        for _ in range(10):
            wm.decay_rule(rid)
        row = next(r for r in wm.list_rules(include_decayed=True) if r[0] == rid)
        assert row[2] < _RULE_DECAY_FLOOR
        assert row[4] == "decayed"

    def test_decayed_rule_excluded_from_active_rules(self, wm):
        rid = wm.add_rule("be terse", "teaching")
        for _ in range(10):
            wm.decay_rule(rid)
        assert rid not in [r[0] for r in wm.active_rules()]

    def test_decayed_rule_excluded_from_default_list(self, wm):
        rid = wm.add_rule("be terse", "teaching")
        for _ in range(10):
            wm.decay_rule(rid)
        assert rid not in [r[0] for r in wm.list_rules()]

    def test_delete_rule_removes_it_entirely(self, wm):
        rid = wm.add_rule("be terse", "teaching")
        wm.delete_rule(rid)
        assert rid not in [r[0] for r in wm.list_rules(include_decayed=True)]

    def test_active_rules_ordered_by_confidence(self, wm):
        low = wm.add_rule("low", "teaching")
        high = wm.add_rule("high", "teaching")
        wm.reinforce_rule(high)
        ordered = wm.active_rules()
        assert [r[0] for r in ordered] == [high, low]

    def test_active_rules_respects_limit(self, wm):
        for i in range(5):
            wm.add_rule(f"rule {i}", "teaching")
        assert len(wm.active_rules(limit=2)) == 2

    def test_decay_stale_rules_decays_unreinforced(self, wm):
        rid = wm.add_rule("be terse", "teaching")
        with wm.conn:
            wm.conn.execute(
                "UPDATE rules SET last_reinforced_at = datetime('now', '-20 days') WHERE id = ?", (rid,)
            )
        decayed_ids = wm.decay_stale_rules(stale_days=14)
        assert rid in decayed_ids
        row = next(r for r in wm.list_rules(include_decayed=True) if r[0] == rid)
        assert row[2] < _RULE_INITIAL_CONFIDENCE

    def test_decay_stale_rules_skips_recent(self, wm):
        rid = wm.add_rule("be terse", "teaching")
        decayed_ids = wm.decay_stale_rules(stale_days=14)
        assert rid not in decayed_ids

    def test_find_rules_matching_substring(self, wm):
        wm.add_rule("always reply short on Telegram", "teaching")
        wm.add_rule("be brief on Discord", "teaching")
        matches = wm.find_rules_matching("telegram")
        assert len(matches) == 1
        assert "Telegram" in matches[0][1]

    def test_find_rules_matching_includes_proposed(self, wm):
        wm.propose_rule("open spotify after chrome", "pattern")
        matches = wm.find_rules_matching("spotify")
        assert len(matches) == 1

    def test_find_rules_matching_excludes_decayed(self, wm):
        rid = wm.add_rule("be terse", "teaching")
        for _ in range(10):
            wm.decay_rule(rid)
        assert wm.find_rules_matching("terse") == []


class TestPatternDetection:
    def test_no_events_returns_none(self, wm):
        assert wm.detect_app_sequence_pattern() is None

    def test_below_min_occurrences_returns_none(self, wm):
        wm.record_event("app_open", "I've opened chrome for you.")
        wm.record_event("app_open", "I've opened spotify for you.")
        assert wm.detect_app_sequence_pattern(min_occurrences=3) is None

    def test_repeated_pair_detected(self, wm):
        for _ in range(3):
            wm.record_event("app_open", "I've opened chrome for you.")
            wm.record_event("app_open", "I've opened spotify for you.")
        result = wm.detect_app_sequence_pattern(min_occurrences=3)
        assert result == ("chrome", "spotify", 3)

    def test_same_app_pair_ignored(self, wm):
        for _ in range(3):
            wm.record_event("app_open", "I've opened chrome for you.")
            wm.record_event("app_open", "I've opened chrome for you.")
        assert wm.detect_app_sequence_pattern(min_occurrences=3) is None

    def test_outside_window_not_counted(self, wm):
        with wm.conn:
            wm.conn.execute(
                "INSERT INTO machine_events (event_type, detail, created_at) VALUES (?, ?, ?)",
                ("app_open", "I've opened chrome for you.", "2026-01-01T00:00:00.000Z"),
            )
            wm.conn.execute(
                "INSERT INTO machine_events (event_type, detail, created_at) VALUES (?, ?, ?)",
                ("app_open", "I've opened spotify for you.", "2026-01-01T01:00:00.000Z"),
            )
        assert wm.detect_app_sequence_pattern(min_occurrences=1, window_seconds=300) is None


class TestProposedRules:
    def test_propose_rule_not_in_active_rules(self, wm):
        wm.propose_rule("open spotify after chrome", "pattern")
        assert wm.active_rules() == []

    def test_propose_rule_in_full_list(self, wm):
        rid = wm.propose_rule("open spotify after chrome", "pattern")
        rules = wm.list_rules(include_decayed=True)
        row = next(r for r in rules if r[0] == rid)
        assert row[4] == "proposed"

    def test_approve_rule_makes_it_active(self, wm):
        rid = wm.propose_rule("open spotify after chrome", "pattern")
        wm.approve_rule(rid)
        assert rid in [r[0] for r in wm.active_rules()]

    def test_delete_proposed_rule(self, wm):
        rid = wm.propose_rule("open spotify after chrome", "pattern")
        wm.delete_rule(rid)
        assert rid not in [r[0] for r in wm.list_rules(include_decayed=True)]


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

    def test_includes_active_rules_only(self, wm):
        wm.add_rule("always reply short on Telegram", "teaching")
        wm.propose_rule("open spotify after chrome", "pattern")
        slice_text = wm.context_slice()
        assert "reply short on Telegram" in slice_text
        assert "spotify" not in slice_text

    def test_respects_char_budget(self, wm):
        wm.open_thread("x" * 2000, "sess1")
        assert len(wm.context_slice(char_budget=100)) <= 100
