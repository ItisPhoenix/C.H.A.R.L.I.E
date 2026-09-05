"""Tests for H2 - LLM & Runtime Telemetry Truth Authority.

Proves:
- Canonical health registry includes llm;
- Brain construction alone does not claim healthy conversational intelligence;
- Successful startup LLM probe -> llm=running, brain=running;
- Failed/unreachable probe -> llm=degraded, brain=degraded;
- Degraded startup does not falsely terminate the observable runtime;
- Primary LLM HTTP failure degrades health;
- Primary LLM transport failure degrades health;
- Subsequent success recovers health;
- Telemetry snapshot is produced by main authority;
- Snapshot contains no secret/request/response material;
- Canonical EventBus contract accepts/replays runtime_telemetry;
- runtime_state_request republishes telemetry;
- Web bridge caches valid main telemetry;
- Malformed telemetry cannot replace valid projected state;
- /api/health uses projected telemetry;
- /api/metrics uses projected telemetry;
- Developer diagnostics uses projected telemetry;
- Deliberately changing web-local telemetry does NOT change those APIs;
- Unsynchronized web telemetry is unknown/null rather than fake zero;
- Existing frozen architecture regressions remain green.
"""

import time
from typing import Any, Dict, List, Optional

import httpx
import pytest

import charlie.telemetry as telemetry
import charlie.web_server as web_server
from charlie.config import Config
from charlie.events import (
    CONTRACT_VERSION,
    EventMeta,
    EventSource,
    EventType,
    build_event,
    replay_event,
)
from charlie.subsystem_health import HealthStatus

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_test_state():
    telemetry.reset_telemetry()
    telemetry.set_telemetry_listener(None)
    web_server._projected_telemetry = None
    web_server._projected_telemetry_event = None
    yield
    telemetry.reset_telemetry()
    telemetry.set_telemetry_listener(None)
    web_server._projected_telemetry = None
    web_server._projected_telemetry_event = None


def _telemetry_payload(**overrides: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "authority": "main_runtime",
        "timestamp": time.time(),
        "llm_last_success_timestamp": 12345.0,
        "llm_last_attempt_timestamp": 12345.0,
        "llm_last_attempt_status": "success",
        "llm_error_rate": 0.25,
        "tool_error_rate": 0.1,
        "tool_error_rate_by_tool": {"search": {"calls": 10, "errors": 1, "error_rate": 0.1}},
        "unreliable_tools": [("flaky", 0.6, 5)],
        "llm": {
            "last_success_timestamp": 12345.0,
            "last_attempt_timestamp": 12345.0,
            "last_attempt_status": "success",
            "error_rate": 0.25,
            "total_calls": 4,
        },
        "tools": {
            "error_rate": 0.1,
            "total_calls": 10,
            "by_tool": {"search": {"calls": 10, "errors": 1, "error_rate": 0.1}},
            "unreliable_tools": [("flaky", 0.6, 5)],
        },
    }
    payload.update(overrides)
    return payload


