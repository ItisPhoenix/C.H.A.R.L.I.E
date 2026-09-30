"""Tests for Charlie Doctor & Self-Healing."""

import shutil
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from charlie.capabilities import CapabilityDescriptor, CapabilityIndex, CapabilityOperation
from charlie.config import Config
from charlie.doctor import (
    CharlieDoctor,
    CheckSeverity,
    CheckStatus,
    DiagnosticCheck,
    DoctorReport,
    ProbeResult,
    _probe_chat_endpoint,
    _probe_event_port,
    _probe_tesseract,
    _resolve_event_port,
)
from charlie.memory_graph import MemoryGraph
from charlie.memory_service import MemoryService
from charlie.resource_locks import CapabilityLeaseManager
from charlie.runtime_introspector import RuntimeIntrospector
from charlie.subsystem_health import HealthRegistry, HealthStatus
from charlie.task_journal import TaskJournal

_HEALTHY_PROBE = ProbeResult(True, "HTTP 200 from injected healthy probe", 200)


def _closed_port() -> int:
    """Reserve then release a port so nothing is listening on it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def busy_port():
    """Yield a port with a live listener, then release it."""
    held: list[socket.socket] = []

    def _make() -> int:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        held.append(s)
        return int(s.getsockname()[1])

    yield _make
    for s in held:
        s.close()


@pytest.mark.parametrize("check", ["event_bus", "vision_ocr", "subsystem_health"])
def test_doctor_never_reports_missing_runtime_evidence_as_ok(check):
    inspector = SimpleNamespace(get_health_info=lambda: {}, get_subsystem_info=lambda: {})
    result = getattr(CharlieDoctor(introspector=inspector), f"_check_{check}")()
    assert result.status != CheckStatus.OK


@pytest.mark.parametrize("state", ["starting", "stopped", "degraded"])
def test_doctor_reports_nonrunning_subsystem_health(state):
    inspector = SimpleNamespace(get_health_info=lambda: {"voice": {"status": state}})
    result = CharlieDoctor(introspector=inspector)._check_subsystem_health()
    assert result.status == CheckStatus.WARNING


def test_doctor_reports_its_tripped_repair_circuit():
    doctor = CharlieDoctor()
    for _ in range(3):
        doctor.record_repair_attempt("repair_mcp_reconnect", success=False)
    result = doctor._check_recovery_state()
    assert result.status == CheckStatus.WARNING
    assert "repair_mcp_reconnect" in result.evidence


def test_stale_lease_repair_preserves_live_and_non_task_owners(mock_doctor_env):
    from charlie.resource_locks import acquire, current_owner, release

    doctor, _, _, _, journal = mock_doctor_env
    live = journal.create_task("Active work")
    journal.transition(live.id, "running")
    owners = {"desktop": "orphan", "browser": live.id, "terminal": "session_live", "keyboard": "user"}
    try:
        for capability, owner in owners.items():
            assert acquire(capability, owner)
        assert doctor.execute_repair("repair_stale_leases")["success"]
        assert current_owner("desktop") is None
        for capability in ("browser", "terminal", "keyboard"):
            assert current_owner(capability) == owners[capability]
    finally:
        for capability, owner in owners.items():
            release(capability, owner)


def test_mcp_repair_without_runtime_client_cannot_succeed():
    inspector = SimpleNamespace(_get_mcp_client=lambda: None)
    assert not CharlieDoctor(introspector=inspector).execute_repair("repair_mcp_reconnect")["success"]


@pytest.fixture
def mock_doctor_env():
    """Create isolated environment with healthy and simulated broken subsystems."""
    with tempfile.TemporaryDirectory():
        cfg = Config()
        cfg.llm_provider = "openai"
        cfg.llm_model = "gpt-4o"
        cfg.llm_api_key = "sk-test-valid-key"
        # Under pytest CHARLIE_TEST_MODE suppresses the repository .env, so a
        # bare Config() has no LLM route at all. The doctor now fails a model
        # check whose endpoint is unconfigured, so a "healthy environment"
        # fixture has to actually configure one.
        cfg.llm_url = "http://127.0.0.1:1/v1"

        # Capabilities
        cap_idx = CapabilityIndex()
        cap_idx.register_capability(
            CapabilityDescriptor(
                id="system",
                name="System Control",
                description="OS commands",
                owner="charlie.tools",
                operations={
                    "ping": CapabilityOperation(
                        id="ping",
                        name="ping",
                        description="Ping",
                        parameters_schema={"type": "object"},
                        risk_class="safe",
                    )
                },
                availability_check=lambda: True,
                provenance="builtin",
            )
        )

        # Health Registry
        health = HealthRegistry(("brain", "voice", "browser", "desktop", "terminal", "memory"))
        health.set("brain", HealthStatus.RUNNING)
        health.set("voice", HealthStatus.RUNNING)
        health.set("browser", HealthStatus.RUNNING)
        health.set("desktop", HealthStatus.RUNNING)
        health.set("terminal", HealthStatus.RUNNING)
        health.set("memory", HealthStatus.RUNNING)

        # Task journal & lease manager
        journal = TaskJournal()
        lease_mgr = CapabilityLeaseManager()
        memory_graph = MemoryGraph(":memory:")
        memory_service = MemoryService(graph=memory_graph, semantic_expected=False)

        introspector = RuntimeIntrospector(
            config=cfg,
            capability_index=cap_idx,
            health_registry=health,
            task_journal=journal,
            lease_manager=lease_mgr,
            memory_service=memory_service,
        )

        doctor = CharlieDoctor(
            config=cfg,
            introspector=introspector,
            capability_index=cap_idx,
            task_journal=journal,
            lease_manager=lease_mgr,
            health_registry=health,
            # The unit suite must not spend a real completion or depend on
            # outbound reachability; the live probe path is covered separately
            # by test_default_llm_probe_contacts_the_configured_endpoint.
            llm_probe=lambda: _HEALTHY_PROBE,
        )

        try:
            yield doctor, cfg, health, lease_mgr, journal
        finally:
            memory_graph.close()


def test_doctor_healthy_report(mock_doctor_env):
    """Verify healthy subsystem diagnostics yield overall ok status."""
    doctor, _, _, _, _ = mock_doctor_env
    report = doctor.diagnose()

    assert isinstance(report, DoctorReport)
    assert report.total_checks > 10
    assert report.is_healthy is True
    assert len(report.errors) == 0

    # Verify each check has evidence and typed fields
    for check in report.checks:
        assert isinstance(check, DiagnosticCheck)
        assert check.status in (CheckStatus.OK, CheckStatus.INFO, CheckStatus.WARNING)
        assert len(check.evidence) > 0
        assert check.category in (
            "config", "secrets", "models", "capabilities", "tasks",
            "leases", "mcp", "memory", "terminal", "browser",
            "desktop", "vision", "voice", "storage", "health", "recovery", "extensions"
        )


def test_doctor_detects_unconfigured_api_key(mock_doctor_env):
    """Verify Doctor flags unconfigured cloud LLM API key with clear evidence."""
    doctor, cfg, _, _, _ = mock_doctor_env
    cfg.llm_api_key = ""
    cfg.llm_key = ""

    report = doctor.diagnose()
    secret_checks = [c for c in report.checks if c.check_id == "secrets_configured"]
    assert len(secret_checks) == 1
    sc = secret_checks[0]

    assert sc.status == CheckStatus.WARNING
    assert sc.severity == CheckSeverity.HIGH
    assert "not set" in sc.evidence.lower() or "missing" in sc.evidence.lower()
    assert sc.fix_hint is not None


def test_doctor_detects_orphan_capability_lease(mock_doctor_env):
    """Verify Doctor detects orphaned capability lease and offers safe auto-repair."""
    doctor, _, _, lease_mgr, journal = mock_doctor_env

    # Acquire lease for non-existent task
    from charlie.resource_locks import acquire as sync_acquire
    sync_acquire("desktop", "orphan-task-999")

    report = doctor.diagnose()
    lease_checks = [c for c in report.checks if c.check_id == "capability_leases"]
    assert len(lease_checks) == 1
    lc = lease_checks[0]

    assert lc.status == CheckStatus.WARNING
    assert "orphan" in lc.evidence.lower() or "desktop" in lc.evidence.lower()
    assert lc.repair_available is True
    assert lc.repair_id == "repair_stale_leases"
    assert lc.requires_approval is False  # Safe internal repair

    # Execute safe repair
    repair_res = doctor.execute_repair("repair_stale_leases")
    assert repair_res["success"] is True

    # Post-repair verification: lease should be cleared
    post_report = doctor.diagnose()
    post_lc = [c for c in post_report.checks if c.check_id == "capability_leases"][0]
    assert post_lc.status == CheckStatus.OK


def test_doctor_consequential_repair_requires_approval(mock_doctor_env):
    """Verify consequential repairs fail without explicit approval."""
    doctor, _, _, _, _ = mock_doctor_env

    res = doctor.execute_repair("repair_consequential_action", approved=False)
    assert res["success"] is False
    assert "approval required" in res["message"].lower()


def test_doctor_repair_circuit_breaker_and_bounded_retries(mock_doctor_env):
    """Verify repair circuit breaker prevents endless retry loops."""
    doctor, _, _, _, _ = mock_doctor_env

    # Simulate failing repair
    for _ in range(3):
        doctor.record_repair_attempt("repair_failing_service", success=False)

    assert doctor.is_repair_circuit_broken("repair_failing_service") is True
    res = doctor.execute_repair("repair_failing_service")
    assert res["success"] is False
    assert "circuit breaker" in res["message"].lower() or "too many failed" in res["message"].lower()


def test_doctor_report_serialization_and_formatting(mock_doctor_env):
    """Verify Doctor report converts to dict and formats cleanly."""
    doctor, _, _, _, _ = mock_doctor_env
    report = doctor.diagnose()

    rep_dict = report.to_dict()
    assert "checks" in rep_dict
    assert rep_dict["total_checks"] > 10

    report_text = doctor.format_report(report)
    assert "CHARLIE DOCTOR" in report_text
    assert "Evidence:" in report_text


# ---------------------------------------------------------------------------
# ITEM 0.8 -- the model check must reflect a real probe, not config
# ---------------------------------------------------------------------------


def _model_doctor(probe):
    """Doctor wired to a stubbed config plus the given probe source."""
    cfg = SimpleNamespace(llm_url="http://127.0.0.1:1/v1", llm_model="m", llm_key="k", llm_trust_env=False)
    introspector = SimpleNamespace(
        _get_config=lambda: cfg,
        get_model_info=lambda: {
            "provider": "openai",
            "model": "m",
            "api_base_url": "http://127.0.0.1:1/v1",
            "api_key_configured": True,
            "vision_model": "local",
        },
    )
    return CharlieDoctor(introspector=introspector, llm_probe=probe)


def test_model_provider_reports_error_when_live_probe_fails():
    """A network failure on the LLM route must surface as ERROR, not a config echo."""
    doctor = _model_doctor(lambda: ProbeResult(False, "ConnectError contacting http://127.0.0.1:1/v1"))

    result = doctor._check_model_provider()

    assert result.status == CheckStatus.ERROR
    assert "unreachable" in result.summary.lower()
    assert "ConnectError" in result.evidence
    assert result.fix_hint is not None


def test_failing_model_probe_makes_the_whole_report_unhealthy(mock_doctor_env):
    """charlie/AGENTS.md 12: never report healthy while the dependency is failing."""
    doctor, _, _, _, _ = mock_doctor_env
    # Swap the injected verdict for a failed one so the full report path, not
    # just the single check, is exercised against a real introspector.
    doctor._llm_probe = lambda: ProbeResult(False, "ConnectError contacting the configured LLM route")
    doctor._llm_probe_cache = None

    report = doctor.diagnose()

    model = [c for c in report.checks if c.check_id == "model_provider"]
    assert len(model) == 1
    assert model[0].status == CheckStatus.ERROR
    assert report.is_healthy is False
    assert [c.check_id for c in report.errors] == ["model_provider"]


def test_model_provider_is_not_ok_when_no_probe_is_available():
    """No probe capability means unverified. Reporting OK there is the original bug."""
    doctor = _model_doctor(lambda: None)

    result = doctor._check_model_provider()

    assert result.status == CheckStatus.INFO
    assert result.status != CheckStatus.OK
    assert "no live probe is available" in result.evidence.lower()
    assert "unverified" in result.evidence.lower()


def test_model_provider_is_not_ok_without_any_configuration():
    inspector = SimpleNamespace(
        _get_config=lambda: None,
        get_model_info=lambda: {"provider": "unknown", "model": "unknown"},
    )
    result = CharlieDoctor(introspector=inspector)._check_model_provider()
    assert result.status == CheckStatus.INFO
    assert "not probed" in result.evidence.lower()


def test_model_provider_errors_when_endpoint_url_is_unconfigured():
    """An unconfigured route cannot answer at all, so it is not 'fine'."""
    cfg = SimpleNamespace(llm_url="", llm_model="", llm_key="")
    introspector = SimpleNamespace(
        _get_config=lambda: cfg,
        get_model_info=lambda: {"provider": "unknown", "model": "unknown", "api_base_url": None},
    )
    result = CharlieDoctor(introspector=introspector)._check_model_provider()
    assert result.status == CheckStatus.ERROR
    assert "not configured" in result.summary.lower()


def test_model_provider_is_ok_only_after_a_successful_probe():
    doctor = _model_doctor(lambda: _HEALTHY_PROBE)
    result = doctor._check_model_provider()
    assert result.status == CheckStatus.OK
    assert "Live probe succeeded" in result.evidence


def test_default_llm_probe_contacts_the_configured_endpoint():
    """The built-in probe is a real HTTP attempt, not a stand-in for one."""
    port = _closed_port()
    started = time.monotonic()
    result = _probe_chat_endpoint(
        base_url=f"http://127.0.0.1:{port}/v1",
        model="m",
        api_key="k",
        timeout=5.0,
    )
    elapsed = time.monotonic() - started

    assert result is not None
    assert result.ok is False
    assert str(port) in result.detail
    # A refused connect must fail fast; a probe that silently hung would be a
    # worse diagnostic than no probe at all.
    assert elapsed < 5.0


def test_llm_probe_verdict_is_reused_within_a_doctor_instance():
    """Re-diagnosing must not fan out one billable completion per caller."""
    calls = []

    def probe():
        calls.append(1)
        return _HEALTHY_PROBE

    doctor = _model_doctor(probe)
    doctor._check_model_provider()
    doctor._check_model_provider()

    assert len(calls) == 1


def test_secrets_check_distinguishes_present_key_from_accepted_key():
    """A key that is present but rejected by the endpoint is not a healthy secret."""
    doctor = _model_doctor(lambda: ProbeResult(False, "HTTP 401 from http://127.0.0.1:1/v1", 401))

    # First pass: secrets runs before the model probe, so it has no verdict yet
    # and must say so instead of implying the key was validated.
    first = doctor._check_secrets_configured()
    assert first.status == CheckStatus.OK
    assert "unverified" in first.evidence.lower()

    # Second pass: the probe verdict is cached, so secrets can use it without
    # issuing another request.
    doctor._check_model_provider()
    second = doctor._check_secrets_configured()
    assert second.status == CheckStatus.WARNING
    assert "rejected" in second.summary.lower()
    assert "401" in second.evidence


def test_secrets_check_reports_presence_only_when_probe_is_silent():
    doctor = _model_doctor(lambda: None)
    doctor._check_model_provider()
    result = doctor._check_secrets_configured()
    assert result.status == CheckStatus.OK
    assert "no live probe evidence" in result.evidence.lower()


# ---------------------------------------------------------------------------
# Secrets vs. probe verdict -- a present key is not a working key
#
# The probe verdict is only ever produced by the model check, so the secrets
# check can only tell "present" from "accepted" if the model check runs first.
# These tests drive the real HTTP probe against a local stub, never a provider.
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_llm_endpoint():
    """Yield a factory that starts a local LLM route stub with a fixed status.

    The stub records every POST it receives, so a test can also prove the
    secrets check reused the cached verdict instead of paying for a second
    completion. Owned servers/threads are always shut down.
    """
    servers: list[ThreadingHTTPServer] = []
    threads: list[threading.Thread] = []

    def _start(status: int):
        received: list[str] = []

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - stdlib handler naming
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                received.append(self.path)
                body = b'{"error": "stubbed"}'
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                """Keep the stub out of the test log."""

        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        threads.append(thread)
        host, port = server.server_address[0], server.server_address[1]
        return f"http://{host}:{port}/v1", received

    try:
        yield _start
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=5.0)


def _stubbed_route_doctor(mock_doctor_env, base_url):
    """Point the fixture's doctor at ``base_url`` using the real HTTP probe."""
    doctor, cfg, _, _, _ = mock_doctor_env
    cfg.llm_url = base_url
    # Drop the injected verdict so the check performs the genuine HTTP request
    # the runtime would make, against a loopback stub.
    doctor._llm_probe = None
    doctor._llm_probe_cache = None
    return doctor


