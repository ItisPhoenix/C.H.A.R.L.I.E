"""Bounded cleanup of Cua-owned browser process trees."""

from __future__ import annotations

import sys
import types

from charlie.computer import browser as browser_mod
from charlie.computer.scope import BrowserTaskScope


def test_owned_process_tree_terminates_without_touching_unrecorded_processes(monkeypatch):
    alive = {100, 101, 999}

    class _Process:
        def __init__(self, pid):
            self.pid = int(pid)

        def terminate(self):
            alive.discard(self.pid)

        def kill(self):
            alive.discard(self.pid)

        def create_time(self):
            return 1.0 if self.pid == 100 else 1.1

        def is_running(self):
            return self.pid in alive

    fake_psutil = types.SimpleNamespace(
        Process=_Process, NoSuchProcess=RuntimeError,
        ZombieProcess=RuntimeError, AccessDenied=PermissionError,
    )
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    def terminate(owned, *, timeout):
        alive.discard(100)
        alive.discard(101)
        return True

    monkeypatch.setattr(browser_mod, "terminate_process_tree", terminate)

    report = {
        "available": True,
        "root_pid": 100,
        "processes": [
            {"pid": 100, "ppid": 0, "create_time": 1.0, "name": "browser.exe"},
            {"pid": 101, "ppid": 100, "create_time": 1.1, "name": "renderer.exe"},
        ],
    }
    result = browser_mod._terminate_owned_process_tree(report, timeout_s=0.1, owned_root=object())

    assert result["verified"] is True
    assert result["remaining"] == []
    assert 999 not in result["terminated"]
    assert 999 in alive


def test_scope_records_cleanup_report_from_owned_browser():
    class _Browser:
        cleanup_report = {"verified": True, "root_pid": 100, "remaining": []}

        def end_session(self):
            return types.SimpleNamespace(detail={"process_cleanup": self.cleanup_report})

    scope = BrowserTaskScope(["https://example.com"])
    scope._browser = _Browser()
    scope.close()

    assert scope.cleanup_report == _Browser.cleanup_report


def test_end_session_exception_still_runs_cleanup(monkeypatch):
    class _Adapter:
        def _call(self, *_args, **_kwargs):
            raise RuntimeError("driver disconnected")

    cleanup = {"verified": True, "root_pid": 100, "remaining": []}
    monkeypatch.setattr(browser_mod, "_terminate_owned_process_tree", lambda *_a, **_k: cleanup)
    browser = browser_mod.CuaBrowser(_Adapter(), session="cleanup-test")
    browser._process_tree = {"available": True, "root_pid": 100, "processes": []}

    result = browser.end_session()

    assert result.outcome.value == "failed"
    assert result.detail["process_cleanup"] == cleanup
