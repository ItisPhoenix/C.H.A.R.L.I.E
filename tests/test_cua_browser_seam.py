"""Behavioural tests for the Cua browser dispatch seam and per-task scope.

These are behavioural rather than structural: they drive the seam predicate and the
scope the way Brain does, with a fake browser, so origin enforcement, cancellation and
cleanup are exercised without a real browser.
"""

from __future__ import annotations

import asyncio

import pytest

from charlie.computer import scope as scope_mod
from charlie.computer.scope import BrowserTaskScope, build_origins, origin_of
from charlie.core import (
    _cua_browser_seam_applies,
    _cua_interaction_summary,
    _requires_user_default_browser,
)

# ---------------------------------------------------------------- seam routing


@pytest.mark.parametrize(
    "task,expected",
    [
        ("click the button at https://example.com", False),
        ("fill in the form on https://example.com/signup", False),
        ("submit https://example.com", False),
        ("download the file from https://example.com/f.pdf", False),
        ("what does this page say?", False),
        ("summarise https://example.com", True),
        ("go to https://example.com", True),
        ("what is in my current tab", False),
        ("check brave", False),
        ("open https://example.com in brave", False),
        ("https://example.com", True),
        ("click something", False),  # interaction, but no named destination
        ("", False),
    ],
)
def test_seam_selection(task, expected):
    assert _cua_browser_seam_applies(task) is expected


def test_seam_does_not_claim_ordinary_questions():
    """Cua must not take work Playwright already does well."""
    for task in ("what is the weather?", "summarise this page", "who won in 1998"):
        assert not _cua_browser_seam_applies(task)


# -------------------------------------------------------- origin derivation


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://example.com", "https://example.com"),
        ("https://example.com/a/b?c=d", "https://example.com"),
        ("http://example.com:8080/x", "http://example.com:8080"),
        ("about:blank", "about:"),
        ("", None),
        ("not a url", None),
    ],
)
def test_origin_of(url, expected):
    assert origin_of(url) == expected


def test_origins_always_include_about_blank():
    assert "about:blank" in build_origins([])


def test_origins_come_from_destinations_only():
    origins = build_origins(["https://example.com/page"])
    assert origins == ("about:blank", "https://example.com")


def test_declared_workflow_redirects_are_included():
    origins = build_origins(
        ["https://example.com"], workflow_redirects=["https://www.iana.org/help"]
    )
    assert "https://www.iana.org" in origins


def test_page_content_never_widens_scope():
    """A discovered link must not silently become an authorised origin."""
    scope = BrowserTaskScope(["https://example.com"])
    boundary = scope.check("https://elsewhere.test/linked")
    assert boundary is not None
    assert "https://elsewhere.test" not in scope.origins
    assert "https://elsewhere.test" not in boundary.authorised_origins


# ------------------------------------------------------------- scope lifetime


def test_scope_allows_its_own_destinations():
    scope = BrowserTaskScope(["https://example.com", "https://example.org"])
    assert scope.check("https://example.com/x") is None
    assert scope.check("https://example.org") is None
    assert scope.check("https://example.net") is not None


def test_scope_is_usable_as_a_context_manager():
    with BrowserTaskScope(["https://example.com"]) as scope:
        assert scope.admission_open
    assert not scope.admission_open


def test_closed_scope_refuses_further_browser_use():
    scope = BrowserTaskScope(["https://example.com"])
    scope.close()
    with pytest.raises(RuntimeError):
        scope.browser()


def test_close_is_idempotent():
    scope = BrowserTaskScope(["https://example.com"])
    scope.close()
    scope.close()


# ------------------------------------------------------------- cancellation


def test_closing_admission_blocks_further_input():
    scope = BrowserTaskScope(["https://example.com"])
    assert scope.admission_open
    scope.close_admission()
    assert not scope.admission_open
    with pytest.raises(PermissionError):
        scope.require_admission()


def test_cancellation_preserves_the_last_verified_result():
    scope = BrowserTaskScope(["https://example.com"])
    scope.record_verified({"url": "https://example.com", "answer": "first page"})
    scope.close_admission()
    # Closing admission must not erase verified work.
    assert scope.last_verified == {"url": "https://example.com", "answer": "first page"}


def test_boundary_is_immutable_and_carries_its_approved_set():
    boundary = scope_mod.ScopeBoundary(
        origin="https://x.test", reason="test", authorised_origins=("about:blank",)
    )
    assert boundary.origins == ("about:blank",)
    assert "x.test" in boundary.summary
    with pytest.raises(Exception):
        boundary.origin = "https://y.test"  # type: ignore[misc]


# ------------------------------------------------------ truthful reporting


def test_navigation_only_task_reports_no_limitation():
    summary = _cua_interaction_summary("go to https://example.com", [])
    assert summary["limitation"] == ""
    assert summary["performed"] == "navigation_and_read"


def test_interaction_task_reports_its_limitation():
    """Reaching a URL must not be presented as having clicked something."""
    summary = _cua_interaction_summary("click the login button on https://example.com", [])
    assert summary["limitation"]
    assert "click" in summary["limitation"]
    assert "click" in summary["requested"]


def test_interaction_summary_lists_every_requested_action():
    summary = _cua_interaction_summary(
        "fill in the form and submit on https://example.com", []
    )
    assert {"fill", "submit"} <= set(summary["requested"])


