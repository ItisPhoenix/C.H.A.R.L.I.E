"""A2 execution/postcondition verification contracts."""

import pytest

from charlie.browser.agent import run_task
from charlie.browser.recipes import BrowserResult
from charlie.core import (
    _ground_external_action_response,
    _normalize_tool_result,
    _result_envelope_to_model_text,
)
from charlie.presentation import PresentationKind, PresentationResolver
from charlie.tools import ToolExecutionResult
from charlie.verifiers import run_verifier_for_match


def _identity() -> dict[str, str]:
    return {
        "turn_id": "turn-a2",
        "task_id": "task-a2",
        "session_id": "session-a2",
    }


def test_browser_dispatcher_uses_observed_url_not_expected_url():
    result = run_verifier_for_match(
        verifier_name="verify_browser_navigate",
        tool_name="browser_read",
        arguments={"url": "https://expected.example"},
        result_text="URL: https://observed.example\n\nObserved page text.",
    )

    assert result.verified is False
    assert "observed.example" in result.message


@pytest.mark.asyncio
async def test_browser_agent_done_requires_independent_post_observation(monkeypatch):
    from charlie.browser import controller

    calls = []

    def fake_run(fn, timeout=None, retry_on_stale=True):
        calls.append((fn, timeout, retry_on_stale))
        url = "https://example.test" if len(calls) == 1 else "https://different.test"
        return (
            f"URL: {url}\nTITLE: Example\n(no marked elements)",
            [],
            False,
        )

    monkeypatch.setattr(controller, "run", fake_run)

    async def complete(_prompt):
        return 'DONE url="https://expected.test" answer="finished"'

    result = await run_task("navigate to the expected page", complete, max_steps=1, deadline_s=100)

    assert result.success is False
    assert result.verification == "agent-done-unverified"
    assert len(calls) == 2


def test_desktop_dispatch_success_without_postcondition_is_not_success():
    envelope = _normalize_tool_result(
        "desktop_click",
        "Clicked mark [1].",
        request="click the button",
        **_identity(),
    )

    assert envelope.status == "unverified"
    assert getattr(envelope, "verification_status", None) == "executed_unverified"
    assert "couldn't verify" in _result_envelope_to_model_text(envelope).lower()


def test_existing_structured_desktop_verification_remains_successful():
    envelope = _normalize_tool_result(
        "desktop_open_app",
        ToolExecutionResult(
            "Opened Notepad.",
            {"ok": True, "verified": True, "apps": ["notepad"]},
            "desktop_app_open",
        ),
        request="open Notepad",
        **_identity(),
    )

    assert envelope.status == "completed"
    assert getattr(envelope, "verification_status", None) == "verified_success"


def test_terminal_direct_goal_uses_process_result_verification():
    envelope = _normalize_tool_result(
        "shell_execute",
        ToolExecutionResult(
            "STDOUT:\nPython 3.14",
            {"ok": True, "exit_code": 0, "stdout": "Python 3.14"},
            "terminal_result",
        ),
        request="python --version",
        **_identity(),
    )

    assert envelope.status == "completed"
    assert getattr(envelope, "verification_status", None) == "verified_success"


def test_terminal_higher_level_goal_stays_unverified_without_postcondition():
    envelope = _normalize_tool_result(
        "shell_execute",
        ToolExecutionResult(
            "Installer exited with code 0.",
            {"ok": True, "exit_code": 0, "goal_verified": False},
            "terminal_result",
        ),
        request="install the package and make sure it works",
        **_identity(),
    )

    assert envelope.status == "unverified"
    assert getattr(envelope, "verification_status", None) == "executed_unverified"
    assert "couldn't verify" in _result_envelope_to_model_text(envelope).lower()


def test_observed_contradiction_is_verified_failure_not_success():
    envelope = _normalize_tool_result(
        "desktop_window",
        ToolExecutionResult(
            "The window was resized.",
            {
                "ok": True,
                "verified": False,
                "verification_status": "verified_failure",
                "message": "Observed size did not match the requested size.",
            },
            "desktop_window",
        ),
        request="resize the window",
        **_identity(),
    )

    assert envelope.status == "failed"
    assert getattr(envelope, "verification_status", None) == "verified_failure"
    assert "did not match" in _result_envelope_to_model_text(envelope).lower()


def test_unverified_operation_projects_truthful_completion_language():
    envelope = _normalize_tool_result(
        "desktop_click",
        "Clicked mark [1].",
        request="click the button",
        **_identity(),
    )

    intent = PresentationResolver().resolve(envelope)

    assert intent.kind == PresentationKind.NOTIFICATION
    assert "couldn't verify" in (intent.spoken_text or "").lower()


def test_followup_model_cannot_promote_unverified_desktop_action_to_success():
    envelope = _normalize_tool_result(
        "desktop_click",
        "Clicked mark [1].",
        request="click the button",
        **_identity(),
    )

    response = _ground_external_action_response(
        "click the button",
        "Done — the button was clicked.",
        [envelope],
    )

    assert response == "I executed the action, but I couldn't verify the resulting state."


def test_browser_result_contract_remains_domain_specific():
    result = BrowserResult(
        url="https://example.test",
        answer="Verified page.",
        success=True,
        verification="page-opened",
    )

    assert result.success is True
    assert result.verification == "page-opened"
