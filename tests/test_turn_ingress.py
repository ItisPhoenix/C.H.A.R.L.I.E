"""Focused tests for canonical voice/owner turn ingress."""

import ast
from pathlib import Path

import main
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
