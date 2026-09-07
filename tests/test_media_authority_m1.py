from __future__ import annotations

import asyncio
import threading
from collections import OrderedDict
from types import SimpleNamespace

import pytest

import main
from charlie import capabilities, fastpaths, tools, web_server
from charlie.turn_contracts import ResultEnvelope, ResultStatus


class _Adapter:
    def __init__(self):
        self.calls = []

    async def snapshot(self):
        return {"available": True, "volume_percent": 42, "muted": False, "status": "playing"}

    async def control(self, action, percent=None):
        self.calls.append((action, percent))
        return {"ok": True, "available": True, "volume_percent": 55, "muted": action == "mute"}


def test_media_capability_is_registered_with_media_lease():
    media_op = capabilities.get_capability_index().get_operation("media_control")
    alias_op = capabilities.get_capability_index().get_operation("system_control")
    snapshot_op = capabilities.get_capability_index().get_operation("media_snapshot")

    assert media_op is not None
    assert capabilities.get_capability_index().get_operation_domain("media_control") == "media"
    assert media_op.required_leases == ("media",)
    assert media_op.id == "media.control.execute"
    assert alias_op is not None
    assert alias_op.id == "media.control.compatibility"
    assert not capabilities.get_capability_index().is_tool_registered("system_control")
    assert snapshot_op is not None
    assert snapshot_op.id == "media.snapshot.read"
    assert snapshot_op.required_leases == ("media",)


def test_media_fastpaths_are_matchers_only():
    set_match = fastpaths.match_media_volume("set volume to 45%")
    mute_match = fastpaths.match_media_volume("mute audio")
    play_match = fastpaths.match_media_volume("pause music")

    assert set_match is not None
    assert set_match.tool_name == "media_control"
    assert set_match.direct_handler is None
    assert set_match.arguments == {"action": "set_volume", "percent": 45}
    assert mute_match is not None and mute_match.tool_name == "media_control"
    assert mute_match.semantic_op_id == "media.mute.set"
    assert play_match is not None and play_match.tool_name == "media_control"
    assert play_match.semantic_op_id == "media.playback.toggle"
    assert play_match.direct_handler is None

    assert fastpaths.match_media_volume("set volume to -1%").arguments == {
        "action": "set_volume",
        "percent": -1,
    }
    assert fastpaths.match_media_volume("set volume to 150%").arguments == {
        "action": "set_volume",
        "percent": 150,
    }


@pytest.mark.asyncio
async def test_media_adapter_volume_and_mute_are_set_semantics_with_readback(monkeypatch):
    from charlie import media_adapter

    class Endpoint:
        def __init__(self):
            self.scalar = 0.40
            self.muted = False

        def GetMasterVolumeLevelScalar(self):
            return self.scalar

        def SetMasterVolumeLevelScalar(self, scalar, _context):
            self.scalar = scalar

        def GetMute(self):
            return self.muted

        def SetMute(self, muted, _context):
            self.muted = bool(muted)

    endpoint = Endpoint()
    monkeypatch.setattr(
        media_adapter,
        "AudioUtilities",
        SimpleNamespace(GetSpeakers=lambda: SimpleNamespace(EndpointVolume=endpoint)),
    )
    monkeypatch.setattr(media_adapter, "_audio_endpoint", None)
    adapter = media_adapter.WindowsMediaAdapter()

    set_result = await adapter.control("set_volume", percent=73)
    assert set_result["ok"] is True
    assert set_result["verified"] is True
    assert set_result["volume_percent"] == 73

    up_result = await adapter.control("volume_up")
    assert up_result["volume_percent"] == 78

    first_mute = await adapter.control("mute")
    second_mute = await adapter.control("mute")
    unmute = await adapter.control("unmute")
    assert first_mute["muted"] is True
    assert second_mute["muted"] is True
    assert unmute["muted"] is False


@pytest.mark.asyncio
async def test_media_adapter_reacquires_stale_audio_endpoint(monkeypatch):
    from charlie import media_adapter

    class StaleEndpoint:
        def GetMasterVolumeLevelScalar(self):
            raise RuntimeError("stale endpoint")

        def GetMute(self):
            raise RuntimeError("stale endpoint")

    class FreshEndpoint:
        def GetMasterVolumeLevelScalar(self):
            return 0.62

        def GetMute(self):
            return False

    fresh = FreshEndpoint()
    speakers = iter(
        [
            SimpleNamespace(EndpointVolume=StaleEndpoint()),
            SimpleNamespace(EndpointVolume=fresh),
        ]
    )
    monkeypatch.setattr(media_adapter, "AudioUtilities", SimpleNamespace(GetSpeakers=lambda: next(speakers)))
    monkeypatch.setattr(media_adapter, "_audio_endpoint", None)

    assert media_adapter._volume_snapshot() == {"volume_percent": 62, "muted": False}


