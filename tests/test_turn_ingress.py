"""Behavioral coverage for one TurnRequest across the live runtime ingress."""

import ast
import asyncio
import os
import textwrap
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from charlie.events import EventMeta, EventSource, build_event
from charlie.presentation import PresentationResolver
from charlie.turn_contracts import ResultEnvelope, TurnRequest

MAIN_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
with open(MAIN_PATH, "r", encoding="utf-8") as _main_file:
    MAIN_SOURCE = _main_file.read()


class _StopWebCommandLoop(BaseException):
    """Stop the extracted web command loop after one real chat command."""


class _NullLogger:
    def debug(self, *args: Any, **kwargs: Any) -> None:
        pass

    def info(self, *args: Any, **kwargs: Any) -> None:
        pass


def _function_source(name: str) -> str:
    module = ast.parse(MAIN_SOURCE)
    matches = [
        node
        for node in ast.walk(module)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]
    assert matches, f"{name} was not found in main.py"
    return ast.get_source_segment(MAIN_SOURCE, matches[0]) or ""


def _recording_allocator(captured: list[TurnRequest]) -> Callable[[str, str, str], TurnRequest]:
    def allocate(text: str, session_id: str, channel: str) -> TurnRequest:
        request = TurnRequest.allocate(text, session_id, channel)
        captured.append(request)
        return request

    return allocate


def _load_voice_ingress(allocator: Callable[..., TurnRequest], dispatched: list[TurnRequest]):
    source = _function_source("on_speech")

    def dispatch(request: TurnRequest) -> None:
        dispatched.append(request)

    namespace = {
        "_normalize_app_list": lambda text: text,
        "_allocate_turn_request": allocator,
        "logger": _NullLogger(),
        "time": __import__("time"),
        "ensure_session_ready": lambda session_id: None,
        "_schedule_process": lambda _result, _loop: None,
        "_dispatch_or_queue": dispatch,
        "loop": None,
        "voice_diagnostic_traces": {},
    }
    wrapper_source = (
        "def _wrapper():\n"
        "    _voice_fallback_session_id = 'voice-fallback'\n"
        "    current_web_session_id = _voice_fallback_session_id\n"
        "    recent_turn_texts = {}\n"
        "    _DEDUPE_WINDOW_SEC = 20.0\n"
        + textwrap.indent(source, "    ")
        + "\n    return on_speech\n"
    )
    exec(compile(wrapper_source, "<main.on_speech>", "exec"), namespace)
    return namespace["_wrapper"]()


def test_voice_ingress_allocates_one_request_and_keeps_asr_dedupe() -> None:
    allocated: list[TurnRequest] = []
    dispatched: list[TurnRequest] = []
    on_speech = _load_voice_ingress(_recording_allocator(allocated), dispatched)

    on_speech("What is on my screen?")
    on_speech("What is on my screen?")
    on_speech("A different request")

    assert len(allocated) == 2
    assert len(dispatched) == 2
    assert dispatched == allocated
    assert allocated[0].channel == "voice"
    assert allocated[0].session_id == "voice-fallback"
    assert allocated[0].input == "What is on my screen?"
    assert allocated[0].turn_id != allocated[1].turn_id


@pytest.mark.asyncio
async def test_web_ingress_allocates_one_request_before_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _function_source("consume_web_commands")
    allocated: list[TurnRequest] = []
    dispatched: list[TurnRequest] = []

    async def dispatch(request: TurnRequest) -> None:
        dispatched.append(request)

    class CommandBus:
        async def next_command(self):
            if not dispatched:
                return {
                    "type": "chat",
                    "session_id": "web-session",
                    "text": "web request",
                }
            raise _StopWebCommandLoop

    namespace = {
        "_allocate_turn_request": _recording_allocator(allocated),
        "_dispatch_or_queue": dispatch,
        "logger": _NullLogger(),
        "asyncio": asyncio,
    }
    wrapper_source = (
        "def _wrapper():\n"
        "    _voice_fallback_session_id = 'voice-fallback'\n"
        "    current_web_session_id = 'initial-session'\n"
        "    voice = None\n"
        "    mcp_client = None\n"
        + textwrap.indent(source, "    ")
        + "\n    return consume_web_commands\n"
    )
    exec(compile(wrapper_source, "<main.consume_web_commands>", "exec"), namespace)
    consume = namespace["_wrapper"]()
    monkeypatch.setattr("charlie.recovery.set_active_session_id", lambda _session_id: None)

    with pytest.raises(_StopWebCommandLoop):
        await consume(CommandBus(), None)

    assert len(allocated) == 1
    assert dispatched == allocated
    assert allocated[0].channel == "web"
    assert allocated[0].session_id == "web-session"
    assert allocated[0].input == "web request"


