import sqlite3
from types import SimpleNamespace

import pytest

from charlie.background_task import BackgroundTask, _store_result
from charlie.results import ResultsStore


class FakeTelegram:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.messages = []

    async def send_message(self, chat_id, text):
        if self.fail:
            raise RuntimeError("offline")
        self.messages.append((chat_id, text))


class FakeVoice:
    def __init__(self, *, ready, result=None):
        self.is_ready = ready
        self.result = result
        self.spoken = []

    def speak(self, text, _emotion):
        self.spoken.append(text)
        return self.result


class FakeEventBus:
    def __init__(self, *, submitted=True):
        self.submitted = submitted
        self.events = []

    async def emit(self, event_type, payload, **_kwargs):
        self.events.append((event_type, payload))
        return self.submitted


def _store_record(path):
    store = ResultsStore(str(path))
    store.store("task-1", "Background task 'Report' done. Result: Findings", "Findings", 2)
    store.close()


@pytest.mark.asyncio
async def test_main_result_callback_reports_full_result_and_queues_ready_voice(tmp_path):
    from main import _deliver_background_result

    db_path = tmp_path / "results.sqlite3"
    _store_record(db_path)
    telegram, voice = FakeTelegram(), FakeVoice(ready=True)
    delivery = await _deliver_background_result(
        "task-1", "Background task 'Report' done. Result: Findings",
        db_path=str(db_path), telegram_bot=telegram, telegram_user_id=42, voice=voice,
    )

    assert delivery == {"telegram": "accepted", "voice": "queued"}
    assert telegram.messages[0][0] == 42
    assert "Findings" in telegram.messages[0][1]
    assert voice.spoken == ["Background task 'Report' done. Result: Findings"]


@pytest.mark.asyncio
async def test_telegram_failure_does_not_suppress_local_event_voice_or_result(tmp_path):
    from main import _deliver_background_result

    db_path = tmp_path / "results.sqlite3"
    telegram, voice, bus = FakeTelegram(fail=True), FakeVoice(ready=True), FakeEventBus()

    async def callback(task_id, summary, _attention_level):
        return await _deliver_background_result(
            task_id, summary, db_path=str(db_path), telegram_bot=telegram,
            telegram_user_id=42, voice=voice,
        )

    task = BackgroundTask(
        id="task-1", text="Report", status="done",
        brain=SimpleNamespace(config=SimpleNamespace(session_db_path=str(db_path)), on_result_stored=callback),
        session_id="session-1",
    )
    await _store_result(task, bus, "Findings")

    stored = ResultsStore(str(db_path))
    result = stored.get("task-1")
    stored.close()
    event = [payload for kind, payload in bus.events if kind == "result_stored"]
    assert result is not None and result.full_result == "Findings"
    assert result.telegram_status == "failed"
    assert result.voice_status == "queued"
    assert result.local_event_status == "submitted"
    assert event[0]["delivery"] == {"telegram": "failed", "voice": "queued"}
    assert voice.spoken


@pytest.mark.asyncio
async def test_event_failure_keeps_accepted_telegram_state_and_result(tmp_path):
    from main import _deliver_background_result

    db_path = tmp_path / "results.sqlite3"
    telegram, voice, bus = FakeTelegram(), FakeVoice(ready=False), FakeEventBus(submitted=False)

    async def callback(task_id, summary, _attention_level):
        return await _deliver_background_result(
            task_id, summary, db_path=str(db_path), telegram_bot=telegram,
            telegram_user_id=42, voice=voice,
        )

    task = BackgroundTask(
        id="task-1", text="Report", status="done",
        brain=SimpleNamespace(config=SimpleNamespace(session_db_path=str(db_path)), on_result_stored=callback),
        session_id="session-1",
    )
    await _store_result(task, bus, "Findings")

    stored = ResultsStore(str(db_path))
    result = stored.get("task-1")
    stored.close()
    assert result is not None and result.full_result == "Findings"
    assert result.telegram_status == "accepted"
    assert result.voice_status == "not_ready"
    assert result.local_event_status == "failed"
    assert len(telegram.messages) == 1
    assert voice.spoken == []


@pytest.mark.asyncio
async def test_unconfigured_channels_are_recorded_separately(tmp_path):
    from main import _deliver_background_result

    db_path = tmp_path / "results.sqlite3"
    _store_record(db_path)
    delivery = await _deliver_background_result(
        "task-1", "summary", db_path=str(db_path), telegram_bot=None,
        telegram_user_id=0, voice=None,
    )
    assert delivery == {"telegram": "not_configured", "voice": "not_configured"}


@pytest.mark.asyncio
async def test_suppressed_voice_speech_is_recorded_as_not_queued(tmp_path):
    from main import _deliver_background_result

    db_path = tmp_path / "results.sqlite3"
    _store_record(db_path)
    voice = FakeVoice(ready=True, result="")
    delivery = await _deliver_background_result(
        "task-1", "summary", db_path=str(db_path), telegram_bot=None,
        telegram_user_id=0, voice=voice,
    )
    assert delivery == {"telegram": "not_configured", "voice": "not_queued"}
    assert voice.spoken == ["summary"]


@pytest.mark.asyncio
async def test_failed_result_insert_skips_callback_and_result_event(tmp_path):
    db_path = tmp_path / "results.sqlite3"
    store = ResultsStore(str(db_path))
    store.conn.execute(
        "CREATE TRIGGER reject_task_result BEFORE INSERT ON task_results "
        "BEGIN SELECT RAISE(ABORT, 'test insert failure'); END"
    )
    store.conn.commit()
    assert store.store("task-1", "summary", "Findings", 2) is False
    store.close()

    callback_calls = []

    async def callback(*args):
        callback_calls.append(args)
        return {"telegram": "accepted", "voice": "queued"}

    task = BackgroundTask(
        id="task-1", text="Report", status="done",
        brain=SimpleNamespace(config=SimpleNamespace(session_db_path=str(db_path)), on_result_stored=callback),
        session_id="session-1",
    )
    bus = FakeEventBus()
    await _store_result(task, bus, "Findings")

    stored = ResultsStore(str(db_path))
    assert stored.get("task-1") is None
    stored.close()
    assert callback_calls == []
    assert bus.events == []


def test_results_store_migrates_delivery_columns_with_truthful_defaults(tmp_path):
    path = tmp_path / "legacy-results.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE task_results (id INTEGER PRIMARY KEY, task_id TEXT NOT NULL, summary TEXT NOT NULL, "
        "full_result TEXT NOT NULL, attention_level INTEGER NOT NULL, seen INTEGER NOT NULL DEFAULT 0, "
        "created_at TEXT)"
    )
    connection.execute(
        "INSERT INTO task_results (task_id, summary, full_result, attention_level) VALUES (?, ?, ?, ?)",
        ("task-old", "summary", "result", 2),
    )
    connection.commit()
    connection.close()

    store = ResultsStore(str(path))
    record = store.get("task-old")
    store.close()
    assert record is not None
    assert record.telegram_status == "not_configured"
    assert record.local_event_status == "not_configured"
    assert record.voice_status == "not_configured"