@pytest.mark.asyncio
async def test_media_adapter_playback_uses_winrt_and_reports_unverified_postcondition(monkeypatch):
    from charlie import media_adapter

    calls = []

    class Session:
        async def try_toggle_play_pause_async(self):
            calls.append("play_pause")
            return True

        async def try_skip_next_async(self):
            calls.append("next_track")
            return True

        async def try_skip_previous_async(self):
            calls.append("prev_track")
            return True

        async def try_stop_async(self):
            calls.append("stop")
            return True

    class Manager:
        @staticmethod
        async def request_async():
            return Manager()

        def get_current_session(self):
            return Session()

    monkeypatch.setattr(media_adapter, "_manager_type", Manager)
    adapter = media_adapter.WindowsMediaAdapter()
    for action in ("play_pause", "next_track", "prev_track", "stop"):
        result = await adapter.control(action)
        assert result["ok"] is True
        assert result["verified"] is False
    assert calls == ["play_pause", "next_track", "prev_track", "stop"]


@pytest.mark.asyncio
async def test_brain_media_snapshot_and_mutation_share_dedicated_worker(monkeypatch):
    from charlie.config import Config
    from charlie.core import Brain
    from charlie.media_runtime import media_executor_thread_id

    worker_threads = []

    class Adapter:
        async def snapshot(self):
            worker_threads.append(threading.get_ident())
            return {"available": True, "adapter_available": True, "volume_percent": 40, "muted": False}

        async def control(self, action, percent=None):
            worker_threads.append(threading.get_ident())
            return {"ok": True, "available": True, "verified": True, "volume_percent": 45, "muted": False}

    monkeypatch.setattr(tools, "_get_media_adapter", lambda: Adapter())
    brain = Brain(Config(llm_url="https://example.com/v1", llm_key="test-key", llm_model="dummy"))
    loop_thread = threading.get_ident()

    snapshot = await brain.execute_tool_operation(
        "media_snapshot", {}, request="snapshot", task_id="snapshot-worker", session_id=None
    )
    mutation = await brain.execute_tool_operation(
        "media_control", {"action": "volume_up"}, request="volume up", task_id="mutation-worker", session_id=None
    )

    assert snapshot.operation == "media.snapshot.read"
    assert mutation.operation == "media.control.execute"
    assert len(worker_threads) == 2
    assert worker_threads[0] == worker_threads[1] == media_executor_thread_id()
    assert worker_threads[0] != loop_thread


@pytest.mark.asyncio
async def test_media_lease_serializes_snapshot_and_mutation(monkeypatch):
    from charlie.config import Config
    from charlie.core import Brain

    state = {"active": 0, "maximum": 0}
    guard = threading.Lock()

    class Adapter:
        async def snapshot(self):
            with guard:
                state["active"] += 1
                state["maximum"] = max(state["maximum"], state["active"])
            await asyncio.sleep(0.02)
            with guard:
                state["active"] -= 1
            return {"available": True, "adapter_available": True, "volume_percent": 40, "muted": False}

        async def control(self, action, percent=None):
            return await self.snapshot()

    monkeypatch.setattr(tools, "_get_media_adapter", lambda: Adapter())
    brain = Brain(Config(llm_url="https://example.com/v1", llm_key="test-key", llm_model="dummy"))
    await asyncio.gather(
        brain.execute_tool_operation("media_snapshot", {}, request="snapshot", task_id="snapshot", session_id=None),
        brain.execute_tool_operation(
            "media_control", {"action": "volume_up"}, request="volume", task_id="mutation", session_id=None
        ),
    )
    assert state["maximum"] == 1


def test_windows_settings_fastpath_has_truthful_operation():
    match = fastpaths.match_windows_settings("open bluetooth settings")
    assert match is not None
    assert match.tool_name == "open_windows_settings"
    assert match.semantic_op_id == "system.settings.open"
    assert match.direct_handler is None


