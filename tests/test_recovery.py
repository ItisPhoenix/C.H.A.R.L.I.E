import os
import subprocess

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