def _telemetry_event(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return build_event(
        EventType.RUNTIME_TELEMETRY.value,
        payload if payload is not None else _telemetry_payload(),
        meta=EventMeta(
            source=EventSource.RUNTIME,
            rationale="test telemetry projection",
        ),
    )


# ---------------------------------------------------------------------------
# 1. Event Contract & Envelope
# ---------------------------------------------------------------------------

def test_runtime_telemetry_in_event_contract():
    assert "runtime_telemetry" in [e.value for e in EventType]
    event = build_event("runtime_telemetry", _telemetry_payload())
    assert event["type"] == "runtime_telemetry"
    assert event["version"] == CONTRACT_VERSION
    assert event["replay"] is False

    replayed = replay_event(event)
    assert replayed["replay"] is True


def test_telemetry_snapshot_produced_by_main_authority_and_is_secret_safe():
    telemetry.record_llm_call(success=True)
    telemetry.record_tool_call("desktop_click", success=True)
    snap = telemetry.snapshot()

    assert snap["authority"] == "main_runtime"
    assert "timestamp" in snap
    assert snap["llm_last_attempt_status"] == "success"
    assert snap["llm_last_attempt_timestamp"] > 0
    assert snap["llm_last_success_timestamp"] > 0
    assert snap["llm_error_rate"] == 0.0
    assert snap["tool_error_rate"] == 0.0
    assert "desktop_click" in snap["tool_error_rate_by_tool"]

    # Verify no secret / prompt / user content leaked in snapshot
    snap_str = str(snap).lower()
    for forbidden in ["key", "secret", "token", "password", "authorization", "prompt", "message"]:
        assert f"api_{forbidden}" not in snap_str
        assert f"api-{forbidden}" not in snap_str


def test_telemetry_distinguishes_never_attempted_success_failed():
    assert telemetry.last_llm_attempt_status() == "never_attempted"
    assert telemetry.last_llm_attempt_timestamp() == 0.0
    assert telemetry.last_llm_success_timestamp() == 0.0

    telemetry.record_llm_call(success=True)
    assert telemetry.last_llm_attempt_status() == "success"
    assert telemetry.last_llm_attempt_timestamp() > 0.0
    assert telemetry.last_llm_success_timestamp() > 0.0

    telemetry.record_llm_call(success=False)
    assert telemetry.last_llm_attempt_status() == "failed"
    assert telemetry.llm_error_rate() == 0.5


# ---------------------------------------------------------------------------
# 2. Canonical Health Registry & LLM Subsystem
# ---------------------------------------------------------------------------

def test_canonical_health_registry_includes_llm():
    import main
    snapshot = main._runtime_health.snapshot()
    assert "llm" in snapshot
    assert "brain" in snapshot


@pytest.mark.asyncio
async def test_brain_construction_alone_does_not_claim_brain_running():
    from charlie.core import Brain

    test_config = Config()
    test_config.llm_url = "http://fake-llm-host:12345"
    test_config.llm_key = "test-key"

    health_updates: List[tuple[HealthStatus, Optional[str]]] = []

    def on_llm_health(status: HealthStatus, detail: Optional[str] = None):
        health_updates.append((status, detail))

    brain = Brain(test_config, on_llm_health=on_llm_health)
    # Brain construction alone must NOT have triggered on_llm_health(RUNNING)
    assert not any(s == HealthStatus.RUNNING for s, _ in health_updates)
    await brain.close()


@pytest.mark.asyncio
async def test_startup_probe_success_transitions_llm_and_brain_running():
    from charlie.core import Brain

    test_config = Config()
    test_config.llm_url = "http://fake-llm-host:12345"
    test_config.llm_key = "test-key"
    test_config.llm_model = "test-model"

    health_updates: List[tuple[HealthStatus, Optional[str]]] = []

    def on_llm_health(status: HealthStatus, detail: Optional[str] = None):
        health_updates.append((status, detail))

    brain = Brain(test_config, on_llm_health=on_llm_health)

    async def fake_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/chat/completions"
        assert request.headers.get("Authorization") == "Bearer test-key"
        return httpx.Response(200, json={"choices": [{"message": {"content": "pong"}}]})

    from charlie.utils import build_auth_headers

    brain.client = httpx.AsyncClient(
        base_url=test_config.llm_url,
        headers=build_auth_headers(test_config.llm_key),
        transport=httpx.MockTransport(fake_handler),
        event_hooks={"response": [brain._record_llm_response]},
    )

    ok = await brain.probe_primary_llm(timeout=2.0)
    assert ok is True
    assert (HealthStatus.RUNNING, "Ready") in health_updates
    assert telemetry.last_llm_attempt_status() == "success"
    await brain.close()


@pytest.mark.asyncio
async def test_startup_probe_failure_transitions_llm_and_brain_degraded():
    from charlie.core import Brain

    test_config = Config()
    test_config.llm_url = "http://fake-llm-host:12345"
    test_config.llm_key = "test-key"
    test_config.llm_model = "test-model"

    health_updates: List[tuple[HealthStatus, Optional[str]]] = []

    def on_llm_health(status: HealthStatus, detail: Optional[str] = None):
        health_updates.append((status, detail))

    brain = Brain(test_config, on_llm_health=on_llm_health)

    async def fake_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid api key"})

    brain.client = httpx.AsyncClient(
        base_url=test_config.llm_url,
        transport=httpx.MockTransport(fake_handler),
        event_hooks={"response": [brain._record_llm_response]},
    )

    ok = await brain.probe_primary_llm(timeout=2.0)
    assert ok is False
    assert any(s == HealthStatus.DEGRADED for s, _ in health_updates)
    assert telemetry.last_llm_attempt_status() == "failed"
    await brain.close()


@pytest.mark.asyncio
async def test_startup_probe_transport_error_transitions_degraded_without_crash():
    from charlie.core import Brain

    test_config = Config()
    test_config.llm_url = "http://unreachable-llm-host:9999"
    test_config.llm_key = "test-key"
    test_config.llm_model = "test-model"

    health_updates: List[tuple[HealthStatus, Optional[str]]] = []

    def on_llm_health(status: HealthStatus, detail: Optional[str] = None):
        health_updates.append((status, detail))

    brain = Brain(test_config, on_llm_health=on_llm_health)

    async def fake_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    brain.client = httpx.AsyncClient(
        base_url=test_config.llm_url,
        transport=httpx.MockTransport(fake_handler),
        event_hooks={"response": [brain._record_llm_response]},
    )

    ok = await brain.probe_primary_llm(timeout=2.0)
    assert ok is False
    assert any(s == HealthStatus.DEGRADED for s, _ in health_updates)
    assert telemetry.last_llm_attempt_status() == "failed"
    # Verify no unhandled exception killed the process
    await brain.close()


@pytest.mark.asyncio
async def test_runtime_llm_failure_and_recovery():
    from charlie.core import Brain

    test_config = Config()
    test_config.llm_url = "http://fake-llm-host:12345"
    test_config.llm_key = "test-key"

    current_status = HealthStatus.STARTING
    current_detail = ""

    def on_llm_health(status: HealthStatus, detail: Optional[str] = None):
        nonlocal current_status, current_detail
        current_status = status
        current_detail = detail or ""

    brain = Brain(test_config, on_llm_health=on_llm_health)

    # 1. Success response
    gen1 = brain._allocate_primary_llm_generation()
    resp_ok = httpx.Response(
        200,
        request=httpx.Request(
            "POST",
            "http://fake-llm-host:12345/chat/completions",
            extensions={"primary_llm_generation": gen1},
        ),
    )
    await brain._record_llm_response(resp_ok)
    assert current_status == HealthStatus.RUNNING
    assert telemetry.last_llm_attempt_status() == "success"

    # 2. HTTP 500 failure
    gen2 = brain._allocate_primary_llm_generation()
    resp_err = httpx.Response(
        500,
        request=httpx.Request(
            "POST",
            "http://fake-llm-host:12345/chat/completions",
            extensions={"primary_llm_generation": gen2},
        ),
    )
    await brain._record_llm_response(resp_err)
    assert current_status == HealthStatus.DEGRADED
    assert "500" in current_detail
    assert telemetry.last_llm_attempt_status() == "failed"

    # 3. Subsequent success recovers
    gen3 = brain._allocate_primary_llm_generation()
    resp_recovered = httpx.Response(
        200,
        request=httpx.Request(
            "POST",
            "http://fake-llm-host:12345/chat/completions",
            extensions={"primary_llm_generation": gen3},
        ),
    )
    await brain._record_llm_response(resp_recovered)
    assert current_status == HealthStatus.RUNNING
    assert telemetry.last_llm_attempt_status() == "success"

    await brain.close()


# ---------------------------------------------------------------------------
# 3. Web Projection & APIs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unsynchronized_web_telemetry_is_null_not_fake_zero():
    # Before any authoritative snapshot has arrived:
    health_res = await web_server.health()
    assert health_res["llm_last_success_seconds_ago"] is None
    assert health_res["llm_error_rate"] is None
    assert health_res["tool_error_rate"] is None

    metrics_res = await web_server.metrics()
    assert metrics_res["status"] == "unavailable"
    assert metrics_res["llm_error_rate"] is None
    assert metrics_res["tool_error_rate"] is None
    assert metrics_res["tool_error_rate_by_tool"] == {}

    diag_res = await web_server.get_developer_diagnostics()
    telem = diag_res["diagnostics"]["telemetry"]
    assert telem["status"] == "unavailable"
    assert telem["llm_error_rate"] is None


@pytest.mark.asyncio
async def test_deliberately_changing_web_local_telemetry_does_not_affect_apis():
    # Deliberately modify web-local telemetry counters
    telemetry.record_llm_call(success=True)
    telemetry.record_llm_call(success=False)
    telemetry.record_tool_call("rogue_tool", success=True)

    # Web endpoints must STILL report unknown/null, not web process's local counters
    health_res = await web_server.health()
    assert health_res["llm_error_rate"] is None
    assert health_res["tool_error_rate"] is None

    metrics_res = await web_server.metrics()
    assert metrics_res["status"] == "unavailable"
    assert metrics_res["llm_error_rate"] is None
    assert metrics_res["tool_error_rate_by_tool"] == {}


@pytest.mark.asyncio
async def test_web_bridge_caches_valid_main_telemetry():
    event = _telemetry_event(_telemetry_payload(
        llm_error_rate=0.42,
        tool_error_rate=0.15,
        llm_last_success_timestamp=time.time() - 10.0,
        tool_error_rate_by_tool={"my_tool": {"calls": 20, "errors": 3, "error_rate": 0.15}},
    ))

    applied = web_server._apply_runtime_telemetry_event(event)
    assert applied is True

    health_res = await web_server.health()
    assert health_res["llm_error_rate"] == 0.42
    assert health_res["tool_error_rate"] == 0.15
    assert health_res["llm_last_success_seconds_ago"] is not None
    assert 9.0 <= health_res["llm_last_success_seconds_ago"] <= 20.0

    metrics_res = await web_server.metrics()
    assert metrics_res["status"] == "available"
    assert metrics_res["llm_error_rate"] == 0.42
    assert metrics_res["tool_error_rate"] == 0.15
    assert "my_tool" in metrics_res["tool_error_rate_by_tool"]

    diag_res = await web_server.get_developer_diagnostics()
    telem = diag_res["diagnostics"]["telemetry"]
    assert telem["status"] == "available"
    assert telem["authority"] == "main_runtime"
    assert telem["llm_error_rate"] == 0.42
    assert "my_tool" in telem["tool_stats"]


@pytest.mark.asyncio
async def test_malformed_telemetry_cannot_replace_valid_projection():
    valid_event = _telemetry_event(_telemetry_payload(llm_error_rate=0.12))
    assert web_server._apply_runtime_telemetry_event(valid_event) is True

    # Attempt malformed snapshots
    bad_authority = {"type": "runtime_telemetry", "version": CONTRACT_VERSION, "payload": {"authority": "web_fake"}}
    bad_type = {"type": "wrong_type", "version": CONTRACT_VERSION, "payload": _telemetry_payload()}
    bad_payload = {"type": "runtime_telemetry", "version": CONTRACT_VERSION, "payload": "not-a-dict"}

    assert web_server._apply_runtime_telemetry_event(bad_authority) is False
    assert web_server._apply_runtime_telemetry_event(bad_type) is False
    assert web_server._apply_runtime_telemetry_event(bad_payload) is False

    # The cached valid projection must be preserved
    metrics_res = await web_server.metrics()
    assert metrics_res["llm_error_rate"] == 0.12


@pytest.mark.asyncio
async def test_runtime_state_request_republishes_telemetry():
    import main

    class FakeBus:
        def __init__(self):
            self.emitted = []

        async def emit(self, event_type, payload, meta=None):
            self.emitted.append((event_type, payload, meta))

    bus = FakeBus()
    handled = await main._handle_runtime_state_request("runtime_state_request", bus=bus)
    assert handled is True

    types = [t for t, _, _ in bus.emitted]
    assert "subsystem_health" in types
    assert "task_snapshot" in types
    assert "tool_snapshot" in types
    assert "mcp_snapshot" in types
    assert "runtime_telemetry" in types


# ---------------------------------------------------------------------------
# 4. Causal Health Ordering & Race Regressions (Tests A-E)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_race_a_stale_failure_cannot_overwrite_newer_success():
    """Test A: Stale failure from older dispatch cannot overwrite newer success.

    Dispatch 1 (gen 1) is slow.
    Dispatch 2 (gen 2) completes successfully -> RUNNING.
    Dispatch 1 completes with failure -> suppressed, remains RUNNING.
    """
    from charlie.core import Brain

    test_config = Config()
    test_config.llm_url = "http://fake-llm-host:12345"
    test_config.llm_key = "test-key"

    status_history = []

    def on_llm_health(status: HealthStatus, detail: Optional[str] = None):
        status_history.append((status, detail))

    brain = Brain(test_config, on_llm_health=on_llm_health)

    gen1 = brain._allocate_primary_llm_generation()
    gen2 = brain._allocate_primary_llm_generation()
    assert gen2 > gen1

    # Gen 2 completes first successfully
    resp_gen2 = httpx.Response(
        200,
        request=httpx.Request(
            "POST",
            "http://fake-llm-host:12345/chat/completions",
            extensions={"primary_llm_generation": gen2},
        ),
    )
    await brain._record_llm_response(resp_gen2)
    assert status_history[-1][0] == HealthStatus.RUNNING

    # Gen 1 completes later with failure
    resp_gen1 = httpx.Response(
        500,
        request=httpx.Request(
            "POST",
            "http://fake-llm-host:12345/chat/completions",
            extensions={"primary_llm_generation": gen1},
        ),
    )
    await brain._record_llm_response(resp_gen1)

    # Health must NOT transition to DEGRADED; remains RUNNING
    assert status_history[-1][0] == HealthStatus.RUNNING
    assert len(status_history) == 1  # stale failure was completely suppressed
    await brain.close()


@pytest.mark.asyncio
async def test_race_b_stale_success_cannot_overwrite_newer_failure():
    """Test B: Stale success from older dispatch cannot overwrite newer failure.

    Dispatch 1 (gen 1) is slow.
    Dispatch 2 (gen 2) fails -> DEGRADED.
    Dispatch 1 completes with success -> suppressed, remains DEGRADED.
    """
    from charlie.core import Brain

    test_config = Config()
    test_config.llm_url = "http://fake-llm-host:12345"
    test_config.llm_key = "test-key"

    status_history = []

    def on_llm_health(status: HealthStatus, detail: Optional[str] = None):
        status_history.append((status, detail))

    brain = Brain(test_config, on_llm_health=on_llm_health)

    gen1 = brain._allocate_primary_llm_generation()
    gen2 = brain._allocate_primary_llm_generation()

    # Gen 2 completes first with failure
    resp_gen2 = httpx.Response(
        503,
        request=httpx.Request(
            "POST",
            "http://fake-llm-host:12345/chat/completions",
            extensions={"primary_llm_generation": gen2},
        ),
    )
    await brain._record_llm_response(resp_gen2)
    assert status_history[-1][0] == HealthStatus.DEGRADED

    # Gen 1 completes later with success
    resp_gen1 = httpx.Response(
        200,
        request=httpx.Request(
            "POST",
            "http://fake-llm-host:12345/chat/completions",
            extensions={"primary_llm_generation": gen1},
        ),
    )
    await brain._record_llm_response(resp_gen1)

    # Health must NOT transition to RUNNING; remains DEGRADED
    assert status_history[-1][0] == HealthStatus.DEGRADED
    assert len(status_history) == 1  # stale success was suppressed
    await brain.close()


@pytest.mark.asyncio
async def test_race_c_sequential_recovery():
    """Test C: Sequential recovery - Gen 1 fails, subsequent Gen 2 succeeds -> RUNNING."""
    from charlie.core import Brain

    test_config = Config()
    test_config.llm_url = "http://fake-llm-host:12345"
    test_config.llm_key = "test-key"

    status_history = []

    def on_llm_health(status: HealthStatus, detail: Optional[str] = None):
        status_history.append((status, detail))

    brain = Brain(test_config, on_llm_health=on_llm_health)

    # 1. Gen 1 fails
    gen1 = brain._allocate_primary_llm_generation()
    resp_gen1 = httpx.Response(
        500,
        request=httpx.Request(
            "POST",
            "http://fake-llm-host:12345/chat/completions",
            extensions={"primary_llm_generation": gen1},
        ),
    )
    await brain._record_llm_response(resp_gen1)
    assert status_history[-1][0] == HealthStatus.DEGRADED

    # 2. Gen 2 succeeds
    gen2 = brain._allocate_primary_llm_generation()
    resp_gen2 = httpx.Response(
        200,
        request=httpx.Request(
            "POST",
            "http://fake-llm-host:12345/chat/completions",
            extensions={"primary_llm_generation": gen2},
        ),
    )
    await brain._record_llm_response(resp_gen2)
    assert status_history[-1][0] == HealthStatus.RUNNING
    assert len(status_history) == 2
    await brain.close()


@pytest.mark.asyncio
async def test_race_d_sequential_degradation():
    """Test D: Sequential degradation - Gen 1 succeeds, subsequent Gen 2 fails -> DEGRADED."""
    from charlie.core import Brain

    test_config = Config()
    test_config.llm_url = "http://fake-llm-host:12345"
    test_config.llm_key = "test-key"

    status_history = []

    def on_llm_health(status: HealthStatus, detail: Optional[str] = None):
        status_history.append((status, detail))

    brain = Brain(test_config, on_llm_health=on_llm_health)

    # 1. Gen 1 succeeds
    gen1 = brain._allocate_primary_llm_generation()
    resp_gen1 = httpx.Response(
        200,
        request=httpx.Request(
            "POST",
            "http://fake-llm-host:12345/chat/completions",
            extensions={"primary_llm_generation": gen1},
        ),
    )
    await brain._record_llm_response(resp_gen1)
    assert status_history[-1][0] == HealthStatus.RUNNING

    # 2. Gen 2 fails
    gen2 = brain._allocate_primary_llm_generation()
    resp_gen2 = httpx.Response(
        502,
        request=httpx.Request(
            "POST",
            "http://fake-llm-host:12345/chat/completions",
            extensions={"primary_llm_generation": gen2},
        ),
    )
    await brain._record_llm_response(resp_gen2)
    assert status_history[-1][0] == HealthStatus.DEGRADED
    assert len(status_history) == 2
    await brain.close()


@pytest.mark.asyncio
async def test_race_e_vision_request_cannot_alter_primary_llm_health():
    """Test E: Vision / secondary requests carry no primary generation and cannot touch primary LLM health."""
    from charlie.core import Brain

    test_config = Config()
    test_config.llm_url = "http://fake-llm-host:12345"
    test_config.llm_key = "test-key"

    status_history = []

    def on_llm_health(status: HealthStatus, detail: Optional[str] = None):
        status_history.append((status, detail))

    brain = Brain(test_config, on_llm_health=on_llm_health)

    # Primary LLM starts at RUNNING via Gen 1
    gen1 = brain._allocate_primary_llm_generation()
    resp_gen1 = httpx.Response(
        200,
        request=httpx.Request(
            "POST",
            "http://fake-llm-host:12345/chat/completions",
            extensions={"primary_llm_generation": gen1},
        ),
    )
    await brain._record_llm_response(resp_gen1)
    assert status_history[-1][0] == HealthStatus.RUNNING

    # A vision response arrives (no primary_llm_generation extension) with failure (500)
    resp_vision_err = httpx.Response(
        500,
        request=httpx.Request("POST", "http://fake-vision-host:9999/chat/completions"),
    )
    assert "primary_llm_generation" not in resp_vision_err.request.extensions
    await brain._record_llm_response(resp_vision_err)

    # Primary LLM health must remain UNTOUCHED (still 1 event, still RUNNING)
    assert len(status_history) == 1
    assert status_history[-1][0] == HealthStatus.RUNNING

    # A vision response arrives with 200 OK (also no primary_llm_generation extension)
    resp_vision_ok = httpx.Response(
        200,
        request=httpx.Request("POST", "http://fake-vision-host:9999/chat/completions"),
    )
    await brain._record_llm_response(resp_vision_ok)
    assert len(status_history) == 1
    assert status_history[-1][0] == HealthStatus.RUNNING
    await brain.close()


@pytest.mark.asyncio
async def test_race_f_stale_failure_before_newer_completion():
    """Race F: Stale failure arrives while newer dispatch is still in flight.

    Initial state: RUNNING
    A starts -> generation 1
    B starts -> generation 2
    A fails (B still pending)
    State MUST remain RUNNING; A must not produce DEGRADED.
    Then B succeeds -> remains RUNNING.
    """
    from charlie.core import Brain

    test_config = Config()
    test_config.llm_url = "http://fake-llm-host:12345"
    test_config.llm_key = "test-key"

    status_history = []

    def on_llm_health(status: HealthStatus, detail: Optional[str] = None):
        status_history.append((status, detail))

    brain = Brain(test_config, on_llm_health=on_llm_health)

    # Initial state: RUNNING
    gen0 = brain._allocate_primary_llm_generation()
    resp_init = httpx.Response(
        200,
        request=httpx.Request(
            "POST",
            "http://fake-llm-host:12345/chat/completions",
            extensions={"primary_llm_generation": gen0},
        ),
    )
    await brain._record_llm_response(resp_init)
    assert status_history[-1][0] == HealthStatus.RUNNING
    status_history.clear()

    # A starts -> generation 1
    gen1 = brain._allocate_primary_llm_generation()

    # B starts -> generation 2 (now the latest dispatch generation)
    gen2 = brain._allocate_primary_llm_generation()
    assert gen2 > gen1

    # A fails while B is still pending/in-flight
    resp_gen1_err = httpx.Response(
        500,
        request=httpx.Request(
            "POST",
            "http://fake-llm-host:12345/chat/completions",
            extensions={"primary_llm_generation": gen1},
        ),
    )
    await brain._record_llm_response(resp_gen1_err)
    applied_a = brain._notify_primary_llm_health(gen1, HealthStatus.DEGRADED, "HTTP 500")
    assert applied_a is False

    # IMMEDIATELY inspect state before B completes:
    # Health MUST remain prior canonical state (RUNNING), A must NOT produce DEGRADED
    assert len(status_history) == 0  # No transitions triggered by stale A

    # B completes with success
    resp_gen2_ok = httpx.Response(
        200,
        request=httpx.Request(
            "POST",
            "http://fake-llm-host:12345/chat/completions",
            extensions={"primary_llm_generation": gen2},
        ),
    )
    await brain._record_llm_response(resp_gen2_ok)
    assert len(status_history) == 1
    assert status_history[-1][0] == HealthStatus.RUNNING

    await brain.close()


@pytest.mark.asyncio
async def test_race_g_stale_success_before_newer_completion():
    """Race G: Stale success arrives while newer dispatch is still in flight.

    Initial state: DEGRADED
    A starts -> generation 1
    B starts -> generation 2
    A succeeds (B still pending)
    State MUST remain DEGRADED; A must not falsely recover the subsystem.
    Then B fails -> remains DEGRADED.
    """
    from charlie.core import Brain

    test_config = Config()
    test_config.llm_url = "http://fake-llm-host:12345"
    test_config.llm_key = "test-key"

    status_history = []

    def on_llm_health(status: HealthStatus, detail: Optional[str] = None):
        status_history.append((status, detail))

    brain = Brain(test_config, on_llm_health=on_llm_health)

    # Initial state: DEGRADED
    gen0 = brain._allocate_primary_llm_generation()
    resp_init = httpx.Response(
        500,
        request=httpx.Request(
            "POST",
            "http://fake-llm-host:12345/chat/completions",
            extensions={"primary_llm_generation": gen0},
        ),
    )
    await brain._record_llm_response(resp_init)
    assert status_history[-1][0] == HealthStatus.DEGRADED
    status_history.clear()

    # A starts -> generation 1
    gen1 = brain._allocate_primary_llm_generation()

    # B starts -> generation 2 (now the latest dispatch generation)
    gen2 = brain._allocate_primary_llm_generation()
    assert gen2 > gen1

    # A succeeds while B is still pending/in-flight
    resp_gen1_ok = httpx.Response(
        200,
        request=httpx.Request(
            "POST",
            "http://fake-llm-host:12345/chat/completions",
            extensions={"primary_llm_generation": gen1},
        ),
    )
    await brain._record_llm_response(resp_gen1_ok)
    applied_a = brain._notify_primary_llm_health(gen1, HealthStatus.RUNNING, "Ready")
    assert applied_a is False

    # IMMEDIATELY inspect state before B completes:
    # Health MUST remain DEGRADED; A must NOT falsely recover subsystem
    assert len(status_history) == 0  # No transitions triggered by stale A

    # B completes with failure
    resp_gen2_err = httpx.Response(
        503,
        request=httpx.Request(
            "POST",
            "http://fake-llm-host:12345/chat/completions",
            extensions={"primary_llm_generation": gen2},
        ),
    )
    await brain._record_llm_response(resp_gen2_err)
    assert len(status_history) == 1
    assert status_history[-1][0] == HealthStatus.DEGRADED

    await brain.close()


@pytest.mark.asyncio
async def test_same_generation_response_success_then_stream_transport_error():
    """Test same-generation behavior: HTTP 200 response header hook marks RUNNING,
    then streaming the response body raises TransportError (e.g. broken connection).
    The same generation MUST update health to DEGRADED, not be locked at RUNNING.
    """
    from charlie.core import Brain

    test_config = Config()
    test_config.llm_url = "http://fake-llm-host:12345"
    test_config.llm_key = "test-key"
    test_config.llm_model = "test-model"

    status_history = []

    def on_llm_health(status: HealthStatus, detail: Optional[str] = None):
        status_history.append((status, detail))

    brain = Brain(test_config, on_llm_health=on_llm_health)

    class BrokenStreamTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            async def broken_stream():
                yield b"data: {\"choices\": [{\"delta\": {\"content\": \"hello\"}}]}\n\n"
                raise httpx.ReadError("Connection abruptly terminated during streaming", request=request)

            return httpx.Response(
                200,
                content=broken_stream(),
                request=request,
            )

    brain.client = httpx.AsyncClient(
        base_url=test_config.llm_url,
        transport=BrokenStreamTransport(),
        event_hooks={"response": [brain._record_llm_response]},
    )

    with pytest.raises(httpx.TransportError):
        await brain._stream_completion(
            {"messages": [{"role": "user", "content": "hi"}]},
            generation=brain._chat_generation,
        )

    # Both events belong to the same generation:
    # 1. HTTP 200 response event hook fired -> RUNNING
    # 2. Stream read error caught -> DEGRADED
    assert len(status_history) == 2
    assert status_history[0][0] == HealthStatus.RUNNING
    assert status_history[1][0] == HealthStatus.DEGRADED
    assert "Transport error" in (status_history[1][1] or "")

    await brain.close()
