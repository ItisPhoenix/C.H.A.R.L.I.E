import sys

import pytest

from charlie.config import Config
from charlie.core import Brain


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

    from charlie.core import _detect_close_app

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
    monkeypatch.setattr("charlie.tools.registry.execute_tool", mock_execute_tool)

    results = []
    async for chunk in brain.chat_stream("press enter"):
        results.append(chunk)

    assert any("halted" in str(r).lower() for r in results)
    assert exec_count == 2  # took 2 identical failures before halting


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
    from charlie.core import _detect_background_task_status

    monkeypatch.setattr(background_task, "get_current_task", lambda: None)
    assert _detect_background_task_status("what are you doing") is None


def test_detect_background_task_status_no_match():
    from charlie.core import _detect_background_task_status

    assert _detect_background_task_status("what's the weather like") is None


def test_detect_background_task_status_running_task(monkeypatch):
    from charlie import background_task
    from charlie.core import _detect_background_task_status

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
    from charlie.core import _detect_background_task_status

    task = background_task.BackgroundTask(id="t1", text="x", status="paused")
    monkeypatch.setattr(background_task, "get_current_task", lambda: task)

    res = _detect_background_task_status("how's the task going")
    assert res is not None
    assert "paused" in res


def test_detect_background_task_status_terminal_task_falls_through(monkeypatch):
    """A finished task must not keep answering "what are you doing" forever --
    once terminal, the query should fall through to normal chat."""
    from charlie import background_task
    from charlie.core import _detect_background_task_status

    task = background_task.BackgroundTask(id="t1", text="x", status="done")
    monkeypatch.setattr(background_task, "get_current_task", lambda: task)
    assert _detect_background_task_status("what are you doing") is None


def test_detect_open_app(monkeypatch):
    import subprocess

    from charlie.core import _detect_open_app

    called_cmds = []

    def mock_popen(cmd, *args, **kwargs):
        called_cmds.append(cmd)

        class MockProcess:
            pid = 12345

        return MockProcess()

    monkeypatch.setattr(subprocess, "Popen", mock_popen)
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("charlie.core.is_process_running", lambda name: False)

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

    # 3. Test opening whitelisted websites by name
    called_cmds.clear()
    res = _detect_open_app("open youtube and github")
    msg, remaining = res
    assert "Youtube and Github" in msg or "Github and Youtube" in msg
    assert remaining is None
    assert 'start "" https://youtube.com' in called_cmds
    assert 'start "" https://github.com' in called_cmds

    # 4. Test opening generic domains/URLs
    called_cmds.clear()
    res = _detect_open_app("open reddit.com, wikipedia.org and https://neon.tech")
    msg, remaining = res
    assert "reddit.com" in msg
    assert "wikipedia.org" in msg
    assert "https://neon.tech" in msg
    assert remaining is None
    assert 'start "" https://reddit.com' in called_cmds
    assert 'start "" https://wikipedia.org' in called_cmds
    assert 'start "" https://neon.tech' in called_cmds

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

    from charlie.core import _detect_open_app

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
    monkeypatch.setattr("charlie.core.is_process_running", lambda name: False)

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

    from charlie.core import _detect_open_app

    def mock_fail(*_a, **_kw):
        raise OSError("Mock failure")

    monkeypatch.setattr(subprocess, "Popen", mock_fail)
    monkeypatch.setattr(os, "startfile", mock_fail, raising=False)
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("charlie.core.is_process_running", lambda name: False)

    res = _detect_open_app("open chrome notepad")
    assert res is not None
    msg, remaining = res
    assert remaining is None
    assert "chrome" in msg.lower() or "Chrome" in msg
    # Must not crash with AttributeError on tuples


@pytest.mark.skipif(sys.platform != "win32", reason="process name ends in .exe on Windows only")
def testis_process_running_against_real_processes():
    from charlie.core import is_process_running

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

    from charlie.core import _detect_open_app

    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("charlie.core.is_process_running", lambda name: name == "notepad.exe")

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

    from charlie.core import _detect_open_app

    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("charlie.core.is_process_running", lambda name: name == "notepad.exe")

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

    from charlie.core import _detect_open_app

    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("charlie.core.is_process_running", lambda name: False)
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
    monkeypatch.setattr("charlie.core.is_process_running", lambda name: False)

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
    monkeypatch.setattr("charlie.core.is_process_running", lambda name: False)

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
