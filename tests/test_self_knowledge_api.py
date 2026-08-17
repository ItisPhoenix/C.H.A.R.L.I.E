"""Integration tests for SelfKnowledge, CodeIndex, and Charlie Doctor REST API endpoints."""

import pytest

from charlie import web_server


@pytest.mark.asyncio
async def test_self_introspection_endpoint():
    """Test GET /api/self/introspect returns valid secret-masked runtime snapshot."""
    res = await web_server.get_self_introspection()
    assert isinstance(res, dict)
    assert "process" in res
    assert "model" in res
    assert "capabilities" in res
    assert "tasks" in res
    assert "subsystem_health" in res
    assert res["model"]["api_key_configured"] is not None


@pytest.mark.asyncio
async def test_self_query_endpoint():
    """Test POST /api/self/query returns grounded answers."""
    # Valid query
    res = await web_server.query_self_knowledge({"query": "What model are you configured to use?"})
    assert res["is_self_question"] is True
    assert "model" in res["answer"].lower()
    assert "evidence_sources" in res

    # Empty query error
    err_res = await web_server.query_self_knowledge({"query": ""})
    assert err_res.status_code == 400


@pytest.mark.asyncio
async def test_code_index_search_endpoint():
    """Test GET /api/code_index/search for symbols and files."""
    # Symbols search
    sym_res = await web_server.search_code_index(q="Config", type="symbols")
    assert "symbols" in sym_res
    assert len(sym_res["symbols"]) >= 1

    # Files search
    file_res = await web_server.search_code_index(q="config", type="files")
    assert "files" in file_res
    assert len(file_res["files"]) >= 1


@pytest.mark.asyncio
async def test_doctor_diagnose_and_repair_endpoints():
    """Test GET /api/doctor/diagnose and POST /api/doctor/repair."""
    # Run diagnosis
    diag_res = await web_server.run_doctor_diagnostics()
    assert "checks" in diag_res
    assert diag_res["total_checks"] > 10
    assert "is_healthy" in diag_res

    # Attempt safe repair
    repair_res = await web_server.execute_doctor_repair({"repair_id": "repair_refresh_code_index"})
    assert isinstance(repair_res, dict)
    assert repair_res["success"] is True

    # Empty repair_id error
    err_res = await web_server.execute_doctor_repair({"repair_id": ""})
    assert err_res.status_code == 400
