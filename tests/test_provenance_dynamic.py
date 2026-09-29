"""Focused tests for dynamic capability provenance classification."""

import pytest

from charlie.security.provenance import trust_level_for_tool
from charlie.tools import ToolRegistry


@pytest.mark.parametrize("tool_name", ["skill_demo_run", "api_get_widget"])
def test_dynamic_extension_prefixes_are_external(tool_name):
    assert trust_level_for_tool(tool_name) == "tool_external"


def test_registered_generated_extension_is_external():
    registry = ToolRegistry()
    tool_name = "generated_provenance_probe"
    registry.register_tool(
        name=tool_name,
        description="Generated extension probe",
        schema={"type": "object"},
        owner="extensions",
        risk_class="security_sensitive",
    )(lambda: "ok")

    try:
        assert trust_level_for_tool(tool_name) == "tool_external"
    finally:
        registry.unregister_tool(tool_name)


def test_registered_native_tool_overrides_dynamic_prefix():
    registry = ToolRegistry()
    tool_name = "api_native_probe"
    registry.register_tool(
        name=tool_name,
        description="Native tool probe",
        schema={"type": "object"},
        owner="tools",
        risk_class="safe",
    )(lambda: "ok")

    try:
        assert trust_level_for_tool(tool_name) == "user_turn"
    finally:
        registry.unregister_tool(tool_name)
