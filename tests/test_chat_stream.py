import asyncio
import json
import sys
from typing import Optional, Tuple

import pytest

from charlie import router
from charlie.config import Config
from charlie.core import Brain
from charlie.streaming import FollowupStreamState


def _detect_close_app(query: str) -> Optional[str]:
    """Test helper: compose router's match/execute split back into the old single-call shape."""
    matched = router.match_close_app(query)
    return None if matched is None else router.execute_close_app(matched[0], matched[1])


def _detect_open_app(query: str) -> Optional[Tuple[str, Optional[str]]]:
    """Test helper: compose router's match/execute split back into the old single-call shape."""
    matched = router.match_open_app(query)
    if matched is None:
        return None
    apps, commands, leftover = matched
    return router.execute_open_app(apps, commands), leftover


def test_social_freshness_phrase_does_not_trigger_core_research():
    from charlie.core import _needs_web_search

    assert _needs_web_search("How are you doing today?") is False


@pytest.fixture
def brain_config():
    return Config(
        llm_url="http://localhost:11434",
        llm_key="no-key",
        llm_model="dummy",
        iteration_budget_max=3,
    )


@pytest.mark.asyncio
async def test_budget_exhaustion(monkeypatch, brain_config):
    brain = Brain(brain_config)

    followup_count = 0

    def mock_stream(*args, **kwargs):
        nonlocal followup_count
        followup_count += 1

        class MockResponse:
            def raise_for_status(self):
                pass

            async def aiter_lines(self):
                if followup_count <= 4:
                    yield 'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"123","function":{"name":"web_search","arguments":"{\\"query\\":\\"test\\"}"}}]}}]}'  # noqa: E501
                else:
                    yield 'data: {"choices":[{"delta":{"content":"done"}}]}'
                yield "data: [DONE]"

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        return MockResponse()

    monkeypatch.setattr(brain.client, "stream", mock_stream)

    monkeypatch.setattr(
        "charlie.tools.registry.execute_tool",
        lambda name, args: "mock result",
    )

    results = []
    async for chunk in brain.chat_stream("test"):
        results.append(chunk)

    assert any("tool limit" in str(r) for r in results)


@pytest.mark.asyncio
async def test_explicit_remember_bypasses_model_tool_calling(monkeypatch, brain_config):
    brain = Brain(brain_config)
    calls = []
    monkeypatch.setattr(
        "charlie.tools.registry.execute_tool",
        lambda name, args: calls.append((name, args)) or "Remembered: my name is Alex",
    )

    result = [chunk async for chunk in brain.chat_stream("Please remember that my name is Alex")]

    assert result == ["Remembered: my name is Alex"]
    assert calls == [("vector_memory", {"action": "remember", "content": "my name is Alex"})]


@pytest.mark.asyncio
async def test_explicit_recall_bypasses_model_tool_calling(monkeypatch, brain_config):
    brain = Brain(brain_config)
    calls = []
    monkeypatch.setattr(
        "charlie.tools.registry.execute_tool",
        lambda name, args: calls.append((name, args)) or "- my name is Alex",
    )

    result = [chunk async for chunk in brain.chat_stream("Do you remember my name?")]

    assert result == ["- my name is Alex"]
    assert calls == [("vector_memory", {"action": "recall", "content": "my name"})]


@pytest.mark.asyncio
async def test_partial_budget_spend_runs_affordable_calls_not_whole_batch_abort(monkeypatch):
    # Budget for 1 call, model requests 2 in one round -- the affordable one must still execute.
    config = Config(llm_url="http://localhost:11434", llm_key="no-key", llm_model="dummy", iteration_budget_max=1)
    brain = Brain(config)

    call_count = 0

    def mock_stream(*args, **kwargs):
        nonlocal call_count
        call_count += 1

        class MockResponse:
            def raise_for_status(self):
                pass

            async def aiter_lines(self):
                if call_count == 1:
                    yield (
                        'data: {"choices":[{"delta":{"tool_calls":['
                        '{"index":0,"id":"a","function":{"name":"web_search","arguments":"{\\"query\\":\\"x\\"}"}},'
                        '{"index":1,"id":"b","function":{"name":"web_search","arguments":"{\\"query\\":\\"y\\"}"}}'
                        "]}}]}"
                    )
                else:
                    yield 'data: {"choices":[{"delta":{"content":"done"}}]}'
                yield "data: [DONE]"

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        return MockResponse()

    monkeypatch.setattr(brain.client, "stream", mock_stream)

    executed_args = []
    monkeypatch.setattr(
        "charlie.tools.registry.execute_tool",
        lambda name, args: executed_args.append(args) or "mock result",
    )

    results = []
    async for chunk in brain.chat_stream("test"):
        results.append(chunk)

    assert len(executed_args) == 1


def test_extract_bare_tool_calls():
    """Local LLMs output bare tool_name(args) without TOOL: prefix."""
    from charlie.config import Config

    brain = Brain(
        Config(llm_url="http://localhost:11434", llm_key="no-key", llm_model="dummy")
    )
    text = 'shell_execute(command="start https://youtube.com")\nshell_execute(command="start https://twitter.com")'
    calls = brain._extract_tool_calls(text)
    assert len(calls) == 2
    assert calls[0]["name"] == "shell_execute"
    assert calls[0]["arguments"]["command"] == "start https://youtube.com"
    assert calls[1]["name"] == "shell_execute"
    assert calls[1]["arguments"]["command"] == "start https://twitter.com"


def test_extract_bare_tool_dedup():
    """Bare and TOOL: prefixed matches should deduplicate."""
    from charlie.config import Config

    brain = Brain(
        Config(llm_url="http://localhost:11434", llm_key="no-key", llm_model="dummy")
    )
    text = 'TOOL: web_search("test")\nweb_search("test")'
    calls = brain._extract_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["name"] == "web_search"


def test_extract_mixed_tool_formats():
    """Mixed TOOL: and bare formats in same response."""
    from charlie.config import Config

    brain = Brain(
        Config(llm_url="http://localhost:11434", llm_key="no-key", llm_model="dummy")
    )
    text = 'TOOL: web_search("news")\nshell_execute(command="dir")'
    calls = brain._extract_tool_calls(text)
    assert len(calls) == 2
    names = {c["name"] for c in calls}
    assert names == {"web_search", "shell_execute"}