def _secrets_check(report):
    return next(c for c in report.checks if c.check_id == "secrets_configured")


def test_diagnose_reports_a_rejected_credential_in_the_secrets_check(
    stub_llm_endpoint, mock_doctor_env
):
    """A 401 key is not [OK] anywhere in the report, and costs one request."""
    base_url, received = stub_llm_endpoint(401)
    doctor = _stubbed_route_doctor(mock_doctor_env, base_url)

    report = doctor.diagnose()
    secrets = _secrets_check(report)
    model = next(c for c in report.checks if c.check_id == "model_provider")

    # The model check owns the probe, so it has to be the one that runs first.
    ids = [c.check_id for c in report.checks]
    assert ids.index("model_provider") < ids.index("secrets_configured")

    assert secrets.status == CheckStatus.WARNING
    assert secrets.status != CheckStatus.OK
    assert "rejected" in secrets.summary.lower()
    assert "401" in secrets.evidence
    assert secrets.fix_hint is not None
    assert model.status == CheckStatus.ERROR
    assert report.is_healthy is False
    # The secrets check read the cached verdict instead of probing again.
    assert len(received) == 1


def test_secrets_check_does_not_call_a_server_error_a_rejected_credential(
    stub_llm_endpoint, mock_doctor_env
):
    """A 5xx is the route's fault; the key was read and not refused."""
    base_url, _ = stub_llm_endpoint(500)
    doctor = _stubbed_route_doctor(mock_doctor_env, base_url)

    report = doctor.diagnose()
    secrets = _secrets_check(report)
    model = next(c for c in report.checks if c.check_id == "model_provider")

    assert secrets.status == CheckStatus.OK
    assert "rejected" not in secrets.summary.lower()
    assert "credential rejection" in secrets.evidence
    assert "not disproved" in secrets.evidence
    # The dependency itself is still failing, and model_provider owns that.
    assert model.status == CheckStatus.ERROR
    assert "returned an error" in model.summary.lower()


