"""Safe executor fallback for the Cua seam.

"Unavailable" on its own must never authorise handing a task to another executor. Only
a task where no mutation was dispatched and completion is known may be replayed
elsewhere; anything partial or unknown stays with the executor that started it.
"""

from __future__ import annotations

import asyncio

import pytest

from charlie.computer.backend import CuaBrowserUnavailable
from charlie.computer.cua_adapter import CuaActionResult, CuaOutcome
from charlie.core import Brain

TASK = "read https://example.com"


def _cfg():
    from charlie.config import Config

    return Config(
        llm_url="https://example.com/v1",
        llm_key="test",
        llm_model="dummy",
        llm_trust_env=False,
        browser_enabled=True,
    )


class _Target:
    target_id = "t"
    tab_id = "b"

    def text(self):
        return "page text"


class FakeBrowser:
    """A browser whose behaviour is dictated at the point each test cares about."""

    def __init__(self, *, on_prepare=None, on_navigate=None, binds=True):
        self.target = _Target() if binds else None
        self._on_prepare = on_prepare
        self._on_navigate = on_navigate
        self.navigated = False
        self.ended = False

    def prepare(self):
        if self._on_prepare:
            self._on_prepare()
        return CuaActionResult(outcome=CuaOutcome.CONFIRMED, summary="ok")

    def navigate(self, _url):
        self.navigated = True
        if self._on_navigate:
            self._on_navigate()
        return CuaActionResult(outcome=CuaOutcome.EXECUTED_UNVERIFIED, summary="sent")

    def current_url(self):
        return "https://example.com/"

    def wait_for_url(self, *_a, **_k):
        return CuaActionResult(outcome=CuaOutcome.CONFIRMED, summary="settled")

    def end_session(self):
        self.ended = True


def run():
    brain = Brain(_cfg())
    return asyncio.run(brain._cua_browser_task(TASK, return_envelope=True))


def _install(monkeypatch, browser):
    monkeypatch.setattr(
        "charlie.computer.scope.BrowserTaskScope.browser", lambda self: browser
    )


def test_startup_failure_permits_fallback(monkeypatch):
    """Nothing dispatched: another executor may safely take the task."""

    def _boom(*_a, **_k):
        raise CuaBrowserUnavailable("bounded manifest missing")

    monkeypatch.setattr("charlie.computer.backend.get_cua_browser", _boom)
    envelope = run()
    assert envelope.data["failure_kind"] == "unavailable"
    assert envelope.data["mutation_started"] is False
    assert envelope.data["safe_to_fallback"] is True


def test_binding_failure_permits_fallback(monkeypatch):
    _install(monkeypatch, FakeBrowser(binds=False))
    envelope = run()
    assert envelope.data["failure_kind"] == "unavailable"
    assert envelope.data["mutation_started"] is False
    assert envelope.data["safe_to_fallback"] is True


def test_timeout_during_dispatch_forbids_fallback(monkeypatch):
    def _boom():
        raise TimeoutError("page did not settle")

    browser = FakeBrowser(on_navigate=_boom)
    _install(monkeypatch, browser)
    envelope = run()
    assert browser.navigated is True
    assert envelope.data["mutation_started"] is True
    assert envelope.data["safe_to_fallback"] is False
    assert envelope.data.get("uncertain") is True


def test_failure_after_a_mutation_forbids_fallback(monkeypatch):
    def _boom():
        raise RuntimeError("driver died after dispatch")

    browser = FakeBrowser(on_navigate=_boom)
    _install(monkeypatch, browser)
    envelope = run()
    assert browser.navigated is True
    assert envelope.data["safe_to_fallback"] is False


def test_unavailable_after_dispatch_forbids_fallback(monkeypatch):
    """The exact case that made a bare 'unavailable' unsafe."""

    def _boom():
        raise CuaBrowserUnavailable("runtime went away mid-task")

    browser = FakeBrowser(on_navigate=_boom)
    _install(monkeypatch, browser)
    envelope = run()
    if envelope.data.get("failure_kind") == "unavailable":
        assert envelope.data["safe_to_fallback"] is False
        assert envelope.data["mutation_started"] is True


@pytest.mark.parametrize(
    "payload",
    [
        {"failure_kind": "unavailable"},
        {"failure_kind": "failed", "uncertain": True},
        {"failure_kind": "unverified", "uncertain": True},
    ],
)
def test_bare_statuses_do_not_grant_fallback(payload):
    """The seam must require an explicit permission flag, not infer from status."""
    assert payload.get("safe_to_fallback") is not True


def test_every_response_declares_the_flag_and_mutation_state(monkeypatch):
    _install(monkeypatch, FakeBrowser(binds=False))
    envelope = run()
    assert isinstance(envelope.data["safe_to_fallback"], bool)
    assert isinstance(envelope.data["mutation_started"], bool)


def test_scope_is_closed_even_when_a_mutation_failed(monkeypatch):
    """Cleanup must run on the failure path or the isolated profile leaks."""

    def _boom():
        raise RuntimeError("boom")

    closed = []
    browser = FakeBrowser(on_navigate=_boom)
    monkeypatch.setattr(
        "charlie.computer.scope.BrowserTaskScope.close", lambda self: closed.append(True)
    )
    _install(monkeypatch, browser)
    envelope = run()
    assert envelope.data["safe_to_fallback"] is False
    assert closed == [True], "the owned scope must be closed on the failure path"
