import json
import os
from pathlib import Path

import httpx
import pytest

import charlie.web_server as web_server
from charlie.config import config

_REAL_HTTPX_ASYNC_CLIENT = httpx.AsyncClient


def _patch_model_transport(monkeypatch, provider_url, responder):
    requests = []

    def handler(request):
        requests.append(request)
        if str(request.url) == provider_url:
            return responder(request)
        return httpx.Response(404, request=request)

    transport = httpx.MockTransport(handler)

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return _REAL_HTTPX_ASYNC_CLIENT(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    return requests


def _configure_model_endpoint(monkeypatch, *, url, key="no-key", model="configured-model"):
    monkeypatch.setattr(web_server.config, "llm_url", url)
    monkeypatch.setattr(web_server.config, "llm_key", key)
    monkeypatch.setattr(web_server.config, "llm_model", model)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("base_url", "expected_url"),
    [
        ("https://api.kilo.ai/api/gateway", "https://api.kilo.ai/api/gateway/models"),
        ("https://api.openai.com/v1", "https://api.openai.com/v1/models"),
    ],
)
async def test_models_use_canonical_configured_base_without_v1_guessing(monkeypatch, base_url, expected_url):
    import httpx

    _configure_model_endpoint(monkeypatch, url=base_url)
    requests = _patch_model_transport(
        monkeypatch,
        expected_url,
        lambda request: httpx.Response(200, json={"data": [{"id": "provider/model"}]}, request=request),
    )

    result = await web_server.get_available_models()

    provider_requests = [request for request in requests if str(request.url) == expected_url]
    assert len(provider_requests) == 1
    assert str(provider_requests[0].url) == expected_url
    assert result["provider_discovery"] == {"status": "available", "count": 1, "error": None}
    assert "provider/model" in result["models"]


@pytest.mark.asyncio
async def test_models_discover_without_api_key_and_use_auth_only_when_configured(monkeypatch):
    expected_url = "https://api.kilo.ai/api/gateway/models"
    _configure_model_endpoint(monkeypatch, url="https://api.kilo.ai/api/gateway", key="no-key")
    requests = _patch_model_transport(
        monkeypatch,
        expected_url,
        lambda request: httpx.Response(200, json={"data": [{"id": "anonymous/model"}]}, request=request),
    )

    result = await web_server.get_available_models()

    provider_request = next(request for request in requests if str(request.url) == expected_url)
    assert provider_request.headers.get("authorization") is None
    assert result["has_api_key"] is False
    assert result["provider_discovery"]["status"] == "available"

    _configure_model_endpoint(monkeypatch, url="https://api.kilo.ai/api/gateway", key="secret-key")
    requests = _patch_model_transport(
        monkeypatch,
        expected_url,
        lambda request: httpx.Response(200, json={"data": [{"id": "authenticated/model"}]}, request=request),
    )
    result = await web_server.get_available_models()
    provider_request = next(request for request in requests if str(request.url) == expected_url)
    assert provider_request.headers["authorization"] == "Bearer secret-key"
    assert "secret-key" not in json.dumps(result)


@pytest.mark.asyncio
async def test_models_preserve_all_unique_provider_ids_and_active_model(monkeypatch):
    expected_url = "https://api.kilo.ai/api/gateway/models"
    provider_ids = [f"provider/model-{index}" for index in range(300)]
    entries = [{"id": model_id} for model_id in provider_ids]
    entries.extend([{"id": provider_ids[0]}, {"id": ""}, {"id": 42}, {"name": "malformed"}])
    _configure_model_endpoint(monkeypatch, url="https://api.kilo.ai/api/gateway", model="active-model")
    _patch_model_transport(
        monkeypatch,
        expected_url,
        lambda request: httpx.Response(200, json={"data": entries}, request=request),
    )

    result = await web_server.get_available_models()

    assert result["active_model"] == "active-model"
    assert set(provider_ids).issubset(result["models"])
    assert result["models"].count("provider/model-0") == 1
    assert result["provider_discovery"] == {"status": "available", "count": 300, "error": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403, 404])
