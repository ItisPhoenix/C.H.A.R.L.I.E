import ast
import asyncio
import inspect
import logging.handlers
import os
import sqlite3
import textwrap
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import HTTPException

import charlie.privacy_service as privacy_service_module
import charlie.web_server as web_server
from charlie import resource_locks
from charlie.audit_store import AuditStore
from charlie.backup_service import decrypt_snapshot
from charlie.privacy_service import (
    PrivacyService,
    UnsafePrivacyPathError,
    canonical_privacy_request_fingerprint,
    validate_browser_profile_path,
)
from charlie.session_store import SessionStore, canonical_session_request_fingerprint


class _EventBus:
    def __init__(self):
        self.events = []

    async def emit(self, event_type, payload, **_kwargs):
        self.events.append((event_type, payload))


class _CommandBus(_EventBus):
    def __init__(self, command):
        super().__init__()
        self.command = command
        self.sent = False

    async def next_command(self):
        if not self.sent:
            self.sent = True
            return self.command
        raise asyncio.CancelledError


def _payload(operation: str, request_id: str, **fields):
    payload = {"operation": operation, "request_id": request_id, **fields}
    payload["request_fingerprint"] = canonical_privacy_request_fingerprint(operation, payload)
    return payload


def _composed_service(tmp_path: Path):
    db_path = tmp_path / "sessions.sqlite3"
    store = SessionStore(str(db_path))
    audit = AuditStore(str(db_path))
    service = PrivacyService(
        sessions_db_path=str(db_path),
        audit_db_path=str(db_path),
        browser_dir_path=str(tmp_path / "configured-browser"),
        memory_paths=(tmp_path / "memory-vector", tmp_path / "memory-graph.sqlite3"),
        logs_dir_path=str(tmp_path / "logs"),
        artifact_paths=(tmp_path / "scratchpad.db",),
        session_store=store,
        audit_store=audit,
    )
    return service, store, audit


def _consume_web_commands_source() -> str:
    source_path = Path(__file__).resolve().parents[1] / "main.py"
    source = source_path.read_text(encoding="utf-8")
    module = ast.parse(source)
    matches = [
        node
        for node in ast.walk(module)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "consume_web_commands"
    ]
    assert len(matches) == 1
    return ast.get_source_segment(source, matches[0]) or ""


def _load_consume_web_commands(command, *, service=None, store=None):
    import main

    scheduled = []

    def submit(coroutine):
        task = asyncio.create_task(coroutine)
        scheduled.append(task)
        return task

    namespace = vars(main).copy()
    namespace["_submit_event_task"] = submit
    namespace["service"] = service
    namespace["store_obj"] = store
    wrapper_source = (
        "def _wrapper():\n"
        "    current_web_session_id = 'initial-session'\n"
        "    _voice_fallback_session_id = 'voice-fallback'\n"
        "    voice = None\n"
        "    mcp_client = None\n"
        "    privacy_service = service\n"
        "    store = store_obj\n"
        "    privacy_operation_results = OrderedDict()\n"
        "    privacy_operation_in_flight = {}\n"
        "    privacy_operation_fingerprints = {}\n"
        "    session_operation_results = OrderedDict()\n"
        "    session_operation_in_flight = {}\n"
        "    session_operation_fingerprints = {}\n"
        "    pending_turns = []\n"
        "    active_turn_session_id = None\n"
        "    runtime_shutting_down = False\n"
        "    session_lifecycle_gate = asyncio.Lock()\n"
        + textwrap.indent(_consume_web_commands_source(), "    ")
        + "\n    return consume_web_commands\n"
    )
    exec(compile(wrapper_source, "<main.consume_web_commands>", "exec"), namespace)
    return namespace["_wrapper"](), scheduled


@pytest.mark.asyncio
async def test_consume_web_commands_privacy_callback_uses_canonical_background_authority(monkeypatch):
    import main

    class SummaryService:
        def __init__(self):
            self.calls = 0

        def get_storage_summary(self):
            self.calls += 1
            return {"authority": "main_runtime", "categories": {}}

    monkeypatch.setattr(main, "_main_event_bus_registry", None)
    service = SummaryService()
    command = {"type": "privacy_operation", "payload": _payload("summary", "runtime-summary")}
    bus = _CommandBus(command)
    consume, scheduled = _load_consume_web_commands(command, service=service)

    await consume(bus, object())
    await asyncio.gather(*scheduled)

    results = [
        payload
        for event_type, payload in bus.events
        if event_type == "privacy_operation_result"
    ]
    assert service.calls == 1
    assert len(results) == 1
    assert results[0]["request_id"] == "runtime-summary"
    assert results[0]["status"] == "completed"
    assert results[0]["result"]["authority"] == "main_runtime"


