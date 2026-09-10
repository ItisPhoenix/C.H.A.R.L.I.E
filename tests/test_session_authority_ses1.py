import asyncio
import inspect
import json
import sqlite3
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

import pytest
from starlette.websockets import WebSocketDisconnect

from charlie.session_read_projection import SessionReadProjection
from charlie.session_store import (
    SessionNotFoundError,
    SessionOutcomeUnknownError,
    SessionStore,
    canonical_session_request_fingerprint,
)
from charlie.tasks import ManagedTask, TaskManager, TaskManagerAdmissionClosed
from charlie.turn_contracts import TurnRequest


class _EventBus:
    def __init__(self):
        self.events = []

    async def emit(self, event_type, payload, **_kwargs):
        self.events.append((event_type, payload))


@pytest.mark.asyncio
async def test_main_session_mutation_is_correlated_and_idempotent(tmp_path):
    from main import _handle_session_operation_request

    store = SessionStore(str(tmp_path / "authority.db"))
    bus = _EventBus()
    result_cache = OrderedDict()
    in_flight = {}
    fingerprint_cache = {}
    payload = {
        "operation": "create",
        "request_id": "create-1",
        "session_id": "web-1",
        "title": "Chat",
        "parent_session_id": None,
    }
    payload["request_fingerprint"] = canonical_session_request_fingerprint("create", payload)

    kwargs = {
        "result_cache": result_cache,
        "in_flight": in_flight,
        "fingerprint_cache": fingerprint_cache,
        "launch_id": "launch-1",
    }
    first = await _handle_session_operation_request(store, bus, payload, **kwargs)
    replay = await _handle_session_operation_request(store, bus, payload, **kwargs)
    conflict_payload = {**payload, "title": "Different"}
    conflict_payload["request_fingerprint"] = canonical_session_request_fingerprint("create", conflict_payload)
    conflict = await _handle_session_operation_request(store, bus, conflict_payload, **kwargs)

    assert first["status"] == "completed"
    assert replay["status"] == first["status"]
    assert replay["result"]["session"] == first["result"]["session"]
    assert replay["result"]["replayed"] is True
    assert conflict["status"] == "request_id_conflict"
    assert store.get_session_record("web-1")["title"] == "Chat"
    assert any(event_type == "session_operation_result" for event_type, _ in bus.events)
    store.close()


@pytest.mark.asyncio
async def test_chat_admission_replay_does_not_accept_second_execution(tmp_path):
    from main import _handle_session_operation_request

    store = SessionStore(str(tmp_path / "chat-idempotency.db"))
    store.create_session("s1", "Chat", source="web", launch_id="launch-1")
    bus = _EventBus()
    caches = {"result_cache": OrderedDict(), "in_flight": {}, "fingerprint_cache": {}}
    payload = {"operation": "chat", "request_id": "chat-1", "session_id": "s1", "text": "hello"}
    payload["request_fingerprint"] = canonical_session_request_fingerprint("chat", payload)
    accepted = []

    def accept_callback():
        request = TurnRequest.allocate(payload["text"], payload["session_id"], "web")
        accepted.append(request)
        return {"turn_id": request.turn_id}

    first = await _handle_session_operation_request(
        store,
        bus,
        payload,
        launch_id="launch-1",
        accept_callback=accept_callback,
        **caches,
    )
    retry = await _handle_session_operation_request(
        store,
        bus,
        payload,
        launch_id="launch-1",
        accept_callback=accept_callback,
        **caches,
    )
    assert first["status"] == "accepted"
    assert retry["result"]["replayed"] is True
    assert retry["result"]["turn_id"] == first["result"]["turn_id"]
    assert len(accepted) == 1
    conflict_payload = {**payload, "text": "different"}
    conflict_payload["request_fingerprint"] = canonical_session_request_fingerprint("chat", conflict_payload)
    conflict = await _handle_session_operation_request(
        store,
        bus,
        conflict_payload,
        launch_id="launch-1",
        accept_callback=accept_callback,
        **caches,
    )
    assert conflict["status"] == "request_id_conflict"
    assert len(accepted) == 1
    store.close()


