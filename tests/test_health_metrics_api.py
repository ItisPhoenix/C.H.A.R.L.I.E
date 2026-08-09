"""Tests for GET /api/health and GET /api/metrics (charlie/telemetry.py-backed)."""

import pytest

import charlie.telemetry as telemetry
import charlie.web_server as web_server


@pytest.fixture(autouse=True)
def _reset_telemetry():
    telemetry._llm_calls.clear()
    telemetry._tool_calls.clear()
    telemetry._last_llm_success = 0.0
    yield
    telemetry._llm_calls.clear()
    telemetry._tool_calls.clear()
    telemetry._last_llm_success = 0.0


@pytest.mark.asyncio
async def test_health_with_no_recorded_calls():
    res = await web_server.health()
    assert res["llm_last_success_seconds_ago"] is None
    assert res["llm_error_rate"] == 0.0
    assert res["tool_error_rate"] == 0.0
    assert "uptime_seconds" in res


@pytest.mark.asyncio
async def test_health_reflects_recorded_llm_success():
    telemetry.record_llm_call(success=True)
    res = await web_server.health()
    assert res["llm_last_success_seconds_ago"] is not None
    assert res["llm_last_success_seconds_ago"] < 5.0


@pytest.mark.asyncio
async def test_health_reflects_llm_error_rate():
    telemetry.record_llm_call(success=True)
    telemetry.record_llm_call(success=False)
    res = await web_server.health()
    assert res["llm_error_rate"] == 0.5


@pytest.mark.asyncio
async def test_metrics_breaks_down_by_tool():
    telemetry.record_tool_call("web_search", success=True)
    telemetry.record_tool_call("web_search", success=False)
    telemetry.record_tool_call("shell_execute", success=True)
    res = await web_server.metrics()
    assert res["tool_error_rate_by_tool"]["web_search"] == {"calls": 2, "errors": 1, "error_rate": 0.5}
    assert res["tool_error_rate_by_tool"]["shell_execute"] == {"calls": 1, "errors": 0, "error_rate": 0.0}
