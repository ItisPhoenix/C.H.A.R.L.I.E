import pytest

from charlie.config import Config
from charlie.core import Brain


@pytest.fixture
def brain_config():
    return Config(
        small_llm_url="http://localhost:11434",
        small_llm_key="no-key",
        small_llm_model="dummy",
        iteration_budget_max=3,
    )


@pytest.fixture
def brain_config_with_fallback():
    return Config(
        small_llm_url="http://localhost:11434",
        small_llm_key="no-key",
        small_llm_model="dummy",
        big_llm_url="http://example.com/v1",
        big_llm_key="real-key",
        big_llm_model="big-dummy",
        iteration_budget_max=5,
    )


@pytest.mark.asyncio
async def test_followup_fallback_on_primary_error_streams_content(
    monkeypatch, brain_config_with_fallback
):
    """Regression test: when the primary follow-up stream errors mid-turn,
    the fallback (big) LLM's response must be parsed correctly and its
    content must reach the caller. This was broken by a missing [0] index
    on `chunk.get("choices", [{}]).get("delta", {})` in the on-error
    fallback branch, which raised AttributeError on every fallback chunk
    (silently swallowed) and produced no output at all.
    """
    brain = Brain(brain_config_with_fallback)

    primary_call_count = 0

    def mock_primary_stream(*args, **kwargs):
        nonlocal primary_call_count
        primary_call_count += 1
        call_num = primary_call_count

        class MockResponse:
            def raise_for_status(self):
                pass

            async def aiter_lines(self):
                # Initial completion call: return a tool call.
                yield (
                    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"1",'
                    '"function":{"name":"web_search","arguments":"{\\"query\\":\\"x\\"}"}}]}}]}'
                )
                yield "data: [DONE]"

            async def __aenter__(self):
                if call_num == 2:
                    # Simulate a connection drop on the follow-up request.
                    raise RuntimeError("simulated primary connection drop")
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        return MockResponse()

    def mock_fallback_stream(*args, **kwargs):
        class MockResponse:
            def raise_for_status(self):
                pass

            async def aiter_lines(self):
                yield 'data: {"choices":[{"delta":{"content":"fallback answer"}}]}'
                yield "data: [DONE]"

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        return MockResponse()

    monkeypatch.setattr(brain.client, "stream", mock_primary_stream)
    monkeypatch.setattr(brain._big_client, "stream", mock_fallback_stream)
    monkeypatch.setattr(
        "charlie.tools.registry.execute_tool",
        lambda name, args: "mock search result",
    )

    results = []
    async for chunk in brain.chat_stream("test"):
        results.append(chunk)

    assert "fallback answer" in "".join(results)


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
        Config(small_llm_url="http://localhost:11434", small_llm_key="no-key", small_llm_model="dummy")
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
        Config(small_llm_url="http://localhost:11434", small_llm_key="no-key", small_llm_model="dummy")
    )
    text = 'TOOL: web_search("test")\nweb_search("test")'
    calls = brain._extract_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["name"] == "web_search"


def test_extract_mixed_tool_formats():
    """Mixed TOOL: and bare formats in same response."""
    from charlie.config import Config

    brain = Brain(
        Config(small_llm_url="http://localhost:11434", small_llm_key="no-key", small_llm_model="dummy")
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
        Config(small_llm_url="http://localhost:11434", small_llm_key="no-key", small_llm_model="dummy")
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

    # 1. Test opening single app
    res = _detect_open_app("open calculator")
    assert res == "I've opened Calculator for you."
    assert 'start "" calc' in called_cmds
    called_cmds.clear()
    res = _detect_open_app("open chrome and calculator")
    assert "Calculator and Chrome" in res
    assert 'start "" chrome' in called_cmds
    assert 'start "" calc' in called_cmds

    # 3. Test opening whitelisted websites by name
    called_cmds.clear()
    res = _detect_open_app("open youtube and github")
    assert "Youtube and Github" in res or "Github and Youtube" in res
    assert 'start "" https://youtube.com' in called_cmds
    assert 'start "" https://github.com' in called_cmds

    # 4. Test opening generic domains/URLs
    called_cmds.clear()
    res = _detect_open_app("open reddit.com, wikipedia.org and https://neon.tech")
    assert "reddit.com" in res
    assert "wikipedia.org" in res
    assert "https://neon.tech" in res
    assert 'start "" https://reddit.com' in called_cmds
    assert 'start "" https://wikipedia.org' in called_cmds
    assert 'start "" https://neon.tech' in called_cmds

    # 5. Test float/version number exclusion (must not match as domain)
    res = _detect_open_app("open version 3.5")
    assert res is None

    # 6. Test unknown app
    res = _detect_open_app("open unknownapp")
    assert res is None

    # 7. Test compound command bypass
    res = _detect_open_app("open notepad and write hello")
    assert res is None





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
    monkeypatch.setattr(os, "startfile", mock_startfile)
    monkeypatch.setattr("sys.platform", "win32")

    # Test: open two apps, one fails
    res = _detect_open_app("open chrome notepad")
    assert "Chrome" in res  # First app succeeds
    assert "Notepad" in res  # Second app should appear in failed list
    assert "Failed to open" in res
    # Ensure no raw tuple syntax leaks (the old bug)
    assert "(" not in res or res.count("(") == res.count(")")
    # Ensure .exe/.title tuple artifacts don't leak
    assert "error_detail" not in res.lower()
    assert "OSError" not in res


def test_detect_open_app_all_failures(monkeypatch):
    """All apps failing must return a graceful error, not a crash."""
    import os
    import subprocess

    from charlie.core import _detect_open_app

    def mock_fail(*_a, **_kw):
        raise OSError("Mock failure")

    monkeypatch.setattr(subprocess, "Popen", mock_fail)
    monkeypatch.setattr(os, "startfile", mock_fail)
    monkeypatch.setattr("sys.platform", "win32")

    res = _detect_open_app("open chrome notepad")
    assert res is not None
    assert "chrome" in res.lower() or "Chrome" in res
    # Must not crash with AttributeError on tuples

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
async def test_chat_stream_skip_tools(monkeypatch, brain_config):
    from charlie.core import Brain

    called_tool = False
    def mock_execute(*args, **kwargs):
        nonlocal called_tool
        called_tool = True
        return "mocked"

    monkeypatch.setattr("charlie.tools.registry.execute_tool", mock_execute)

    async def mock_stream_completion(*args, **kwargs):
        return ('Hello world TOOL: file_write("C:\\\\test.txt", "hello")', [], False)

    brain = Brain(brain_config)
    monkeypatch.setattr(brain, "_stream_completion", mock_stream_completion)

    results = []
    async for chunk in brain.chat_stream("test", skip_tools=True):
        results.append(chunk)

    assert "Hello world" in "".join(results)
    assert "TOOL: file_write" not in "".join(results)
    assert not called_tool
