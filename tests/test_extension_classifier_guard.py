"""Tests for Controlled Self-Extension Classifier and Authorization Guard."""

import pytest

from charlie.capabilities import CapabilityDescriptor, CapabilityIndex, CapabilityOperation
from charlie.self_extension.classifier import ExtensionClassifier
from charlie.self_extension.guard import AuthorizationGuard
from charlie.self_extension.models import (
    ExtensionClassification,
    ExtensionKind,
    ExtensionRequest,
    RiskClass,
)


@pytest.fixture
def mock_classifier_env():
    """Create isolated environment with CapabilityIndex and mock SelfKnowledge."""
    cap_idx = CapabilityIndex()
    cap_idx.register_capability(
        CapabilityDescriptor(
            id="desktop",
            name="Desktop Control",
            description="Control mouse and keyboard",
            owner="charlie.desktop",
            operations={
                "click": CapabilityOperation(
                    id="click",
                    name="click",
                    description="Click mouse",
                    parameters_schema={"type": "object"},
                    risk_class="reversible",
                )
            },
            availability_check=lambda: True,
            provenance="builtin",
        )
    )

    classifier = ExtensionClassifier(capability_index=cap_idx)
    guard = AuthorizationGuard()
    return classifier, guard, cap_idx


def test_classify_config_request(mock_classifier_env):
    """Verify config/settings adjustment requests classify as CONFIG."""
    classifier, _, _ = mock_classifier_env
    res = classifier.classify("Change your wake word sensitivity to high")
    assert isinstance(res, ExtensionClassification)
    assert res.kind == ExtensionKind.CONFIG
    assert res.is_already_supported is False


def test_classify_skill_request(mock_classifier_env):
    """Verify reusable process/procedure requests classify as SKILL."""
    classifier, _, _ = mock_classifier_env
    res = classifier.classify("Remember this reusable procedure for triaging server alerts")
    assert res.kind == ExtensionKind.SKILL


def test_classify_mcp_tool_request(mock_classifier_env):
    """Verify MCP server connection requests classify as MCP_TOOL."""
    classifier, _, _ = mock_classifier_env
    res = classifier.classify("Connect the weather MCP server at npx -y @weather/mcp")
    assert res.kind == ExtensionKind.MCP_TOOL


def test_classify_code_small_request(mock_classifier_env):
    """Verify targeted tool/function authoring requests classify as CODE_SMALL."""
    classifier, _, _ = mock_classifier_env
    res = classifier.classify("Add a small calculator capability to compute Fibonacci numbers")
    assert res.kind == ExtensionKind.CODE_SMALL


def test_classify_architecture_large_request(mock_classifier_env):
    """Verify major architectural refactor requests classify as ARCHITECTURE_LARGE."""
    classifier, _, _ = mock_classifier_env
    res = classifier.classify("Replace your entire orchestration engine and memory subsystem")
    assert res.kind == ExtensionKind.ARCHITECTURE_LARGE


def test_classify_already_existing_capability(mock_classifier_env):
    """Verify classifier detects capabilities Charlie already has."""
    classifier, _, _ = mock_classifier_env
    res = classifier.classify("Add a tool to click on the screen")
    assert res.is_already_supported is True
    assert "desktop" in res.existing_capability_id


def test_guard_allows_explicit_user_request(mock_classifier_env):
    """Verify explicit user requests within bounded scope pass the guard."""
    _, guard, _ = mock_classifier_env
    req = ExtensionRequest(
        request_id="req-1",
        user_prompt="Add a helper function for base64 encoding",
        classification=ExtensionClassification(kind=ExtensionKind.CODE_SMALL, confidence=0.9),
        explicit_user_request=True,
        affected_files=["charlie/utils.py"],
        risk_class=RiskClass.SAFE,
    )
    decision = guard.evaluate(req)
    assert decision.is_authorized is True
    assert decision.requires_approval is False


def test_guard_blocks_spontaneous_self_mutation(mock_classifier_env):
    """Verify spontaneous self-edits (not initiated by explicit user command) require approval."""
    _, guard, _ = mock_classifier_env
    req = ExtensionRequest(
        request_id="req-2",
        user_prompt="Internal optimization: rewrite router",
        classification=ExtensionClassification(kind=ExtensionKind.CODE_SMALL, confidence=0.95),
        explicit_user_request=False,  # Spontaneous
        affected_files=["charlie/router.py"],
        risk_class=RiskClass.DANGEROUS,
    )
    decision = guard.evaluate(req)
    assert decision.is_authorized is False
    assert decision.requires_approval is True
    assert "spontaneous" in decision.reason.lower()


def test_guard_gates_scope_expansion_and_large_architecture(mock_classifier_env):
    """Verify large architecture and dependency changes always require approval."""
    _, guard, _ = mock_classifier_env
    req = ExtensionRequest(
        request_id="req-3",
        user_prompt="Replace entire memory subsystem",
        classification=ExtensionClassification(kind=ExtensionKind.ARCHITECTURE_LARGE, confidence=0.99),
        explicit_user_request=True,
        affected_files=["charlie/memory_service.py", "charlie/core.py"],
        risk_class=RiskClass.CRITICAL,
    )
    decision = guard.evaluate(req)
    assert decision.requires_approval is True
