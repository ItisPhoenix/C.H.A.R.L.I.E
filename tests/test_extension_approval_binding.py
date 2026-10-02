"""Approval binding for self-extension requests.

Covers the P0-1 chain end to end:

* an MCP_TOOL request can never be authorized without approval;
* Rule 3 (external dependency) is live, not dead code;
* the extension install path honours MCP enablement;
* extension/server-declared metadata cannot lower an MCP tool to ``safe``;
* an approval is bound to the exact argv that was reviewed, so a different
  command is a different decision.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

import pytest

from charlie.self_extension.guard import AuthorizationGuard
from charlie.self_extension.models import (
    ExtensionClassification,
    ExtensionKind,
    ExtensionPlan,
    ExtensionRequest,
    GuardDecision,
    RiskClass,
    TransactionStatus,
)

MCP_PROMPT = (
    "Connect the MCP server named weather, env: WEATHER_KEY=abc, "
    "command: npx, args: -y @weather/mcp"
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _FakeToolRegistry:
    """Minimal ToolRegistry stand-in recording register_tool kwargs."""

    def __init__(self) -> None:
        self._tools: Dict[str, Dict[str, Any]] = {}

    def register_tool(
        self, name: str, description: str, schema: Dict[str, Any], **kwargs: Any
    ) -> Callable:
        def decorator(func: Callable) -> Callable:
            self._tools[name] = {
                "name": name,
                "description": description,
                "schema": schema,
                "func": func,
                "risk_class": kwargs.get("risk_class"),
            }
            return func

        return decorator

    def unregister_tool(self, name: str) -> None:
        self._tools.pop(name, None)

    def get_risk_class(self, name: str) -> Optional[str]:
        entry = self._tools.get(name)
        return entry.get("risk_class") if entry else None


def _make_plan(command: str = "npx", args: Optional[List[str]] = None) -> ExtensionPlan:
    return ExtensionPlan(
        plan_id="plan-binding",
        kind=ExtensionKind.MCP_TOOL,
        description="Connect an MCP server.",
        mcp_name="weather",
        mcp_command=command,
        mcp_args=list(args if args is not None else ["-y", "@weather/mcp"]),
    )


def _make_request(
    plan: Optional[ExtensionPlan] = None,
    *,
    required_dependencies: Optional[List[str]] = None,
    explicit_user_request: bool = True,
) -> ExtensionRequest:
    return ExtensionRequest(
        user_prompt=MCP_PROMPT,
        classification=ExtensionClassification(kind=ExtensionKind.MCP_TOOL, confidence=0.95),
        plan=plan,
        explicit_user_request=explicit_user_request,
        required_dependencies=list(required_dependencies or []),
    )


def _bare_orchestrator():
    from charlie.capabilities import CapabilityIndex
    from charlie.self_extension.classifier import ExtensionClassifier
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


class _StubMcpAdapter:
    def __init__(self) -> None:
        self.registered: List[tuple] = []

    def register_mcp_server(
        self,
        name: str,
        command: str,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        declared_tools: Optional[List[str]] = None,
    ):
        self.registered.append((name, command, list(args or [])))

        class _Result:
            success = True
            message = "registered"

        return _Result()


# ---------------------------------------------------------------------------
# 1. MCP tools must require approval
# ---------------------------------------------------------------------------


def test_mcp_tool_request_requires_approval():
    decision = AuthorizationGuard().evaluate(_make_request(_make_plan()))

    assert decision.requires_approval is True
    assert decision.is_authorized is False
    assert decision.risk_class == RiskClass.DANGEROUS
    assert "MCP" in decision.reason


def test_mcp_tool_without_plan_cannot_be_authorized():
    decision = AuthorizationGuard().evaluate(_make_request(None))

    assert decision.requires_approval is True
    assert decision.is_authorized is False
    # An unbound decision must not be usable as an approval for anything.
    assert decision.approval_binding == ""
    assert decision.approved_argv == []
    assert decision.binds_plan(_make_plan()) is False


# ---------------------------------------------------------------------------
# 2. Rule 3 is live for MCP_TOOL
# ---------------------------------------------------------------------------


def test_required_dependencies_gate_mcp_tool_request():
    request = _make_request(_make_plan(), required_dependencies=["npx"])

    decision = AuthorizationGuard().evaluate(request)

    assert decision.requires_approval is True
    assert decision.is_authorized is False
    assert "npx" in decision.reason


def test_plan_request_records_external_dependency_for_mcp():
    request = _bare_orchestrator().plan_request(MCP_PROMPT)

    assert request.classification.kind == ExtensionKind.MCP_TOOL
    assert request.required_dependencies == ["npx"]
    assert request.plan is not None
    # The exact argv the planner extracted from the prompt, verbatim.
    assert request.plan.launch_argv == ["npx", "-y @weather/mcp"]
    assert request.plan.resolve_argv() == ["npx", "-y @weather/mcp"]

    decision = AuthorizationGuard().evaluate(request)
    assert decision.requires_approval is True


# ---------------------------------------------------------------------------
# 3. Enablement honoured on the extension install path
# ---------------------------------------------------------------------------


def _fake_server_factory(tool_payloads):
    class _FakeServer:
        def __init__(self, config):
            self.config = config
            self.running = False

        def start(self):
            self.running = True

        def is_running(self):
            return self.running

        def list_tools(self):
            from charlie.mcp_client import MCPTool

            tools = []
            for payload in tool_payloads:
                tools.append(
                    MCPTool(
                        name=payload["name"],
                        description=payload.get("description", ""),
                        input_schema=payload.get("inputSchema", {}),
                        annotations=payload.get("annotations", {}),
                    )
                )
            return tools

        def stop(self):
            self.running = False

    return _FakeServer


def _install_mcp(monkeypatch, tool_payloads, **kwargs):
    from charlie.extensions.install import install_extension
    from charlie.mcp_client import MCPClient

    monkeypatch.setattr("charlie.mcp_client._ManagedServer", _fake_server_factory(tool_payloads))
    client = MCPClient()
    registry = _FakeToolRegistry()
    tool_names, client = install_extension(
        "mcp",
        "weather",
        "",
        '{"name": "weather", "command": "npx", "args": ["-y", "@weather/mcp"]}',
        registry=registry,
        plugin_manager=None,
        mcp_client=client,
        plugin_allow_dirs=[],
        **kwargs,
    )
    return list(tool_names), client, registry


def test_mcp_install_refused_when_enablement_is_off(monkeypatch):
    with pytest.raises(ValueError) as excinfo:
        _install_mcp(
            monkeypatch,
            [{"name": "get_forecast"}],
            mcp_enabled=False,
        )

    message = str(excinfo.value)
    assert "MCP_ENABLED" in message
    assert "weather" in message


def test_mcp_install_refusal_mutates_nothing(monkeypatch):
    from charlie.extensions.install import install_extension
    from charlie.mcp_client import MCPClient

    monkeypatch.setattr(
        "charlie.mcp_client._ManagedServer", _fake_server_factory([{"name": "get_forecast"}])
    )
    client = MCPClient()
    registry = _FakeToolRegistry()

    with pytest.raises(ValueError):
        install_extension(
            "mcp",
            "weather",
            "",
            '{"name": "weather", "command": "npx"}',
            registry=registry,
            plugin_manager=None,
            mcp_client=client,
            plugin_allow_dirs=[],
            mcp_enabled=False,
        )

    assert "weather" not in client._servers
    assert registry._tools == {}


def test_mcp_install_still_works_when_enablement_is_on(monkeypatch):
    tool_names, client, registry = _install_mcp(
        monkeypatch, [{"name": "get_forecast"}], mcp_enabled=True
    )

    assert tool_names == ["mcp_weather_get_forecast"]
    assert "weather" in client._servers


def test_mcp_install_default_preserves_existing_caller_behaviour(monkeypatch):
    """Existing callers pass no enablement argument; behaviour must not change."""
    tool_names, _client, _registry = _install_mcp(monkeypatch, [{"name": "get_forecast"}])

    assert tool_names == ["mcp_weather_get_forecast"]


# ---------------------------------------------------------------------------
# 4. Extension metadata must never lower required policy
# ---------------------------------------------------------------------------


def test_risk_authority_floors_server_declared_allow():
    from charlie.mcp_client import mcp_tool_risk_class

    assert mcp_tool_risk_class("allow", declared_policy="allow") == "security_sensitive"
    assert mcp_tool_risk_class("allow", declared_policy="safe") == "security_sensitive"
    assert mcp_tool_risk_class("ask", declared_policy="allow") == "security_sensitive"
    assert mcp_tool_risk_class("deny", declared_policy="allow") == "security_sensitive"


def test_risk_authority_keeps_local_allow_below_no_approval():
    from charlie.mcp_client import mcp_tool_risk_class

    assert mcp_tool_risk_class("allow") == "safe"
    assert mcp_tool_risk_class("ask") == "security_sensitive"
    assert mcp_tool_risk_class("deny") == "security_sensitive"
    assert mcp_tool_risk_class("") == "security_sensitive"


def test_advisory_annotations_are_not_permission_claims():
    from charlie.mcp_client import server_declared_policy

    assert server_declared_policy({"readOnlyHint": True, "destructiveHint": False}) is None
    assert server_declared_policy({"policy": "allow"}) == "allow"
    assert server_declared_policy({"riskClass": "safe"}) == "safe"
    assert server_declared_policy("not-a-dict") is None
    assert server_declared_policy(None) is None


def test_server_declared_allow_cannot_yield_safe_tool(monkeypatch):
    """A server that claims permission for itself cannot remove approval."""
    from charlie.mcp_client import MCPClient, MCPServerConfig

    monkeypatch.setattr(
        "charlie.mcp_client._ManagedServer",
        _fake_server_factory(
            [
                {
                    "name": "get_forecast",
                    "description": "server-declared safe",
                    "annotations": {"readOnlyHint": True, "policy": "allow"},
                }
            ]
        ),
    )
    client = MCPClient(tool_policies={"weather:get_forecast": "allow"})
    registry = _FakeToolRegistry()
    try:
        client.add_server(MCPServerConfig(name="weather", command="npx", args=["-y"]))
        registered = client.enable_server(registry, "weather")

        assert registered == ["mcp_weather_get_forecast"]
        assert registry.get_risk_class("mcp_weather_get_forecast") != "safe"
        assert registry.get_risk_class("mcp_weather_get_forecast") == "security_sensitive"
    finally:
        client.remove_server(registry, "weather")


def test_local_allow_policy_is_still_honoured_over_advisory_hints(monkeypatch):
    from charlie.mcp_client import MCPClient, MCPServerConfig

    monkeypatch.setattr(
        "charlie.mcp_client._ManagedServer",
        _fake_server_factory(
            [
                {
                    "name": "get_forecast",
                    "description": "read-only",
                    "annotations": {"readOnlyHint": False, "destructiveHint": True},
                }
            ]
        ),
    )
    client = MCPClient(tool_policies={"weather:get_forecast": "allow"})
    registry = _FakeToolRegistry()
    try:
        client.add_server(MCPServerConfig(name="weather", command="npx", args=["-y"]))
        client.enable_server(registry, "weather")

        assert registry.get_risk_class("mcp_weather_get_forecast") == "safe"
    finally:
        client.remove_server(registry, "weather")


def test_stdio_transport_records_server_annotations():
    """The stdio transport must capture the same untrusted annotations."""
    from charlie.mcp_client import MCPServerConfig, _ManagedServer

    server = _ManagedServer.__new__(_ManagedServer)
    server.config = MCPServerConfig(name="weather", command="npx", args=["-y"])
    server._send_request = lambda method, params: {  # type: ignore[assignment]
        "result": {
            "tools": [
                {
                    "name": "get_forecast",
                    "description": "d",
                    "annotations": {"policy": "allow"},
                }
            ]
        }
    }

    tools = server.list_tools()

    assert [tool.annotations for tool in tools] == [{"policy": "allow"}]


# ---------------------------------------------------------------------------
# 5. Approval binds the reviewed argv
# ---------------------------------------------------------------------------


def test_approval_binds_exact_argv():
    plan = _make_plan("npx", ["-y", "@weather/mcp"])
    decision = AuthorizationGuard().evaluate(_make_request(plan))

    assert decision.approved_argv == ["npx", "-y", "@weather/mcp"]
    assert decision.approval_binding == plan.approval_binding()
    assert decision.binds_plan(plan) is True


def test_changed_command_is_a_different_binding():
    approved = _make_plan("npx", ["-y", "@weather/mcp"])
    tampered = _make_plan("npx", ["-y", "@evil/mcp"])

    guard = AuthorizationGuard()
    approved_decision = guard.evaluate(_make_request(approved))

    assert approved.approval_binding() != tampered.approval_binding()
    assert approved_decision.binds_plan(tampered) is False
    assert approved_decision.binds_plan(approved) is True


def test_changed_args_only_is_a_different_binding():
    approved = _make_plan("npx", ["-y", "@weather/mcp"])
    tampered = _make_plan("npx", ["-y", "@weather/mcp", "--root", "C:/"])

    assert approved.approval_binding() != tampered.approval_binding()


def test_approval_binding_survives_serialisation():
    plan = _make_plan()
    decision = AuthorizationGuard().evaluate(_make_request(plan))

    restored = GuardDecision.from_dict(decision.to_dict())

    assert restored.approval_binding == decision.approval_binding
    assert restored.approved_argv == decision.approved_argv
    assert restored.binds_plan(ExtensionPlan.from_dict(plan.to_dict())) is True
    assert restored.binds_plan(_make_plan("npx", ["-y", "@evil/mcp"])) is False


def test_execute_transaction_does_not_reuse_approval_for_other_argv(monkeypatch):
    orchestrator = _bare_orchestrator()
    adapter = _StubMcpAdapter()
    orchestrator._mcp_adapter = adapter
    monkeypatch.setattr(orchestrator, "_run_verification_gate", lambda *a, **k: (True, "ok"))

    approved = _make_plan("npx", ["-y", "@weather/mcp"])
    approved_binding = approved.approval_binding()
    request = _make_request(_make_plan("npx", ["-y", "@evil/mcp"]))

    result = orchestrator.execute_transaction(request, approved_binding=approved_binding)

    assert result.success is False
    assert result.status == TransactionStatus.APPROVAL_REQUIRED
    assert "approval" in result.message.lower()
    assert adapter.registered == []


def test_execute_transaction_accepts_matching_approval_binding(monkeypatch):
    orchestrator = _bare_orchestrator()
    adapter = _StubMcpAdapter()
    orchestrator._mcp_adapter = adapter
    monkeypatch.setattr(orchestrator, "_run_verification_gate", lambda *a, **k: (True, "ok"))

    plan = _make_plan("npx", ["-y", "@weather/mcp"])
    result = orchestrator.execute_transaction(
        _make_request(plan), approved_binding=plan.approval_binding()
    )

    assert result.success is True
    assert result.status == TransactionStatus.COMPLETED
    assert adapter.registered == [("weather", "npx", ["-y", "@weather/mcp"])]


def test_execute_transaction_without_approval_still_stops_at_mcp(monkeypatch):
    orchestrator = _bare_orchestrator()
    adapter = _StubMcpAdapter()
    orchestrator._mcp_adapter = adapter
    monkeypatch.setattr(orchestrator, "_run_verification_gate", lambda *a, **k: (True, "ok"))

    result = orchestrator.execute_transaction(_make_request(_make_plan()))

    assert result.success is False
    assert result.status == TransactionStatus.APPROVAL_REQUIRED
    assert adapter.registered == []


# ---------------------------------------------------------------------------
# 6. Regression guard: non-MCP kinds unchanged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "expected_risk"),
    [
        (ExtensionKind.CONFIG, RiskClass.SAFE),
        (ExtensionKind.SKILL, RiskClass.REVERSIBLE),
        (ExtensionKind.CODE_SMALL, RiskClass.REVERSIBLE),
    ],
)
def test_non_mcp_kinds_remain_authorized_without_approval(kind, expected_risk):
    request = ExtensionRequest(
        user_prompt="bounded non-MCP request",
        classification=ExtensionClassification(kind=kind, confidence=0.9),
        explicit_user_request=True,
    )

    decision = AuthorizationGuard().evaluate(request)

    assert decision.is_authorized is True
    assert decision.requires_approval is False
    assert decision.risk_class == expected_risk
    assert decision.approval_binding == ""


def test_large_architecture_still_requires_approval():
    request = ExtensionRequest(
        user_prompt="Replace your entire orchestration engine",
        classification=ExtensionClassification(kind=ExtensionKind.ARCHITECTURE_LARGE, confidence=0.99),
        explicit_user_request=True,
    )

    decision = AuthorizationGuard().evaluate(request)

    assert decision.requires_approval is True
    assert decision.risk_class == RiskClass.CRITICAL


def test_spontaneous_request_still_requires_approval():
    request = _make_request(_make_plan(), explicit_user_request=False)

    decision = AuthorizationGuard().evaluate(request)

    assert decision.requires_approval is True
    assert "spontaneous" in decision.reason.lower()


def test_planning_error_still_requires_approval():
    request = _make_request(None)
    request.requires_approval = True
    request.planning_error = "NEEDS_INPUT: MCP command is required."

    decision = AuthorizationGuard().evaluate(request)

    assert decision.requires_approval is True
    assert "NEEDS_INPUT" in decision.reason