def test_media_tool_rejects_out_of_range_and_unsupported_without_adapter(monkeypatch):
    calls = []

    class Adapter:
        async def control(self, action, percent=None):
            calls.append((action, percent))
            return {"ok": True}

    monkeypatch.setattr(tools, "_get_media_adapter", lambda: Adapter())
    for percent in (-1, 101, 150):
        result = tools.registry.execute_tool_structured("media_control", {"action": "set_volume", "percent": percent})
        assert result.structured_data["failure_kind"] == "invalid_arguments"
    unsupported = tools.registry.execute_tool_structured("media_control", {"action": "karaoke"})
    assert unsupported.structured_data["failure_kind"] == "unsupported"
    assert calls == []


@pytest.mark.asyncio
async def test_web_media_result_requires_matching_fingerprint():
    loop = asyncio.get_running_loop()
    request_id = "fingerprint-check"
    future = loop.create_future()
    web_server._pending_media_operations[request_id] = future
    web_server._pending_media_fingerprints[request_id] = '{"action":"mute","operation":"control"}'
    try:
        web_server._resolve_media_operation_result(
            {
                "request_id": request_id,
                "request_fingerprint": '{"action":"volume_up","operation":"control"}',
                "operation": "control",
                "status": "completed",
                "result": {},
            }
        )
        assert future.done() is False
        web_server._resolve_media_operation_result(
            {
                "request_id": request_id,
                "request_fingerprint": '{"action":"mute","operation":"control"}',
                "operation": "control",
                "status": "completed",
                "result": {},
            }
        )
        assert future.done() is True
    finally:
        web_server._pending_media_operations.pop(request_id, None)
        web_server._pending_media_fingerprints.pop(request_id, None)


@pytest.mark.asyncio
async def test_media_request_id_replays_without_duplicate_physical_execution(monkeypatch):
    calls = []
    envelope = ResultEnvelope(
        request="media control: volume_up",
        task_id="media-duplicate",
        status=ResultStatus.COMPLETED,
        result="Volume adjusted to 45%.",
        capability="media",
        operation="media.volume.adjust",
    )

    class Brain:
        async def execute_tool_operation(self, *_args, **_kwargs):
            calls.append(1)
            return envelope

    class Bus:
        def __init__(self):
            self.events = []

        async def emit(self, event_type, payload, meta=None):
            self.events.append((event_type, payload, meta))

    bus = Bus()
    cache = OrderedDict()
    in_flight = {}
    fingerprints = {}
    first = await main._handle_media_operation_request(
        Brain(),
        bus,
        {
            "request_id": "same-media",
            "operation": "control",
            "action": "volume_up",
            "request_fingerprint": '{"action":"volume_up","operation":"control"}',
        },
        result_cache=cache,
        in_flight=in_flight,
        fingerprint_cache=fingerprints,
    )
    replay = await main._handle_media_operation_request(
        Brain(),
        bus,
        {
            "request_id": "same-media",
            "operation": "control",
            "action": "volume_up",
            "request_fingerprint": '{"action":"volume_up","operation":"control"}',
        },
        result_cache=cache,
        in_flight=in_flight,
        fingerprint_cache=fingerprints,
    )
    conflict = await main._handle_media_operation_request(
        Brain(),
        bus,
        {
            "request_id": "same-media",
            "operation": "control",
            "action": "mute",
            "request_fingerprint": '{"action":"mute","operation":"control"}',
        },
        result_cache=cache,
        in_flight=in_flight,
        fingerprint_cache=fingerprints,
    )

    assert calls == [1]
    assert replay == first
    assert conflict["status"] == "request_id_conflict"


@pytest.mark.asyncio
async def test_media_fastpath_calls_shared_brain_operation(monkeypatch):
    from charlie.config import Config
    from charlie.core import Brain

    brain = Brain(Config(llm_url="https://example.com/v1", llm_key="test-key", llm_model="dummy"))
    calls = []

    async def execute(tool_name, arguments, **kwargs):
        calls.append((tool_name, arguments, kwargs.get("operation_override")))
        return ResultEnvelope(
            request="set volume to 45%",
            status=ResultStatus.COMPLETED,
            result="Volume set to 45%.",
            capability="media",
            operation=kwargs.get("operation_override"),
        )

    monkeypatch.setattr(brain, "execute_tool_operation", execute)
    monkeypatch.setattr(
        "charlie.fastpaths.execute_fast_path",
        lambda _match: (_ for _ in ()).throw(AssertionError("direct media fastpath")),
    )

    result = "".join([chunk async for chunk in brain.chat_stream("set volume to 45%", platform="voice")])
    assert result == "Volume set to 45%."
    assert calls == [("media_control", {"action": "set_volume", "percent": 45}, "media.volume.set")]