def test_extract_multi_arg_tool_calls():
    """Verify tool calls with multiple arguments map to correct names."""
    from charlie.config import Config
    brain = Brain(
        Config(llm_url="http://localhost:11434", llm_key="no-key", llm_model="dummy")
    )
    # Test TOOL: format
    text = 'TOOL: file_write("C:\\\\test.txt", "hello world")'
    calls = brain._extract_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["name"] == "file_write"
    assert calls[0]["arguments"] == {"path": "C:\\\\test.txt", "content": "hello world"}

    # Test bare format
    text = 'file_write("C:\\\\test.txt", "hello world")'
    calls = brain._extract_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["name"] == "file_write"
    assert calls[0]["arguments"] == {"path": "C:\\\\test.txt", "content": "hello world"}

def test_detect_close_app(monkeypatch):
    import subprocess


    called_cmds = []

    def mock_run(cmd, *args, **kwargs):
        called_cmds.append(cmd)

        class MockResult:
            returncode = 0
            stdout = ""
            stderr = ""

        return MockResult()

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr(router, "is_process_running", lambda _: False)

    # 1. Test match and successful taskkill (single)
    res = _detect_close_app("close chrome")
    assert res == "Chrome has been closed for you."
    assert "taskkill /IM chrome.exe /F" in called_cmds

    # 2. Test direct .exe usage
    res = _detect_close_app("charlie, close notepad.exe")
    assert res == "Notepad has been closed for you."
    assert "taskkill /IM notepad.exe /F" in called_cmds

    # 3. Test closing multiple apps
    called_cmds.clear()
    res = _detect_close_app("close chrome and notepad")
    assert "Notepad and Chrome" in res
    assert "closed for you" in res
    assert "taskkill /IM chrome.exe /F" in called_cmds
    assert "taskkill /IM notepad.exe /F" in called_cmds

    # 4. Test closing running and not running mix
    called_cmds.clear()

    def mock_run_mix(cmd, *args, **kwargs):
        called_cmds.append(cmd)

        class MockResult:
            returncode = 128 if "chrome" in cmd else 0
            stdout = ""
            stderr = "ERROR: The process not found." if "chrome" in cmd else ""

        return MockResult()

    monkeypatch.setattr(subprocess, "run", mock_run_mix)
    res = _detect_close_app("close chrome and notepad")
    assert "Notepad has been closed for you." in res
    assert "Chrome is not currently running." in res

    # 5. Test unknown app
    res = _detect_close_app("close unknownapp")
    assert res is None


@pytest.mark.parametrize("utterance", ["close not bad", "close noteped", "close nod pad"])
def test_close_app_recovers_observed_notepad_asr_variants(utterance):
    assert router.match_close_app(utterance) == (["notepad"], ["notepad.exe"])


@pytest.mark.parametrize("utterance", ["open noteped", "open nod pad"])
def test_open_app_recovers_observed_notepad_asr_variants(utterance):
    assert router.match_open_app(utterance) == (["notepad"], ["notepad"], None)


def test_calculator_close_prefers_verified_window_identity(monkeypatch):
    import subprocess

    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr(
        "charlie.desktop.windows.find_window",
        lambda title: {"hwnd": 42, "title": "Calculator"},
    )
    closed = []
    monkeypatch.setattr(
        "charlie.desktop.windows.close_window_and_verify",
        lambda title: closed.append(title) or True,
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("must not taskkill a resolved window"),
    )

    assert router.execute_close_app(["calculator"], ["calc.exe"]) == "Calculator has been closed for you."
    assert closed == ["Calculator"]


def test_calculator_close_tries_modern_candidate_after_legacy_candidate_is_absent(monkeypatch):
    import subprocess

    called_cmds = []

    def mock_run(cmd, *args, **kwargs):
        called_cmds.append(cmd)
        return type("Result", (), {
            "returncode": 128 if "calc.exe" in cmd else 0,
            "stdout": "",
            "stderr": "ERROR: The process was not found." if "calc.exe" in cmd else "",
        })()

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr(router, "is_process_running", lambda _: False)

    result = router.execute_close_app(["calculator"], ["calc.exe"])

    assert result == "Calculator has been closed for you."
    assert called_cmds == ["taskkill /IM calc.exe /F", "taskkill /IM CalculatorApp.exe /F"]


def test_calculator_close_reports_not_running_when_all_candidates_are_absent(monkeypatch):
    import subprocess

    called_cmds = []

    def mock_run(cmd, *args, **kwargs):
        called_cmds.append(cmd)
        return type("Result", (), {"returncode": 128, "stdout": "", "stderr": "not found"})()

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr("sys.platform", "win32")

    result = router.execute_close_app(["calculator"], ["calc.exe"])

    assert result == "Calculator is not currently running."
    assert called_cmds == ["taskkill /IM calc.exe /F", "taskkill /IM CalculatorApp.exe /F"]


def test_calculator_close_reports_failure_when_candidate_termination_fails(monkeypatch):
    import subprocess

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 1, "stdout": "", "stderr": "Access denied"})(),
    )
    monkeypatch.setattr("sys.platform", "win32")

    result = router.execute_close_app(["calculator"], ["calc.exe"])

    assert result == "Failed to close Calculator."


def test_close_app_verifies_successful_taskkill_postcondition(monkeypatch):
    import subprocess

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    )
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr(router, "is_process_running", lambda _: True)

    result = router.execute_close_app(["calculator"], ["calc.exe"])

    assert result == "Failed to close Calculator."


def test_close_app_keeps_per_app_truth_for_multiple_apps(monkeypatch):
    import subprocess

    called_cmds = []

    def mock_run(cmd, *args, **kwargs):
        called_cmds.append(cmd)
        if "notepad.exe" in cmd:
            return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        return type("Result", (), {"returncode": 128, "stdout": "", "stderr": "not found"})()

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr(router, "is_process_running", lambda _: False)

    result = router.execute_close_app(["calculator", "notepad"], ["calc.exe", "notepad.exe"])

    assert "Calculator is not currently running." in result
    assert "Notepad has been closed for you." in result
    assert called_cmds == [
        "taskkill /IM calc.exe /F",
        "taskkill /IM CalculatorApp.exe /F",
        "taskkill /IM notepad.exe /F",
    ]


def _vision_content_line(text):
    return "data: " + json.dumps({"choices": [{"delta": {"content": text}}]})


def _vision_payload():
    return {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is visible?"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,screen"}},
                ],
            }
        ],
        "stream": True,
    }


