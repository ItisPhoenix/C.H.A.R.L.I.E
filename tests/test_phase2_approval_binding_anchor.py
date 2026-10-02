"""The approval digest must be anchored to the fields that are actually executed.

``ExtensionPlan.resolve_argv()`` preferred ``launch_argv``, but
``SelfExtensionOrchestrator`` executes ``mcp_command`` / ``mcp_args``.  Today both
come from one constructor call so they cannot diverge, but
``ExtensionPlan.from_dict`` reads them independently, so a hand-edited or merged
persisted plan can.  The digest would then be anchored to the field that is *not*
executed.

The contract: divergence is refused, not resolved.

Evidence class: TEST/MOCK.
"""

from __future__ import annotations

import pytest

from charlie.self_extension.guard import AuthorizationGuard
from charlie.self_extension.models import (
    ExtensionClassification,
    ExtensionKind,
    ExtensionPlan,
    ExtensionRequest,
)


def _plan(**overrides) -> ExtensionPlan:
    base = dict(
        plan_id="plan-anchor",
        kind=ExtensionKind.MCP_TOOL,
        description="Connect an MCP server.",
        mcp_name="weather",
        mcp_command="npx",
        mcp_args=["-y", "@weather/mcp"],
    )
    base.update(overrides)
    return ExtensionPlan(**base)


def _request(plan: ExtensionPlan) -> ExtensionRequest:
    return ExtensionRequest(
        user_prompt="Connect the MCP server named weather",
        classification=ExtensionClassification(kind=ExtensionKind.MCP_TOOL, confidence=0.95),
        plan=plan,
        explicit_user_request=True,
    )


class TestAgreementIsPreserved:
    def test_a_plan_with_only_the_executed_fields_resolves_them(self):
        plan = _plan()

        assert plan.resolve_argv() == ["npx", "-y", "@weather/mcp"]

    def test_matching_fields_resolve_to_one_argv(self):
        plan = _plan(launch_argv=["npx", "-y", "@weather/mcp"])

        assert plan.resolve_argv() == ["npx", "-y", "@weather/mcp"]
        assert plan.approval_binding() != ""


class TestDivergenceIsRefused:
    def test_a_different_launch_argv_resolves_to_nothing(self):
        plan = _plan(launch_argv=["npx", "-y", "@evil/mcp"])

        assert plan.resolve_argv() == []

    def test_a_different_command_resolves_to_nothing(self):
        plan = _plan(mcp_command="curl", launch_argv=["npx", "-y", "@weather/mcp"])

        assert plan.resolve_argv() == []

    def test_a_different_argument_list_resolves_to_nothing(self):
        plan = _plan(mcp_args=["-y", "@evil/mcp"], launch_argv=["npx", "-y", "@weather/mcp"])

        assert plan.resolve_argv() == []

    def test_a_diverged_plan_has_no_binding(self):
        plan = _plan(launch_argv=["npx", "-y", "@evil/mcp"])

        assert plan.approval_binding() == ""


class TestDivergenceSurvivesDeserialisation:
    def test_from_dict_can_reconstruct_a_diverged_plan(self):
        """This is the real attack path: a merged or hand-edited persisted plan."""
        payload = _plan(launch_argv=["npx", "-y", "@weather/mcp"]).to_dict()
        payload["mcp_args"] = ["-y", "@evil/mcp"]

        restored = ExtensionPlan.from_dict(payload)

        assert restored.launch_argv == ["npx", "-y", "@weather/mcp"]
        assert restored.mcp_args == ["-y", "@evil/mcp"]
        assert restored.resolve_argv() == []

    def test_a_json_round_trip_of_an_agreeing_plan_still_works(self):
        plan = _plan(launch_argv=["npx", "-y", "@weather/mcp"])

        restored = ExtensionPlan.from_dict(plan.to_dict())

        assert restored.resolve_argv() == plan.resolve_argv()
        assert restored.approval_binding() == plan.approval_binding()


class TestTheGuardCannotBindADivergedPlan:
    def test_the_decision_is_left_unbound(self):
        request = _request(_plan(launch_argv=["npx", "-y", "@evil/mcp"]))

        decision = AuthorizationGuard().evaluate(request)

        assert decision.requires_approval is True
        assert decision.is_authorized is False
        assert decision.approval_binding == ""
        assert decision.approved_argv == []
        assert decision.binds_plan(request.plan) is False

    def test_an_orchestrator_cannot_be_approved_with_a_foreign_digest(self):
        from charlie.capabilities import CapabilityIndex
        from charlie.self_extension.classifier import ExtensionClassifier
        from charlie.self_extension.orchestrator import SelfExtensionOrchestrator

        orch = SelfExtensionOrchestrator.__new__(SelfExtensionOrchestrator)
        orch._transactions = {}
        orch._event_bus = None
        orch._event_loop = None
        orch._classifier = ExtensionClassifier(capability_index=CapabilityIndex())
        from charlie.self_extension.guard import AuthorizationGuard

        orch._guard = AuthorizationGuard()
        orch._code_index = None
        orch._self_knowledge = None

        class _StubAdapter:
            def __init__(self) -> None:
                self.registered: list = []

            def register_mcp_server(self, **_kwargs):
                self.registered.append(_kwargs)

                class _R:
                    success = True
                    message = "ok"

                return _R()

        adapter = _StubAdapter()
        orch._mcp_adapter = adapter
        orch._run_verification_gate = lambda *a, **k: (True, "ok")

        # The owner approves a well-formed plan; a plan that diverges is a
        # different decision and must not be covered by that digest.
        approved = _plan(launch_argv=["npx", "-y", "@weather/mcp"])
        diverged = _plan(
            launch_argv=["npx", "-y", "@weather/mcp"],
            mcp_args=["-y", "@evil/mcp"],
        )

        result = orch.execute_transaction(_request(diverged), approved_binding=approved.approval_binding())

        assert result.success is False
        assert adapter.registered == []


class TestNonMcpPlansAreUnaffected:
    def test_a_plan_with_no_argv_at_all_resolves_to_nothing(self):
        plan = ExtensionPlan(
            plan_id="plan-config",
            kind=ExtensionKind.CONFIG,
            description="Set a standard setting.",
        )

        assert plan.resolve_argv() == []

    @pytest.mark.parametrize(
        "kind", [ExtensionKind.CONFIG, ExtensionKind.SKILL, ExtensionKind.CODE_SMALL]
    )
    def test_unbound_kinds_still_authorise_without_approval(self, kind):
        from charlie.self_extension.models import RiskClass

        request = ExtensionRequest(
            user_prompt="a bounded request",
            classification=ExtensionClassification(kind=kind, confidence=0.9),
            explicit_user_request=True,
        )

        decision = AuthorizationGuard().evaluate(request)

        assert decision.is_authorized is True
        assert decision.approval_binding == ""
        assert decision.risk_class in (RiskClass.SAFE, RiskClass.REVERSIBLE)
