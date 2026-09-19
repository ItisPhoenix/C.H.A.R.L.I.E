"""A2.1 canonical terminal authority and approval correlation contracts."""

from asyncio import Queue
from collections import OrderedDict

import pytest

import charlie.core as core
import charlie.web_server as web_server
import main
from charlie.config import Config
from charlie.events import EventType, build_event
from charlie.terminal_service import TerminalManager, TerminalSession
from charlie.tools import ToolExecutionResult
from charlie.turn_contracts import ResultEnvelope, ResultStatus


def _identity() -> dict[str, str]:
    return {
        "task_id": "terminal-task-a21",
        "session_id": "terminal-session-a21",
        "turn_id": "terminal-turn-a21",
    }


def test_terminal_execute_request_is_a_registered_runtime_event():
    event = build_event(
        EventType.TERMINAL_EXECUTE_REQUEST.value,
        {
            "request_id": "request-event",
            "terminal_session_id": "selected-session",
            "command": "python --version",
        },
    )

    assert event["type"] == "terminal_execute_request"


@pytest.mark.asyncio
async def test_brain_terminal_override_uses_selected_session_instead_of_one_shot_shell(monkeypatch):
    brain = core.Brain(
        Config(llm_url="http://localhost:11434", llm_key="no-key", llm_model="dummy"),
        register_panic_hotkey=False,
    )

    def fail_one_shot(*_args, **_kwargs):
        raise AssertionError("selected terminal execution must not call one-shot shell_execute")

    monkeypatch.setattr(core.tool_registry, "execute_tool", fail_one_shot)

    async def approve(*_args, **_kwargs):
        return True

    monkeypatch.setattr(brain, "request_tool_approval", approve)

    async def execute_selected_session():
        return ToolExecutionResult(
            "STDOUT:\nPython 3.14",
            {
                "ok": True,
                "exit_code": 0,
                "stdout": "Python 3.14",
                "terminal_session_id": "selected-session",
                "pid": 1234,
            },
            "terminal_session_result",
        )

    try:
        result = await brain.execute_tool_operation(
            "shell_execute",
            {"command": "python --version"},
            request="python --version",
            execution_owner_id="terminal-owner-a21",
            execute_override=execute_selected_session,
            **_identity(),
        )
    finally:
        await brain.close()

    assert result.status == ResultStatus.COMPLETED.value
    assert result.data["structured_data"]["terminal_session_id"] == "selected-session"
    assert result.data["structured_data"]["pid"] == 1234


@pytest.mark.asyncio
async def test_main_terminal_request_emits_exact_session_execution_request(monkeypatch):
    monkeypatch.setattr(main, "_terminal_session_result_waiters", {}, raising=False)
    events = []

    class Bus:
        async def emit(self, event_type, payload, meta=None):
            events.append((event_type, payload, meta))
            if event_type == "terminal_execute_request":
                main._resolve_terminal_session_result(
                    {
                        "request_id": payload["request_id"],
                        "terminal_session_id": payload["terminal_session_id"],
                        "command": payload["command"],
                        "status": "completed",
                        "stdout": "Python 3.14",
                        "stderr": "",
                        "exit_code": 0,
                        "pid": 1234,
                    }
                )

    class Brain:
        async def execute_tool_operation(self, *_args, **kwargs):
            override = kwargs.get("execute_override")
            assert override is not None
            raw = await override()
            return ResultEnvelope(
                request="python --version",
                task_id="terminal-request-a21",
                capability="terminal",
                operation="terminal.shell.execute",
                status=ResultStatus.COMPLETED.value,
                result=raw.model_text,
                data={"structured_data": raw.structured_data},
            )

    result = await main._handle_terminal_command_request(
        Brain(),
        Bus(),
        request_id="terminal-request-a21",
        terminal_session_id="selected-session",
        command="python --version",
        result_cache=OrderedDict(),
        in_flight={},
        execution_target="terminal_session",
    )

    execution_events = [event for event in events if event[0] == "terminal_execute_request"]
    assert len(execution_events) == 1
    assert execution_events[0][1]["terminal_session_id"] == "selected-session"
    assert result["result"]["data"]["structured_data"]["terminal_session_id"] == "selected-session"