@pytest.mark.asyncio
async def test_concurrent_identical_chat_admission_submits_once(tmp_path):
    from main import _handle_session_operation_request

    store = SessionStore(str(tmp_path / "chat-concurrent-idempotency.db"))
    store.create_session("s1", "Chat", source="web", launch_id="launch-1")
    bus = _EventBus()
    caches = {"result_cache": OrderedDict(), "in_flight": {}, "fingerprint_cache": {}}
    payload = {"operation": "chat", "request_id": "chat-concurrent", "session_id": "s1", "text": "hello"}
    payload["request_fingerprint"] = canonical_session_request_fingerprint("chat", payload)
    started = asyncio.Event()
    release = asyncio.Event()
    accepted = []

    async def accept_callback():
        request = TurnRequest.allocate(payload["text"], payload["session_id"], "web")
        accepted.append(request)
        started.set()
        await release.wait()
        return {"turn_id": request.turn_id}

    kwargs = {
        "store": store,
        "event_bus": bus,
        "payload": payload,
        "launch_id": "launch-1",
        "accept_callback": accept_callback,
        **caches,
    }
    first_task = asyncio.create_task(_handle_session_operation_request(**kwargs))
    await started.wait()
    second_task = asyncio.create_task(_handle_session_operation_request(**kwargs))
    await asyncio.sleep(0)
    assert len(accepted) == 1
    release.set()
    first, second = await asyncio.gather(first_task, second_task)

    assert first["status"] == "accepted"
    assert second["status"] == "accepted"
    assert second["result"]["replayed"] is True
    assert second["result"]["turn_id"] == first["result"]["turn_id"]
    assert len(accepted) == 1
    store.close()


@pytest.mark.asyncio
async def test_production_shape_websocket_chat_is_canonicalized_before_main_authority(tmp_path, monkeypatch):
    import charlie.web_server as web_server
    from main import _handle_session_operation_request

    class ChatWebSocket:
        def __init__(self, message):
            self.headers = {"origin": "http://localhost"}
            self._messages = [json.dumps(message)]
            self.sent = []

        async def accept(self):
            return None

        async def send_text(self, message):
            self.sent.append(json.loads(message))

        async def receive_text(self):
            if self._messages:
                return self._messages.pop(0)
            raise WebSocketDisconnect(code=1000)

    class IngressBus(_EventBus):
        def __init__(self):
            super().__init__()
            self.commands = []

        async def send_command(self, command):
            self.commands.append(command)
            return True

    bus = IngressBus()
    socket = ChatWebSocket(
        {
            "type": "chat",
            "payload": {"text": "hello", "session_id": "s1", "request_id": "ws-chat-1"},
        }
    )
    monkeypatch.setattr(web_server, "event_bus", bus)
    monkeypatch.setattr(web_server, "_initial_state_events", lambda: [])
    monkeypatch.setattr(web_server, "active_connections", set())
    monkeypatch.setattr(web_server, "ws_sessions", {})

    await web_server.websocket_endpoint(socket)

    chat_commands = [command for command in bus.commands if command.get("type") == "chat"]
    assert len(chat_commands) == 1
    payload = chat_commands[0]["payload"]
    assert payload["operation"] == "chat"
    assert payload["request_id"] == "ws-chat-1"
    assert payload["request_fingerprint"] == canonical_session_request_fingerprint("chat", payload)

    store = SessionStore(str(tmp_path / "websocket-chat-authority.db"))
    store.create_session("s1", "Chat", source="web", launch_id="launch-1")
    accepted = []

    def accept_callback():
        request = TurnRequest.allocate(payload["text"], payload["session_id"], "web")
        accepted.append(request)
        return {"turn_id": request.turn_id}

    result = await _handle_session_operation_request(
        store,
        bus,
        payload,
        result_cache=OrderedDict(),
        in_flight={},
        fingerprint_cache={},
        accept_callback=accept_callback,
        launch_id="launch-1",
    )

    assert result["status"] == "accepted"
    assert result["result"]["turn_id"] == accepted[0].turn_id
    assert accepted[0].channel == "web"
    store.close()


