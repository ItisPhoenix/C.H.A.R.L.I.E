"""Tests for charlie/telemetry.py, including unreliable_tools (Phase 1c outcome-feedback signal)."""

from collections import deque

import pytest

from charlie import telemetry


@pytest.fixture(autouse=True)
def _reset_telemetry_state():
    telemetry._tool_calls = deque(maxlen=telemetry._MAX_SAMPLES)
    telemetry._llm_calls = deque(maxlen=telemetry._MAX_SAMPLES)
    yield


class TestToolErrorRateByName:
    def test_empty_state(self):
        assert telemetry.tool_error_rate_by_name() == {}

    def test_tracks_calls_and_errors_per_tool(self):
        telemetry.record_tool_call("web_search", success=True)
        telemetry.record_tool_call("web_search", success=False)
        stats = telemetry.tool_error_rate_by_name()
        assert stats["web_search"]["calls"] == 2
        assert stats["web_search"]["errors"] == 1
        assert stats["web_search"]["error_rate"] == 0.5


class TestUnreliableTools:
    def test_below_min_calls_not_flagged(self):
        for _ in range(4):
            telemetry.record_tool_call("flaky_tool", success=False)
        assert telemetry.unreliable_tools() == []

    def test_below_error_threshold_not_flagged(self):
        for _ in range(10):
            telemetry.record_tool_call("mostly_fine", success=True)
        telemetry.record_tool_call("mostly_fine", success=False)
        assert telemetry.unreliable_tools() == []

    def test_flags_tool_at_threshold(self):
        for _ in range(3):
            telemetry.record_tool_call("flaky_tool", success=True)
        for _ in range(3):
            telemetry.record_tool_call("flaky_tool", success=False)
        flagged = telemetry.unreliable_tools()
        assert len(flagged) == 1
        assert flagged[0][0] == "flaky_tool"
        assert flagged[0][1] == 0.5
        assert flagged[0][2] == 6

    def test_sorted_worst_first(self):
        for _ in range(2):
            telemetry.record_tool_call("bad", success=True)
        for _ in range(4):
            telemetry.record_tool_call("bad", success=False)
        for _ in range(5):
            telemetry.record_tool_call("worse", success=False)
        names = [name for name, _rate, _n in telemetry.unreliable_tools()]
        assert names[0] == "worse"
