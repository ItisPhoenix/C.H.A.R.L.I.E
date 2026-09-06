import asyncio
import time
from collections import OrderedDict
from pathlib import Path

import pytest

import charlie.core as core
from charlie.config import Config
from charlie.events import build_event
from charlie.resource_locks import default_lease_manager
from charlie.turn_contracts import ResultEnvelope, ResultStatus


def _brain() -> core.Brain:
    default_lease_manager.manual_takeover(["terminal"])
    return core.Brain(
        Config(llm_url="http://localhost:11434", llm_key="no-key", llm_model="dummy"),
        register_panic_hotkey=False,
    )


@pytest.mark.asyncio
async def test_terminal_operation_success_uses_brain_canonical_envelope(monkeypatch):
    brain = _brain()
    envelopes = []
    calls = []
    results = []
    persisted = []
    telemetry_calls = []
    brain.on_operation_result = lambda name, envelope: envelopes.append((name, envelope))
    brain.on_tool_call = lambda name, args, **_identity: calls.append((name, args))
    brain.on_tool_result = lambda name, result, **_identity: results.append((name, result))
    brain.session_store = type("Store", (), {"append_tool": lambda _self, **kwargs: persisted.append(kwargs)})()
    monkeypatch.setattr(brain, "request_tool_approval", _approve_approval)
    monkeypatch.setattr(core.tool_registry, "execute_tool", lambda name, args: "shell output")
    monkeypatch.setattr(
        core.telemetry,
        "record_tool_call",
        lambda name, success: telemetry_calls.append((name, success)),
    )

    try:
        result = await brain.execute_tool_operation(
            "shell_execute",
            {"command": "echo hi"},
            request="echo hi",
            task_id="terminal-request-1",
            session_id=None,
            platform="web",
        )
    finally:
        await brain.close()

    assert result.status == ResultStatus.COMPLETED
    assert result.task_id == "terminal-request-1"
    assert result.data["approval_status"] == "approved"
    assert calls[0][0] == "shell_execute"
    assert envelopes == [("shell_execute", result)]
    assert results == [("shell_execute", "shell output")]
    assert len(persisted) == 1
    assert telemetry_calls == [("shell_execute", True)]


@pytest.mark.asyncio
async def test_terminal_operation_rejection_does_not_execute(monkeypatch):
    brain = _brain()
    executed = []
    monkeypatch.setattr(brain, "request_tool_approval", _reject_approval)
    monkeypatch.setattr(core.tool_registry, "execute_tool", lambda *args: executed.append(args) or "wrong")

    try:
        result = await brain.execute_tool_operation(
            "shell_execute",
            {"command": "echo denied"},
            request="echo denied",
            task_id="terminal-request-rejected",
            session_id=None,
        )
    finally:
        await brain.close()

    assert result.status == ResultStatus.CANCELLED
    assert result.data["approval_status"] == "rejected"
    assert executed == []


@pytest.mark.asyncio
async def test_terminal_operation_hard_block_does_not_request_approval_or_execute(monkeypatch):
    brain = _brain()
    approvals = []
    executed = []

    async def fail_approval(*args, **kwargs):
        approvals.append((args, kwargs))
        raise AssertionError("hard block must not request approval")

    monkeypatch.setattr(brain, "request_tool_approval", fail_approval)
    monkeypatch.setattr(core.tool_registry, "execute_tool", lambda *args: executed.append(args) or "wrong")

    try:
        result = await brain.execute_tool_operation(
            "shell_execute",
            {"command": "echo hi && whoami"},
            request="echo hi && whoami",
            task_id="terminal-request-blocked",
            session_id=None,
        )
    finally:
        await brain.close()

    assert result.status == ResultStatus.BLOCKED
    assert result.data["approval_status"] == "blocked"
    assert approvals == []
    assert executed == []


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["timed_out", "unavailable"])
async def test_terminal_approval_outcomes_are_truthful(monkeypatch, decision):
    from charlie.core import ApprovalDecision

    brain = _brain()
    executed = []
    monkeypatch.setattr(
        brain,
        "_request_tool_approval_decision",
        lambda *_args, **_kwargs: _return_decision(ApprovalDecision(decision)),
    )
    monkeypatch.setattr(core.tool_registry, "execute_tool", lambda *args: executed.append(args) or "wrong")

    try:
        result = await brain.execute_tool_operation(
            "shell_execute",
            {"command": "echo approval"},
            request="echo approval",
            task_id=f"terminal-{decision}",
            session_id=None,
        )
    finally:
        await brain.close()

    assert result.status == ResultStatus.CANCELLED
    assert result.data["approval_status"] == decision
    assert executed == []


