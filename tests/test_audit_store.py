import pytest

from charlie.audit_store import AuditStore


def test_audit_store_redacts_sensitive_arguments(tmp_path):
    store = AuditStore(str(tmp_path / "audit.sqlite3"))
    store.record("shell_execute", {"command": "echo token=secret"}, "requested")

    entries = store.list(limit=10)

    assert entries[0]["tool_name"] == "shell_execute"
    assert "secret" not in entries[0]["arguments"]
    assert "[REDACTED]" in entries[0]["arguments"]


@pytest.mark.parametrize(
    ("tool_name", "arguments", "private_text"),
    [
        ("memory", {"action": "add", "target": "user", "content": "Aadhaar 1234 5678 9012"}, "1234 5678 9012"),
        ("vector_memory", {"action": "remember", "content": "Password is hunter2"}, "hunter2"),
        ("graph_add_fact", {"subject": "user", "predicate": "has", "object": "PAN ABCDE1234F"}, "ABCDE1234F"),
    ],
)
def test_audit_store_omits_memory_tool_free_text(tmp_path, tool_name, arguments, private_text):
    store = AuditStore(str(tmp_path / "audit.sqlite3"))
    store.record(tool_name, arguments, "requested")

    entry = store.list(limit=1)[0]

    assert entry["tool_name"] == tool_name
    assert private_text not in entry["arguments"]
    assert "[OMITTED]" in entry["arguments"]