@pytest.mark.asyncio
async def test_consume_web_commands_session_helper_preserves_enclosing_session_id(tmp_path):
    store = SessionStore(str(tmp_path / "runtime-session-binding.sqlite3"))
    payload = {
        "operation": "create",
        "request_id": "runtime-session-create",
        "session_id": "runtime-created-session",
        "title": "Runtime helper session",
        "parent_session_id": None,
    }
    payload["request_fingerprint"] = canonical_session_request_fingerprint("create", payload)
    bus = _CommandBus({"type": "session_operation", "payload": payload})
    consume, scheduled = _load_consume_web_commands(bus.command, store=store)

    try:
        await consume(bus, object())
        await asyncio.gather(*scheduled)

        results = [
            event_payload
            for event_type, event_payload in bus.events
            if event_type == "session_operation_result"
        ]
        assert len(results) == 1
        assert results[0]["request_id"] == "runtime-session-create"
        assert results[0]["status"] == "completed"
        assert store.session_exists("runtime-created-session")
    finally:
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"active_turn_session_id": "busy"},
        {"queued_session_ids": ("busy",)},
        {"background_session_ids": ("busy",)},
    ],
)
async def test_full_transcript_purge_rejects_busy_sessions(tmp_path, kwargs):
    from main import _handle_privacy_operation_request

    service, store, audit = _composed_service(tmp_path)
    try:
        store.create_session("busy", "Live", source="web", launch_id="launch")
        store.append("user", "must survive", session_id="busy")
        result = await _handle_privacy_operation_request(
            service,
            _EventBus(),
            _payload("purge", "busy-purge", category="transcripts", confirmed=True),
            result_cache=OrderedDict(),
            in_flight={},
            fingerprint_cache={},
            **kwargs,
        )
        assert result["status"] == "busy"
        assert store.session_exists("busy")
        assert store.get_session_messages("busy") == [("user", "must survive")]
    finally:
        audit.close()
        store.close()


def test_age_transcript_purge_preserves_metadata_and_fts(tmp_path):
    service, store, audit = _composed_service(tmp_path)
    try:
        store.create_session("old", "Keep metadata", source="web", launch_id="launch")
        store.append("user", "retireme transcript", session_id="old")
        store.append_tool_event("old", "call", "old_tool", "retireme event")
        store.create_session("new", "New metadata", source="web", launch_id="launch")
        store.append("user", "keepme transcript", session_id="new")
        store.conn.execute(
            "UPDATE messages SET timestamp = ? WHERE session_id = ?",
            ("2000-01-01T00:00:00.000000Z", "old"),
        )
        store.conn.execute(
            "UPDATE tool_events SET created_at = ? WHERE session_id = ?",
            ("2000-01-01T00:00:00.000000Z", "old"),
        )
        store.conn.commit()

        result = service.purge_category("transcripts", older_than_days=1)

        assert result["status"] == "ok"
        assert store.session_exists("old")
        assert store.get_session_record("old")["title"] == "Keep metadata"
        assert store.get_session_messages("old") == []
        assert store.get_tool_events("old") == []
        assert store.get_session_messages("new") == [("user", "keepme transcript")]
        assert not store.search("retireme")
    finally:
        audit.close()
        store.close()


def test_audit_store_serializes_record_list_and_purge(tmp_path):
    audit = AuditStore(str(tmp_path / "audit.sqlite3"))
    try:
        barrier = threading.Barrier(3)

        def record(index):
            barrier.wait()
            return audit.record("tool", {"index": index}, "ok")

        def purge():
            barrier.wait()
            return audit.purge()

        def list_entries():
            barrier.wait()
            return audit.list()

        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda fn: fn(), (lambda: record(1), purge, list_entries)))
        assert any(isinstance(result, dict) for result in results)
        assert isinstance(audit.list(), list)
    finally:
        audit.close()


