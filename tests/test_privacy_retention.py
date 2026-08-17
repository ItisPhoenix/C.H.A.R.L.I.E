import tempfile
from pathlib import Path

import pytest

import charlie.web_server as web_server
from charlie.privacy_service import PrivacyService


def test_privacy_service_summary_and_purge():
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        sessions_db = base / "sessions.db"
        sessions_db.write_text("session data dummy", encoding="utf-8")

        audit_db = base / "audit.db"
        audit_db.write_text("audit logs dummy", encoding="utf-8")

        browser_dir = base / "browser_profile"
        browser_dir.mkdir()
        (browser_dir / "cache.tmp").write_bytes(b"0" * 1024)

        service = PrivacyService(
            sessions_db_path=str(sessions_db),
            audit_db_path=str(audit_db),
            browser_dir_path=str(browser_dir),
        )

        # 1. Summary
        summary = service.get_storage_summary()
        assert "categories" in summary
        assert "total_bytes" in summary
        assert summary["categories"]["transcripts"]["bytes"] > 0
        assert summary["categories"]["audit"]["bytes"] > 0
        assert summary["categories"]["browser"]["bytes"] == 1024

        # 2. Purge browser cache
        res = service.purge_category("browser")
        assert res["status"] == "ok"
        assert res["freed_bytes"] >= 1024
        assert not (browser_dir / "cache.tmp").exists()

        # 3. Purge invalid category
        res_invalid = service.purge_category("invalid_category")
        assert res_invalid["status"] == "error"


@pytest.mark.asyncio
async def test_web_server_privacy_endpoints(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        browser_dir = base / "browser_profile"
        browser_dir.mkdir()
        (browser_dir / "test.png").write_bytes(b"fake image data")

        service = PrivacyService(browser_dir_path=str(browser_dir))
        monkeypatch.setattr(web_server, "_privacy_service", service)

        # GET /api/privacy/summary
        summary_res = await web_server.get_privacy_summary()
        assert "categories" in summary_res
        assert summary_res["categories"]["browser"]["bytes"] > 0

        # POST /api/privacy/purge
        purge_res = await web_server.purge_privacy_data({"category": "browser"})
        assert purge_res["status"] == "ok"
        assert purge_res["freed_bytes"] > 0

        # Summary after purge
        summary_res2 = await web_server.get_privacy_summary()
        assert summary_res2["categories"]["browser"]["bytes"] == 0