def test_secrets_check_does_not_call_a_dead_route_a_rejected_credential(mock_doctor_env):
    """No HTTP response at all means the credential was never judged."""
    doctor = _stubbed_route_doctor(mock_doctor_env, f"http://127.0.0.1:{_closed_port()}/v1")

    report = doctor.diagnose()
    secrets = _secrets_check(report)
    model = next(c for c in report.checks if c.check_id == "model_provider")

    assert secrets.status == CheckStatus.OK
    assert "rejected" not in secrets.summary.lower()
    assert "never obtained an HTTP response" in secrets.evidence
    assert "transport failure" in secrets.evidence
    assert "unreachable" in model.summary.lower()


def test_secrets_check_says_so_when_no_probe_evidence_exists(mock_doctor_env):
    """A probe source that yields no verdict must not read as a passing test."""
    doctor, _, _, _, _ = mock_doctor_env
    doctor._llm_probe = lambda: None
    doctor._llm_probe_cache = None

    report = doctor.diagnose()
    secrets = _secrets_check(report)
    model = next(c for c in report.checks if c.check_id == "model_provider")

    assert "no live probe evidence" in secrets.evidence.lower()
    assert "unverified" in secrets.evidence.lower()
    # The two lines must agree: nothing was contacted, so nothing failed.
    assert model.status == CheckStatus.INFO
    assert "no live probe is available" in model.evidence.lower()
    assert report.is_healthy is True


