import os
import subprocess

import pytest

from charlie.recovery import (
    DeclassProcessStrategy,
    FailureClass,
    RelativePathResolveStrategy,
    SystemPathSearchStrategy,
    is_safe_to_recover,
    normalize_exception,
)
from charlie.recovery_cache import CACHE_FILE, get_cached_resolution, set_cached_resolution


def test_normalize_exception():
    # FileNotFoundError
    fnf_e = FileNotFoundError("The system cannot find the file specified")
    fnf_normalized = normalize_exception(fnf_e)
    assert fnf_normalized["failure_class"] == FailureClass.NOT_FOUND

    # Timeout
    to_e = subprocess.TimeoutExpired(cmd="test", timeout=5)
    to_normalized = normalize_exception(to_e)
    assert to_normalized["failure_class"] == FailureClass.TIMEOUT

    # Permission
    perm_e = PermissionError("Access is denied")
    perm_normalized = normalize_exception(perm_e)
    assert perm_normalized["failure_class"] == FailureClass.PERMISSION

    # OSError WSAEADDRINUSE
    os_e = OSError(10048, "Only one usage of each socket address is normally permitted")
    os_normalized = normalize_exception(os_e)
    assert os_normalized["failure_class"] == FailureClass.RESOURCE_LIMIT

def test_is_safe_to_recover():
    # Safe commands
    assert is_safe_to_recover("start notepad.exe") is True
    assert is_safe_to_recover("echo hello") is True

    # Blocked paths
    assert is_safe_to_recover("dir C:\\Windows\\System32\\config") is False
    assert is_safe_to_recover("del c:\\windows\\notepad.exe") is False

    # Blocked processes
    assert is_safe_to_recover("taskkill /IM explorer.exe") is False
    assert is_safe_to_recover("kill code.exe") is False

    # Blocked ports
    assert is_safe_to_recover("netstat -an | findstr :80") is False
    assert is_safe_to_recover("curl localhost:443") is False


def test_is_safe_to_recover_shares_shell_execute_blocklist():
    """Regression test: recovery commands (LLM-suggested or strategy-rewritten)
    must be blocked by the same metacharacter/hard-keyword guard as
    shell_execute, not just the narrower path/process/port checks. Before
    this fix, a recovery-suggested "format c: /q" or "echo a & del secrets"
    would pass is_safe_to_recover and execute."""
    # Irreversible keywords blocked outright by shell_execute's
    # _HARD_BLOCKED_KEYWORDS -- no approval flow can override these.
    assert is_safe_to_recover("format c: /q") is False
    assert is_safe_to_recover("shutdown /s /t 0") is False

    # Shell metacharacters blocked by shell_execute's _SHELL_METACHARS.
    assert is_safe_to_recover("echo a & type secrets.txt") is False
    assert is_safe_to_recover("dir; del test.txt") is False
    assert is_safe_to_recover("echo $(whoami)") is False

    # A command with none of the above still passes.
    assert is_safe_to_recover("notepad test.txt") is True


def test_is_safe_to_recover_allows_gated_keywords():
    """"reg delete" moved from hard-blocked to gated (approve/decline) --
    is_safe_to_recover only enforces the hard-block tier, so a gated keyword
    now passes here. The recovery pipeline's own proposal/approval flow
    (request_recovery_approval) still gates execution before it runs."""
    assert is_safe_to_recover("reg delete HKCU\\Software\\Test") is True

def test_strategies_can_handle():
    timeout_strategy = DeclassProcessStrategy()
    search_strategy = SystemPathSearchStrategy()
    resolve_strategy = RelativePathResolveStrategy()

    assert timeout_strategy.can_handle({"failure_class": FailureClass.TIMEOUT}) is True
    assert timeout_strategy.can_handle({"failure_class": FailureClass.NOT_FOUND}) is False

    assert search_strategy.can_handle({"failure_class": FailureClass.NOT_FOUND}) is True
    assert resolve_strategy.can_handle({"failure_class": FailureClass.NOT_FOUND}) is True

def test_recovery_cache():
    # Clean cache first
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)

    cmd = "start notepad"
    failure_class = "NOT_FOUND"
    err = "not found"
    resolved = "C:\\Windows\\System32\\notepad.exe"

    # Not found initially
    assert get_cached_resolution(cmd, failure_class, err) is None

    # Write to cache
    set_cached_resolution(cmd, failure_class, err, resolved)

    # Retrieval matches
    assert get_cached_resolution(cmd, failure_class, err) == resolved

    # Clean up
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)

@pytest.mark.asyncio
async def test_recover_tool_file_write_redirect(monkeypatch):
    from charlie.recovery import recover_tool

    e = PermissionError("[WinError 5] Access is denied")

    class DummyBrain:
        _fallback_client = None

    brain = DummyBrain()
    args = {"path": "C:\\Windows\\test.txt", "content": "I am Charlie"}

    redirected_path = None
    def mock_file_write(path, content):
        nonlocal redirected_path
        redirected_path = path
        return "Successfully wrote"

    monkeypatch.setattr("charlie.tools.file_write", mock_file_write)

    res = await recover_tool(brain, "file_write", args, e)

    assert res is not None
    assert "Redirected save" in res
    assert redirected_path is not None
    assert "Documents" in redirected_path