class _VisionClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class _VisionResponse:
    status_code = 200

    def __init__(self, entries, clock=None):
        self.entries = entries
        self.clock = clock

    def raise_for_status(self):
        pass

    async def aiter_lines(self):
        for advance, line in self.entries:
            if self.clock is not None:
                self.clock.advance(advance)
            if line is None:
                await asyncio.Future()
            else:
                yield line


class _VisionStreamContext:
    def __init__(self, response):
        self.response = response
        self.close_count = 0

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *_args):
        self.close_count += 1
        return False


class _VisionClient:
    def __init__(self, response):
        self.context = _VisionStreamContext(response)

    def stream(self, *_args, **_kwargs):
        return self.context

    async def aclose(self):
        pass


async def _collect_vision_followup(brain, client, state):
    return [
        chunk
        async for chunk in brain._stream_followup_once(
            client,
            "vision-model",
            _vision_payload(),
            brain._chat_generation,
            state,
        )
    ]


def test_is_low_confidence_desktop_call_true_for_click_at():
    from charlie.core import _is_low_confidence_desktop_call

    assert _is_low_confidence_desktop_call("desktop_click_at", {"x": 1, "y": 2}) is True


def test_is_low_confidence_desktop_call_uses_mark_resolution(monkeypatch):
    from charlie import core

    monkeypatch.setattr(core.desktop_uia, "is_low_confidence_mark", lambda mark_id: mark_id == 7)
    assert core._is_low_confidence_desktop_call("desktop_click", {"mark_id": 7}) is True
    assert core._is_low_confidence_desktop_call("desktop_click", {"mark_id": 1}) is False


def test_is_low_confidence_desktop_call_false_for_non_mark_tool():
    from charlie.core import _is_low_confidence_desktop_call

    assert _is_low_confidence_desktop_call("desktop_key", {"key": "enter"}) is False


@pytest.mark.asyncio
async def test_auto_halt_threshold_is_one_for_low_confidence_call(monkeypatch, brain_config):
    """desktop_click_at is always low-confidence (raw coords, no verification)
    -- a single failure must halt, not the usual 2-strike threshold."""
    from charlie.core import Brain

    call_count = 0

    def mock_stream(*args, **kwargs):
        nonlocal call_count
        call_count += 1

        class MockResponse:
            def raise_for_status(self):
                pass

            async def aiter_lines(self):
                yield (
                    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"1",'
                    f'"function":{{"name":"desktop_click_at","arguments":"{{\\"x\\":{call_count},\\"y\\":1}}"}}'
                    '}]}}]}'
                )
                yield "data: [DONE]"

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        return MockResponse()

    exec_count = 0

    def mock_execute_tool(name, args):
        nonlocal exec_count
        exec_count += 1
        return "Error: click missed"

    brain = Brain(brain_config)
    monkeypatch.setattr(brain.client, "stream", mock_stream)
    async def approve_desktop_action(*args, **kwargs):
        return True
    monkeypatch.setattr(brain, "request_tool_approval", approve_desktop_action)
    monkeypatch.setattr("charlie.tools.registry.execute_tool", mock_execute_tool)

    results = []
    async for chunk in brain.chat_stream("click somewhere"):
        results.append(chunk)

    assert any("halted" in str(r).lower() for r in results)
    assert exec_count == 1  # halted after the first failure, never retried


@pytest.mark.asyncio
async def test_auto_halt_threshold_is_two_for_regular_desktop_call(monkeypatch, brain_config):
    """A non-mark-based desktop tool (desktop_key) keeps the original 2-strike threshold."""
    from charlie.core import Brain

    def mock_stream(*args, **kwargs):
        class MockResponse:
            def raise_for_status(self):
                pass

            async def aiter_lines(self):
                yield (
                    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"1",'
                    '"function":{"name":"desktop_key","arguments":"{\\"key\\":\\"enter\\"}"}'
                    '}]}}]}'
                )
                yield "data: [DONE]"

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        return MockResponse()

    exec_count = 0

    def mock_execute_tool(name, args):
        nonlocal exec_count
        exec_count += 1
        return "Error: key send failed"

    brain = Brain(brain_config)
    monkeypatch.setattr(brain.client, "stream", mock_stream)
    async def approve_desktop_action(*args, **kwargs):
        return True
    monkeypatch.setattr(brain, "request_tool_approval", approve_desktop_action)
    monkeypatch.setattr("charlie.tools.registry.execute_tool", mock_execute_tool)

    results = []
    async for chunk in brain.chat_stream("press enter"):
        results.append(chunk)

    assert any("halted" in str(r).lower() for r in results)
    assert exec_count == 1  # repeated identical failure was suppressed before re-execution


@pytest.mark.asyncio
async def test_chat_stream_injects_idle_seconds_when_desktop_available(monkeypatch, brain_config):
    from charlie import core

    monkeypatch.setattr(core, "_DESKTOP_AVAILABLE", True)
    monkeypatch.setattr(core, "desktop_session", type("S", (), {"user_idle_seconds": staticmethod(lambda: 12.0)}))

    captured_payloads = []

    async def mock_stream_completion(payload, generation):
        captured_payloads.append(payload)
        return ("hi", [])

    brain = core.Brain(brain_config)
    monkeypatch.setattr(brain, "_stream_completion", mock_stream_completion)

    async for _ in brain.chat_stream("hello"):
        pass

    system_msg = captured_payloads[0]["messages"][0]["content"]
    assert "idle time: 12s" in system_msg


@pytest.mark.asyncio
async def test_chat_stream_omits_idle_seconds_when_desktop_unavailable(monkeypatch, brain_config):
    from charlie import core

    monkeypatch.setattr(core, "desktop_session", None)

    captured_payloads = []

    async def mock_stream_completion(payload, generation):
        captured_payloads.append(payload)
        return ("hi", [])

    brain = core.Brain(brain_config)
    monkeypatch.setattr(brain, "_stream_completion", mock_stream_completion)

    async for _ in brain.chat_stream("hello"):
        pass

    system_msg = captured_payloads[0]["messages"][0]["content"]
    assert "idle time" not in system_msg.lower()


def test_detect_background_task_status_no_active_task(monkeypatch):
    """Casual phrasing like "what are you doing" must fall through to normal
    chat when nothing is actually running -- not hijacked just by regex match."""
    from charlie import background_task
    from charlie.router import answer_background_task_status as _detect_background_task_status

    monkeypatch.setattr(background_task, "get_current_task", lambda: None)
    assert _detect_background_task_status("what are you doing") is None


def test_detect_background_task_status_no_match():
    from charlie.router import answer_background_task_status as _detect_background_task_status

    assert _detect_background_task_status("what's the weather like") is None