@pytest.mark.asyncio
async def test_terminal_result_correlation_ignores_stale_request():
    main._terminal_session_result_waiters = {}
    waiter = __import__("asyncio").get_running_loop().create_future()
    main._terminal_session_result_waiters["request-a"] = waiter

    assert main._resolve_terminal_session_result({"request_id": "stale-request", "status": "completed"}) is False
    assert waiter.done() is False

    payload = {"request_id": "request-a", "terminal_session_id": "session-a", "status": "completed"}
    assert main._resolve_terminal_session_result(payload) is True
    assert await waiter == payload


@pytest.mark.asyncio
async def test_terminal_manager_delegates_to_exact_session():
    class Session:
        session_id = "selected-session"

        async def execute_command(self, command, request_id):
            return {
                "request_id": request_id,
                "terminal_session_id": self.session_id,
                "command": command,
                "status": "completed",
                "stdout": "Python 3.14",
                "stderr": "",
                "exit_code": 0,
                "pid": 1234,
            }

    manager = TerminalManager.__new__(TerminalManager)
    manager._sessions = {"selected-session": Session()}

    result = await manager.execute_command("selected-session", "python --version", "request-a")

    assert result["terminal_session_id"] == "selected-session"
    assert result["pid"] == 1234


@pytest.mark.asyncio
async def test_terminal_manager_rejects_execution_after_session_close():
    manager = TerminalManager.__new__(TerminalManager)
    manager._sessions = {}

    result = await manager.execute_command("closed-session", "python --version", "request-closed")

    assert result["status"] == "failed"
    assert result["failure_kind"] == "session_unavailable"


@pytest.mark.asyncio
async def test_web_terminal_event_executes_and_returns_result_for_exact_session(monkeypatch):
    calls = []
    responses = []

    class Manager:
        async def execute_command(self, session_id, command, request_id):
            calls.append((session_id, command, request_id))
            return {
                "request_id": request_id,
                "terminal_session_id": session_id,
                "command": command,
                "status": "completed",
                "stdout": "Python 3.14",
                "stderr": "",
                "exit_code": 0,
                "pid": 1234,
            }

    class Bus:
        async def send_command(self, command):
            responses.append(command)
            return True

    monkeypatch.setattr(web_server, "_terminal_manager", Manager())
    monkeypatch.setattr(web_server, "event_bus", Bus())

    await web_server._execute_terminal_request(
        {
            "request_id": "request-web",
            "terminal_session_id": "selected-session",
            "command": "python --version",
        }
    )

    assert calls == [("selected-session", "python --version", "request-web")]
    assert responses[0]["type"] == "terminal_session_result"
    assert responses[0]["payload"]["terminal_session_id"] == "selected-session"


@pytest.mark.asyncio
async def test_terminal_session_expands_powershell_exit_marker(monkeypatch):
    session = TerminalSession.__new__(TerminalSession)
    session.session_id = "selected-session"
    session.pid = 1234
    session.shell_name = "powershell.exe"
    session.status = "running"
    session.exit_code = None
    session.lease_holder = "idle"
    session._closed = False
    queue = Queue()
    captured = []

    def subscribe():
        return queue

    def unsubscribe(_queue):
        return None

    def write_bytes(data, source="user"):
        captured.append((data, source))
        import re

        marker = re.search(r"__CHARLIE_EXIT_[0-9a-f]+__", data).group(0)
        queue.put_nowait({"type": "output", "data": f"{marker}:0\r\n"})
        return len(data)

    session.subscribe = subscribe
    session.unsubscribe = unsubscribe
    session.write_bytes = write_bytes

    result = await session.execute_command("python --version", "request-marker")

    assert result["exit_code"] == 0
    assert captured[0][1] == "charlie"
    assert 'Write-Output "' in captured[0][0]