def test_probe_verdicts_classify_three_distinct_faults():
    """credential_rejected vs transport_failed must not overlap."""
    rejected = ProbeResult(False, "HTTP 403 from http://127.0.0.1:1/v1", 403)
    server_error = ProbeResult(False, "HTTP 500 from http://127.0.0.1:1/v1", 500)
    dead_socket = ProbeResult(False, "ConnectError contacting http://127.0.0.1:1/v1")
    healthy = ProbeResult(True, "HTTP 200 from http://127.0.0.1:1/v1", 200)

    assert rejected.credential_rejected is True
    assert rejected.transport_failed is False
    assert server_error.credential_rejected is False
    assert server_error.transport_failed is False
    assert dead_socket.credential_rejected is False
    assert dead_socket.transport_failed is True
    assert healthy.transport_failed is False
    assert healthy.credential_rejected is False


# ---------------------------------------------------------------------------
# ITEM 0.9a -- the event bus check must observe, not assert a constant
# ---------------------------------------------------------------------------


def test_event_bus_reports_bound_transport_as_ok(busy_port, monkeypatch):
    """A live publisher on the event port is real, observable evidence."""
    monkeypatch.setenv("CHARLIE_TEST_MODE", "true")
    monkeypatch.setenv("CHARLIE_TEST_EVENT_PORT", str(busy_port()))
    doctor = _model_doctor(lambda: _HEALTHY_PROBE)

    result = doctor._check_event_bus()

    assert result.status == CheckStatus.OK
    assert "not exercised" in result.evidence.lower()


