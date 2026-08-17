"""Tests for Self-Extension REST endpoints."""

import pytest
from starlette.testclient import TestClient
from charlie.web_server import app


@pytest.fixture
def client():
    return TestClient(app)


def test_api_extension_request_spontaneous_blocked(client):
    """Verify POST /api/extensions/request blocks spontaneous requests without explicit flag."""
    res = client.post("/api/extensions/request", json={"prompt": "Internal optimize", "explicit": False})
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is False
    assert data["status"] == "approval_required"
    assert "spontaneous" in data["message"].lower()


def test_api_extension_request_config_applied(client):
    """Verify POST /api/extensions/request applies config updates when explicit."""
    res = client.post(
        "/api/extensions/request",
        json={
            "prompt": "Change your LLM_MODEL setting to gpt-4o",
            "explicit": True,
            "settings": {"LLM_MODEL": "gpt-4o"},
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["status"] == "completed"


def test_api_extension_transactions_list(client):
    """Verify GET /api/extensions/transactions returns transaction history."""
    res = client.get("/api/extensions/transactions")
    assert res.status_code == 200
    data = res.json()
    assert "transactions" in data
    assert isinstance(data["transactions"], list)
