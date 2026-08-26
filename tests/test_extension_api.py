"""Tests for Self-Extension REST endpoints."""

import pytest
from starlette.testclient import TestClient

from charlie.ipc import EventBus
from charlie.web_server import app


@pytest.fixture
def client(monkeypatch):
    async def isolated_send_command(self, command):
        """Never forward self-extension tests into a concurrently running Charlie."""
        return None

    monkeypatch.setattr(EventBus, "send_command", isolated_send_command)
    with TestClient(app) as test_client:
        yield test_client


def test_api_extension_request_spontaneous_blocked(client):
    """Verify web delegates request; voice runtime owns approval decision."""
    res = client.post("/api/extensions/request", json={"prompt": "Internal optimize", "explicit": False})
    assert res.status_code == 202
    data = res.json()
    assert data["status"] == "requested"
    assert data["request_id"]


def test_api_extension_request_config_applied(client):
    """Verify web sends typed config request to authoritative voice runtime."""
    res = client.post(
        "/api/extensions/request",
        json={
            "prompt": "Change your LLM_MODEL setting to gpt-4o",
            "explicit": True,
            "settings": {"LLM_MODEL": "gpt-4o"},
        },
    )
    assert res.status_code == 202
    data = res.json()
    assert data["status"] == "requested"


def test_api_extension_transactions_list(client):
    """Verify GET /api/extensions/transactions returns transaction history."""
    res = client.get("/api/extensions/transactions")
    assert res.status_code == 200
    data = res.json()
    assert "transactions" in data
    assert isinstance(data["transactions"], list)
