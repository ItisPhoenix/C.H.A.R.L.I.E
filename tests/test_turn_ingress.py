"""Focused tests for canonical voice/owner turn ingress."""

import ast
import asyncio
import io
import threading
from pathlib import Path

import pytest

import main
from charlie.config import Config
from charlie.console_ingress import ConsoleTextIngress
from charlie.core import Brain
from charlie.turn_contracts import TurnRequest

MAIN_SOURCE = Path("main.py").read_text(encoding="utf-8")


def _brain() -> Brain:
    return Brain(
        Config(llm_url="http://localhost:11434/v1", llm_key="test-key", llm_model="dummy"),
        is_background=False,
    )


def _main_source() -> str:
    return Path("main.py").read_text(encoding="utf-8")


def _main_function(name: str):
    """main()'s own nested function/closure, as unparsed source."""
    module = ast.parse(_main_source())
    main_fn = next(
        node
        for node in ast.walk(module)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "main"
    )
    return next(
        node for node in ast.walk(main_fn) if getattr(node, "name", None) == name
    )


def test_voice_and_telegram_ingress_allocate_distinct_authoritative_requests():
    voice = main._allocate_turn_request("hello", "voice-session", "voice")
    telegram = main._allocate_turn_request("hello", "telegram-session", "telegram")

    assert isinstance(voice, TurnRequest)
    assert voice.channel == "voice"
    assert voice.session_id == "voice-session"
    assert voice.turn_id
    assert telegram.channel == "telegram"
    assert telegram.session_id == "telegram-session"
    assert telegram.turn_id != voice.turn_id


def test_main_exposes_no_removed_web_command_ingress():
    """The removed client-command loop must not reappear as a live symbol.

    Previously this asserted three strings were absent from main.py. A removed
    loop reintroduced under a different name (or a re-export) still leaves a
    callable attribute behind, so assert on the module surface instead.
    """
    for removed in ("consume_web_commands", "current_web_session_id", "_dispatch_web_command"):
        assert not hasattr(main, removed), f"main.{removed} is live again"


@pytest.mark.asyncio
async def test_console_ingress_dispatches_one_canonical_turn_request(monkeypatch):
    """Typed text reaches Brain as one authoritative console turn.

    The old version asserted four substrings of _process. This drives a real
    Brain turn and checks the observable postcondition: one operation executed,
    correlated with the request's turn/task/session identity, and no TTS.
    """
    brain = _brain()
    envelopes = []
    brain.on_operation_result = lambda name, envelope: envelopes.append((name, envelope))
    monkeypatch.setattr(
        "charlie.tools.registry.execute_tool",
        lambda name, _args: "The notes file was read successfully.",
    )
    monkeypatch.setattr(brain.client, "stream", _tool_call_stream("file_read"))
    try:
        chunks = [
            chunk
            async for chunk in brain.chat_stream(
                "read notes.txt",
                platform="console",
                skip_pre_search=True,
                session_id="session-console",
                task_id="task-console",
                turn_id="turn-console",
            )
        ]
    finally:
        await brain.close()

    assert len(envelopes) == 1
    name, envelope = envelopes[0]
    assert name == "file_read"
    assert (envelope.turn_id, envelope.task_id, envelope.session_id) == (
        "turn-console",
        "task-console",
        "session-console",
    )
    assert envelope.result == "The notes file was read successfully."
    assert chunks == ["Here are the notes."]


def _tool_call_stream(tool_name: str):
    """One tool call, then a plain content reply."""
    calls = [0]

    def stream(*_args, **_kwargs):
        class MockResponse:
            def raise_for_status(self):
                pass

            async def aiter_lines(self):
                calls[0] += 1
                if calls[0] == 1:
                    yield (
                        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"1",'
                        '"function":{"name":"' + tool_name + '","arguments":'
                        '"{\\"path\\":\\"notes.txt\\"}"}}]}}]}'
                    )
                else:
                    yield 'data: {"choices":[{"delta":{"content":"Here are the notes."}}]}'
                yield "data: [DONE]"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return None

        return MockResponse()

    return stream


def test_console_handler_allocates_through_the_shared_turn_factory():
    """on_console_text must not hand-build a TurnRequest or bypass the queue."""
    node = _main_function("on_console_text")
    allocated = []
    scheduled = []

    namespace = {
        "_allocate_turn_request": lambda text, session_id, channel: allocated.append(
            (text, session_id, channel)
        )
        or TurnRequest.allocate(text, session_id, channel),
        "_schedule_process": lambda coro, loop: scheduled.append(coro),
        "_dispatch_or_queue": lambda request: asyncio.sleep(0),
        "current_session_id": "launch-session",
        "loop": None,
        "__name__": "console_handler",
    }
    exec(compile(ast.unparse(node), "<main.on_console_text>", "exec"), namespace)
    try:
        namespace["on_console_text"]("  hello Charlie  ")
    finally:
        for coro in scheduled:
            coro.close()

    assert allocated == [("  hello Charlie  ", "launch-session", "console")]
    assert len(scheduled) == 1