def test_event_bus_reports_absent_publisher_as_info(monkeypatch):
    """No bound publisher is 'not publishing right now', not a broken subsystem."""
    monkeypatch.setenv("CHARLIE_TEST_MODE", "true")
    monkeypatch.setenv("CHARLIE_TEST_EVENT_PORT", str(_closed_port()))
    doctor = _model_doctor(lambda: _HEALTHY_PROBE)

    result = doctor._check_event_bus()

    assert result.status == CheckStatus.INFO
    assert "not currently publishing" in result.evidence
    assert result.status != CheckStatus.OK


def test_event_bus_errors_when_the_transport_is_missing(monkeypatch):
    """A missing required dependency must not be softened into INFO."""
    # A None entry in sys.modules makes `import zmq` raise, which is exactly
    # what a broken/missing pyzmq install looks like from the doctor's side.
    monkeypatch.setitem(sys.modules, "zmq", None)
    doctor = _model_doctor(lambda: _HEALTHY_PROBE)

    result = doctor._check_event_bus()

    assert result.status == CheckStatus.ERROR
    assert "zeromq" in result.evidence.lower()
    assert result.fix_hint is not None


def test_event_bus_is_unknown_without_runtime_config():
    inspector = SimpleNamespace(get_health_info=lambda: {}, get_subsystem_info=lambda: {})
    result = CharlieDoctor(introspector=inspector)._check_event_bus()
    assert result.status == CheckStatus.INFO
    assert "could not be observed" in result.evidence