@pytest.mark.asyncio
async def test_terminal_operation_timeout_is_truthful(monkeypatch):
    brain = _brain()
    monkeypatch.setattr(core, "_tool_timeout", lambda *_args: 0.01)
    monkeypatch.setattr(brain, "request_tool_approval", _approve_approval)

    def slow_execute(*_args):
        time.sleep(0.05)
        return "late"

    async def no_recovery(*_args, **_kwargs):
        return None

    monkeypatch.setattr(core.tool_registry, "execute_tool", slow_execute)
    monkeypatch.setattr("charlie.recovery.recover_tool", no_recovery)

    try:
        result = await brain.execute_tool_operation(
            "shell_execute",
            {"command": "echo slow"},
            request="echo slow",
            task_id="terminal-request-timeout",
            session_id=None,
        )
    finally:
        await brain.close()

    assert result.status == ResultStatus.FAILED
    assert result.data["failure_kind"] == "timeout"
    assert result.data["approval_status"] == "approved"


@pytest.mark.asyncio
async def test_terminal_request_id_dedupes_inflight_and_completed_requests():
    from main import _handle_terminal_command_request

    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    class Brain:
        async def execute_tool_operation(self, *_args, **_kwargs):
            calls.append(1)
            started.set()
            await release.wait()
            return ResultEnvelope(
                request="echo once",
                task_id="terminal-request-dedupe",
                status=ResultStatus.COMPLETED,
                result="once",
                capability="terminal",
                operation="terminal.shell.execute",
                data={"approval_status": "approved"},
            )

    class Bus:
        def __init__(self):
            self.events = []

        async def emit(self, event_type, payload, meta=None):
            self.events.append(build_event(event_type, payload, meta=meta))

    bus = Bus()
    result_cache = OrderedDict()
    in_flight = {}
    first = asyncio.create_task(
        _handle_terminal_command_request(
            Brain(),
            bus,
            request_id="terminal-request-dedupe",
            terminal_session_id="primary",
            command="echo once",
            result_cache=result_cache,
            in_flight=in_flight,
        )
    )
    await started.wait()
    assert bus.events == []
    duplicate = asyncio.create_task(
        _handle_terminal_command_request(
            Brain(),
            bus,
            request_id="terminal-request-dedupe",
            terminal_session_id="primary",
            command="echo once",
            result_cache=result_cache,
            in_flight=in_flight,
        )
    )
    release.set()
    await first
    duplicate_result = await duplicate
    assert duplicate_result["status"] == "completed"

    replay = await _handle_terminal_command_request(
        Brain(),
        bus,
        request_id="terminal-request-dedupe",
        terminal_session_id="primary",
        command="echo once",
        result_cache=result_cache,
        in_flight=in_flight,
    )

    assert len(calls) == 1
    assert len(bus.events) == 3
    assert replay["status"] == "completed"
    assert bus.events[0]["payload"] == bus.events[1]["payload"] == bus.events[2]["payload"]
    assert bus.events[0]["payload"]["result"]["status"] == "completed"


def test_chat_loop_and_terminal_path_share_operation_primitive():
    source = Path(core.__file__).read_text(encoding="utf-8")
    assert source.count("self._execute_operation_primitive(") >= 2
    assert "def _finalize_operation_common(" in source


@pytest.mark.asyncio
async def test_shared_failure_finalization_records_world_model_once(monkeypatch):
    brain = _brain()
    failures = []
    monkeypatch.setattr(brain, "request_tool_approval", _approve_approval)
    monkeypatch.setattr(brain.world_model, "record_event", lambda *args: failures.append(args))
    monkeypatch.setattr(core.tool_registry, "execute_tool", lambda *_args: "Error: shell failed")
    monkeypatch.setattr("charlie.recovery.recover_tool", _no_recovery)

    try:
        result = await brain.execute_tool_operation(
            "shell_execute",
            {"command": "echo fail"},
            request="echo fail",
            task_id="terminal-world-error",
            session_id=None,
        )
    finally:
        await brain.close()

    assert result.status == ResultStatus.FAILED
    assert len(failures) == 1
    assert failures[0][0] == "tool_error"


@pytest.mark.asyncio
async def test_terminal_result_callback_failure_does_not_drop_result(monkeypatch):
    brain = _brain()
    monkeypatch.setattr(brain, "request_tool_approval", _approve_approval)
    monkeypatch.setattr(core.tool_registry, "execute_tool", lambda *_args: "shell output")

    def fail_callback(*_args, **_kwargs):
        raise RuntimeError("presentation callback failed")

    brain.on_tool_result = fail_callback
    try:
        result = await brain.execute_tool_operation(
            "shell_execute",
            {"command": "echo callback"},
            request="echo callback",
            task_id="terminal-callback-error",
            session_id=None,
        )
    finally:
        await brain.close()

    assert result.status == ResultStatus.COMPLETED