def test_dynamic_welcome_uses_current_launch_session():
    module = ast.parse(MAIN_SOURCE)
    main_fn = next(
        node
        for node in ast.walk(module)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "main"
    )
    welcome_call = next(
        node
        for node in ast.walk(main_fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "chat_stream"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and "startup welcome" in node.args[0].value
    )

    session_kw = next((keyword for keyword in welcome_call.keywords if keyword.arg == "session_id"), None)
    assert isinstance(session_kw.value, ast.Name)
    assert session_kw.value.id == "current_session_id"

    launch_session_ready = [
        node
        for node in ast.walk(main_fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "ensure_session_ready"
        and node.args
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "current_session_id"
        and node.lineno < welcome_call.lineno
    ]
    assert launch_session_ready, "dynamic welcome must initialize its launch-owned session first"


def test_turn_request_allocation_is_stable_for_replay_identity():
    first = TurnRequest.allocate("run the task", "session-1", "voice")
    replay = TurnRequest(
        turn_id=first.turn_id,
        task_id=first.task_id,
        session_id=first.session_id,
        input=first.input,
        channel=first.channel,
        created_at=first.created_at,
    )
    assert replay == first


class _InteractiveInput(io.StringIO):
    def isatty(self):
        return True


def test_console_ingress_delivers_text_on_the_runtime_loop():
    loop = asyncio.new_event_loop()
    received = []
    delivered = asyncio.Event()

    def on_text(text):
        received.append((text, threading.get_ident()))
        delivered.set()

    ingress = ConsoleTextIngress(loop, on_text, _InteractiveInput("\n  hello Charlie  \n"))
    try:
        assert ingress.start()
        loop.run_until_complete(asyncio.wait_for(delivered.wait(), timeout=1))
        assert received == [("hello Charlie", threading.get_ident())]
    finally:
        assert ingress.stop()
        loop.close()


def test_console_ingress_disables_noninteractive_stdin():
    loop = asyncio.new_event_loop()
    ingress = ConsoleTextIngress(loop, lambda _text: None, io.StringIO("ignored\n"))
    try:
        assert not ingress.start()
        assert ingress.stop()
    finally:
        loop.close()


def test_console_replies_print_to_stdout_and_skip_tts(capsys):
    class VoiceSpy:
        def speak(self, *_args):
            raise AssertionError("console replies must not invoke TTS")

    main._print_console_reply("Typed reply")
    main._safe_speak(VoiceSpy(), "Typed reply", "neutral", channel="console")

    assert capsys.readouterr().out == "\nCharlie: Typed reply\n"


def test_console_ingress_stop_drops_queued_input_and_joins_after_read_returns():
    loop = asyncio.new_event_loop()
    line_read = threading.Event()
    release_read = threading.Event()
    received = []

    class BlockingInput:
        calls = 0

        def isatty(self):
            return True

        def readline(self):
            self.calls += 1
            if self.calls == 1:
                return "queued before shutdown\n"
            line_read.set()
            release_read.wait(timeout=1)
            return "late input\n"

    ingress = ConsoleTextIngress(loop, received.append, BlockingInput())
    try:
        assert ingress.start()
        assert line_read.wait(timeout=1)
        assert not ingress.stop(timeout=0.01)
        release_read.set()
        assert ingress.stop(timeout=1)
        loop.run_until_complete(asyncio.sleep(0))
        assert received == []
    finally:
        release_read.set()
        ingress.stop(timeout=1)
        loop.close()


def test_main_wires_console_ingress_to_the_handler_and_stops_it_on_shutdown():
    """Structural wiring check: the runtime owns both ends of the console bridge.

    main() has no importable entrypoint for this (the wiring is inline in the
    startup body), so assert on the call graph rather than on source text: the
    ingress must be constructed with main's own on_console_text, and shutdown
    must close it. Both were previously substring matches, which would keep
    passing if the call were rewritten while the wiring itself rotted.
    """
    module = ast.parse(MAIN_SOURCE)
    main_fn = next(
        node
        for node in ast.walk(module)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "main"
    )
    ingress_calls = [
        node
        for node in ast.walk(main_fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "ConsoleTextIngress"
    ]
    assert len(ingress_calls) == 1, "runtime must construct exactly one console ingress"
    handler_arg = next(
        (
            arg
            for arg in ingress_calls[0].args
            if isinstance(arg, ast.Name) and arg.id == "on_console_text"
        ),
        None,
    )
    assert handler_arg is not None

    shutdown = next(
        node
        for node in ast.walk(main_fn)
        if isinstance(node, ast.FunctionDef) and node.name == "_begin_shutdown"
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "console_ingress"
        and node.func.attr == "stop"
        for node in ast.walk(shutdown)
    ), "shutdown must stop the console ingress"


def test_console_streamed_turn_suppresses_all_tts_flushes():
    """Every streamed flush in _process must carry the request's channel.

    _safe_speak drops console-channel text, so a flush that omitted
    ``channel=platform`` would read typed answers aloud at the user's desk.
    The AST check covers the call sites; _safe_speak's own gate is exercised
    behaviourally in test_console_replies_print_to_stdout_and_skip_tts.
    """
    module = ast.parse(MAIN_SOURCE)
    process = next(
        node
        for node in ast.walk(module)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_process"
    )
    speak_calls = [
        node
        for node in ast.walk(process)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_safe_speak"
    ]
    assert speak_calls
    assert all(
        any(
            keyword.arg == "channel"
            and isinstance(keyword.value, ast.Name)
            and keyword.value.id == "platform"
            for keyword in call.keywords
        )
        for call in speak_calls
    )