def test_log_purge_preserves_active_file_and_removes_rotated_files(tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    active = logs_dir / "charlie.log"
    rotated = logs_dir / "charlie.log.1"
    active.write_text("active", encoding="utf-8")
    rotated.write_text("rotated", encoding="utf-8")
    handler = logging.handlers.RotatingFileHandler(active, encoding="utf-8", backupCount=2)
    try:
        service = PrivacyService(
            logs_dir_path=str(logs_dir),
            active_log_path=str(active),
            log_handler=handler,
        )
        result = service.purge_category("logs")
        assert result["status"] == "ok"
        assert result["active_log_preserved"] is True
        assert active.exists()
        assert not rotated.exists()
    finally:
        handler.close()


def test_artifact_purge_uses_only_explicit_allowlist(tmp_path):
    allowed = tmp_path / "scratchpad.db"
    unrelated = tmp_path / "unrelated.txt"
    allowed.write_bytes(b"temporary")
    unrelated.write_bytes(b"preserve")
    service = PrivacyService(artifact_paths=(allowed,))

    result = service.purge_category("artifacts")

    assert result["status"] == "ok"
    assert not allowed.exists()
    assert unrelated.exists()


def test_artifact_purge_preserves_active_task_artifact(tmp_path):
    artifact = tmp_path / "active-artifact.bin"
    artifact.write_bytes(b"active")
    service = PrivacyService(
        artifact_paths=(artifact,),
        active_artifact_paths=lambda: (artifact,),
    )

    result = service.purge_category("artifacts")

    assert result["status"] == "partial"
    assert result["items_skipped"] == 1
    assert artifact.exists()


@pytest.mark.parametrize(
    "target_factory",
    [
        lambda: Path(Path(__file__).anchor),
        Path.home,
        lambda: Path(__file__).resolve().parents[1],
        lambda: Path(Path(__file__).anchor) / "broad-browser-target",
    ],
)
def test_browser_profile_validation_rejects_broad_targets(target_factory):
    with pytest.raises(UnsafePrivacyPathError):
        validate_browser_profile_path(
            target_factory(),
            project_root=Path(__file__).resolve().parents[1],
        )


def test_browser_profile_purge_revalidates_unsafe_configured_target():
    service = PrivacyService(
        browser_dir_path=Path(__file__).resolve().parents[1],
        project_root=Path(__file__).resolve().parents[1],
    )

    with pytest.raises(UnsafePrivacyPathError):
        service.purge_category("browser")


def test_browser_profile_validation_rejects_symlink_components(tmp_path):
    target = tmp_path / "real-profile"
    target.mkdir()
    link = tmp_path / "linked-profile"
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this host")

    with pytest.raises(UnsafePrivacyPathError):
        validate_browser_profile_path(link, project_root=Path(__file__).resolve().parents[1])


@pytest.mark.asyncio
async def test_active_browser_capability_blocks_profile_purge(tmp_path):
    from main import _handle_privacy_operation_request

    service, store, audit = _composed_service(tmp_path)
    profile = Path(service.browser_dir_path)
    profile.mkdir()
    marker = profile / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    assert resource_locks.acquire("browser", "browser-task")
    try:
        result = await _handle_privacy_operation_request(
            service,
            _EventBus(),
            _payload("purge", "browser-busy", category="browser", confirmed=True),
            result_cache=OrderedDict(),
            in_flight={},
            fingerprint_cache={},
        )
        assert result["status"] == "busy"
        assert marker.exists()
    finally:
        resource_locks.release("browser", "browser-task")
        audit.close()
        store.close()


def test_backup_uses_wal_snapshot_and_preserves_encryption(tmp_path):
    service, store, audit = _composed_service(tmp_path)
    try:
        store.create_session("wal-session", "WAL row", source="web", launch_id="launch")
        store.append("user", "committed in WAL", session_id="wal-session")
        audit.record("tool", {"secret": "redacted"}, "completed")
        target = tmp_path / "backups" / "backup.charlie"

        result = service.export_backup(target, "correct horse battery staple")

        assert target.is_file()
        assert result["encrypted"] is True
        assert result["scope"] == ["sessions", "audit"]
        snapshot = tmp_path / "snapshot.sqlite3"
        snapshot.write_bytes(decrypt_snapshot(target.read_bytes(), "correct horse battery staple")["sessions.sqlite3"])
        connection = sqlite3.connect(snapshot)
        try:
            assert connection.execute("SELECT title FROM sessions WHERE session_id = 'wal-session'").fetchone() == (
                "WAL row",
            )
            assert connection.execute("SELECT content FROM messages WHERE session_id = 'wal-session'").fetchone() == (
                "committed in WAL",
            )
            assert connection.execute("SELECT COUNT(*) FROM audit_entries").fetchone() == (1,)
        finally:
            connection.close()
    finally:
        audit.close()
        store.close()


def test_backup_failure_is_not_reported_as_success(tmp_path, monkeypatch):
    service, store, audit = _composed_service(tmp_path)
    try:
        store.create_session("backup-failure", "Failure", source="web", launch_id="launch")

        def fail_export(*_args, **_kwargs):
            raise OSError("archive unavailable")

        monkeypatch.setattr(privacy_service_module, "export_snapshot", fail_export)
        with pytest.raises(OSError):
            service.export_backup(tmp_path / "backup.zip")
        assert not list((tmp_path / "backups").glob("backup.zip"))
    finally:
        audit.close()
        store.close()


@pytest.mark.asyncio
async def test_duplicate_backup_executes_once():
    from main import _handle_privacy_operation_request

    class SlowBackupService:
        def __init__(self):
            self.calls = 0
            self.started = threading.Event()
            self.release = threading.Event()

        def export_backup(self, target, passphrase):
            self.calls += 1
            self.started.set()
            assert self.release.wait(2)
            return {
                "path": str(target),
                "manifest": {"files": ["sessions.sqlite3"]},
                "encrypted": bool(passphrase),
                "scope": ["sessions"],
            }

    service = SlowBackupService()
    bus = _EventBus()
    caches = {"result_cache": OrderedDict(), "in_flight": {}, "fingerprint_cache": {}}
    payload = _payload("backup_export", "backup-same", passphrase=None)
    first = asyncio.create_task(
        _handle_privacy_operation_request(service, bus, payload, backup_dir="backups", **caches)
    )
    await asyncio.to_thread(service.started.wait, 2)
    second = asyncio.create_task(
        _handle_privacy_operation_request(service, bus, payload, backup_dir="backups", **caches)
    )
    await asyncio.sleep(0)
    service.release.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert first_result["status"] == "completed"
    assert second_result["result"]["replayed"] is True
    assert service.calls == 1


@pytest.mark.asyncio
async def test_duplicate_privacy_mutation_executes_once_and_conflict_is_rejected():
    from main import _handle_privacy_operation_request

    class SlowService:
        def __init__(self):
            self.calls = 0
            self.started = threading.Event()
            self.release = threading.Event()

        def purge_category(self, *_args, **_kwargs):
            self.calls += 1
            self.started.set()
            assert self.release.wait(2)
            return {"status": "ok", "category": "audit", "freed_bytes": 0, "items_purged": 1}

    service = SlowService()
    bus = _EventBus()
    caches = {"result_cache": OrderedDict(), "in_flight": {}, "fingerprint_cache": {}}
    first_payload = _payload("purge", "same-id", category="audit", confirmed=True)
    first = asyncio.create_task(_handle_privacy_operation_request(service, bus, first_payload, **caches))
    await asyncio.to_thread(service.started.wait, 2)
    second = asyncio.create_task(_handle_privacy_operation_request(service, bus, first_payload, **caches))
    await asyncio.sleep(0)
    service.release.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert first_result["status"] == "completed"
    assert second_result["result"]["replayed"] is True
    assert service.calls == 1

    conflict_payload = _payload("purge", "same-id", category="logs", confirmed=True)
    conflict = await _handle_privacy_operation_request(service, bus, conflict_payload, **caches)
    assert conflict["status"] == "request_id_conflict"


@pytest.mark.asyncio
async def test_full_transcript_purge_holds_lifecycle_gate_against_new_admission():
    from main import _handle_privacy_operation_request

    class SlowService:
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()
            self.protected_session_ids = None

        def purge_category(self, _category, _older_than_days, *, protected_session_ids):
            self.protected_session_ids = tuple(protected_session_ids)
            self.started.set()
            assert self.release.wait(2)
            return {"status": "ok", "category": "transcripts", "freed_bytes": 0, "items_purged": 0}

    service = SlowService()
    lifecycle_gate = asyncio.Lock()
    state = {"active": None}
    purge = asyncio.create_task(
        _handle_privacy_operation_request(
            service,
            _EventBus(),
            _payload("purge", "lifecycle-race", category="transcripts", confirmed=True),
            result_cache=OrderedDict(),
            in_flight={},
            fingerprint_cache={},
            lifecycle_gate=lifecycle_gate,
            session_state=lambda: (
                state["active"],
                (),
                (),
            ),
        )
    )
    admission_acquired = asyncio.Event()

    async def admit_turn():
        async with lifecycle_gate:
            state["active"] = "new-session"
            admission_acquired.set()

    admission = None
    try:
        assert await asyncio.to_thread(service.started.wait, 2)
        admission = asyncio.create_task(admit_turn())
        await asyncio.sleep(0)
        assert not admission_acquired.is_set()
        assert state["active"] is None
        assert service.protected_session_ids == ()

        service.release.set()
        result = await asyncio.wait_for(purge, 3)
        await asyncio.wait_for(admission, 3)
        assert result["status"] == "completed"
        assert admission_acquired.is_set()
        assert state["active"] == "new-session"
    finally:
        service.release.set()
        if admission is not None and not admission.done():
            await admission
        if not purge.done():
            await purge


@pytest.mark.asyncio
async def test_privacy_physical_worker_remains_tracked_until_thread_quiesces(monkeypatch):
    import main as main_module

    registry = main_module._EventBusSubmissionRegistry()
    monkeypatch.setattr(main_module, "_main_event_bus_registry", registry)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    class Closable:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    audit_store = Closable()
    session_store = Closable()

    def blocked_worker():
        started.set()
        release.wait(2)
        finished.set()
        return "finished"

    task = registry.submit_task(
        main_module._run_privacy_sync(blocked_worker),
        asyncio.get_running_loop(),
    )
    assert task is not None
    try:
        assert await asyncio.to_thread(started.wait, 2)
        tracked_tasks, _ = registry.snapshot()
        assert len(tracked_tasks) >= 2

        registry.close()
        with pytest.raises(RuntimeError, match="quiescence"):
            await main_module._drain_event_bus_submissions(registry, timeout=0)
        assert not finished.is_set()
        assert not registry.is_empty()
        main_module._close_runtime_stores(audit_store, session_store, quiescent=False)
        assert not audit_store.closed
        assert not session_store.closed

        release.set()
        assert await asyncio.to_thread(finished.wait, 2)
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(main_module._drain_event_bus_submissions(registry, timeout=1), 3)
        assert registry.is_empty()
        main_module._close_runtime_stores(audit_store, session_store, quiescent=True)
        assert audit_store.closed
        assert session_store.closed
    finally:
        release.set()
        if not finished.is_set():
            await asyncio.to_thread(finished.wait, 2)


@pytest.mark.asyncio
async def test_privacy_operation_rejected_after_admission_closes():
    from main import _handle_privacy_operation_request

    class NeverCalled:
        def purge_category(self, *_args, **_kwargs):
            raise AssertionError("closed runtime admitted a privacy mutation")

    result = await _handle_privacy_operation_request(
        NeverCalled(),
        _EventBus(),
        _payload("purge", "closed", category="audit", confirmed=True),
        result_cache=OrderedDict(),
        in_flight={},
        fingerprint_cache={},
        admission_open=lambda: False,
    )
    assert result["status"] == "shutting_down"


@pytest.mark.asyncio
async def test_cancelled_privacy_operation_drains_worker_io():
    from main import _handle_privacy_operation_request

    class SlowService:
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()
            self.finished = threading.Event()

        def purge_category(self, *_args, **_kwargs):
            self.started.set()
            self.release.wait(2)
            self.finished.set()
            return {"status": "ok", "category": "audit", "freed_bytes": 0, "items_purged": 0}

    service = SlowService()
    task = asyncio.create_task(
        _handle_privacy_operation_request(
            service,
            _EventBus(),
            _payload("purge", "cancel-drain", category="audit", confirmed=True),
            result_cache=OrderedDict(),
            in_flight={},
            fingerprint_cache={},
        )
    )
    await asyncio.to_thread(service.started.wait, 2)
    task.cancel()
    await asyncio.sleep(0)
    service.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert service.finished.is_set()


@pytest.mark.asyncio
async def test_web_privacy_authority_unavailable_is_503(monkeypatch):
    monkeypatch.setattr(web_server, "event_bus", None)
    with pytest.raises(HTTPException) as error:
        await web_server.get_privacy_summary()
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_web_privacy_summary_forwards_to_main(monkeypatch):
    class Bus:
        async def send_command(self, command):
            payload = command["payload"]
            result = {
                "request_id": payload["request_id"],
                "request_fingerprint": payload["request_fingerprint"],
                "operation": "summary",
                "status": "completed",
                "result": {"ok": True, "authority": "main_runtime", "categories": {}},
            }
            web_server._resolve_privacy_operation_result(result)
            return True

    monkeypatch.setattr(web_server, "event_bus", Bus())
    assert await web_server.get_privacy_summary() == {
        "ok": True,
        "authority": "main_runtime",
        "categories": {},
    }


def test_web_privacy_module_has_no_mutating_store_construction():
    source = inspect.getsource(web_server)
    assert "PrivacyService" not in source
    assert "AuditStore" not in source
    assert "export_snapshot" not in source
