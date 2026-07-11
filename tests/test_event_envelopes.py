"""Tests that session-scoped events emitted from main.py carry session_id.

These events drive the frontend's per-session UI (transcript, tokens, tool
calls, thinking, speaking, response_done). Events that are scoped to a session
MUST include the active session_id in their payload, otherwise the frontend
cannot attribute them to the right session.

Why we don't import main directly:
    main.py transitively imports heavy ML libraries (whisper, kokoro, torch)
    at module load, which block/hang inside a pytest worker. To verify the
    REAL emit call sites without that cost, we execute the actual callback
    *source* from main.py (extracted via AST) against a capturing EventBus.
    This runs main.py's own code -- not a copy -- so the test is a genuine
    behavioral guard, not a string search.

For the two emits that are inline within the large _process() coroutine
(thinking, response_done) we assert via AST that the emit call passes
session_id, which is the same guarantee the runtime path provides.
"""

import ast
import os
from typing import Any, Dict, List

import pytest

MAIN_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"
)


def _extract_function_source(module_ast: ast.Module, name: str) -> str:
    """Find a FunctionDef/AsyncFunctionDef with `name` anywhere in the tree."""
    for node in ast.walk(module_ast):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f"Function {name} not found in main.py")


def _run_callback(name: str, captured: List[Dict[str, Any]],
                  session_id: str, args: tuple) -> None:
    module_ast = ast.parse(_MAIN_SOURCE)  # module-level string with main.py source
    src = _extract_function_source(module_ast, name)

    # Capturing event_bus whose emit appends to `captured` synchronously.
    # The real EventBus.emit is async, but the recording side-effect happens
    # at call time, so a synchronous emit captures the payload faithfully.
    class _CapturingBus:
        def emit(self, event_type: str, payload: dict):
            captured.append({"type": event_type, "payload": dict(payload)})

    bus = _CapturingBus()

    # run_coroutine_threadsafe / create_task appear in the callback source as
    # asyncio.<method>(event_bus.emit(...), loop). Since emit already recorded
    # synchronously, these scheduling calls are no-ops in the test namespace.
    class _FakeAsyncio:
        @staticmethod
        def run_coroutine_threadsafe(coro, loop):
            return None

        @staticmethod
        def create_task(coro):
            return None

    namespace: Dict[str, Any] = {
        "event_bus": bus,
        "loop": None,
        "current_web_session_id": session_id,
        "asyncio": _FakeAsyncio,
    }
    exec(compile(src, f"<main.{name}>", "exec"), namespace)
    callback = namespace[name]
    callback(*args)


def _load_main_source() -> str:
    with open(MAIN_PATH, "r", encoding="utf-8") as fh:
        return fh.read()


_MAIN_SOURCE = _load_main_source()


@pytest.mark.asyncio
async def test_session_scoped_events_carry_session_id():
    session_id = "sess_test_123"
    captured: List[Dict[str, Any]] = []

    # 1) Tool callbacks (nested in main()) -- fire directly with real source.
    _run_callback("on_tool_call", captured, session_id,
                  ("web_search", {"query": "weather"}))
    _run_callback("on_tool_result", captured, session_id,
                  ("web_search", "It is sunny."))
    _run_callback("on_thinking_update", captured, session_id,
                  ("web_search", {"query": "weather"}))
    _run_callback("on_tts_start", captured, session_id, ())
    _run_callback("on_tts_stop", captured, session_id, ())

    by_type: Dict[str, Any] = {e["type"]: e["payload"] for e in captured}

    for etype in ("tool_call", "tool_result", "thinking_update",
                  "speaking_start", "speaking_stop"):
        assert etype in by_type, f"{etype} event was never emitted"
        assert by_type[etype].get("session_id") == session_id, (
            f"{etype} payload missing/invalid session_id: {by_type[etype]}"
        )

    # 2) Inline emits inside _process(): thinking (currently {}) and
    #    response_done (already {"session_id": session_id}). Both must carry
    #    session_id. Verify via AST on the real main.py source.
    module_ast = ast.parse(_MAIN_SOURCE)
    process_fn = None
    for n in ast.walk(module_ast):
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_process":
            process_fn = n
            break
    assert process_fn is not None, "_process() not found in main.py"
    emits = [
        n for n in ast.walk(process_fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "emit"
    ]
    # Find the emits with first argument "thinking" / "response_done".
    for target in ("thinking", "response_done"):
        matching = [
            c for c in emits
            if c.args and isinstance(c.args[0], ast.Constant) and c.args[0].value == target
        ]
        assert matching, f"_process() does not emit '{target}'"
        call = matching[0]
        # The payload is the second arg (a dict literal); check it contains a
        # session_id keyword/assignment referencing session_id.
        assert len(call.args) >= 2, f"'{target}' emit has no payload dict"
        payload = call.args[1]
        has_session_id = _dict_has_session_id(payload)
        assert has_session_id, (
            f"_process() '{target}' emit payload does not carry session_id"
        )


def _dict_has_session_id(node: ast.AST) -> bool:
    """Return True if a dict literal contains a 'session_id' key or a kw-style
    key:value where the key is 'session_id'."""
    if not isinstance(node, ast.Dict):
        return False
    for key in node.keys:
        if isinstance(key, ast.Constant) and key.value == "session_id":
            return True
    return False