def test_probe_event_port_distinguishes_bound_from_free(busy_port):
    held = busy_port()
    assert _probe_event_port(held).occupied is True
    assert _probe_event_port(_closed_port()).occupied is False


def test_resolve_event_port_mirrors_event_bus_resolution(monkeypatch):
    from charlie.ipc import DEFAULT_EVENT_PORT

    monkeypatch.setenv("CHARLIE_TEST_MODE", "false")
    monkeypatch.setenv("CHARLIE_TEST_EVENT_PORT", "9999")
    assert _resolve_event_port() == DEFAULT_EVENT_PORT

    monkeypatch.setenv("CHARLIE_TEST_MODE", "true")
    assert _resolve_event_port() == 9999


# ---------------------------------------------------------------------------
# ITEM 0.9b -- vision/OCR must exercise the real dependencies
# ---------------------------------------------------------------------------


def _vision_doctor(tesseract_cmd, vision_enabled=False, vision_url=""):
    cfg = SimpleNamespace(
        tesseract_cmd=tesseract_cmd,
        vision_enabled=vision_enabled,
        vision_llm_url=vision_url,
        vision_llm_key="k",
        vision_llm_model="v",
        llm_url="http://127.0.0.1:1/v1",
        llm_model="m",
        llm_key="k",
        llm_trust_env=False,
    )
    introspector = SimpleNamespace(
        _get_config=lambda: cfg,
        get_model_info=lambda: {
            "provider": "openai",
            "model": "m",
            "api_base_url": cfg.llm_url,
            "api_key_configured": True,
        },
    )
    return CharlieDoctor(introspector=introspector, llm_probe=lambda: _HEALTHY_PROBE)


