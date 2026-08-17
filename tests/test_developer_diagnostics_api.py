import tempfile
from pathlib import Path

import pytest

import charlie.web_server as web_server
from charlie.config import Config


@pytest.mark.asyncio
async def test_developer_diagnostics_endpoints(monkeypatch):
    # 1. Developer mode disabled by default or when set to false
    test_config = Config()
    test_config.developer_mode_enabled = False
    monkeypatch.setattr(web_server, "config", test_config)

    diag_res = await web_server.get_developer_diagnostics()
    assert diag_res["developer_mode_enabled"] is False
    assert "diagnostics" in diag_res

    # 2. Enable developer mode
    test_config.developer_mode_enabled = True
    diag_res_enabled = await web_server.get_developer_diagnostics()
    assert diag_res_enabled["developer_mode_enabled"] is True
    diag = diag_res_enabled["diagnostics"]

    assert "tasks" in diag
    assert "leases" in diag
    assert "telemetry" in diag
    assert "system" in diag
    assert "uptime_seconds" in diag["system"]


@pytest.mark.asyncio
async def test_developer_logs_endpoint(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        logs_dir = Path(tmpdir) / "logs"
        logs_dir.mkdir()
        log_file = logs_dir / "charlie.log"
        log_content = "[2026-08-17 12:00:00] [INFO] System started\n[2026-08-17 12:00:01] [DEBUG] Event emitted\n"
        log_file.write_text(log_content, encoding="utf-8")

        test_config = Config()
        test_config.developer_mode_enabled = True
        monkeypatch.setattr(web_server, "config", test_config)
        monkeypatch.setattr(web_server, "_DEV_LOGS_PATH", log_file)

        logs_res = await web_server.get_developer_logs(limit=10)
        assert len(logs_res["lines"]) == 2
        assert "System started" in logs_res["lines"][0]
