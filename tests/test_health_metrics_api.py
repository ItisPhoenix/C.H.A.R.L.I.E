"""Tests for GET /api/health and GET /api/metrics (projected from authoritative runtime telemetry)."""

import time

import pytest

import charlie.telemetry as telemetry
import charlie.web_server as web_server
from charlie.events import EventMeta, EventSource, build_event


@pytest.fixture(autouse=True)
def _reset_telemetry():
    telemetry.reset_telemetry()
    web_server._projected_telemetry = None
    web_server._projected_telemetry_event = None
    yield
    telemetry.reset_telemetry()
    web_server._projected_telemetry = None
    web_server._projected_telemetry_event = None


def _make_telemetry_event(payload: dict) -> dict:
    base = {
        "authority": "main_runtime",
        "timestamp": time.time(),
        "llm_last_success_timestamp": None,
        "last_llm_attempt_timestamp": None,
        "last_llm_attempt_status": "none",
        "llm_error_rate": 0.0,
        "tool_error_rate": 0.0,
        "tool_error_rate_by_tool": {},
        "unreliable_tools": [],
    }
    base.update(payload)
    return build_event(
        "runtime_telemetry",
        base,
        meta=EventMeta(source=EventSource.RUNTIME, rationale="test telemetry projection"),
    )


@pytest.mark.asyncio
async def test_health_with_no_recorded_calls():
    # Unsynchronized web telemetry returns None rather than fake 0.0
    res = await web_server.health()
    assert res["llm_last_success_seconds_ago"] is None
    assert res["llm_error_rate"] is None
    assert res["tool_error_rate"] is None
    assert "uptime_seconds" in res


@pytest.mark.asyncio
async def test_metrics_unsynchronized():
    res = await web_server.metrics()
    assert res["status"] == "unavailable"
    assert res["llm_error_rate"] is None
    assert res["tool_error_rate"] is None
    assert res["tool_error_rate_by_tool"] == {}


@pytest.mark.asyncio
async def test_web_local_telemetry_does_not_affect_health_or_metrics():
    # Mutating web-local telemetry counters must NOT affect read-only projection
    telemetry.record_llm_call(success=True)
    telemetry.record_llm_call(success=False)
    telemetry.record_tool_call("web_search", success=True)

    health_res = await web_server.health()
    assert health_res["llm_last_success_seconds_ago"] is None
    assert health_res["llm_error_rate"] is None
    assert health_res["tool_error_rate"] is None

    metrics_res = await web_server.metrics()
    assert metrics_res["status"] == "unavailable"
    assert metrics_res["llm_error_rate"] is None
    assert metrics_res["tool_error_rate"] is None
    assert metrics_res["tool_error_rate_by_tool"] == {}


@pytest.mark.asyncio
async def test_health_reflects_recorded_llm_success():
    event = _make_telemetry_event({
        "llm_last_success_timestamp": time.time() - 2.0,
        "llm_error_rate": 0.0,
    })
    assert web_server._apply_runtime_telemetry_event(event) is True

    res = await web_server.health()
    assert res["llm_last_success_seconds_ago"] is not None
    assert 1.0 <= res["llm_last_success_seconds_ago"] < 10.0


@pytest.mark.asyncio
async def test_health_reflects_llm_error_rate():
    event = _make_telemetry_event({
        "llm_error_rate": 0.5,
        "tool_error_rate": 0.2,
    })
    assert web_server._apply_runtime_telemetry_event(event) is True

    res = await web_server.health()
    assert res["llm_error_rate"] == 0.5
    assert res["tool_error_rate"] == 0.2


@pytest.mark.asyncio
async def test_metrics_breaks_down_by_tool():
    event = _make_telemetry_event({
        "llm_error_rate": 0.1,
        "tool_error_rate": 0.25,
        "tool_error_rate_by_tool": {
            "web_search": {"calls": 2, "errors": 1, "error_rate": 0.5},
            "shell_execute": {"calls": 1, "errors": 0, "error_rate": 0.0},
        },
    })
    assert web_server._apply_runtime_telemetry_event(event) is True

    res = await web_server.metrics()
    assert res["status"] == "available"
    assert res["llm_error_rate"] == 0.1
    assert res["tool_error_rate"] == 0.25
    assert res["tool_error_rate_by_tool"]["web_search"] == {"calls": 2, "errors": 1, "error_rate": 0.5}
    assert res["tool_error_rate_by_tool"]["shell_execute"] == {"calls": 1, "errors": 0, "error_rate": 0.0}