# ------------------------------------------------------ handler integration


def _cfg():
    from charlie.config import Config

    return Config(
        llm_url="https://example.com/v1",
        llm_key="test",
        llm_model="dummy",
        llm_trust_env=False,
        browser_enabled=True,
    )


def test_handler_returns_none_without_a_destination(monkeypatch):
    """No named destination means the seam must defer to Playwright."""
    from charlie.core import Brain

    brain = Brain(_cfg())
    assert asyncio.run(brain._cua_browser_task("click the button")) is None

def test_handler_reports_cancellation_before_dispatch(monkeypatch):
    """A cancel landing mid-flight must stop dispatch and never claim success."""
    from charlie.core import Brain

    brain = Brain(_cfg())

    def _cancel_then_refuse(self):
        brain.cancel_chat()
        raise AssertionError("handler dispatched after cancellation")

    monkeypatch.setattr(
        "charlie.computer.scope.BrowserTaskScope.browser", _cancel_then_refuse
    )
    envelope = asyncio.run(
        brain._cua_browser_task("click https://example.com", return_envelope=True)
    )
    assert envelope.status in {"blocked", "failed"}
    assert envelope.status != "completed"
    assert envelope.data.get("uncertain") is True

def test_handler_reports_unavailable_runtime(monkeypatch):
    """A runtime that cannot start must be reported, never silently skipped."""
    from charlie.computer import backend as backend_mod
    from charlie.core import Brain

    def _boom(*_a, **_k):
        raise backend_mod.CuaBrowserUnavailable("bounded manifest missing")

    monkeypatch.setattr(backend_mod, "get_cua_browser", _boom)
    brain = Brain(_cfg())
    envelope = asyncio.run(
        brain._cua_browser_task("click https://example.com", return_envelope=True)
    )
    # Unavailability must be distinguishable from failure so the seam can fall
    # through to the authorized cascade instead of failing the task.
    assert envelope.status == "blocked"
    assert envelope.data["failure_kind"] == "unavailable"


def test_handler_reports_a_binding_failure(monkeypatch):
    """If no target binds, that is a refusal with a reason, not a silent success."""
    from charlie.core import Brain

    class _NoTarget:
        target = None

        def prepare(self):
            from charlie.computer.cua_adapter import CuaActionResult, CuaOutcome

            return CuaActionResult(outcome=CuaOutcome.FAILED, summary="no target")

        def end_session(self):
            pass

    monkeypatch.setattr(
        "charlie.computer.scope.BrowserTaskScope.browser", lambda self: _NoTarget()
    )
    brain = Brain(_cfg())
    envelope = asyncio.run(
        brain._cua_browser_task("click https://example.com", return_envelope=True)
    )
    assert envelope.status == "blocked"
    assert envelope.data["failure_kind"] == "unavailable"


def test_handler_closes_the_scope_even_when_it_fails(monkeypatch):
    """Cleanup must run on the failure path too, or the isolated profile leaks."""
    from charlie.core import Brain

    closed = []

    class _Scope:
        def __init__(self, *_a, **_k):
            self.origins = ("about:blank",)
            self.session = None
            self.last_verified = None

        def check(self, _url):
            return None

        def browser(self):
            raise RuntimeError("boom")

        def close(self):
            closed.append(True)

    monkeypatch.setattr("charlie.computer.scope.BrowserTaskScope", _Scope)
    brain = Brain(_cfg())
    envelope = asyncio.run(
        brain._cua_browser_task("click https://example.com", return_envelope=True)
    )
    assert envelope.status == "failed"
    assert closed == [True], "scope must be closed on the failure path"


def test_cua_does_not_claim_interactions_it_cannot_perform():
    """Cua may only claim work it can finish. Claiming then partially completing is worse."""
    for task in (
        "click the button at https://example.com",
        "fill in the form on https://example.com/signup",
        "submit https://example.com",
        "download https://example.com/f.pdf",
        "log in to https://example.com",
    ):
        assert not _cua_browser_seam_applies(task), task


def test_user_browser_intent_stays_out_of_private_cua():
    """A user-browser task must not be claimed by the isolated Cua lane."""
    for task in (
        "what is in my current tab",
        "check brave",
        "open https://example.com in brave",
        "read my open tab",
        "use my default browser",
        "open this in my web page",
    ):
        assert _requires_user_default_browser(task), task
        assert not _cua_browser_seam_applies(task), task




def test_public_reads_are_still_claimable():
    for task in ("read https://example.com", "summarise https://example.com"):
        assert _cua_browser_seam_applies(task), task
        assert not _requires_user_default_browser(task), task


def test_minimal_input_dispatch_preserves_action_order_and_excludes_submissions():
    from charlie.core import _cua_requested_actions

    task = 'fill "Name" with "Charlie" and click "Change" at https://example.com'
    assert _cua_requested_actions(task) == [
        {"kind": "type", "label": "Name", "text": "Charlie"},
        {"kind": "click", "label": "Change"},
    ]
    assert _cua_browser_seam_applies(task)
    for label in ("Send", "Publish", "Confirm payment", "Delete account"):
        assert not _cua_browser_seam_applies(f'click "{label}" at https://example.com')
