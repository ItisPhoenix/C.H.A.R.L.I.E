import asyncio
import threading

import pytest

import main
from charlie.attention import AttentionLevel
from charlie.subsystem_health import HealthRegistry, HealthStatus
from charlie.watchers import WatcherRegistry, start_watcher_thread


class _Store:
    def close(self):
        return None


class _Brain:
    async def close(self):
        return None

    def cancel_background_tasks(self):
        return []

    def cancel_chat(self):
        return None

    async def probe_primary_llm(self, timeout=5.0):
        return True


class _Voice:
    def __init__(self, order):
        self.order = order
        self.is_ready = False
        self.spoken = []

    def stop(self):
        self.order.append("voice_stop")

    def set_event_bus(self, _bus):
        return None

    def set_wake_word_callback(self, _callback):
        return None

    def speak(self, text, emotion="neutral"):
        self.spoken.append((text, emotion))

    def readiness_detail(self):
        return "fake voice"

    def asr_readiness_detail(self):
        return "fake asr"


class _Bus:
    def __init__(self, order):
        self.order = order
        self.events = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.order.append("event_bus_close")

    def set_state_listener(self, _callback):
        return None

    async def emit(self, event_type, *_args, **_kwargs):
        self.events.append(event_type)

    async def next_command(self):
        await asyncio.sleep(3600)
        return {}


class _WatcherThread:
    def __init__(self, order):
        self.order = order
        self.stopped = False
        self.join_timeouts = []

    def is_alive(self):
        return not self.stopped

    def join(self, timeout=None):
        self.join_timeouts.append(timeout)
        self.order.append("watcher_join")
        self.stopped = True


def _health() -> HealthRegistry:
    return HealthRegistry(
        (
            "brain",
            "llm",
            "plugins",
            "mcp",
            "web",
            "voice",
            "watchers",
            "companion",
            "telegram",
        )
    )


def test_watcher_stop_helper_sets_event_and_joins_promptly():
    stop_event = threading.Event()
    thread = start_watcher_thread(
        WatcherRegistry(),
        lambda *_args: None,
        poll_interval_s=60.0,
        stop_event=stop_event,
    )

    assert main._stop_watcher_thread(stop_event, thread, timeout=0.5) is True
    assert stop_event.is_set()
    assert not thread.is_alive()


def test_watcher_stop_helper_is_idempotent_and_bounded_for_stuck_thread():
    stop_event = threading.Event()
    join_timeouts = []

    class StuckThread:
        def is_alive(self):
            return True

        def join(self, timeout=None):
            join_timeouts.append(timeout)

    thread = StuckThread()
    assert main._stop_watcher_thread(stop_event, thread, timeout=0.01) is False
    assert main._stop_watcher_thread(stop_event, thread, timeout=0.02) is False
    assert stop_event.is_set()
    assert join_timeouts == [0.01, 0.02]


@pytest.mark.asyncio
async def test_main_stops_watcher_before_voice_and_eventbus_teardown_and_rejects_late_callback(monkeypatch):
    order = []
    captured = {}
    voice = _Voice(order)
    bus = _Bus(order)
    watcher_thread = _WatcherThread(order)

    monkeypatch.setattr(main, "_runtime_health", _health())
    monkeypatch.setattr(main, "SessionStore", lambda _path: _Store())
    monkeypatch.setattr("charlie.audit_store.AuditStore", lambda _path: _Store())
    monkeypatch.setattr(main, "_compose_memory_dependencies", lambda _config: (_Store(), None, object()))
    monkeypatch.setattr(main, "Brain", lambda *args, **kwargs: _Brain())
    monkeypatch.setattr(main, "_wire_memory_service", lambda _service: None)
    monkeypatch.setattr("charlie.plugins.PluginManager", lambda: object())
    monkeypatch.setattr("charlie.tools.register_plugin_tools", lambda _config: None)
    monkeypatch.setattr(main.config, "mcp_enabled", False)
    monkeypatch.setattr(main.config, "pet_enabled", False)
    monkeypatch.setattr(main, "_start_web_subprocess", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "_start_voice_or_degrade", lambda *args, **kwargs: voice)
    monkeypatch.setattr(main, "EventBus", lambda *args, **kwargs: bus)
    monkeypatch.setattr(main, "_log_port_release", lambda *args: None)

    def fake_start(_registry, callback, *, stop_event=None, **_kwargs):
        captured["callback"] = callback
        captured["stop_event"] = stop_event
        captured["thread"] = watcher_thread
        return watcher_thread

    monkeypatch.setattr(main, "start_watcher_thread", fake_start)

    async def drain(*_args, **_kwargs):
        order.append("eventbus_drain")

    monkeypatch.setattr(main, "_drain_event_bus_submissions", drain)
    original_gather = main.asyncio.gather

    async def fast_gather(*args, **kwargs):
        if kwargs.get("return_exceptions"):
            return await original_gather(*args, **kwargs)
        for arg in args:
            if asyncio.iscoroutine(arg):
                arg.close()
            elif isinstance(arg, asyncio.Task) and not arg.done():
                arg.cancel()
        return []

    monkeypatch.setattr(main.asyncio, "gather", fast_gather)
    monkeypatch.setattr(main, "_voice_loop_idle", lambda *_args, **_kwargs: asyncio.sleep(0))

    assert await main.main() == 0
    assert captured["stop_event"] is not None
    assert captured["stop_event"].is_set()
    assert captured["thread"] is watcher_thread
    assert not watcher_thread.is_alive()
    assert order.index("watcher_join") < order.index("voice_stop")
    assert order.index("watcher_join") < order.index("eventbus_drain")
    assert main._runtime_health.snapshot()["watchers"]["status"] == HealthStatus.STOPPED.value

    voice.spoken.clear()
    bus.events.clear()
    captured["callback"](
        {"type": "alert", "payload": {"message": "late watcher signal"}},
        AttentionLevel.ATTENTION,
        "late watcher signal",
    )
    assert voice.spoken == []
    assert bus.events == []


def test_startup_failure_health_stays_degraded_when_no_thread_started():
    health = _health()
    health.set("watchers", HealthStatus.DEGRADED)
    stop_event = threading.Event()

    assert main._stop_watcher_thread(stop_event, None) is True
    assert stop_event.is_set()
    assert health.snapshot()["watchers"]["status"] == HealthStatus.DEGRADED.value
