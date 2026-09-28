import json
import zipfile

from charlie.backup_service import decrypt_snapshot, export_snapshot


def test_export_snapshot_contains_selected_local_records(tmp_path):
    source = tmp_path / "sessions.sqlite3"
    source.write_text("sqlite-placeholder", encoding="utf-8")
    target = tmp_path / "backup.zip"

    result = export_snapshot(target, {"sessions.sqlite3": source})

    assert result["encrypted"] is False
    with zipfile.ZipFile(target) as archive:
        assert archive.read("manifest.json")
        assert archive.read("sessions.sqlite3") == b"sqlite-placeholder"
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["encryption"] == "deferred-key-mechanism"


def test_export_snapshot_can_be_decrypted_with_explicit_passphrase(tmp_path):
    source = tmp_path / "sessions.sqlite3"
    source.write_text("private", encoding="utf-8")
    target = tmp_path / "backup.charlie"

    result = export_snapshot(target, {"sessions.sqlite3": source}, passphrase="correct horse battery staple")

    assert result["encrypted"] is True
    decrypted = decrypt_snapshot(target.read_bytes(), "correct horse battery staple")
    assert decrypted["sessions.sqlite3"] == b"private"