def test_detect_background_task_status_running_task(monkeypatch):
    from charlie import background_task
    from charlie.router import answer_background_task_status as _detect_background_task_status

    task = background_task.BackgroundTask(
        id="t1", text="open notepad and calculator", status="running",
        steps=["Open notepad", "Open calculator"], current_step=0,
    )
    monkeypatch.setattr(background_task, "get_current_task", lambda: task)

    res = _detect_background_task_status("what are you doing")
    assert res is not None
    assert "open notepad and calculator" in res
    assert "step 1 of 2" in res
    assert "Open notepad" in res


def test_detect_background_task_status_paused_task(monkeypatch):
    from charlie import background_task
    from charlie.router import answer_background_task_status as _detect_background_task_status

    task = background_task.BackgroundTask(id="t1", text="x", status="paused")
    monkeypatch.setattr(background_task, "get_current_task", lambda: task)

    res = _detect_background_task_status("how's the task going")
    assert res is not None
    assert "paused" in res


def test_detect_background_task_status_terminal_task_falls_through(monkeypatch):
    """A finished task must not keep answering "what are you doing" forever --
    once terminal, the query should fall through to normal chat."""
    from charlie import background_task
    from charlie.router import answer_background_task_status as _detect_background_task_status

    task = background_task.BackgroundTask(id="t1", text="x", status="done")
    monkeypatch.setattr(background_task, "get_current_task", lambda: task)
    assert _detect_background_task_status("what are you doing") is None


def test_detect_open_app(monkeypatch):
    import subprocess


    called_cmds = []

    def mock_popen(cmd, *args, **kwargs):
        called_cmds.append(cmd)

        class MockProcess:
            pid = 12345

        return MockProcess()

    monkeypatch.setattr(subprocess, "Popen", mock_popen)
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("charlie.router.is_process_running", lambda name: False)

    # 1. Test opening single app
    res = _detect_open_app("open calculator")
    msg, remaining = res
    assert msg == "I've opened Calculator for you."
    assert remaining is None
    assert 'start "" calc' in called_cmds
    called_cmds.clear()
    res = _detect_open_app("open chrome and calculator")
    msg, remaining = res
    assert "Calculator and Chrome" in msg
    assert remaining is None
    assert 'start "" chrome' in called_cmds
    assert 'start "" calc' in called_cmds

    # Bare websites belong to Charlie BrowserSession, not external OS launch.
    called_cmds.clear()
    assert router.match_open_app("open youtube and github") == (
        [], [], "open youtube and github"
    )
    assert router.match_open_app("open reddit.com, wikipedia.org and https://neon.tech") == (
        [], [], "open reddit.com, wikipedia.org and https://neon.tech"
    )
    assert called_cmds == []

    # 5. Test float/version number exclusion (must not match as domain)
    res = _detect_open_app("open version 3.5")
    assert res is None

    # 6. Test unknown app
    res = _detect_open_app("open unknownapp")
    assert res is None

    # 7. Compound instruction: the app still opens as a side effect (no more
    # full bypass), and the leftover instruction comes back for the caller
    # to hand to the LLM instead of the fast-path silently doing nothing extra.
    called_cmds.clear()
    res = _detect_open_app("open notepad and write hello")
    assert res is not None
    msg, remaining = res
    assert "Notepad" in msg
    assert remaining == "and write hello"
    assert 'start "" notepad' in called_cmds


def test_detect_open_app_partial_failure(monkeypatch):
    """Partial launch failures must not crash and must format correctly."""
    import os
    import subprocess


    call_count = 0

    def mock_popen(cmd, *args, **kwargs):
        nonlocal call_count
        call_count += 1

        class MockProcess:
            pid = 12345

        # First call succeeds, second call fails
        if call_count == 1:
            return MockProcess()
        raise OSError("Mock launch failure")

    def mock_startfile(_cmd, *_a, **_kw):
        raise OSError("Mock startfile failure")

    monkeypatch.setattr(subprocess, "Popen", mock_popen)
    monkeypatch.setattr(os, "startfile", mock_startfile, raising=False)
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("charlie.router.is_process_running", lambda name: False)

    # Test: open two apps, one fails
    res = _detect_open_app("open chrome notepad")
    msg, remaining = res
    assert remaining is None
    assert "Chrome" in msg  # First app succeeds
    assert "Notepad" in msg  # Second app should appear in failed list
    assert "Failed to open" in msg
    # Ensure no raw tuple syntax leaks (the old bug)
    assert "(" not in msg or msg.count("(") == msg.count(")")
    # Ensure .exe/.title tuple artifacts don't leak
    assert "error_detail" not in msg.lower()
    assert "OSError" not in msg


def test_detect_open_app_all_failures(monkeypatch):
    """All apps failing must return a graceful error, not a crash."""
    import os
    import subprocess


    def mock_fail(*_a, **_kw):
        raise OSError("Mock failure")

    monkeypatch.setattr(subprocess, "Popen", mock_fail)
    monkeypatch.setattr(os, "startfile", mock_fail, raising=False)
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("charlie.router.is_process_running", lambda name: False)

    res = _detect_open_app("open chrome notepad")
    assert res is not None
    msg, remaining = res
    assert remaining is None
    assert "chrome" in msg.lower() or "Chrome" in msg
    # Must not crash with AttributeError on tuples


@pytest.mark.skipif(sys.platform != "win32", reason="process name ends in .exe on Windows only")
def testis_process_running_against_real_processes():
    from charlie.utils import is_process_running

    assert is_process_running("python.exe") is True  # this test itself is running
    assert is_process_running("definitely-not-a-real-process-xyz.exe") is False


def test_detect_open_app_focuses_already_running_instead_of_relaunching(monkeypatch):
    """Regression: several apps (Windows 11's modern Notepad included) allow
    multiple simultaneous instances, so a blind relaunch piles up duplicate
    windows instead of erroring like a single-instance app would -- found
    live during Phase 3 background-task testing (4+ Notepad windows from
    repeated "open notepad" calls). An already-running app gets focused via
    the same native focus_window() the desktop_focus tool uses, not relaunched."""
    import subprocess


    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("charlie.router.is_process_running", lambda name: name == "notepad.exe")

    popen_calls = []
    monkeypatch.setattr(
        subprocess, "Popen", lambda cmd, *a, **kw: popen_calls.append(cmd)
    )

    focus_calls = []
    monkeypatch.setattr(
        "charlie.desktop.windows.focus_window",
        lambda title: focus_calls.append(title) or f"Focused window: {title}",
    )

    res = _detect_open_app("open notepad")

    msg, remaining = res
    assert remaining is None
    assert "already open" in msg
    assert focus_calls == ["notepad"]
    assert popen_calls == []


