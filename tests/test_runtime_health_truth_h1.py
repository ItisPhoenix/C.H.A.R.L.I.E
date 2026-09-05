"""Focused regression coverage for composite memory runtime health."""

from __future__ import annotations

import pytest

import charlie.memory_service as memory_service_module
import main
from charlie.doctor import CharlieDoctor, CheckSeverity, CheckStatus, DiagnosticCheck, DoctorReport
from charlie.memory_graph import MemoryGraph
from charlie.memory_service import MemoryService
from charlie.runtime_introspector import RuntimeIntrospector
from charlie.subsystem_health import HealthRegistry, HealthStatus


class _SemanticStore:
    def __init__(self, *, available: bool, document_count: int = 0, error: Exception | None = None) -> None:
        self.is_available = available
        self.document_count = document_count
        self.error = error

    def get_stats(self) -> dict:
        if self.error is not None:
            raise self.error
        return {"available": self.is_available, "document_count": self.document_count}


class _BrokenGraph:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def get_stats(self) -> dict:
        raise self.error


class _ExplodingMemoryService:
    def get_stats(self) -> dict:
        raise RuntimeError("api-key=do-not-leak")


@pytest.fixture
def graph():
    value = MemoryGraph(":memory:")
    try:
        yield value
    finally:
        value.close()


def _memory_service(graph, store=None, *, semantic_expected: bool | None = None) -> MemoryService:
    return MemoryService(
        graph=graph,
        memory_store=store,
        semantic_expected=semantic_expected,
    )


def _memory_check(info: dict) -> DiagnosticCheck:
    class _Introspector:
        def get_memory_info(self) -> dict:
            return info

    return CharlieDoctor(introspector=_Introspector())._check_memory_subsystem()


def _ok_check() -> DiagnosticCheck:
    return DiagnosticCheck(
        check_id="other",
        category="test",
        status=CheckStatus.OK,
        severity=CheckSeverity.LOW,
        summary="ok",
        evidence="ok",
    )


def _diagnose_memory(monkeypatch, info: dict) -> DoctorReport:
    class _Introspector:
        def get_memory_info(self) -> dict:
            return info

    doctor = CharlieDoctor(introspector=_Introspector())
    check_methods = (
        "_check_config_validity",
        "_check_secrets_configured",
        "_check_model_provider",
        "_check_capability_registry",
        "_check_event_bus",
        "_check_task_journal",
        "_check_capability_leases",
        "_check_mcp_subsystem",
        "_check_terminal_subsystem",
        "_check_browser_subsystem",
        "_check_desktop_subsystem",
        "_check_vision_ocr",
        "_check_voice_subsystem",
        "_check_data_directories",
        "_check_subsystem_health",
        "_check_recovery_state",
        "_check_extensions_registry",
    )
    for method_name in check_methods:
        monkeypatch.setattr(doctor, method_name, _ok_check)
    return doctor.diagnose()


def test_structured_and_semantic_available_is_healthy_and_zero_items_are_valid(graph):
    service = _memory_service(graph, _SemanticStore(available=True), semantic_expected=True)
    introspector = RuntimeIntrospector(memory_service=service)

    info = introspector.get_memory_info()

    assert info["status"] == "available"
    assert info["total_items"] == 0
    assert info["structured"] == {"status": "available", "available": True}
    assert info["semantic"]["status"] == "available"
    assert info["semantic"]["available"] is True
    assert _memory_check(info).status == CheckStatus.OK


def test_expected_semantic_unavailable_degrades_memory_without_false_healthy(graph):
    service = _memory_service(graph, _SemanticStore(available=False), semantic_expected=True)
    info = RuntimeIntrospector(memory_service=service).get_memory_info()
    check = _memory_check(info)

    assert info["status"] == "degraded"
    assert info["structured"]["status"] == "available"
    assert info["semantic"]["status"] == "unavailable"
    assert check.status == CheckStatus.WARNING
    assert check.summary != "Memory subsystem healthy"
    assert "healthy" not in check.summary.lower()


def test_optional_semantic_memory_disabled_does_not_degrade_structured_memory(graph):
    service = _memory_service(graph, semantic_expected=False)
    info = RuntimeIntrospector(memory_service=service).get_memory_info()
    check = _memory_check(info)

    assert info["status"] == "available"
    assert info["semantic"]["status"] == "disabled"
    assert check.status == CheckStatus.INFO
    assert "disabled" in check.summary.lower()


def test_missing_memory_service_is_unavailable():
    info = RuntimeIntrospector(memory_service=None).get_memory_info()
    check = _memory_check(info)

    assert info["status"] == "unavailable"
    assert check.status == CheckStatus.ERROR
    assert "unavailable" in check.summary.lower()


def test_memory_service_component_exception_is_error_and_secret_safe():
    service = _memory_service(
        _BrokenGraph(RuntimeError("password=super-secret")),
        _SemanticStore(available=True),
        semantic_expected=True,
    )
    info = service.get_health()

    assert info["status"] == "error"
    assert info["structured"]["status"] == "error"
    assert "super-secret" not in str(info)


