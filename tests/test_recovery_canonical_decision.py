"""P0-4: recovery must take the canonical decision.

The canonical owner of the approval decision is
``Brain._request_tool_approval_decision`` -> ``charlie.core.ApprovalDecision``.
Every gated operation path in the runtime already routes through it.

Before this fix ``charlie.recovery`` held no owner reference at all:
``request_recovery_approval`` hardcoded "no", returned a free-text
``Optional[str]``, and ``recover_tool`` re-derived the outcome by
substring-sniffing that text for "rejected"/"error" -- a second, competing
decision vocabulary living beside the canonical one (AGENTS.md 7: "One
capability has one authoritative owner").

Evidence class: TEST/MOCK.

The decision words asserted here are substrings of the runtime's *user-facing
rejection text* (see ``_execute_operation_primitive``'s rejection mapping in
charlie/core.py). They are evidence about a failing run, not runtime evidence.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from typing import Any, Dict, List, Optional

import pytest

from charlie import recovery
from charlie.core import ApprovalDecision
from charlie.execution_context import ExecutionContext
from charlie.recovery import (
    BaseRecoveryStrategy,
    RecoveryResult,
    recover_tool,
    request_recovery_approval,
)

_NOT_FOUND = FileNotFoundError("[WinError 2] The system cannot find the file specified")
_RESOLVED = "C:/Windows/System32/notepad.exe"
_ORIGINAL = "notepad report.txt"


class CanonicalOwner:
    """Stands in for Brain's canonical approval decision function."""

    def __init__(self, decision: Any):
        self._decision = decision
        self.calls: List[Dict[str, Any]] = []

    async def _request_tool_approval_decision(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        reason: str,
        platform: str = "voice",
        risk_class: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        self.calls.append(
            {
                "tool_name": tool_name,
                "arguments": dict(arguments or {}),
                "reason": reason,
                "platform": platform,
                "kwargs": kwargs,
            }
        )
        if isinstance(self._decision, BaseException):
            raise self._decision
        return self._decision

    @property
    def asked(self) -> bool:
        return bool(self.calls)


class _ConfidentStrategy(BaseRecoveryStrategy):
    """A strategy that is certain it found the executable."""

    def __init__(self, command: str = _RESOLVED):
        self._command = command
        self.seen: List[str] = []

    def can_handle(self, failure: Dict[str, Any]) -> bool:
        return True

    async def recover(self, command: str, failure: Dict[str, Any]) -> RecoveryResult:
        self.seen.append(command)
        return RecoveryResult(
            success=True,
            command=self._command,
            message=f"Found in PATH: {self._command}",
        )


@pytest.fixture
def written(monkeypatch) -> List[Any]:
    """Isolate the on-disk recovery cache and record what gets written."""
    from charlie import recovery_cache

    writes: List[Any] = []
    monkeypatch.setattr(recovery_cache, "get_cached_resolution", lambda *_a, **_k: None)
    monkeypatch.setattr(recovery_cache, "set_cached_resolution", lambda *a, **k: writes.append(a))
    return writes


async def _ask(owner: Any) -> Any:
    return await request_recovery_approval(
        owner,
        original_command=_ORIGINAL,
        proposed_command=_RESOLVED,
        failure_class="NOT_FOUND",
        explanation="Found in PATH",
        source="strategy",
    )


class TestRecoveryDelegatesToTheCanonicalDecision:
    """(a) recovery asks the one authoritative owner instead of re-deriving."""

    @pytest.mark.asyncio
    async def test_request_recovery_approval_returns_the_canonical_decision(self):
        owner = CanonicalOwner(ApprovalDecision.APPROVED)

        decision = await _ask(owner)

        assert decision is ApprovalDecision.APPROVED
        assert len(owner.calls) == 1

    @pytest.mark.asyncio
    async def test_the_owner_receives_the_proposal_through_the_canonical_arguments_channel(self):
        """The exact pending operation must reach the owner as tool arguments.

        ``_request_tool_approval_decision`` renders those arguments into a
        redacted, control-char-escaped ``operation_preview`` for the approver.
        Recovery therefore proposes through ``arguments``, and states only why
        approval is required in ``reason``.
        """
        owner = CanonicalOwner(ApprovalDecision.APPROVED)

        await _ask(owner)

        call = owner.calls[0]
        assert call["tool_name"] == "shell_execute"
        assert call["arguments"] == {"command": _RESOLVED}
        assert "NOT_FOUND" in call["reason"], "the owner must see the failure classification"
        assert "strategy" in call["reason"], "the owner must see where the proposal came from"

    @pytest.mark.asyncio
    async def test_the_failing_command_is_not_smuggled_past_the_redaction_channel(self):
        """``reason`` is not the hardened preview channel.

        Only ``_approval_operation_preview`` escapes control characters and
        redacts secrets for an approver. Putting the raw failing command into
        ``reason`` would show it to the owner unredacted, so recovery must
        leave it there.
        """
        owner = CanonicalOwner(ApprovalDecision.APPROVED)

        await _ask(owner)

        assert _ORIGINAL not in owner.calls[0]["reason"]

    @pytest.mark.asyncio
    async def test_recover_tool_consults_the_owner_exactly_once(self, monkeypatch, written):
        owner = CanonicalOwner(ApprovalDecision.APPROVED)
        monkeypatch.setattr(recovery, "RECOVERY_REGISTRY", [_ConfidentStrategy()])

        result = await recover_tool(
            owner,
            "shell_execute",
            {"command": _ORIGINAL},
            _NOT_FOUND,
        )

        assert owner.asked, "recover_tool must route its gate through the canonical owner"
        assert len(owner.calls) == 1
        assert result is not None, "an approved recovery must report its outcome"

    @pytest.mark.asyncio
    async def test_a_refused_recovery_produces_no_outcome_and_caches_nothing(self, monkeypatch, written):
        owner = CanonicalOwner(ApprovalDecision.REJECTED)
        monkeypatch.setattr(recovery, "RECOVERY_REGISTRY", [_ConfidentStrategy()])

        result = await recover_tool(
            owner,
            "shell_execute",
            {"command": _ORIGINAL},
            _NOT_FOUND,
        )

        assert owner.asked
        assert result is None, "a refused recovery must not be reported as a result"
        assert written == [], "a refused resolution must never enter the cache"


class TestRecoveryFailsClosedWithoutTheCanonicalDecision:
    """(b) no canonical decision means no, never an invented permissive answer."""

    @pytest.mark.asyncio
    async def test_no_owner_fails_closed(self):
        decision = await _ask(None)

        assert decision is ApprovalDecision.UNAVAILABLE
        assert decision is not ApprovalDecision.APPROVED

    @pytest.mark.asyncio
    async def test_a_broken_owner_fails_closed(self):
        owner = CanonicalOwner(RuntimeError("approval channel exploded"))

        decision = await _ask(owner)

        assert decision is ApprovalDecision.UNAVAILABLE
        assert decision is not ApprovalDecision.APPROVED

    @pytest.mark.asyncio
    async def test_an_owner_without_the_canonical_surface_fails_closed(self):
        class LegacyOnly:
            """No canonical decision function exists on this object."""

            _fallback_client = None

        decision = await _ask(LegacyOnly())

        assert decision is ApprovalDecision.UNAVAILABLE
        assert decision is not ApprovalDecision.APPROVED

    @pytest.mark.asyncio
    async def test_cancellation_fails_closed_without_asking(self):
        owner = CanonicalOwner(ApprovalDecision.APPROVED)
        context = ExecutionContext()
        context.request_cancel()

        decision = await request_recovery_approval(
            owner,
            original_command=_ORIGINAL,
            proposed_command=_RESOLVED,
            failure_class="NOT_FOUND",
            explanation="Found in PATH",
            source="strategy",
            execution_context=context,
        )

        assert decision is ApprovalDecision.UNAVAILABLE
        assert not owner.asked, "a cancelled recovery must not open an approval prompt"

    @pytest.mark.asyncio
    async def test_recover_tool_without_an_owner_fails_closed(self, monkeypatch, written):
        monkeypatch.setattr(recovery, "RECOVERY_REGISTRY", [_ConfidentStrategy()])

        result = await recover_tool(
            None,
            "shell_execute",
            {"command": _ORIGINAL},
            _NOT_FOUND,
        )

        assert result is None
        assert written == []


_DECISION_WORDS = {"approved", "rejected", "timed_out", "unavailable", "denied", "error", "ok"}


class TestNoCompetingDecisionLogicRemains:
    """(c) recovery keeps no second decision of its own."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("decision", list(ApprovalDecision))
    async def test_only_approval_produces_a_recovery_outcome(self, decision, monkeypatch, written):
        """The canonical decision is the only input that can open the gate.

        Whatever the strategy or the cache was confident about, every
        canonical outcome other than APPROVED must answer no.
        """
        owner = CanonicalOwner(decision)
        monkeypatch.setattr(recovery, "RECOVERY_REGISTRY", [_ConfidentStrategy()])

        result = await recover_tool(
            owner,
            "shell_execute",
            {"command": _ORIGINAL},
            _NOT_FOUND,
        )

        if decision is ApprovalDecision.APPROVED:
            assert result is not None
        else:
            assert result is None, f"{decision} must not yield a recovery outcome"
            assert written == []

    def test_no_free_text_decision_inference(self):
        """Recovery must not infer an outcome by matching words in prose."""
        tree = ast.parse(inspect.getsource(recovery))
        offenders: List[int] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            operands = [node.left, *node.comparators]
            folds = any(
                isinstance(o, ast.Call) and getattr(o.func, "attr", None) in {"lower", "casefold"}
                for o in operands
            )
            if not folds:
                continue
            for operand in operands:
                if not isinstance(operand, ast.Constant) or not isinstance(operand.value, str):
                    continue
                if operand.value.strip().lower() in _DECISION_WORDS:
                    offenders.append(node.lineno)

        assert not offenders, (
            "recovery re-derives the decision by substring matching at lines "
            f"{offenders}; it must consume the canonical ApprovalDecision instead"
        )

    def test_recovery_defines_no_competing_decision_type(self):
        tree = ast.parse(inspect.getsource(recovery))
        names = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name.endswith("Decision")
        ]

        assert not names, f"recovery must not define its own decision type(s): {names}"


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(pytest.main([__file__, "-q"]))


class TestRecoveryProposalsAreSafetyChecked:
    """A recovery-proposed command must be vetted before the owner is asked.

    ``is_safe_to_recover`` existed and was unit-tested, but nothing in the live path
    called it. Without this gate the owner was asked to approve a command nobody had
    checked -- which left the canonical-decision fix incomplete.
    """

    def test_an_unsafe_strategy_command_is_never_approved(self, monkeypatch):
        from charlie import recovery as recovery_module

        asked: list = []

        async def _approve(brain, **kwargs):
            asked.append(kwargs.get("proposed_command"))
            from charlie.core import ApprovalDecision

            return ApprovalDecision.APPROVED

        class _UnsafeStrategy:
            def can_handle(self, failure):
                return True

            async def recover(self, command, failure):
                return recovery_module.RecoveryResult(
                    success=True, command="format c:"
                )

        monkeypatch.setattr(recovery_module, "RECOVERY_REGISTRY", [_UnsafeStrategy()])
        monkeypatch.setattr(recovery_module, "request_recovery_approval", _approve)

        async def _run():
            return await recovery_module.recover_tool(
                object(),
                "shell_execute",
                {"command": "notepad p2safety_a.exe"},
                FileNotFoundError("boom"),
            )

        assert asyncio.run(_run()) is None
        assert asked == [], "an unsafe proposed command must never reach the owner"

    def test_an_unsafe_cached_command_is_never_replayed(self, monkeypatch):
        from charlie import recovery as recovery_module

        asked: list = []

        async def _approve(brain, **kwargs):
            asked.append(kwargs.get("proposed_command"))
            from charlie.core import ApprovalDecision

            return ApprovalDecision.APPROVED

        monkeypatch.setattr(
            recovery_module, "get_cached_resolution", lambda *a, **k: "format c:", raising=False
        )
        cache_mod = __import__("charlie.recovery_cache", fromlist=["get_cached_resolution"])
        monkeypatch.setattr(
            cache_mod, "get_cached_resolution", lambda *a, **k: "format c:"
        )
        monkeypatch.setattr(cache_mod, "set_cached_resolution", lambda *a, **k: None)
        monkeypatch.setattr(recovery_module, "request_recovery_approval", _approve)
        monkeypatch.setattr(recovery_module, "RECOVERY_REGISTRY", [])

        async def _run():
            return await recovery_module.recover_tool(
                object(),
                "shell_execute",
                {"command": "notepad p2safety_b.exe"},
                FileNotFoundError("boom"),
            )

        assert asyncio.run(_run()) is None
        assert asked == [], "an unsafe cached command must never reach the owner"

    def test_a_safe_strategy_command_still_reaches_the_owner(self, monkeypatch):
        from charlie import recovery as recovery_module

        asked: list = []

        async def _approve(brain, **kwargs):
            asked.append(kwargs.get("proposed_command"))
            from charlie.core import ApprovalDecision

            return ApprovalDecision.APPROVED

        class _SafeStrategy:
            def can_handle(self, failure):
                return True

            async def recover(self, command, failure):
                return recovery_module.RecoveryResult(
                    success=True, command="notepad.exe report.txt"
                )

        monkeypatch.setattr(recovery_module, "RECOVERY_REGISTRY", [_SafeStrategy()])
        monkeypatch.setattr(recovery_module, "request_recovery_approval", _approve)

        async def _run():
            return await recovery_module.recover_tool(
                object(),
                "shell_execute",
                {"command": "notepad p2safety_c.exe"},
                FileNotFoundError("boom"),
            )

        assert asyncio.run(_run()) is not None
        assert asked == ["notepad.exe report.txt"], "safe proposals must still work"
