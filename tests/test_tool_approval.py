"""Tests for gated-tool approval and Web approval IPC concurrency."""

import ast
import asyncio
import textwrap
from collections import OrderedDict
from pathlib import Path

import pytest

import main
from charlie import recovery
from charlie.config import Config
from charlie.core import (
    Brain,
    get_active_voice_approval,
    pending_tool_approvals,
    resolve_tool_approval,
)
from charlie.session_store import canonical_session_request_fingerprint
from charlie.turn_contracts import TurnRequest


class _StopWebCommandLoop(BaseException):
    pass


class _SilentLogger:
    def debug(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class _SessionStore:
    def get_session_record(self, session_id):
        return {"session_id": session_id, "launch_id": None}


class _BackgroundTasks:
    @staticmethod
    def list_tasks():
        return []


class _CommandBus:
    def __init__(self, commands):
        self.commands = list(commands)
        self.events = []

    async def next_command(self):
        if not self.commands:
            raise _StopWebCommandLoop
        command = self.commands.pop(0)
        if callable(command):
            return await command()
        return command

    async def emit(self, event_type, payload, **_kwargs):
        self.events.append((event_type, payload))


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


def _chat_command(command_type, request_id, *, session_id="web-session", text="gated request"):
    payload = {
        "session_id": session_id,
        "text": text,
        "request_id": request_id,
    }
    payload["request_fingerprint"] = canonical_session_request_fingerprint("chat", payload)
    return {"type": command_type, "payload": payload}


def _load_web_consumer(submit, resolver, dispatch, *, runtime_shutting_down=False):
    namespace = vars(main).copy()
    namespace.update(
        {
            "_submit_event_task": submit,
            "_resolve_tool_approval_and_notify": resolver,
            "_dispatch_or_queue": dispatch,
            "logger": _SilentLogger(),
            "store_obj": _SessionStore(),
            "background_obj": _BackgroundTasks(),
            "OrderedDict": OrderedDict,
        }
    )
    wrapper_source = (
        "def _wrapper():\n"
        "    current_web_session_id = 'initial-session'\n"
        "    _voice_fallback_session_id = 'voice-fallback'\n"
        "    voice = None\n"
        "    mcp_client = None\n"
        "    store = store_obj\n"
        "    background_task = background_obj\n"
        "    privacy_operation_results = OrderedDict()\n"
        "    privacy_operation_in_flight = {}\n"
        "    privacy_operation_fingerprints = {}\n"
        "    session_operation_results = OrderedDict()\n"
        "    session_operation_in_flight = {}\n"
        "    session_operation_fingerprints = {}\n"
        "    pending_turns = []\n"
        "    active_turn_session_id = None\n"
        f"    runtime_shutting_down = {runtime_shutting_down!r}\n"
        "    session_lifecycle_gate = asyncio.Lock()\n"
        + textwrap.indent(_consume_web_commands_source(), "    ")
        + "\n    return consume_web_commands\n"
    )
    exec(compile(wrapper_source, "<main.consume_web_commands>", "exec"), namespace)
    return namespace["_wrapper"]()


async def _wait_for_approval_event(event_bus):
    while not event_bus.events:
        await asyncio.sleep(0)
    return event_bus.events[0][1]["request_id"]


@pytest.fixture
def brain_config():
    return Config(
        llm_url="http://localhost:11434",
        llm_key="no-key",
        llm_model="dummy",
        iteration_budget_max=3,
    )


@pytest.fixture(autouse=True)
def _no_active_ws(monkeypatch):
    """Deterministic no-dashboard-connected state regardless of test order."""
    monkeypatch.setattr(recovery, "_active_ws_count", 0)
    monkeypatch.setattr(recovery, "_event_bus", None)
    yield
    pending_tool_approvals.clear()


@pytest.mark.asyncio
async def test_web_chat_approval_keeps_command_consumer_responsive_and_effect_once():
    registry = main._EventBusSubmissionRegistry()
    submitted = []
    registered = asyncio.Event()
    effect_done = asyncio.Event()
    effects = []
    approval_id = "approval-web-match"

    def submit(coroutine):
        task = registry.submit_task(coroutine, asyncio.get_running_loop())
        submitted.append(task)
        return task

    async def dispatch(request: TurnRequest):
        future = asyncio.get_running_loop().create_future()
        pending_tool_approvals[approval_id] = future
        registered.set()
        try:
            approved = await future
            assert effects == []
            if approved:
                effects.append(request.turn_id)
            effect_done.set()
        finally:
            pending_tool_approvals.pop(approval_id, None)

    resolved = []

    def resolver(request_id, approved):
        resolved.append((request_id, approved))
        return resolve_tool_approval(request_id, approved)

    tracked_while_waiting = []

    async def unknown_approval():
        await registered.wait()
        tracked_while_waiting.extend(registry.snapshot()[0])
        return {"type": "tool_approve", "payload": {"request_id": "unknown"}}

    async def matching_approval():
        await registered.wait()
        return {"type": "tool_approve", "payload": {"request_id": approval_id}}

    async def late_duplicate():
        await effect_done.wait()
        return {"type": "tool_approve", "payload": {"request_id": approval_id}}

    async def stop_after_duplicate():
        await effect_done.wait()
        raise _StopWebCommandLoop

    bus = _CommandBus(
        [
            _chat_command("chat", "web-chat-admission"),
            unknown_approval,
            matching_approval,
            late_duplicate,
            stop_after_duplicate,
        ]
    )
    consumer = _load_web_consumer(submit, resolver, dispatch)
    try:
        with pytest.raises(_StopWebCommandLoop):
            await consumer(bus, None)

        assert len(submitted) == 1
        assert tracked_while_waiting == submitted
        assert resolved == [("unknown", True), (approval_id, True), (approval_id, True)]
        assert len(effects) == 1
        assert pending_tool_approvals == {}
        result_events = [payload for event, payload in bus.events if event == "session_operation_result"]
        assert result_events and result_events[0]["status"] == "accepted"
        assert result_events[0]["result"]["turn_id"] == effects[0]
    finally:
        for future in pending_tool_approvals.values():
            if not future.done():
                future.set_result(False)
        registry.close()
        await main._drain_event_bus_submissions(registry, timeout=1)
        pending_tool_approvals.clear()


@pytest.mark.asyncio
async def test_web_chat_rejection_prevents_effect_and_cleans_approval_state():
    registry = main._EventBusSubmissionRegistry()
    submitted = []
    registered = asyncio.Event()
    effects = []
    approval_id = "approval-web-reject"

    def submit(coroutine):
        task = registry.submit_task(coroutine, asyncio.get_running_loop())
        submitted.append(task)
        return task

    async def dispatch(_request):
        future = asyncio.get_running_loop().create_future()
        pending_tool_approvals[approval_id] = future
        registered.set()
        try:
            if await future:
                effects.append("executed")
        finally:
            pending_tool_approvals.pop(approval_id, None)

    def resolver(request_id, approved):
        return resolve_tool_approval(request_id, approved)

    async def reject():
        await registered.wait()
        return {"type": "tool_reject", "payload": {"request_id": approval_id}}

    async def stop():
        await registered.wait()
        while pending_tool_approvals:
            await asyncio.sleep(0)
        raise _StopWebCommandLoop

    bus = _CommandBus([_chat_command("chat", "web-chat-reject"), reject, stop])
    consumer = _load_web_consumer(submit, resolver, dispatch)
    try:
        with pytest.raises(_StopWebCommandLoop):
            await consumer(bus, None)
        assert len(submitted) == 1
        assert effects == []
        assert pending_tool_approvals == {}
    finally:
        registry.close()
        await main._drain_event_bus_submissions(registry, timeout=1)
        pending_tool_approvals.clear()


@pytest.mark.asyncio
async def test_http_session_chat_reports_admission_before_turn_completes_and_tracks_turn():
    registry = main._EventBusSubmissionRegistry()
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()
    submitted_requests = []

    def submit(coroutine):
        return registry.submit_task(coroutine, asyncio.get_running_loop())

    async def dispatch(request):
        submitted_requests.append(request)
        started.set()
        await release.wait()
        finished.set()

    async def stop_after_acceptance():
        while not any(event == "session_operation_result" for event, _ in bus.events):
            await asyncio.sleep(0)
        await started.wait()
        assert not finished.is_set()
        raise _StopWebCommandLoop

    bus = _CommandBus([_chat_command("session_chat", "http-chat-admission"), stop_after_acceptance])
    consumer = _load_web_consumer(submit, lambda *_args: None, dispatch)
    try:
        with pytest.raises(_StopWebCommandLoop):
            await consumer(bus, None)
        result = next(payload for event, payload in bus.events if event == "session_operation_result")
        assert result["status"] == "accepted"
        assert result["result"]["turn_id"] == submitted_requests[0].turn_id
        tracked, _ = registry.snapshot()
        assert tracked
        release.set()
        await asyncio.wait_for(finished.wait(), 1)
    finally:
        release.set()
        registry.close()
        await main._drain_event_bus_submissions(registry, timeout=1)


@pytest.mark.asyncio
async def test_http_session_chat_returns_correlated_admission_result(monkeypatch):
    import charlie.web_server as web_server

    class Bus:
        async def send_command(self, command):
            payload = command["payload"]
            web_server._resolve_session_operation_result(
                {
                    "request_id": payload["request_id"],
                    "request_fingerprint": payload["request_fingerprint"],
                    "operation": "chat",
                    "status": "accepted",
                    "result": {"ok": True, "session_id": payload["session_id"], "turn_id": "canonical-turn"},
                }
            )
            return True

    monkeypatch.setattr(web_server, "event_bus", Bus())
    monkeypatch.setattr(web_server, "_completed_session_operations", OrderedDict())
    monkeypatch.setattr(web_server, "_completed_session_fingerprints", {})
    result = await web_server.session_chat(
        "http-session", {"text": "hello", "request_id": "http-route-admission"}
    )

    assert result["status"] == "accepted"
    assert result["request_id"] == "http-route-admission"
    assert result["result"]["turn_id"] == "canonical-turn"


@pytest.mark.asyncio
async def test_web_chat_submission_failure_is_not_reported_as_accepted():
    submitted = []

    def dispatch(request):
        submitted.append(request)

        async def never_submitted():
            return None

        return never_submitted()

    def reject_submission(coroutine):
        coroutine.close()
        return None

    async def stop():
        raise _StopWebCommandLoop

    bus = _CommandBus([_chat_command("session_chat", "closed-chat"), stop])
    consumer = _load_web_consumer(reject_submission, lambda *_args: None, dispatch, runtime_shutting_down=True)
    with pytest.raises(_StopWebCommandLoop):
        await consumer(bus, None)

    result = next(payload for event, payload in bus.events if event == "session_operation_result")
    assert result["status"] == "shutting_down"
    assert result["result"]["failure_kind"] == "runtime_shutting_down"
    assert submitted and result["result"].get("turn_id") is None


@pytest.mark.asyncio
async def test_web_chat_admission_failure_does_not_change_active_session_projection(monkeypatch):
    active_session_updates = []
    monkeypatch.setattr(recovery, "set_active_session_id", active_session_updates.append)

    async def stop():
        raise _StopWebCommandLoop

    bus = _CommandBus([_chat_command("chat", "closed-ws-chat", session_id="candidate-session"), stop])
    consumer = _load_web_consumer(
        lambda coroutine: (coroutine.close() or None),
        lambda *_args: None,
        lambda _request: asyncio.sleep(0),
        runtime_shutting_down=True,
    )
    with pytest.raises(_StopWebCommandLoop):
        await consumer(bus, None)

    result = next(payload for event, payload in bus.events if event == "session_operation_result")
    assert result["status"] == "shutting_down"
    assert active_session_updates == []


@pytest.mark.asyncio
async def test_web_approval_rejection_is_fail_closed_and_late_response_is_harmless(
    brain_config, monkeypatch
):
    class ApprovalBus:
        def __init__(self):
            self.events = []

        async def emit(self, event_type, payload, **_kwargs):
            self.events.append((event_type, payload))

    bus = ApprovalBus()
    monkeypatch.setattr(recovery, "_active_ws_count", 1)
    monkeypatch.setattr(recovery, "_event_bus", bus)
    brain = Brain(brain_config, register_panic_hotkey=False, approval_timeout=1)
    task = asyncio.create_task(
        brain.request_tool_approval(
            "shell_execute", {"command": "safe-test"}, "test rejection", platform="web", risk_class="destructive"
        )
    )
    request_id = await _wait_for_approval_event(bus)
    assert request_id in pending_tool_approvals
    assert resolve_tool_approval(request_id, False) is True
    assert await task is False
    assert pending_tool_approvals == {}
    assert resolve_tool_approval(request_id, True) is False
    assert resolve_tool_approval("unknown-web-id", True) is False
    await brain.close()


@pytest.mark.asyncio
async def test_web_approval_timeout_prevents_effect_and_removes_pending_state(brain_config, monkeypatch):
    class ApprovalBus:
        def __init__(self):
            self.events = []

        async def emit(self, event_type, payload, **_kwargs):
            self.events.append((event_type, payload))

    bus = ApprovalBus()
    monkeypatch.setattr(recovery, "_active_ws_count", 1)
    monkeypatch.setattr(recovery, "_event_bus", bus)
    brain = Brain(brain_config, register_panic_hotkey=False, approval_timeout=0.01)
    task = asyncio.create_task(
        brain.request_tool_approval(
            "shell_execute", {"command": "safe-test"}, "test timeout", platform="web", risk_class="destructive"
        )
    )
    request_id = await _wait_for_approval_event(bus)
    assert await task is False
    assert pending_tool_approvals == {}
    assert resolve_tool_approval(request_id, True) is False
    await brain.close()


@pytest.mark.asyncio
async def test_web_approval_cancellation_cleans_pending_state(brain_config, monkeypatch):
    class ApprovalBus:
        def __init__(self):
            self.events = []

        async def emit(self, event_type, payload, **_kwargs):
            self.events.append((event_type, payload))

    bus = ApprovalBus()
    monkeypatch.setattr(recovery, "_active_ws_count", 1)
    monkeypatch.setattr(recovery, "_event_bus", bus)
    brain = Brain(brain_config, register_panic_hotkey=False, approval_timeout=None)
    task = asyncio.create_task(
        brain.request_tool_approval(
            "shell_execute", {"command": "safe-test"}, "test cancellation", platform="web", risk_class="destructive"
        )
    )
    await _wait_for_approval_event(bus)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert pending_tool_approvals == {}
    await brain.close()


def test_resolve_tool_approval_unknown_id_returns_false():
    assert resolve_tool_approval("not-a-real-id", True) is False


@pytest.mark.asyncio
async def test_on_tool_approval_request_fires_for_every_platform_not_just_telegram(brain_config):
    """Regression: on_tool_approval_request also drives the HUD approval modal (main.py), not just the
    Telegram relay -- it must fire for voice/web turns too, not be gated to platform == "telegram"."""
    calls = []
    brain = Brain(
        brain_config,
        on_thought_callback=lambda text: None,
        on_tool_approval_request=lambda *args: calls.append(args),
    )

    async def decline_shortly():
        await asyncio.sleep(0.05)
        request_id = get_active_voice_approval()
        resolve_tool_approval(request_id, False)

    decline_task = asyncio.create_task(decline_shortly())
    await brain.request_tool_approval(
        "shell_execute", {"command": "x"}, "reason", platform="voice", risk_class="destructive"
    )
    await decline_task

    assert len(calls) == 1
    request_id, tool_name, reason, platform, risk_class = calls[0]
    assert platform == "voice"
    assert risk_class == "destructive"
    assert tool_name == "shell_execute"


@pytest.mark.asyncio
async def test_request_tool_approval_declines_safely_with_no_channel(brain_config):
    """No web dashboard and no on_thought_callback (voice) wired -- there's
    no way to ask, so the gated call must fail safe (declined), not hang or
    silently proceed."""
    brain = Brain(brain_config)
    approved = await brain.request_tool_approval(
        "shell_execute", {"command": "rm -rf foo"}, "risky keyword 'rm -rf'"
    )
    assert approved is False


@pytest.mark.asyncio
async def test_request_tool_approval_voice_fallback_approved(brain_config):
    """No dashboard connected -- falls back to speaking the prompt via
    on_thought_callback and exposes the request id via
    get_active_voice_approval() for main.py's speech handler to resolve."""
    spoken = []
    brain = Brain(brain_config, on_thought_callback=spoken.append)

    async def approve_shortly():
        # Let request_tool_approval register the pending future first.
        await asyncio.sleep(0.05)
        request_id = get_active_voice_approval()
        assert request_id is not None
        assert resolve_tool_approval(request_id, True) is True

    approve_task = asyncio.create_task(approve_shortly())
    approved = await brain.request_tool_approval(
        "file_write", {"path": ".env"}, "sensitive path '.env'"
    )
    await approve_task

    assert approved is True
    assert spoken and ".env" in spoken[0]
    # Resolved -- must not still be flagged as pending.
    assert get_active_voice_approval() is None


@pytest.mark.asyncio
async def test_request_tool_approval_voice_fallback_declined(brain_config):
    brain = Brain(brain_config, on_thought_callback=lambda text: None)

    async def decline_shortly():
        await asyncio.sleep(0.05)
        request_id = get_active_voice_approval()
        resolve_tool_approval(request_id, False)

    decline_task = asyncio.create_task(decline_shortly())
    approved = await brain.request_tool_approval(
        "shell_execute", {"command": "taskkill /IM notepad.exe /F"}, "risky keyword 'taskkill'"
    )
    await decline_task

    assert approved is False