@pytest.mark.asyncio
async def test_media_tool_uses_adapter_and_returns_structured_readback(monkeypatch):
    adapter = _Adapter()
    monkeypatch.setattr(tools, "_get_media_adapter", lambda: adapter)

    result = await asyncio.to_thread(
        tools.registry.execute_tool_structured,
        "media_control",
        {"action": "volume_up"},
    )

    assert adapter.calls == [("volume_up", None)]
    assert result.structured_data["volume_percent"] == 55
    assert result.result_kind == "media_control"


@pytest.mark.asyncio
async def test_media_snapshot_is_main_owned(monkeypatch):
    bus_events = []

    class Brain:
        async def execute_tool_operation(self, *_args, **_kwargs):
            return ResultEnvelope(
                request="read media snapshot",
                task_id="snapshot-1",
                status=ResultStatus.COMPLETED,
                result="Current media session state read.",
                capability="media",
                operation="media.snapshot.read",
                data={"structured_data": {"available": True, "volume_percent": 42, "muted": False}},
            )

    class Bus:
        async def emit(self, event_type, payload, meta=None):
            bus_events.append((event_type, payload, meta))

    await main._handle_media_operation_request(
        Brain(),
        Bus(),
        {
            "request_id": "snapshot-1",
            "operation": "snapshot",
            "request_fingerprint": '{"operation":"snapshot"}',
        },
    )

    assert bus_events[0][0] == "media_operation_result"
    assert bus_events[0][1]["status"] == "completed"
    assert bus_events[0][1]["result"]["data"]["structured_data"]["volume_percent"] == 42


@pytest.mark.asyncio
async def test_main_media_control_emits_canonical_result(monkeypatch):
    envelope = ResultEnvelope(
        request="media control: mute",
        task_id="media-1",
        status=ResultStatus.COMPLETED,
        result="Audio is now muted (volume 55%).",
        capability="media",
        operation="media.control.execute",
        data={"structured_data": {"ok": True, "muted": True}},
    )

    class Brain:
        async def execute_tool_operation(self, *_args, **_kwargs):
            return envelope

    events = []

    class Bus:
        async def emit(self, event_type, payload, meta=None):
            events.append((event_type, payload, meta))

    await main._handle_media_operation_request(
        Brain(),
        Bus(),
        {
            "request_id": "media-1",
            "operation": "control",
            "action": "mute",
            "request_fingerprint": '{"action":"mute","operation":"control"}',
        },
    )

    assert events[0][0] == "media_operation_result"
    assert events[0][1]["status"] == "completed"
    assert events[0][1]["result"]["operation"] == "media.control.execute"


@pytest.mark.asyncio
async def test_web_media_control_forwards_to_main_without_adapter(monkeypatch):
    sent = []

    class Bus:
        async def send_command(self, command):
            sent.append(command)
            web_server._resolve_media_operation_result(
                {
                    "request_id": command["payload"]["request_id"],
                    "request_fingerprint": command["payload"]["request_fingerprint"],
                    "operation": "control",
                    "status": "completed",
                    "result": {"status": "completed", "result": "muted"},
                }
            )
            return True

    monkeypatch.setattr(web_server, "event_bus", Bus())
    result = await web_server.media_control({"action": "mute"})

    assert not hasattr(web_server, "_media_adapter")
    assert sent[0]["type"] == "media_operation"
    assert sent[0]["payload"]["operation"] == "control"
    assert sent[0]["payload"]["action"] == "mute"
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_web_media_snapshot_projects_main_result(monkeypatch):
    class Bus:
        async def send_command(self, command):
            web_server._resolve_media_operation_result(
                {
                    "request_id": command["payload"]["request_id"],
                    "request_fingerprint": command["payload"]["request_fingerprint"],
                    "operation": "snapshot",
                    "status": "completed",
                    "result": {
                        "operation": "media.snapshot.read",
                        "data": {"structured_data": {"available": True, "volume_percent": 61, "muted": False}},
                    },
                }
            )
            return True

    monkeypatch.setattr(web_server, "event_bus", Bus())
    snapshot = await web_server.media_snapshot()

    assert snapshot == {"available": True, "volume_percent": 61, "muted": False}