def test_detect_open_app_mixed_running_and_not_running(monkeypatch):
    """One app already running (focused) and one not (launched) in a single
    multi-app request are handled independently."""
    import subprocess


    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("charlie.router.is_process_running", lambda name: name == "notepad.exe")

    popen_calls = []
    monkeypatch.setattr(
        subprocess, "Popen", lambda cmd, *a, **kw: popen_calls.append(cmd)
    )

    focus_calls = []
    monkeypatch.setattr(
        "charlie.desktop.windows.focus_window",
        lambda title: focus_calls.append(title) or f"Focused window: {title}",
    )

    res = _detect_open_app("open notepad and calculator")

    msg, remaining = res
    assert remaining is None
    assert "already open" in msg
    assert "opened" in msg.lower()
    assert focus_calls == ["notepad"]
    assert 'start "" calc' in popen_calls


def test_detect_open_app_does_not_open_filename_as_website(monkeypatch):
    """Real bug: "open notepad and write X and save it as test.txt" matched
    "test.txt" as a probable domain (".txt" looks exactly like a TLD-shaped
    suffix) and opened https://test.txt in a browser instead of treating it
    as a filename."""
    import subprocess


    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("charlie.router.is_process_running", lambda name: False)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: None)

    res = _detect_open_app("open notepad and write this is a test and save it as test.txt")
    assert res is not None
    msg, _remaining = res
    assert "test.txt" not in msg


@pytest.mark.asyncio
async def test_chat_stream_fast_path_close_open(monkeypatch, brain_config):
    import subprocess

    from charlie.core import Brain

    def mock_run(cmd, *args, **kwargs):
        class MockResult:
            returncode = 0
            stdout = ""
            stderr = ""

        return MockResult()

    def mock_popen(cmd, *args, **kwargs):
        class MockProcess:
            pid = 12345
        return MockProcess()

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(subprocess, "Popen", mock_popen)
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("charlie.router.is_process_running", lambda name: False)

    called_stream = False

    def mock_stream(*args, **kwargs):
        nonlocal called_stream
        called_stream = True

    brain = Brain(brain_config)
    monkeypatch.setattr(brain.client, "stream", mock_stream)

    # Test close fast-path integration
    results = []
    async for chunk in brain.chat_stream("close chrome"):
        results.append(chunk)
    assert results == ["Chrome has been closed for you."]
    assert not called_stream

    # Test open fast-path integration
    results = []
    async for chunk in brain.chat_stream("open calculator"):
        results.append(chunk)
    assert results == ["I've opened Calculator for you."]
    assert not called_stream


@pytest.mark.asyncio
async def test_chat_stream_background_task_status_fast_path(monkeypatch, brain_config):
    """A live progress query about a running background task must be
    answered deterministically, with no LLM round-trip."""
    from charlie import background_task
    from charlie.core import Brain

    task = background_task.BackgroundTask(
        id="t1", text="tidy up my downloads folder", status="running",
        steps=["List files", "Move files"], current_step=1,
    )
    monkeypatch.setattr(background_task, "get_current_task", lambda: task)

    called_stream = False

    def mock_stream(*args, **kwargs):
        nonlocal called_stream
        called_stream = True

    brain = Brain(brain_config)
    monkeypatch.setattr(brain.client, "stream", mock_stream)

    results = []
    async for chunk in brain.chat_stream("what are you doing"):
        results.append(chunk)

    assert len(results) == 1
    assert "tidy up my downloads folder" in results[0]
    assert "step 2 of 2" in results[0]
    assert not called_stream


@pytest.mark.asyncio
async def test_chat_stream_compound_open_app_continues_with_llm(monkeypatch, brain_config):
    """Decision 2: a compound "open X and <do something>" instruction must
    open the app deterministically via the fast-path (no LLM round-trip
    needed for that part) AND continue the turn with the leftover
    instruction, instead of the old all-or-nothing bypass that sent the
    whole compound sentence to the LLM (re-discovering how to open the app
    via slow, flaky tool calls -- the exact pattern observed live)."""
    import subprocess

    from charlie.core import Brain

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: type("P", (), {"pid": 1})())
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("charlie.router.is_process_running", lambda name: False)

    brain = Brain(brain_config)

    captured_payloads = []

    async def mock_stream_completion(payload, generation):
        captured_payloads.append(payload)
        return ("Sure, writing that now.", [])

    monkeypatch.setattr(brain, "_stream_completion", mock_stream_completion)

    results = []
    async for chunk in brain.chat_stream("open notepad and write hello"):
        results.append(chunk)

    joined = "".join(results)
    assert "Notepad" in joined  # fast-path confirmation streamed first
    assert "Sure, writing that now." in joined  # then the LLM continuation

    # The LLM only saw the leftover instruction, not the app-open part it
    # can't help with any faster than the fast-path already did.
    assert len(captured_payloads) == 1
    llm_message = captured_payloads[0]["messages"][-1]["content"]
    assert "write hello" in llm_message
    assert "open notepad" not in llm_message.lower()

    # But session history keeps the user's full original utterance.
    user_history = [m for m in brain.history if m["role"] == "user"]
    assert user_history[-1]["content"] == "open notepad and write hello"


