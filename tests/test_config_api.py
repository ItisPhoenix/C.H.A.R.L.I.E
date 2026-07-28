import os
import tempfile
from pathlib import Path

import pytest

import charlie.web_server as web_server
from charlie.config import config
from charlie.memory_graph import MemoryGraph


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


def test_desktop_idle_threshold_default():
    from charlie.config import Config

    assert Config().desktop_idle_threshold_s == 120.0


@pytest.mark.asyncio
async def test_get_dashboard_config_masks_secrets():
    res = await web_server.get_dashboard_config()
    by_key = {f["key"]: f for f in res["fields"]}
    secret_field = by_key["SMALL_LLM_API_KEY"]
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
    with tempfile.TemporaryDirectory(suffix="-web-facts-delete") as d:
        db_path = str(Path(d) / "graph.db")
        graph = MemoryGraph(db_path)
        try:
            graph.add_fact("Alice", "works_on", "graphs")
            monkeypatch.setattr(web_server, "_get_memory_graph", lambda: graph)

            # Confirm fact is added
            facts_before = graph.get_all_facts()
            assert len(facts_before) == 1

            # Delete the fact
            del_res = await web_server.delete_memory_fact("Alice", "works_on", "graphs")
            assert del_res["status"] == "ok"

            # Verify it is deleted from sqlite
            facts_after = graph.get_all_facts()
            assert len(facts_after) == 0
        finally:
            graph.close()
            _remove_db(db_path)