@pytest.mark.asyncio
async def test_approval_timeout_and_unavailable_are_distinct(monkeypatch):
    from charlie import recovery
    from charlie.core import ApprovalDecision

    brain = _brain()
    monkeypatch.setattr(recovery, "_event_bus", None)
    monkeypatch.setattr(recovery, "get_active_ws_count", lambda: 0)
    unavailable = await brain._request_tool_approval_decision(
        "shell_execute", {"command": "echo x"}, "approval", platform="web"
    )

    class Bus:
        async def emit(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(recovery, "_event_bus", Bus())
    monkeypatch.setattr(recovery, "get_active_ws_count", lambda: 1)
    brain._approval_timeout = 0.001
    timed_out = await brain._request_tool_approval_decision(
        "shell_execute", {"command": "echo x"}, "approval", platform="web"
    )
    await brain.close()

    assert unavailable is ApprovalDecision.UNAVAILABLE
    assert timed_out is ApprovalDecision.TIMED_OUT


@pytest.mark.asyncio
async def test_cancelled_approval_cleans_pending_future_and_voice_marker(monkeypatch):
    from charlie import recovery
    from charlie.core import get_active_voice_approval

    spoken = []
    brain = core.Brain(
        Config(llm_url="http://localhost:11434", llm_key="no-key", llm_model="dummy"),
        on_thought_callback=spoken.append,
        register_panic_hotkey=False,
    )
    monkeypatch.setattr(recovery, "_event_bus", None)
    monkeypatch.setattr(recovery, "get_active_ws_count", lambda: 0)
    task = asyncio.create_task(
        brain._request_tool_approval_decision(
            "shell_execute", {"command": "echo cancel"}, "approval", platform="voice"
        )
    )
    for _ in range(20):
        if get_active_voice_approval() is not None:
            break
        await asyncio.sleep(0)
    task.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        await brain.close()

    from charlie.core import pending_tool_approvals

    assert spoken
    assert pending_tool_approvals == {}
    assert get_active_voice_approval() is None


@pytest.mark.asyncio
async def test_terminal_unexpected_exception_emits_failed_result():
    from main import _handle_terminal_command_request

    class Brain:
        async def execute_tool_operation(self, *_args, **_kwargs):
            raise RuntimeError("unexpected terminal failure")

    class Bus:
        def __init__(self):
            self.events = []

        async def emit(self, event_type, payload, meta=None):
            self.events.append(build_event(event_type, payload, meta=meta))

    bus = Bus()
    cache = OrderedDict()
    result = await _handle_terminal_command_request(
        Brain(),
        bus,
        request_id="terminal-unexpected",
        terminal_session_id="primary",
        command="echo fail",
        result_cache=cache,
        in_flight={},
    )

    assert result["status"] == ResultStatus.FAILED
    assert result["result"]["data"]["failure_kind"] == "exception"
    assert bus.events[0]["payload"] == result


@pytest.mark.asyncio
async def test_terminal_cancellation_finalizes_and_propagates(monkeypatch):
    from charlie.core import OperationCancelled

    brain = _brain()
    monkeypatch.setattr(brain, "request_tool_approval", _approve_approval)

    def slow_execute(*_args):
        time.sleep(0.1)
        return "late"

    monkeypatch.setattr(core.tool_registry, "execute_tool", slow_execute)
    task = asyncio.create_task(
        brain.execute_tool_operation(
            "shell_execute",
            {"command": "echo cancel"},
            request="echo cancel",
            task_id="terminal-cancel",
            session_id=None,
        )
    )
    await asyncio.sleep(0.01)
    task.cancel()
    try:
        with pytest.raises(OperationCancelled) as exc_info:
            await task
    finally:
        await brain.close()

    assert exc_info.value.envelope.status == ResultStatus.CANCELLED
    assert exc_info.value.envelope.data["failure_kind"] == "cancellation"


def test_completed_terminal_result_cache_is_bounded():
    from main import _TERMINAL_RESULT_CACHE_MAX, _cache_terminal_result

    cache = OrderedDict()
    for index in range(_TERMINAL_RESULT_CACHE_MAX + 3):
        _cache_terminal_result(cache, f"request-{index}", {"request_id": f"request-{index}"})

    assert len(cache) == _TERMINAL_RESULT_CACHE_MAX
    assert "request-0" not in cache
    assert f"request-{_TERMINAL_RESULT_CACHE_MAX + 2}" in cache


async def _reject_approval(*_args, **_kwargs):
    return False


async def _approve_approval(*_args, **_kwargs):
    return True


async def _no_recovery(*_args, **_kwargs):
    return None


async def _return_decision(decision):
    return decision
