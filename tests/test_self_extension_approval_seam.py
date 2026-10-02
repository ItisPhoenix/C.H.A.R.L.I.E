"""The self-extension owner-approval seam.

A gated self-extension request must stop before it mutates anything, and the only
thing that can release it is an owner decision bound to the exact argv the guard
just reviewed. The callback exists to carry that decision, so the property that
matters is that it cannot widen the grant: it may only echo the digest it was
shown.

Evidence class: TEST/MOCK (policy decisions only; nothing is installed or started).
"""

from __future__ import annotations

import json

import pytest

from charlie import tools as tools_module


@pytest.fixture(autouse=True)
def _clear_callback():
    tools_module.set_self_extension_approval_callback(None)
    yield
    tools_module.set_self_extension_approval_callback(None)


def _bare_orchestrator():
    """Construct an orchestrator without the main-owned SettingsService.

    Mirrors the pattern in tests/test_extension_approval_binding.py. No approval
    test ever reaches execution, so the execution-side collaborators stay unset.
    """
    from charlie.capabilities import CapabilityIndex
    from charlie.self_extension.classifier import ExtensionClassifier
    from charlie.self_extension.guard import AuthorizationGuard
    from charlie.self_extension.orchestrator import SelfExtensionOrchestrator

    orch = SelfExtensionOrchestrator.__new__(SelfExtensionOrchestrator)
    orch._transactions = {}
    orch._event_bus = None
    orch._event_loop = None
    orch._classifier = ExtensionClassifier(capability_index=CapabilityIndex())
    orch._guard = AuthorizationGuard()
    orch._code_index = None
    orch._self_knowledge = None
    return orch


def _install_recording_orchestrator(monkeypatch):
    """Route the tool at a real orchestrator and record whether anything executed."""
    orchestrator = _bare_orchestrator()
    executed: list[str] = []

    real_execute = orchestrator.execute_transaction

    def _tracking_execute(request, **kwargs):
        result = real_execute(request, **kwargs)
        executed.append(str(getattr(result.status, "value", result.status)))
        return result

    monkeypatch.setattr(orchestrator, "execute_transaction", _tracking_execute)
    monkeypatch.setattr(tools_module, "_self_extension_orchestrator", orchestrator)
    return orchestrator, executed


MCP_PROMPT = "connect the mcp server command:npx args:-y,@weather/mcp"


def _call_tool(prompt: str = MCP_PROMPT) -> dict:
    return json.loads(tools_module.charlie_self_extension_propose(prompt))


class TestWithoutACallbackTheRequestStops:
    def test_gated_request_does_not_execute(self, monkeypatch):
        _orchestrator, executed = _install_recording_orchestrator(monkeypatch)
        payload = _call_tool()
        assert payload["success"] is False
        assert "did not approve" in payload["message"]
        assert "Nothing was installed or started." in payload["message"]

    def test_the_refusal_names_the_exact_argv(self, monkeypatch):
        _install_recording_orchestrator(monkeypatch)
        payload = _call_tool()
        assert "npx" in payload["message"]
        assert "weather/mcp" in payload["message"]

    def test_first_attempt_is_approval_required(self, monkeypatch):
        _orchestrator, executed = _install_recording_orchestrator(monkeypatch)
        _call_tool()
        assert executed and executed[0] == "approval_required"


class TestTheCallbackCannotWidenTheGrant:
    def test_exact_digest_is_accepted(self, monkeypatch):
        _orchestrator, executed = _install_recording_orchestrator(monkeypatch)
        seen: list[dict] = []

        def _echo(payload):
            seen.append(payload)
            return payload["approval_binding"]

        tools_module.set_self_extension_approval_callback(_echo)
        # Past approval the MCP executor runs and may fail on the absent adapter.
        # That failure proves the grant was accepted; the decision boundary is what
        # this seam owns, so tolerate an execution-side error and assert on it.
        try:
            _call_tool()
        except AttributeError as exc:
            assert "approval" not in str(exc).casefold()
        assert len(seen) == 1, "the owner must be asked exactly once"
        assert seen[0]["argv"], "the owner must be shown the exact argv"
        assert seen[0]["approval_binding"]
        # Approval was requested exactly once. The replay carried the digest, so it
        # was never re-gated -- it ran on into MCP execution and stopped at the bare
        # orchestrator's absent adapter, which is past the decision boundary.
        assert executed.count("approval_required") == 1

    def test_a_different_digest_is_refused(self, monkeypatch):
        _orchestrator, executed = _install_recording_orchestrator(monkeypatch)
        tools_module.set_self_extension_approval_callback(lambda _payload: "0" * 64)
        payload = _call_tool()
        assert payload["success"] is False
        assert executed[-1] == "approval_required"

    def test_callback_returning_none_is_not_approval(self, monkeypatch):
        _orchestrator, executed = _install_recording_orchestrator(monkeypatch)
        tools_module.set_self_extension_approval_callback(lambda _payload: None)
        payload = _call_tool()
        assert payload["success"] is False
        assert executed[-1] == "approval_required"

    def test_a_raising_callback_is_not_approval(self, monkeypatch):
        _orchestrator, executed = _install_recording_orchestrator(monkeypatch)

        def _boom(_payload):
            raise RuntimeError("channel exploded")

        tools_module.set_self_extension_approval_callback(_boom)
        payload = _call_tool()
        assert payload["success"] is False
        assert executed[-1] == "approval_required"

    def test_the_guard_still_verifies_the_digest_itself(self, monkeypatch):
        """Even a correct-looking grant cannot bypass the guard's own comparison."""
        orchestrator, _executed = _install_recording_orchestrator(monkeypatch)
        tools_module.set_self_extension_approval_callback(lambda payload: payload["approval_binding"])
        try:
            _call_tool()
        except AttributeError as exc:
            assert "approval" not in str(exc).casefold()
        # Prove the orchestrator alone rejects a mismatched digest.
        fresh = _bare_orchestrator()
        mismatched_request = fresh.plan_request(MCP_PROMPT, explicit_user_request=True)
        result = fresh.execute_transaction(mismatched_request, approved_binding="deadbeef" * 8)
        assert result.success is False
        assert "APPROVAL_MISMATCH" in result.message


class TestSeamPlumbing:
    def test_setter_and_getter_round_trip(self):
        def _cb(_payload):
            return None

        tools_module.set_self_extension_approval_callback(_cb)
        assert tools_module.get_self_extension_approval_callback() is _cb
        tools_module.set_self_extension_approval_callback(None)
        assert tools_module.get_self_extension_approval_callback() is None

    def test_callback_receives_no_credential_material(self, monkeypatch):
        _install_recording_orchestrator(monkeypatch)
        seen: list[dict] = []
        tools_module.set_self_extension_approval_callback(
            lambda payload: (seen.append(payload), None)[1]
        )
        _call_tool()
        if seen:
            assert set(seen[0]) == {"kind", "prompt", "argv", "approval_binding", "reason"}
