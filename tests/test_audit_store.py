from charlie.audit_store import AuditStore


def test_audit_store_redacts_sensitive_arguments(tmp_path):
    store = AuditStore(str(tmp_path / "audit.sqlite3"))
    store.record("shell_execute", {"command": "echo token=secret"}, "requested")

    entries = store.list(limit=10)

    assert entries[0]["tool_name"] == "shell_execute"
    assert "secret" not in entries[0]["arguments"]
    assert "[REDACTED]" in entries[0]["arguments"]
