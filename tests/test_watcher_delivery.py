"""Watcher notification behavior against main.py's extracted callback."""

import ast
import asyncio
import logging
import textwrap
import threading
from pathlib import Path
from types import SimpleNamespace

from charlie.attention import AttentionLevel
from charlie.events import EventMeta, EventSource
from charlie.watchers import Watcher, WatcherRegistry, mcp_health_watcher, path_change_watcher

_MAIN_PATH = Path(__file__).resolve().parents[1] / "main.py"


class _TelegramSpy:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text):
        self.messages.append((chat_id, text))


class _EventBusSpy:
    def __init__(self):
        self.events = []

    async def emit(self, event_type, payload, *, meta=None):
        self.events.append((event_type, payload, meta))


def _watcher_callback(namespace):
    source = _MAIN_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    main_node = next(
        node for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "main"
    )
    callback_node = next(
        node for node in ast.walk(main_node)
        if isinstance(node, ast.FunctionDef) and node.name == "_on_watcher_signal"
    )
    callback_source = textwrap.dedent(ast.get_source_segment(source, callback_node))
    exec(compile(callback_source, "<main._on_watcher_signal>", "exec"), namespace)
    return namespace["_on_watcher_signal"]


def test_actionable_deduped_watcher_signal_sends_owner_metadata_dm(tmp_path):
    watched_file = tmp_path / "report.txt"
    private_body = "PRIVATE_FILE_BODY_SENTINEL"
    watched_file.write_text("baseline", encoding="utf-8")
    watcher = path_change_watcher([str(watched_file)], interval_s=0.0)
    assert watcher.check() is None  # Establish metadata baseline.

    watched_file.write_text(private_body + " with more bytes", encoding="utf-8")
    event = watcher.check()
    assert event is not None
    assert event["payload"]["signal"]["kind"] == "path_change"
    registry = WatcherRegistry()
    registry.register(Watcher(name="path_change_signal", interval_s=0.0, check=lambda: event))
    signals = registry.run_once(now=0.0)
    assert len(signals) == 1
    assert registry.run_once(now=1.0) == []  # Attention cooldown dedupes the repeated signal.

    event, level, reason = signals[0]
    assert private_body not in repr(event)
    telegram = _TelegramSpy()
    bus = _EventBusSpy()
    submissions = []

    def submit(coroutine, _loop):
        submissions.append(coroutine)

    callback = _watcher_callback({
        "AttentionLevel": AttentionLevel,
        "EventMeta": EventMeta,
        "EventSource": EventSource,
        "_submit_event_threadsafe": submit,
        "_watcher_loop": object(),
        "bus": bus,
        "config": SimpleNamespace(telegram_user_id=42),
        "telegram_bot": telegram,
        "voice": SimpleNamespace(is_ready=False),
        "watcher_callback_lock": threading.Lock(),
        "runtime_shutting_down": False,
        "logger": logging.getLogger(__name__),
    })

    callback(event, level, reason)

    async def drain_submissions():
        await asyncio.gather(*submissions)

    asyncio.run(drain_submissions())
    submissions.clear()

    assert len(bus.events) == 1
    assert len(telegram.messages) == 1
    chat_id, message = telegram.messages[0]
    assert chat_id == 42
    assert watched_file.name in message
    assert "size" in message.casefold()
    assert len(message) <= 500
    assert private_body not in message

    resource_watcher = mcp_health_watcher(lambda: {"example-mcp": False}, interval_s=0.0)
    resource_event = resource_watcher.check()
    assert resource_event is not None
    assert not (resource_event["payload"].get("signal") or {}).get("kind")
    resource_registry = WatcherRegistry()
    resource_registry.register(
        Watcher(name="mcp_health_signal", interval_s=0.0, check=lambda: resource_event)
    )
    resource_signals = resource_registry.run_once(now=2.0)
    assert len(resource_signals) == 1
    resource_event, resource_level, resource_reason = resource_signals[0]
    callback(resource_event, resource_level, resource_reason)
    asyncio.run(drain_submissions())

    assert len(bus.events) == 2
    assert len(telegram.messages) == 1