def test_memory_stats_exception_is_error_and_doctor_does_not_leak_details():
    info = RuntimeIntrospector(memory_service=_ExplodingMemoryService()).get_memory_info()
    check = _memory_check(info)

    assert info["status"] == "error"
    assert "do-not-leak" not in str(info)
    assert check.status == CheckStatus.ERROR
    assert "do-not-leak" not in str(check.to_dict())


def test_runtime_introspector_preserves_injected_memory_service_over_stale_fallback(monkeypatch, graph):
    canonical = _memory_service(graph, _SemanticStore(available=True), semantic_expected=True)
    monkeypatch.setattr(
        memory_service_module,
        "get_memory_service",
        lambda: _ExplodingMemoryService(),
        raising=False,
    )

    info = RuntimeIntrospector(memory_service=canonical).get_memory_info()

    assert info["status"] == "available"
    assert info["semantic"]["status"] == "available"


def test_doctor_report_keeps_error_only_is_healthy_contract(monkeypatch, graph):
    available = RuntimeIntrospector(
        memory_service=_memory_service(graph, _SemanticStore(available=True), semantic_expected=True)
    ).get_memory_info()
    degraded = RuntimeIntrospector(
        memory_service=_memory_service(graph, _SemanticStore(available=False), semantic_expected=True)
    ).get_memory_info()
    unavailable = RuntimeIntrospector(memory_service=None).get_memory_info()

    warning_report = _diagnose_memory(monkeypatch, degraded)
    error_report = _diagnose_memory(monkeypatch, unavailable)
    healthy_report = _diagnose_memory(monkeypatch, available)

    assert warning_report.is_healthy is True
    assert len(warning_report.errors) == 0
    assert error_report.is_healthy is False
    assert len(error_report.errors) == 1
    assert healthy_report.is_healthy is True


def test_main_health_registry_matches_aggregate_memory_state(monkeypatch, graph):
    old_registry = main._runtime_health
    registry = HealthRegistry(("memory",))
    main._runtime_health = registry
    try:
        degraded_service = _memory_service(
            graph,
            _SemanticStore(available=False),
            semantic_expected=True,
        )
        main._set_memory_health_from_service(degraded_service)
        assert registry.snapshot()["memory"]["status"] == HealthStatus.DEGRADED.value

        available_service = _memory_service(graph, semantic_expected=False)
        main._set_memory_health_from_service(available_service)
        assert registry.snapshot()["memory"]["status"] == HealthStatus.RUNNING.value
    finally:
        main._runtime_health = old_registry


def test_doctor_memory_error_mapping_is_explicit(monkeypatch):
    degraded = {
        "status": "degraded",
        "structured": {"status": "available", "available": True},
        "semantic": {"status": "unavailable", "available": False, "configured": True},
    }
    unavailable = {
        "status": "unavailable",
        "structured": {"status": "unavailable", "available": False},
        "semantic": {"status": "disabled", "available": False, "configured": False},
    }

    degraded_check = _memory_check(degraded)
    unavailable_check = _memory_check(unavailable)
    degraded_report = _diagnose_memory(monkeypatch, degraded)
    unavailable_report = _diagnose_memory(monkeypatch, unavailable)

    assert degraded_check.status == CheckStatus.WARNING
    assert unavailable_check.status == CheckStatus.ERROR
    assert degraded_report.is_healthy is True
    assert unavailable_report.is_healthy is False


@pytest.mark.asyncio
async def test_memory_stats_api_exposes_component_health(monkeypatch):
    from charlie import web_server

    async def request(operation, _payload):
        assert operation == "stats"
        return {
            "request_id": "r",
            "operation": operation,
            "success": True,
            "data": {
                "status": "degraded",
                "health": {
                    "structured": {"status": "available"},
                    "semantic": {"status": "unavailable"},
                },
            },
        }

    monkeypatch.setattr(web_server, "_request_authoritative_memory_operation", request)

    result = await web_server.get_memory_stats()

    assert result["status"] == "degraded"
    assert result["health"]["structured"]["status"] == "available"
    assert result["health"]["semantic"]["status"] == "unavailable"


def test_web_health_projection_uses_main_memory_aggregate(monkeypatch):
    from charlie import web_server

    monkeypatch.setattr(
        web_server,
        "_subsystem_health",
        {"memory": {"status": "running", "detail": "Running"}},
    )

    info = web_server._runtime_introspector.get_memory_info()
    check = web_server._doctor._check_memory_subsystem()

    assert info["status"] == "available"
    assert info["health"]["structured"]["status"] == "unknown"
    assert info["health"]["semantic"]["status"] == "unknown"
    assert check.status == CheckStatus.OK
    assert "unavailable" not in check.summary.lower()


def test_configured_vs_optional_semantic_expectation_uses_config(monkeypatch):
    class Configured:
        memory_embedding_url = "http://embedding.local"

    class Optional:
        memory_embedding_url = ""

    assert main._semantic_memory_expected(Configured()) is True
    assert main._semantic_memory_expected(Optional()) is False