def test_vision_ocr_is_ok_when_the_ocr_binary_answers(monkeypatch):
    """A responsive binary plus a disabled vision endpoint is a verified tier."""
    monkeypatch.setattr(shutil, "which", lambda name: None)
    doctor = _vision_doctor(tesseract_cmd=sys.executable, vision_enabled=False)

    result = doctor._check_vision_ocr()

    assert result.status == CheckStatus.OK
    assert "tesseract" in result.evidence
    assert "disabled by configuration" in result.evidence


def test_vision_ocr_names_the_missing_binary_in_its_evidence(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    missing = str(_closed_port())
    doctor = _vision_doctor(tesseract_cmd=missing)

    result = doctor._check_vision_ocr()

    assert result.status == CheckStatus.WARNING
    assert missing in result.evidence
    assert "not found" in result.evidence
    # The old placeholder carried none of this; it said only that evidence was
    # unavailable, so this assertion fails against the previous implementation.
    assert result.summary == "Vision/OCR tier degraded"


def test_vision_ocr_flags_a_configured_but_unreachable_vision_endpoint():
    port = _closed_port()
    doctor = _vision_doctor(
        tesseract_cmd=sys.executable,
        vision_enabled=True,
        vision_url=f"http://127.0.0.1:{port}/v1",
    )

    result = doctor._check_vision_ocr()

    assert result.status == CheckStatus.WARNING
    assert str(port) in result.evidence
    assert "vision endpoint" in result.evidence


def test_vision_ocr_flags_enabled_endpoint_without_a_url():
    doctor = _vision_doctor(tesseract_cmd=sys.executable, vision_enabled=True, vision_url="")
    result = doctor._check_vision_ocr()
    assert result.status == CheckStatus.WARNING
    assert "VISION_LLM_URL" in result.evidence


def test_vision_ocr_is_unknown_without_runtime_config():
    inspector = SimpleNamespace(get_health_info=lambda: {}, get_subsystem_info=lambda: {})
    result = CharlieDoctor(introspector=inspector)._check_vision_ocr()
    assert result.status == CheckStatus.INFO
    assert "no configuration is available" in result.evidence.lower()
    assert "could be observed" in result.evidence


def test_probe_tesseract_reads_the_real_binary_banner():
    result = _probe_tesseract(sys.executable, timeout=10.0)
    assert result.ok is True
    assert "exit 0" in result.detail
    assert "Python" in result.detail


def test_probe_tesseract_reports_a_missing_binary():
    result = _probe_tesseract(str(_closed_port()), timeout=5.0)
    assert result.ok is False
    assert "not found" in result.detail


def test_probe_tesseract_reports_an_unrunnable_binary(tmp_path):
    """A path that exists but cannot execute must not read as verified."""
    not_a_binary = tmp_path / "tesseract.exe"
    not_a_binary.write_text("this is not an executable")

    result = _probe_tesseract(str(not_a_binary), timeout=5.0)

    assert result.ok is False
    assert "not found" in result.detail or "could not execute" in result.detail
