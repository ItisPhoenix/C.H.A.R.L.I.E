import inspect
import tempfile
from collections import OrderedDict
from pathlib import Path

import pytest

import charlie.web_server as web_server
from charlie.audit_store import AuditStore
from charlie.privacy_service import PrivacyService, canonical_privacy_request_fingerprint
from charlie.session_store import SessionStore


class _EventBus:
    def __init__(self):
        self.events = []

    async def emit(self, event_type, payload, **_kwargs):
        self.events.append((event_type, payload))


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
async def test_main_owned_privacy_operation_round_trip():
    from main import _handle_privacy_operation_request

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        sessions_db = base / "sessions.db"
        session_store = SessionStore(str(sessions_db))
        audit_store = AuditStore(str(sessions_db))
        browser_dir = base / "browser_profile"
        browser_dir.mkdir()
        (browser_dir / "test.png").write_bytes(b"fake image data")
        service = PrivacyService(
            sessions_db_path=str(sessions_db),
            audit_db_path=str(sessions_db),
            browser_dir_path=str(browser_dir),
            session_store=session_store,
            audit_store=audit_store,
            artifact_paths=(base / "scratchpad.db",),
        )
        bus = _EventBus()
        caches = {"result_cache": OrderedDict(), "in_flight": {}, "fingerprint_cache": {}}
        summary_payload = {"operation": "summary", "request_id": "summary-1"}
        summary_payload["request_fingerprint"] = canonical_privacy_request_fingerprint("summary", summary_payload)
        summary = await _handle_privacy_operation_request(service, bus, summary_payload, **caches)
        assert summary["status"] == "completed"
        assert summary["result"]["categories"]["browser"]["path"] == str(browser_dir)
        assert summary["result"]["categories"]["browser"]["bytes"] > 0

        purge_payload = {
            "operation": "purge",
            "request_id": "purge-browser-1",
            "category": "browser",
            "confirmed": True,
        }
        purge_payload["request_fingerprint"] = canonical_privacy_request_fingerprint("purge", purge_payload)
        purge = await _handle_privacy_operation_request(service, bus, purge_payload, **caches)
        assert purge["status"] == "completed"
        assert purge["result"]["freed_bytes"] > 0
        assert not browser_dir.exists()

        session_store.close()
        audit_store.close()


def test_web_server_has_no_mutable_privacy_authority():
    source = inspect.getsource(web_server)
    assert "PrivacyService" not in source
    assert "AuditStore" not in source
    assert "export_snapshot" not in source