@pytest.mark.asyncio
async def test_visual_screenshot_queued_after_initial_payload_not_before(monkeypatch):
    """Regression test for the ordering bug: a visual-content query used to
    call desktop_screenshot (which queues a pending vision image) BEFORE the
    initial payload was built. _build_payload unconditionally pops the
    pending image whenever vision is enabled, so the image was consumed by
    the initial (non-vision-routed) request and never reached the follow-up.

    The fix instead records intent and injects a synthetic desktop_screenshot
    tool call once the model's own (empty) tool_calls are known, so it flows
    through the same tool-execution-loop + follow-up path as a real
    model-initiated call. This test proves: (1) the initial _build_payload
    call happens before desktop_screenshot ever executes, and (2) a
    follow-up _build_payload call is still reached even though the model
    itself returned zero tool calls (i.e. the early-return branch is
    correctly bypassed).
    """
    from charlie.config import Config
    from charlie.core import Brain

    cfg = Config(
        llm_url="https://example.com/v1",
        llm_key="test-key",
        llm_model="dummy",
        iteration_budget_max=3,
        native_tool_calling=True,
        vision_enabled=True,
        desktop_control_enabled=True,
    )

    events = []

    def mock_execute(name, args):
        events.append(("execute_tool", name))
        return "Screenshot captured for vision analysis of the current desktop state."

    monkeypatch.setattr("charlie.tools.registry.execute_tool", mock_execute)

    async def mock_stream_completion(*args, **kwargs):
        # Model returns no content and no tool calls at all this turn.
        return ("", [])

    brain = Brain(cfg)
    monkeypatch.setattr(brain, "_stream_completion", mock_stream_completion)

    orig_build_payload = brain._build_payload

    def spy_build_payload(messages, skip_tools=False):
        events.append(("build_payload",))
        return orig_build_payload(messages, skip_tools=skip_tools)

    monkeypatch.setattr(brain, "_build_payload", spy_build_payload)

    async def mock_stream_followup_once(*args, **kwargs):
        return
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(brain, "_stream_followup_once", mock_stream_followup_once)

    async for _ in brain.chat_stream("what am I looking at", skip_pre_search=True):
        pass

    # The initial payload must be built before desktop_screenshot ever runs --
    # this is exactly the ordering the bug got backwards.
    assert events[0] == ("build_payload",)
    execute_idx = events.index(("execute_tool", "desktop_screenshot"))
    assert execute_idx > 0

    # Both the initial and the follow-up payload builds must have happened --
    # proves the synthetic call bypassed the "if not tool_calls: return" early
    # exit, even though the model itself returned zero real tool calls.
    assert events.count(("build_payload",)) == 2


@pytest.mark.asyncio
async def test_interactive_vision_completes_before_deadlines(monkeypatch, brain_config):
    from charlie import core

    clock = _VisionClock()
    monkeypatch.setattr(core, "_monotonic", clock)
    response = _VisionResponse(
        [
            (0.0, _vision_content_line("vision answer")),
            (0.0, 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}'),
        ],
        clock,
    )
    client = _VisionClient(response)
    brain = Brain(brain_config)
    state = FollowupStreamState()
    try:
        chunks = await _collect_vision_followup(brain, client, state)
    finally:
        await brain.close()

    assert chunks == ["vision answer"]
    assert state.finish_reason == "stop"
    assert state.completion_status == "completed"
    assert state.timeout_reason is None
    assert client.context.close_count == 1


