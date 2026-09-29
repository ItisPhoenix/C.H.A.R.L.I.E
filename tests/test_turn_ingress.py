"""Focused tests for canonical voice/owner turn ingress."""

import ast
import asyncio
import io
import threading
from pathlib import Path

import main
from charlie.console_ingress import ConsoleTextIngress
from charlie.turn_contracts import TurnRequest

MAIN_SOURCE = Path("main.py").read_text(encoding="utf-8")


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


def test_main_has_one_runtime_ingress_without_removed_client_command_loop():
    assert "consume_web_commands" not in MAIN_SOURCE
    assert "current_web_session_id" not in MAIN_SOURCE
    assert "_dispatch_web_command" not in MAIN_SOURCE


def test_foreground_processor_preserves_request_identity():
    module = ast.parse(MAIN_SOURCE)
    process = next(
        node
        for node in ast.walk(module)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_process"
    )
    source = ast.get_source_segment(MAIN_SOURCE, process) or ""
    assert "request.turn_id" in source
    assert "request.task_id" in source
    assert "session_id = request.session_id" in source
    assert "brain.chat_stream" in source


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


def test_console_main_ingress_uses_canonical_dispatch_queue():
    module = ast.parse(MAIN_SOURCE)
    main_fn = next(
        node
        for node in ast.walk(module)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "main"
    )
    handler = next(
        node
        for node in ast.walk(main_fn)
        if isinstance(node, ast.FunctionDef) and node.name == "on_console_text"
    )
    source = ast.get_source_segment(MAIN_SOURCE, handler) or ""
    assert '_allocate_turn_request(text, current_session_id, "console")' in source
    assert "_dispatch_or_queue(request)" in source
    assert "ConsoleTextIngress(loop, on_console_text)" in MAIN_SOURCE
    shutdown = next(
        node
        for node in ast.walk(main_fn)
        if isinstance(node, ast.FunctionDef) and node.name == "_begin_shutdown"
    )
    assert "console_ingress.stop()" in (ast.get_source_segment(MAIN_SOURCE, shutdown) or "")


def test_console_streamed_turn_suppresses_all_tts_flushes():
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