@pytest.mark.asyncio
async def test_delete_active_turn_is_rejected(tmp_path):
    from main import _handle_session_operation_request

    store = SessionStore(str(tmp_path / "busy.db"))
    store.create_session("busy", "Busy", source="web", launch_id="launch-1")
    bus = _EventBus()
    payload = {
        "operation": "delete",
        "request_id": "delete-1",
        "session_id": "busy",
    }
    payload["request_fingerprint"] = canonical_session_request_fingerprint("delete", payload)

    result = await _handle_session_operation_request(
        store,
        bus,
        payload,
        active_turn_session_id="busy",
        launch_id="launch-1",
    )

    assert result["status"] == "session_busy"
    assert store.session_exists("busy")
    store.close()


@pytest.mark.asyncio
async def test_delete_background_session_is_rejected(tmp_path):
    from main import _handle_session_operation_request

    store = SessionStore(str(tmp_path / "busy-bg.db"))
    store.create_session("busy", "Busy", source="web", launch_id="launch-1")
    bus = _EventBus()
    payload = {"operation": "delete", "request_id": "delete-bg", "session_id": "busy"}
    payload["request_fingerprint"] = canonical_session_request_fingerprint("delete", payload)
    result = await _handle_session_operation_request(
        store,
        bus,
        payload,
        background_session_ids=("busy",),
        launch_id="launch-1",
    )
    assert result["status"] == "session_busy"
    store.close()


def test_web_has_no_session_store_mutation_authority():
    import charlie.web_server as web_server

    source = inspect.getsource(web_server)
    assert "SessionStore(" not in source
    assert "_get_store" not in source
    assert "SessionReadProjection" in source


def test_read_projection_is_read_only_and_closes(tmp_path):
    db_path = str(tmp_path / "projection.db")
    store = SessionStore(db_path)
    store.create_session("s1", "Projection", source="web")
    store.append("user", "hello", session_id="s1")
    store.close()

    projection = SessionReadProjection(db_path)
    assert projection.get_sessions()[0][0] == "s1"
    assert projection.get_session_messages("s1") == [("user", "hello")]
    projection.close()
    assert projection.closed is True


@pytest.mark.asyncio
async def test_web_only_chat_fails_closed_without_main(monkeypatch):
    import charlie.web_server as web_server

    monkeypatch.setattr(web_server, "event_bus", None)
    response = await web_server.session_chat("missing", {"text": "hello"})
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_task_manager_shutdown_drains_persistence_task():
    started = asyncio.Event()
    finished = asyncio.Event()
    manager = TaskManager(max_parallel=1)

    async def run_task():
        started.set()
        try:
            await asyncio.sleep(60)
        finally:
            finished.set()

    manager.submit(ManagedTask("persist"), run_task)
    await started.wait()
    await manager.shutdown(timeout=1.0)
    assert finished.is_set()


@pytest.mark.asyncio
async def test_task_manager_closes_admission_before_drain():
    manager = TaskManager(max_parallel=0)
    manager.submit(ManagedTask("queued"), lambda: asyncio.sleep(0))
    manager.close_admission()
    with pytest.raises(TaskManagerAdmissionClosed):
        manager.submit(ManagedTask("late"), lambda: asyncio.sleep(0))
    assert manager.get("queued").status == "cancelled"
    assert manager.active_count() == 0


@pytest.mark.asyncio
async def test_background_start_rejects_before_task_state(monkeypatch):
    import charlie.background_task as background_task

    manager = TaskManager(max_parallel=1)
    manager.close_admission()
    monkeypatch.setattr(background_task, "_manager", manager)
    with pytest.raises(TaskManagerAdmissionClosed):
        await background_task.start(None, None, "late task")
    assert manager.list() == []


@pytest.mark.asyncio
async def test_background_start_rechecks_session_before_submit(monkeypatch):
    import charlie.background_task as background_task

    manager = TaskManager(max_parallel=1)
    monkeypatch.setattr(background_task, "_manager", manager)

    class MissingSessionStore:
        def session_exists(self, _session_id):
            return False

    config = type("Config", (), {"background_max_parallel_tasks": 1})()
    with pytest.raises(SessionNotFoundError):
        await background_task.start(
            config,
            None,
            "late session task",
            session_store=MissingSessionStore(),
            session_id="deleted",
        )
    assert manager.list() == []