async def test_models_report_provider_http_failures_truthfully(monkeypatch, status_code):
    expected_url = "https://api.kilo.ai/api/gateway/models"
    _configure_model_endpoint(monkeypatch, url="https://api.kilo.ai/api/gateway", model="active-model")
    _patch_model_transport(
        monkeypatch,
        expected_url,
        lambda request: httpx.Response(status_code, request=request),
    )

    result = await web_server.get_available_models()

    assert result["models"] == ["active-model"]
    assert result["provider_discovery"] == {
        "status": "error",
        "count": 0,
        "error": f"HTTP {status_code}",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"data": {"id": "wrong-shape"}},
        {"unexpected": []},
    ],
)
async def test_models_report_invalid_provider_data_without_leaking_details(monkeypatch, payload):
    expected_url = "https://api.kilo.ai/api/gateway/models"
    _configure_model_endpoint(monkeypatch, url="https://api.kilo.ai/api/gateway")
    _patch_model_transport(
        monkeypatch,
        expected_url,
        lambda request: httpx.Response(200, json=payload, request=request),
    )

    result = await web_server.get_available_models()

    assert result["provider_discovery"] == {"status": "error", "count": 0, "error": "invalid response"}
    assert result["models"] == ["configured-model"]


@pytest.mark.asyncio
async def test_models_report_invalid_json_and_connection_failure(monkeypatch):
    expected_url = "https://api.kilo.ai/api/gateway/models"
    _configure_model_endpoint(monkeypatch, url="https://api.kilo.ai/api/gateway")
    _patch_model_transport(
        monkeypatch,
        expected_url,
        lambda request: httpx.Response(200, content=b"not-json", request=request),
    )
    result = await web_server.get_available_models()
    assert result["provider_discovery"]["error"] == "invalid response"

    def raise_connection(request):
        raise httpx.ConnectError("provider unavailable", request=request)

    _patch_model_transport(monkeypatch, expected_url, raise_connection)
    result = await web_server.get_available_models()
    assert result["provider_discovery"] == {
        "status": "error",
        "count": 0,
        "error": "connection unavailable",
    }


@pytest.mark.asyncio
async def test_models_mark_missing_provider_not_configured(monkeypatch):
    _configure_model_endpoint(monkeypatch, url="", model="active-model")
    requests = _patch_model_transport(
        monkeypatch,
        "unused",
        lambda request: httpx.Response(200, json={"data": []}, request=request),
    )

    result = await web_server.get_available_models()

    assert all(str(request.url) not in {"unused", "https://api.kilo.ai/api/gateway/models"} for request in requests)
    assert result["provider_discovery"] == {"status": "not_configured", "count": 0, "error": None}
    assert result["models"] == ["active-model"]


def _remove_db(db_path: str) -> None:
    for path in (db_path, f"{db_path}-wal", f"{db_path}-shm"):
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


@pytest.mark.asyncio
async def test_get_dashboard_config():
    res = await web_server.get_dashboard_config()
    keys = {f["key"] for f in res["fields"]}
    assert {"GPU_DEVICE", "KOKORO_LANG", "WHISPER_MODEL", "MCP_SERVERS", "DESKTOP_IDLE_THRESHOLD_S"} <= keys
    # a good chunk of the full Config surface should be exposed, not a hand-picked few
    assert len(res["fields"]) > 50


def test_settings_frontend_mapping_covers_backend_metadata_groups():
    """Settings tests must follow the metadata-driven production schema."""
    frontend = (Path(__file__).parents[1] / "frontend/src/scene/settings/Settings.tsx").read_text(
        encoding="utf-8"
    )
    backend_groups = {spec["group"] for spec in config.editable_field_specs()}
    missing_groups = [group for group in sorted(backend_groups) if f'"{group}"' not in frontend]
    assert not missing_groups, f"frontend Settings has no category mapping for: {missing_groups}"
    assert '"ASSISTANT_NAME"' not in frontend
    assert '"THEME_ACCENT"' not in frontend