@pytest.mark.asyncio
async def test_telegram_ingress_allocates_one_request_before_dispatch() -> None:
    source = _function_source("on_telegram_message")
    allocated: list[TurnRequest] = []
    dispatched: list[TurnRequest] = []

    async def dispatch(request: TurnRequest) -> None:
        dispatched.append(request)

    namespace = {
        "_allocate_turn_request": _recording_allocator(allocated),
        "_dispatch_or_queue": dispatch,
    }
    wrapper_source = (
        "def _wrapper():\n"
        "    current_web_session_id = 'telegram-session'\n"
        + textwrap.indent(source, "    ")
        + "\n    return on_telegram_message\n"
    )
    exec(compile(wrapper_source, "<main.on_telegram_message>", "exec"), namespace)
    on_message = namespace["_wrapper"]()

    await on_message("telegram request", 42)

    assert len(allocated) == 1
    assert dispatched == allocated
    assert allocated[0].channel == "telegram"
    assert allocated[0].session_id == "telegram-session"
    assert allocated[0].input == "telegram request"


@pytest.mark.asyncio
async def test_queue_and_dequeue_preserve_the_authoritative_request(monkeypatch: pytest.MonkeyPatch) -> None:
    from charlie import core

    monkeypatch.setattr(core, "get_active_voice_approval", lambda: None)
    source = _function_source("_dispatch_or_queue")
    processed: list[TurnRequest] = []
    namespace = {
        "TurnRequest": TurnRequest,
        "logger": _NullLogger(),
        "processed": processed,
        "asyncio": asyncio,
        "time": __import__("time"),
    }
    wrapper_source = (
        "def _wrapper():\n"
        "    turn_active = True\n"
        "    pending_turns = []\n"
        "    pending_turn_times = {}\n"
        "    voice_diagnostic_traces = {}\n"
        "    active_turn_id = None\n"
        "    active_task_id = None\n"
        "    active_process_task = None\n"
        "    active_operation_name = None\n"
        "    active_operation_task_id = None\n"
        "    active_operation_cancellable = True\n"
        "    brain = object()\n"
        "    voice = SimpleNamespace(is_speaking=SimpleNamespace(is_set=lambda: False))\n"
        "    async def _process(request, _brain, _voice):\n"
        "        processed.append(request)\n"
        + textwrap.indent(source, "    ")
        + "\n"
        + "    def set_active(value):\n"
        + "        nonlocal turn_active\n"
        + "        turn_active = value\n"
        + "    return _dispatch_or_queue, pending_turns, set_active\n"
    )
    namespace["SimpleNamespace"] = SimpleNamespace
    exec(compile(wrapper_source, "<main._dispatch_or_queue>", "exec"), namespace)
    dispatch, pending, set_active = namespace["_wrapper"]()
    request = TurnRequest.allocate("queued request", "session-queue", "voice")

    await dispatch(request)
    assert pending == [request]
    assert processed == []

    set_active(False)
    dequeued = pending.pop(0)
    await dispatch(dequeued)
    assert processed == [request]
    assert processed[0] is request