@pytest.mark.asyncio
async def test_background_persistence_quiesces_before_store_close(tmp_path):
    store = SessionStore(str(tmp_path / "shutdown.db"))
    store.create_session("s1", "Shutdown", source="test")
    started = asyncio.Event()
    manager = TaskManager(max_parallel=1)

    async def persist_on_cancel():
        started.set()
        try:
            await asyncio.sleep(60)
        finally:
            store.append("assistant", "drained", session_id="s1")

    manager.submit(ManagedTask("persist-before-close"), persist_on_cancel)
    await started.wait()
    await manager.shutdown(timeout=1.0)
    store.close()

    check = SessionStore(str(tmp_path / "shutdown.db"))
    assert check.get_session_messages("s1") == [("assistant", "drained")]
    check.close()


def test_concurrent_create_conflicts_are_canonical(tmp_path):
    store = SessionStore(str(tmp_path / "create-race.db"))
    barrier = threading.Barrier(2)

    def create(source: str):
        barrier.wait()
        try:
            return ("ok", store.create_session("same", "Title", source=source, launch_id="launch"))
        except Exception as exc:  # typed conflict is asserted below
            return ("error", type(exc).__name__)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, ("web", "voice")))
    assert sorted(result[0] for result in results) == ["error", "ok"]
    assert store.get_session_record("same")["source"] in {"web", "voice"}
    store.close()


def test_compatible_create_preserves_canonical_title(tmp_path):
    store = SessionStore(str(tmp_path / "create-title.db"))
    store.create_session("same", "Original", source="web", launch_id="launch")
    row = store.create_session("same", "Caller Title", source="web", launch_id="launch")
    assert row["title"] == "Original"
    store.close()


class _MutationConnection:
    def __init__(self, *, begin_busy=False, commit_busy=False, commit_error=False):
        self.begin_busy = begin_busy
        self.commit_busy = commit_busy
        self.commit_error = commit_error
        self.dml_count = 0
        self.commit_count = 0

    def execute(self, sql, _parameters=()):
        if sql == "BEGIN IMMEDIATE" and self.begin_busy:
            self.begin_busy = False
            raise sqlite3.OperationalError("database is locked")
        if sql.startswith("INSERT"):
            self.dml_count += 1
        return self

    def fetchone(self):
        return None

    @property
    def rowcount(self):
        return 1

    def commit(self):
        self.commit_count += 1
        if self.commit_error:
            raise sqlite3.OperationalError("disk I/O error")
        if self.commit_busy and self.commit_count == 1:
            raise sqlite3.OperationalError("database is locked")

    def rollback(self):
        return None

    def close(self):
        return None


def test_mutation_retry_begin_busy_runs_dml_once(tmp_path):
    store = SessionStore(str(tmp_path / "retry-begin.db"))
    fake = _MutationConnection(begin_busy=True)
    store._local.conn = fake
    store._mutate(lambda tx: tx.execute("INSERT INTO messages VALUES (?)", ("x",)), "test append")
    assert fake.dml_count == 1
    store.close()


def test_mutation_retry_commit_busy_retries_commit_not_dml(tmp_path):
    store = SessionStore(str(tmp_path / "retry-commit.db"))
    fake = _MutationConnection(commit_busy=True)
    store._local.conn = fake
    store._mutate(lambda tx: tx.execute("INSERT INTO messages VALUES (?)", ("x",)), "test append")
    assert fake.dml_count == 1
    assert fake.commit_count == 2
    store.close()


def test_mutation_unknown_commit_is_not_replayed(tmp_path):
    store = SessionStore(str(tmp_path / "retry-unknown.db"))
    fake = _MutationConnection(commit_error=True)
    store._local.conn = fake
    with pytest.raises(SessionOutcomeUnknownError):
        store._mutate(lambda tx: tx.execute("INSERT INTO messages VALUES (?)", ("x",)), "test append")
    assert fake.dml_count == 1


def test_tool_execution_truth_survives_history_persistence_failure():
    from charlie.core import _mark_persistence_failure
    from charlie.turn_contracts import ResultEnvelope, ResultStatus

    envelope = ResultEnvelope(status=ResultStatus.COMPLETED.value, result="physical success")
    _mark_persistence_failure(envelope, SessionNotFoundError("deleted"))
    assert envelope.status == ResultStatus.COMPLETED.value
    assert envelope.result == "physical success"
    assert envelope.data["persistence_status"] == "failed"
    assert "session_not_found" in envelope.errors