@pytest.mark.asyncio
async def test_interactive_vision_server_eof_wins_over_elapsed_idle_deadline(monkeypatch, brain_config):
    from charlie import core

    clock = _VisionClock()
    monkeypatch.setattr(core, "_monotonic", clock)
    monkeypatch.setattr(core, "FIRST_CONTENT_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(core, "STREAM_IDLE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(core, "ABSOLUTE_VISION_TIMEOUT_SECONDS", 2.0)
    client = _VisionClient(
        _VisionResponse(
            [
                (0.0, _vision_content_line("vision answer")),
                (0.2, "data: [DONE]"),
            ],
            clock,
        )
    )
    brain = Brain(brain_config)
    state = FollowupStreamState()
    try:
        chunks = await _collect_vision_followup(brain, client, state)
    finally:
        await brain.close()

    assert chunks == ["vision answer"]
    assert state.completion_status == "completed"
    assert state.timeout_reason is None
    assert client.context.close_count == 1


@pytest.mark.asyncio
async def test_interactive_vision_headers_without_content_hit_first_content_timeout(monkeypatch, brain_config):
    from charlie import core

    clock = _VisionClock()
    monkeypatch.setattr(core, "_monotonic", clock)
    monkeypatch.setattr(core, "FIRST_CONTENT_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(core, "STREAM_IDLE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(core, "ABSOLUTE_VISION_TIMEOUT_SECONDS", 0.02)
    response = _VisionResponse(
        [
            (1.1, 'data: {"choices":[{"delta":{}}]}'),
            (0.0, None),
        ],
        clock,
    )
    client = _VisionClient(response)
    brain = Brain(brain_config)
    state = FollowupStreamState()
    try:
        with pytest.raises(asyncio.TimeoutError):
            await _collect_vision_followup(brain, client, state)
    finally:
        await brain.close()

    assert state.timeout_reason == "first_content_timeout"
    assert state.completion_status == "timeout"
    assert client.context.close_count == 1


@pytest.mark.asyncio
async def test_interactive_vision_active_stream_survives_old_nine_second_boundary(monkeypatch, brain_config):
    from charlie import core

    clock = _VisionClock()
    monkeypatch.setattr(core, "_monotonic", clock)
    monkeypatch.setattr(core, "FIRST_CONTENT_TIMEOUT_SECONDS", 12.0)
    monkeypatch.setattr(core, "STREAM_IDLE_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(core, "ABSOLUTE_VISION_TIMEOUT_SECONDS", 20.0)
    entries = [(0.0, _vision_content_line("chunk-0"))]
    entries.extend((0.5, _vision_content_line(f"chunk-{index}")) for index in range(1, 21))
    entries.extend(
        [
            (0.0, 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}'),
            (0.0, "data: [DONE]"),
        ]
    )
    client = _VisionClient(_VisionResponse(entries, clock))
    brain = Brain(brain_config)
    state = FollowupStreamState()
    try:
        chunks = await _collect_vision_followup(brain, client, state)
    finally:
        await brain.close()

    assert len(chunks) == 21
    assert "chunk-20" in chunks[-1]
    assert state.completion_status == "completed"
    assert client.context.close_count == 1


@pytest.mark.asyncio
async def test_interactive_vision_stream_idle_timeout_preserves_accumulated_state(monkeypatch, brain_config):
    from charlie import core

    clock = _VisionClock()
    monkeypatch.setattr(core, "_monotonic", clock)
    monkeypatch.setattr(core, "FIRST_CONTENT_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(core, "STREAM_IDLE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(core, "ABSOLUTE_VISION_TIMEOUT_SECONDS", 2.0)
    client = _VisionClient(
        _VisionResponse(
            [
                (0.0, _vision_content_line("partial")),
                (0.0, None),
            ],
            clock,
        )
    )
    brain = Brain(brain_config)
    state = FollowupStreamState()
    try:
        with pytest.raises(asyncio.TimeoutError):
            await _collect_vision_followup(brain, client, state)
    finally:
        await brain.close()

    assert state.accumulated == "partial"
    assert state.timeout_reason == "stream_idle_timeout"
    assert state.completion_status == "partially_completed"
    assert client.context.close_count == 1


@pytest.mark.asyncio
async def test_interactive_vision_absolute_timeout_bounds_continuously_active_stream(monkeypatch, brain_config):
    from charlie import core

    clock = _VisionClock()
    monkeypatch.setattr(core, "_monotonic", clock)
    monkeypatch.setattr(core, "FIRST_CONTENT_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(core, "STREAM_IDLE_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(core, "ABSOLUTE_VISION_TIMEOUT_SECONDS", 1.0)
    client = _VisionClient(
        _VisionResponse(
            [
                (0.0, _vision_content_line("a")),
                (0.4, _vision_content_line("b")),
                (0.4, _vision_content_line("c")),
                (0.4, _vision_content_line("d")),
            ],
            clock,
        )
    )
    brain = Brain(brain_config)
    state = FollowupStreamState()
    chunks = []
    try:
        with pytest.raises(asyncio.TimeoutError):
            async for chunk in brain._stream_followup_once(
                client,
                "vision-model",
                _vision_payload(),
                brain._chat_generation,
                state,
            ):
                chunks.append(chunk)
    finally:
        await brain.close()

    assert chunks == ["a", "b", "c"]
    assert state.timeout_reason == "absolute_vision_timeout"
    assert state.completion_status == "partially_completed"
    assert client.context.close_count == 1


@pytest.mark.asyncio
async def test_interactive_vision_empty_metadata_does_not_reset_idle_timeout(monkeypatch, brain_config):
    from charlie import core

    clock = _VisionClock()
    monkeypatch.setattr(core, "_monotonic", clock)
    monkeypatch.setattr(core, "FIRST_CONTENT_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(core, "STREAM_IDLE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(core, "ABSOLUTE_VISION_TIMEOUT_SECONDS", 2.0)
    client = _VisionClient(
        _VisionResponse(
            [
                (0.0, _vision_content_line("first")),
                (0.0, 'data: {"choices":[{"delta":{}}]}'),
                (0.0, None),
            ],
            clock,
        )
    )
    brain = Brain(brain_config)
    state = FollowupStreamState()
    try:
        with pytest.raises(asyncio.TimeoutError):
            await _collect_vision_followup(brain, client, state)
    finally:
        await brain.close()

    assert state.accumulated == "first"
    assert state.timeout_reason == "stream_idle_timeout"
    assert client.context.close_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", [True, False])
async def test_interactive_vision_timeout_surfaces_partial_or_existing_fallback(monkeypatch, partial):
    from charlie import core
    from charlie.tools import set_pending_vision_image

    config = Config(
        llm_url="http://cloud.example/v1",
        llm_key="test-key",
        llm_model="chat-model",
        vision_enabled=True,
        vision_llm_url="http://local-vision/v1",
        vision_llm_key="vision-key",
        vision_llm_model="vision-model",
        desktop_control_enabled=True,
        memory_graph_db=":memory:",
        world_model_db_path=":memory:",
    )
    monkeypatch.setattr(core, "FIRST_CONTENT_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(core, "STREAM_IDLE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(core, "ABSOLUTE_VISION_TIMEOUT_SECONDS", 0.5)
    response = _VisionResponse(
        (
            [(0.0, _vision_content_line("partial vision answer")), (0.0, None)]
            if partial
            else [(0.0, None)]
        )
    )
    client = _VisionClient(response)
    brain = Brain(config, register_panic_hotkey=False)
    brain._vision_client = client
    brain._vision_model = "vision-model"

    async def empty_initial_completion(*_args, **_kwargs):
        return "", []

    def execute_tool(name, _arguments):
        if name == "desktop_screenshot":
            set_pending_vision_image("data:image/png;base64,screen")
        return "Screen observation marks."

    monkeypatch.setattr(brain, "_stream_completion", empty_initial_completion)
    monkeypatch.setattr("charlie.tools.registry.execute_tool", execute_tool)
    try:
        result = [
            chunk
            async for chunk in brain.chat_stream(
                "What do you see on my screen?",
                platform="voice",
                skip_pre_search=True,
            )
        ]
    finally:
        set_pending_vision_image(None)
        await brain.close()

    if partial:
        assert result == ["partial vision answer"]
        assert brain.history[-1] == {"role": "assistant", "content": "partial vision answer"}
    else:
        assert result == ["I couldn't inspect the screen within the interactive voice time budget."]
        assert not any(message.get("role") == "assistant" for message in brain.history)
    assert client.context.close_count == 1


@pytest.mark.asyncio
async def test_interactive_vision_payload_uses_160_tokens(monkeypatch):
    from charlie import core

    config = Config(
        llm_url="https://cloud.example/v1",
        llm_key="test-key",
        llm_model="chat-model",
        native_tool_calling=True,
        vision_enabled=True,
        vision_llm_url="",
        vision_llm_key="",
    )
    brain = Brain(config, register_panic_hotkey=False)
    brain._pending_vision_image_url = "data:image/png;base64,screen"
    try:
        payload = brain._build_payload([{"role": "user", "content": "What is visible?"}])
    finally:
        await brain.close()

    assert payload["max_tokens"] == 160
    assert payload["stream"] is True
    assert payload["temperature"] == core._LLM_TEMPERATURE
    assert "Answer in 1-3 concise sentences." in payload["messages"][0]["content"]
    assert "Prioritize the user's specific question." in payload["messages"][0]["content"]


@pytest.mark.asyncio
async def test_browser_image_description_keeps_300_token_budget(brain_config):
    captured = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "browser description"}}]}

    class BrowserVisionClient:
        async def post(self, _path, **kwargs):
            captured.update(kwargs)
            return Response()

        async def aclose(self):
            pass

    brain = Brain(brain_config, register_panic_hotkey=False)
    brain._vision_client = BrowserVisionClient()
    brain._vision_model = "vision-model"
    try:
        result = await brain._describe_image("data:image/png;base64,screen")
    finally:
        await brain.close()

    assert result == "browser description"
    assert captured["json"]["max_tokens"] == 300


@pytest.mark.asyncio
async def test_non_voice_vision_keeps_deep_budget(monkeypatch, brain_config):
    brain_config.vision_enabled = True
    brain = Brain(brain_config)

    try:
        async def deep_stream(*_args, **_kwargs):
            await asyncio.sleep(0.02)
            yield "deep vision result"

        brain._chat_stream_impl = deep_stream
        web_chunks = [
            chunk
            async for chunk in brain.chat_stream(
                "What do you see on my screen?",
                platform="web",
            )
        ]
        assert web_chunks == ["deep vision result"]
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_bounded_vision_cleanup_reaps_hanging_cleanup(monkeypatch):
    from charlie import core

    monkeypatch.setattr(core, "_VISION_CLEANUP_TIMEOUT_S", 0.01)

    async def hanging_cleanup():
        await asyncio.Future()

    tasks_before = set(asyncio.all_tasks())
    assert await core._await_bounded_cleanup(hanging_cleanup(), "test") == "timeout"
    assert asyncio.all_tasks() == tasks_before


@pytest.mark.asyncio
async def test_overlapping_research_turn_is_cancelled_before_latest_voice_turn_runs(brain_config, monkeypatch):
    brain = Brain(brain_config)
    research_started = asyncio.Event()

    async def blocked_research(query, _session_id, turn_id=None):
        if "research" not in query:
            return None
        research_started.set()
        await asyncio.Future()

    async def latest_completion(_payload, _generation):
        return "latest voice answer", []

    monkeypatch.setattr(brain, "_run_research", blocked_research)
    monkeypatch.setattr(brain, "_stream_completion", latest_completion)

    async def collect(text):
        return [chunk async for chunk in brain.chat_stream(text, platform="voice")]

    old_task = asyncio.create_task(collect("research the latest runtime behavior"))
    try:
        await asyncio.wait_for(research_started.wait(), timeout=1.0)
        brain.cancel_chat()
        old_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await old_task

        latest = await collect("tell me something new")
        assert latest == ["latest voice answer"]
    finally:
        if not old_task.done():
            old_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await old_task
        await brain.close()


@pytest.mark.asyncio
async def test_overlapping_vision_turn_closes_stale_stream_before_latest_turn(monkeypatch, brain_config):
    brain_config.vision_enabled = True
    brain = Brain(brain_config)
    vision_started = asyncio.Event()
    vision_closed = asyncio.Event()

    async def stale_vision(*_args, **_kwargs):
        vision_started.set()
        try:
            await asyncio.Future()
        finally:
            vision_closed.set()
        yield "stale vision result"

    async def latest_vision(*_args, **_kwargs):
        yield "latest vision result"

    brain._chat_stream_impl = stale_vision

    async def collect():
        return [
            chunk
            async for chunk in brain.chat_stream("What do you see on my screen?", platform="voice")
        ]

    old_task = asyncio.create_task(collect())
    try:
        await asyncio.wait_for(vision_started.wait(), timeout=1.0)
        brain.cancel_chat()
        old_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await old_task
        assert vision_closed.is_set()

        brain._chat_stream_impl = latest_vision
        assert await collect() == ["latest vision result"]
    finally:
        if not old_task.done():
            old_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await old_task
        await brain.close()


@pytest.mark.asyncio
async def test_overlapping_conversation_turn_closes_stale_stream_before_latest_turn(brain_config):
    brain = Brain(brain_config)
    conversation_started = asyncio.Event()
    conversation_closed = asyncio.Event()

    async def stale_conversation(*_args, **_kwargs):
        conversation_started.set()
        try:
            await asyncio.Future()
        finally:
            conversation_closed.set()
        yield "stale conversation result"

    async def latest_conversation(*_args, **_kwargs):
        yield "latest conversation result"

    brain._chat_stream_impl = stale_conversation

    async def collect():
        return [chunk async for chunk in brain.chat_stream("tell me something", platform="voice")]

    old_task = asyncio.create_task(collect())
    try:
        await asyncio.wait_for(conversation_started.wait(), timeout=1.0)
        brain.cancel_chat()
        old_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await old_task
        assert conversation_closed.is_set()

        brain._chat_stream_impl = latest_conversation
        assert await collect() == ["latest conversation result"]
    finally:
        if not old_task.done():
            old_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await old_task
        await brain.close()


@pytest.mark.asyncio
async def test_chat_stream_skip_tools(monkeypatch, brain_config):
    from charlie.core import Brain

    called_tool = False
    def mock_execute(*args, **kwargs):
        nonlocal called_tool
        called_tool = True
        return "mocked"

    monkeypatch.setattr("charlie.tools.registry.execute_tool", mock_execute)

    async def mock_stream_completion(*args, **kwargs):
        return ('Hello world TOOL: file_write("C:\\\\test.txt", "hello")', [])

    brain = Brain(brain_config)
    monkeypatch.setattr(brain, "_stream_completion", mock_stream_completion)

    results = []
    async for chunk in brain.chat_stream("test", skip_tools=True):
        results.append(chunk)

    assert "Hello world" in "".join(results)
    assert "TOOL: file_write" not in "".join(results)
    assert not called_tool


def test_build_native_tool_results_truncates_oversized_content():
    """A tool result (e.g. a raw image blob from an MCP screenshot tool) must
    be capped before entering the native tool-calling follow-up payload --
    only the older text-based path truncated via _format_text_tool_summary,
    so an oversized/unbounded native tool result reached the API as-is and
    400'd (observed with mcp_windows-mcp_Screenshot's result)."""
    from charlie.core import _TOOL_RESULT_MAX_CHARS, _build_native_tool_results

    tool_calls = [{"id": "call_1", "name": "mcp_windows-mcp_Screenshot", "arguments": {}}]
    huge_result = "x" * (_TOOL_RESULT_MAX_CHARS * 5)

    messages = _build_native_tool_results(tool_calls, [huge_result])

    assert len(messages) == 1
    assert messages[0]["tool_call_id"] == "call_1"
    assert messages[0]["role"] == "tool"
    assert messages[0]["name"] == "mcp_windows-mcp_Screenshot"
    assert len(messages[0]["content"]) == _TOOL_RESULT_MAX_CHARS


def test_build_native_tool_results_keeps_short_content_unchanged():
    from charlie.core import _build_native_tool_results

    tool_calls = [{"id": "call_2", "name": "web_search", "arguments": {}}]
    messages = _build_native_tool_results(tool_calls, ["short result"])

    assert messages[0]["content"] == "short result"


def test_plugin_fs_search_has_extended_timeout():
    """A recursive filesystem search (esp. full-disk with PLUGIN_ALLOW_DIRS=*)
    needs much more than the 15s default tool-call timeout."""
    from charlie.core import _TOOL_TIMEOUT_SEC, _TOOL_TIMEOUTS

    assert _TOOL_TIMEOUTS["plugin_fs_search"] > _TOOL_TIMEOUT_SEC