def test_desktop_idle_threshold_default(monkeypatch):
    # Real .env can override this (it does in dev, for faster live-testing), so isolate from it.
    import importlib
    import sys

    # __init__.py shadows the "config" attribute with the singleton -- go via sys.modules for the real submodule.
    config_module = sys.modules["charlie.config"]
    monkeypatch.delenv("DESKTOP_IDLE_THRESHOLD_S", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    importlib.reload(config_module)
    try:
        assert config_module.Config().desktop_idle_threshold_s == 120.0
    finally:
        monkeypatch.undo()
        importlib.reload(config_module)


@pytest.mark.asyncio
async def test_get_dashboard_config_masks_secrets():
    res = await web_server.get_dashboard_config()
    by_key = {f["key"]: f for f in res["fields"]}
    secret_field = by_key["LLM_API_KEY"]
    assert secret_field["secret"] is True
    assert secret_field["value"] is None
    assert isinstance(secret_field["is_set"], bool)


@pytest.mark.asyncio
async def test_update_dashboard_config(monkeypatch):
    # Mock _update_env_file to avoid changing active .env during tests
    called = []
    def mock_update(updates):
        called.append(updates)
    monkeypatch.setattr(web_server, "_update_env_file", mock_update)
    monkeypatch.setattr(web_server, "event_bus", None)

    test_payload = {
        "GPU_DEVICE": "cpu",
        "KOKORO_LANG": "en-gb",
        "WAKE_WORD_ENABLED": True,
    }

    res = await web_server.update_dashboard_config(test_payload)
    assert res["status"] == "ok"
    assert res["touched"] == ["voice"]  # GPU_DEVICE/WAKE_WORD_ENABLED are voice-tier; KOKORO_LANG is live
    assert config.gpu_device == "cpu"
    assert config.kokoro_lang == "en-gb"
    assert config.wake_word_enabled is True
    assert len(called) == 1
    assert called[0]["GPU_DEVICE"] == "cpu"


@pytest.mark.asyncio
async def test_update_dashboard_config_ignores_unknown_keys(monkeypatch):
    monkeypatch.setattr(web_server, "_update_env_file", lambda updates: None)
    monkeypatch.setattr(web_server, "event_bus", None)

    res = await web_server.update_dashboard_config({"NOT_A_REAL_SETTING": "x"})
    assert res["status"] == "error"


@pytest.mark.asyncio
async def test_update_dashboard_config_rejects_invalid_types_without_partial_apply(monkeypatch):
    monkeypatch.setattr(web_server, "_update_env_file", lambda updates: None)
    before = config.desktop_idle_threshold_s

    res = await web_server.update_dashboard_config({"DESKTOP_IDLE_THRESHOLD_S": "not-a-number", "GPU_DEVICE": "cpu"})

    assert res["status"] == "error"
    assert "not-a-number" not in res.get("message", "")
    assert config.desktop_idle_threshold_s == before


class _FakeEventBus:
    def __init__(self):
        self.sent = []

    async def send_command(self, cmd):
        self.sent.append(cmd)


@pytest.mark.asyncio
async def test_update_dashboard_config_never_pushes_to_live_engine(monkeypatch):
    """Save (POST /api/config) must only persist -- Reload is the only path that applies live."""
    bus = _FakeEventBus()
    monkeypatch.setattr(web_server, "_update_env_file", lambda updates: None)
    monkeypatch.setattr(web_server, "event_bus", bus)

    res = await web_server.update_dashboard_config({"GPU_DEVICE": "cpu"})
    assert res["status"] == "ok"
    assert bus.sent == []


@pytest.mark.asyncio
async def test_reload_engine_config_sends_system_restart(monkeypatch):
    bus = _FakeEventBus()
    monkeypatch.setattr(web_server, "event_bus", bus)

    res = await web_server.reload_engine_config()
    assert res["status"] == "ok"
    assert bus.sent == [{"type": "system_restart"}]


@pytest.mark.asyncio
async def test_reload_engine_config_without_voice_process(monkeypatch):
    monkeypatch.setattr(web_server, "event_bus", None)
    res = await web_server.reload_engine_config()
    assert res["status"] == "error"


@pytest.mark.asyncio
async def test_delete_memory_fact(monkeypatch):
    removed = []

    async def request(operation, payload):
        removed.append((operation, payload))
        return {"request_id": "r", "operation": operation, "success": True, "data": {"removed": True}}

    monkeypatch.setattr(web_server, "_request_authoritative_memory_operation", request)
    del_res = await web_server.delete_memory_fact("Alice", "works_on", "graphs")
    assert del_res["status"] == "ok"
    assert removed == [("delete_fact", {"subject": "Alice", "predicate": "works_on", "object": "graphs"})]