@pytest.mark.asyncio
async def test_new_voice_turn_supersedes_cancellable_foreground_work(monkeypatch: pytest.MonkeyPatch) -> None:
    from charlie import core

    monkeypatch.setattr(core, "get_active_voice_approval", lambda: None)
    source = _function_source("_dispatch_or_queue")
    processed: list[TurnRequest] = []
    cancelled = []

    class Brain:
        def cancel_chat(self):
            cancelled.append(True)

    namespace = {
        "TurnRequest": TurnRequest,
        "logger": _NullLogger(),
        "processed": processed,
        "asyncio": asyncio,
        "time": __import__("time"),
    }
    wrapper_source = (
        "def _wrapper():\n"
        "    turn_active = True\n"
        "    pending_turns = []\n"
        "    pending_turn_times = {}\n"
        "    voice_diagnostic_traces = {}\n"
        "    active_turn_id = 'old-turn'\n"
        "    active_task_id = 'old-task'\n"
        "    active_operation_name = None\n"
        "    active_operation_task_id = None\n"
        "    active_operation_cancellable = True\n"
        "    brain = Brain()\n"
        "    async def _old_process():\n"
        "        await asyncio.sleep(60)\n"
        "    active_process_task = asyncio.create_task(_old_process())\n"
        "    voice = SimpleNamespace(is_speaking=SimpleNamespace(is_set=lambda: False))\n"
        "    async def _process(request, _brain, _voice):\n"
        "        processed.append(request)\n"
        + textwrap.indent(source, "    ")
        + "\n    return _dispatch_or_queue, pending_turns, active_process_task\n"
    )
    namespace["SimpleNamespace"] = SimpleNamespace
    namespace["Brain"] = Brain
    exec(compile(wrapper_source, "<main._dispatch_or_queue>", "exec"), namespace)
    dispatch, pending, old_task = namespace["_wrapper"]()
    request = TurnRequest.allocate("new voice request", "session-queue", "voice")

    await dispatch(request)

    assert processed == [request]
    assert pending == []
    assert cancelled == [True]
    assert old_task.done()


@pytest.mark.asyncio
async def test_new_voice_turn_waits_for_non_cancellable_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    from charlie import core

    monkeypatch.setattr(core, "get_active_voice_approval", lambda: None)
    source = _function_source("_dispatch_or_queue")
    processed: list[TurnRequest] = []

    class Brain:
        def cancel_chat(self):
            pass

    namespace = {
        "TurnRequest": TurnRequest,
        "logger": _NullLogger(),
        "processed": processed,
        "asyncio": asyncio,
        "time": __import__("time"),
        "SimpleNamespace": SimpleNamespace,
        "Brain": Brain,
    }
    wrapper_source = (
        "def _wrapper():\n"
        "    turn_active = True\n"
        "    pending_turns = [TurnRequest.allocate('old queued', 'session-queue', 'voice')]\n"
        "    pending_turn_times = {}\n"
        "    voice_diagnostic_traces = {}\n"
        "    active_turn_id = 'old-turn'\n"
        "    active_task_id = 'old-task'\n"
        "    active_operation_name = 'file_write'\n"
        "    active_operation_task_id = 'old-task'\n"
        "    active_operation_cancellable = False\n"
        "    brain = Brain()\n"
        "    active_process_task = asyncio.create_task(asyncio.sleep(60))\n"
        "    voice = SimpleNamespace(is_speaking=SimpleNamespace(is_set=lambda: False))\n"
        "    async def _process(request, _brain, _voice):\n"
        "        processed.append(request)\n"
        + textwrap.indent(source, "    ")
        + "\n    return _dispatch_or_queue, pending_turns, active_process_task\n"
    )
    exec(compile(wrapper_source, "<main._dispatch_or_queue>", "exec"), namespace)
    dispatch, pending, old_task = namespace["_wrapper"]()
    request = TurnRequest.allocate("latest voice request", "session-queue", "voice")

    await dispatch(request)

    assert processed == []
    assert pending == [request]
    old_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await old_task


def test_process_accepts_only_the_existing_request_and_dequeues_it_unchanged() -> None:
    process_source = _function_source("_process")
    dispatch_source = _function_source("_dispatch_or_queue")

    assert "async def _process(request: TurnRequest" in process_source
    assert "_allocate_turn_request" not in process_source
    assert "task_id = uuid.uuid4().hex" in process_source
    assert "brain.chat_stream(" in process_source
    assert "task_id=task_id" in process_source
    assert "turn_id=request.turn_id" in process_source
    assert "diagnostic_trace=trace" in process_source
    assert "next_request = pending_turns.pop(0)" in process_source
    assert "_dispatch_or_queue(next_request)" in process_source
    assert "turn_task_id" not in process_source
    assert "_allocate_turn_request(request" not in dispatch_source


@pytest.mark.asyncio
async def test_brain_preserves_distinct_task_and_turn_ids_in_result_presentation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from charlie import core, fastpaths
    from charlie.autonomy import Requirement
    from charlie.config import Config
    from charlie.fastpaths import FastPathMatch, FastPathResult

    emitted = []

    class Bus:
        async def emit(self, event_type, payload, meta=None):
            emitted.append((event_type, payload, meta))

    match = FastPathMatch(
        intent="identity_probe",
        semantic_op_id="system.metrics.read",
        tool_name="system_diagnostics",
        target_domain="system",
    )
    monkeypatch.setattr(fastpaths, "match_fast_path", lambda _query: match)
    monkeypatch.setattr(fastpaths, "execute_fast_path", lambda _match: FastPathResult("identity result"))
    monkeypatch.setattr(core, "autonomy_evaluate", lambda *_args: (Requirement.ALLOW, "safe", ""))

    brain = core.Brain(
        Config(llm_url="http://localhost:11434", llm_key="no-key", llm_model="dummy"),
        register_panic_hotkey=False,
    )
    brain.event_bus = Bus()
    monkeypatch.setattr(brain.world_model, "record_event", lambda *args: None)
    try:
        chunks = [
            chunk
            async for chunk in brain.chat_stream(
                "identity probe",
                platform="web",
                skip_pre_search=True,
                session_id="session-brain",
                task_id="task-brain",
                turn_id="turn-brain",
            )
        ]
    finally:
        await brain.close()

    assert chunks == ["identity result"]
    presentation = next(item for item in emitted if item[0] == "presentation_intent")
    event_type, payload, meta = presentation
    assert event_type == "presentation_intent"
    assert payload["turn_id"] == "turn-brain"
    assert payload["task_id"] == "task-brain"
    assert payload["session_id"] == "session-brain"
    assert meta.turn_id == "turn-brain"
    assert meta.task_id == "task-brain"
    assert meta.session_id == "session-brain"
    assert meta.turn_id != meta.task_id


def test_event_meta_preserves_turn_identity_and_allows_ambient_none() -> None:
    interactive = build_event(
        "token",
        {"text": "hello", "session_id": "session-event"},
        meta=EventMeta(
            source=EventSource.BRAIN,
            session_id="session-event",
            task_id="task-event",
            turn_id="turn-event",
        ),
    )
    ambient = build_event("charlie_state", {"state": "idle"}, meta=EventMeta(source=EventSource.RUNTIME))

    assert interactive["turn_id"] == "turn-event"
    assert interactive["task_id"] == "task-event"
    assert interactive["session_id"] == "session-event"
    assert ambient["turn_id"] is None
    assert ambient["task_id"] is None


def test_result_and_presentation_correlation_preserve_the_same_turn() -> None:
    request = TurnRequest.allocate("research request", "session-result", "web")
    result = ResultEnvelope(
        request=request.input,
        turn_id=request.turn_id,
        task_id="task-result",
        session_id=request.session_id,
        capability="research",
        operation="research.web.execute",
        result="verified result",
    )

    intent = PresentationResolver().resolve(result)
    event = intent.to_event()

    assert result.to_dict()["turn_id"] == request.turn_id
    assert result.to_dict()["task_id"] == "task-result"
    assert intent.turn_id == request.turn_id
    assert intent.task_id == "task-result"
    assert intent.session_id == request.session_id
    assert event["turn_id"] == request.turn_id
    assert event["task_id"] == "task-result"


def test_approval_path_uses_the_existing_request_without_allocating_another() -> None:
    process_source = _function_source("_process")
    assert process_source.count("_allocate_turn_request") == 0
    assert "pending_approval_id = get_active_voice_approval()" in process_source
    assert "brain.cancel_chat()" in process_source
    assert "voice.stop_tts()" in process_source

    request_one = TurnRequest.allocate("yes", "session-approval", "voice")
    request_two = TurnRequest.allocate("stop", "session-approval", "voice")
    assert request_one.turn_id != request_two.turn_id
