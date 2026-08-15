from __future__ import annotations

import asyncio

import pytest

from charlie.autonomy import Requirement, RiskClass, classify_action, evaluate
from charlie.capabilities import (
    CapabilityDescriptor,
    CapabilityIndex,
    CapabilityOperation,
    capability_index,
)
from charlie.extensions.skills import SkillManifest, register_skill_scripts
from charlie.mcp_client import MCPClient, MCPTool
from charlie.resource_locks import CapabilityLeaseManager
from charlie.tools import ToolRegistry


def test_capability_operation_validation():
    # Valid operation
    op = CapabilityOperation(
        id="desktop.window.focus",
        name="desktop_focus",
        description="Focus a window by title",
        parameters_schema={"type": "object", "properties": {"title": {"type": "string"}}},
        risk_class="safe",
        required_leases=("desktop",),
        timeout_sec=15.0,
        verifier=None,
    )
    assert op.id == "desktop.window.focus"
    assert op.name == "desktop_focus"
    assert op.required_leases == ("desktop",)
    assert op.risk_class == "safe"

    # Empty id should raise ValueError
    with pytest.raises(ValueError, match="Operation ID cannot be empty"):
        CapabilityOperation(
            id="",
            name="invalid",
            description="desc",
            parameters_schema={},
        )

    # Empty name should raise ValueError
    with pytest.raises(ValueError, match="Operation name cannot be empty"):
        CapabilityOperation(
            id="op.id",
            name="",
            description="desc",
            parameters_schema={},
        )

    # Invalid risk class should raise ValueError
    with pytest.raises(ValueError, match="Invalid risk_class"):
        CapabilityOperation(
            id="op.id",
            name="test_tool",
            description="desc",
            parameters_schema={},
            risk_class="super_risky",
        )


def test_capability_descriptor_validation():
    op = CapabilityOperation(
        id="system.metrics.read",
        name="system_diagnostics",
        description="Read system metrics",
        parameters_schema={"type": "object"},
        risk_class="safe",
    )
    desc = CapabilityDescriptor(
        id="system",
        name="SystemCapability",
        description="Core system telemetry and controls",
        owner="charlie.system",
        provenance="builtin",
        operations={"system_diagnostics": op},
        availability_check=lambda: True,
        health_check=lambda: {"status": "ok"},
    )
    assert desc.id == "system"
    assert desc.is_available() is True
    assert desc.get_health() == {"status": "ok", "available": True}

    # Missing ID raises ValueError
    with pytest.raises(ValueError, match="Capability ID cannot be empty"):
        CapabilityDescriptor(
            id="",
            name="Name",
            description="Desc",
            owner="owner",
        )

    # Invalid provenance raises ValueError
    with pytest.raises(ValueError, match="Invalid provenance"):
        CapabilityDescriptor(
            id="test",
            name="Name",
            description="Desc",
            owner="owner",
            provenance="alien_source",
        )


def test_capability_index_registration_and_lookup():
    index = CapabilityIndex()
    op1 = CapabilityOperation(
        id="file.read",
        name="file_read",
        description="Read file contents",
        parameters_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        risk_class="safe",
    )
    op2 = CapabilityOperation(
        id="file.write",
        name="file_write",
        description="Write file contents",
        parameters_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        risk_class="reversible",
    )
    file_cap = CapabilityDescriptor(
        id="file",
        name="FileCapability",
        description="Filesystem access",
        owner="charlie.file",
        provenance="builtin",
        operations={"file_read": op1, "file_write": op2},
    )

    index.register_capability(file_cap)

    # Duplicate capability ID raises ValueError
    with pytest.raises(ValueError, match="already registered"):
        index.register_capability(file_cap)

    # Lookup capability
    assert index.get_capability("file") == file_cap
    assert index.get_capability("unknown") is None

    # Lookup operations by tool name and semantic ID
    assert index.get_operation("file_read") == op1
    assert index.get_operation("file.read") == op1
    assert index.get_operation("file_write") == op2
    assert index.get_operation("unknown_tool") is None

    # List capabilities
    caps = index.list_capabilities()
    assert len(caps) == 1
    assert caps[0].id == "file"

    # Find operations
    ops = index.find_operations(domain="file")
    assert len(ops) == 2
    assert {op.name for op in ops} == {"file_read", "file_write"}

    # Unregister capability
    assert index.unregister_capability("file") is True
    assert index.get_capability("file") is None
    assert index.get_operation("file_read") is None
    assert index.unregister_capability("file") is False


def test_capability_index_availability_and_health():
    index = CapabilityIndex()

    # Desktop capability with custom availability and health
    desktop_available = [True]
    desktop_op = CapabilityOperation(
        id="desktop.window.focus",
        name="desktop_focus",
        description="Focus window",
        parameters_schema={},
        required_leases=("desktop",),
    )
    desktop_cap = CapabilityDescriptor(
        id="desktop",
        name="DesktopCapability",
        description="Desktop UIA control",
        owner="charlie.desktop",
        provenance="builtin",
        operations={"desktop_focus": desktop_op},
        availability_check=lambda: desktop_available[0],
        health_check=lambda: {"ready": desktop_available[0], "driver": "uia"},
    )
    index.register_capability(desktop_cap)

    assert index.is_available("desktop") is True
    assert index.is_available("desktop_focus") is True
    assert index.get_health("desktop") == {"status": "ok", "ready": True, "driver": "uia", "available": True}

    desktop_available[0] = False
    assert index.is_available("desktop") is False
    assert index.is_available("desktop_focus") is False
    assert index.get_health("desktop") == {"status": "degraded", "ready": False, "driver": "uia", "available": False}

    # Filtering available only
    assert len(index.list_capabilities(include_unavailable=False)) == 0
    assert len(index.find_operations(available_only=True)) == 0
    assert len(index.find_operations(available_only=False)) == 1


def test_capability_index_provenance_and_mcp():
    index = CapabilityIndex()

    mcp_op = CapabilityOperation(
        id="mcp.github.create_issue",
        name="mcp_github_create_issue",
        description="Create issue in GitHub repo",
        parameters_schema={"type": "object"},
        risk_class="reversible",
    )
    mcp_cap = CapabilityDescriptor(
        id="mcp.github",
        name="GitHub MCP",
        description="GitHub integration via MCP",
        owner="mcp",
        provenance="mcp",
        source="github_server",
        operations={"mcp_github_create_issue": mcp_op},
    )
    index.register_capability(mcp_cap)

    retrieved = index.get_capability("mcp.github")
    assert retrieved is not None
    assert retrieved.provenance == "mcp"
    assert retrieved.source == "github_server"

    op = index.get_operation("mcp_github_create_issue")
    assert op is not None
    assert op.id == "mcp.github.create_issue"


def test_capability_schema_filtering():
    index = CapabilityIndex()
    sys_op = CapabilityOperation(
        id="system.diagnostics",
        name="system_diagnostics",
        description="Get system stats",
        parameters_schema={"type": "object", "properties": {"verbose": {"type": "boolean"}}},
    )
    sys_cap = CapabilityDescriptor(
        id="system",
        name="SystemCapability",
        description="System stats",
        owner="charlie.system",
        operations={"system_diagnostics": sys_op},
    )
    file_op = CapabilityOperation(
        id="file.read",
        name="file_read",
        description="Read file",
        parameters_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    )
    file_cap = CapabilityDescriptor(
        id="file",
        name="FileCapability",
        description="File ops",
        owner="charlie.file",
        operations={"file_read": file_op},
    )
    index.register_capability(sys_cap)
    index.register_capability(file_cap)

    # Filter all schemas
    all_schemas = index.filter_schemas()
    assert len(all_schemas) == 2

    # Filter by domain
    file_schemas = index.filter_schemas(domains=["file"])
    assert len(file_schemas) == 1
    assert file_schemas[0]["function"]["name"] == "file_read"


@pytest.mark.asyncio
async def test_capability_lease_manager_integration():
    op = capability_index.get_operation("desktop_click")
    assert op is not None
    assert "desktop" in op.required_leases

    manager = CapabilityLeaseManager()
    lease1 = await manager.acquire("desktop", "task-1")
    assert manager.current_owner("desktop") == "task-1"

    # Second task requesting desktop fails or waits
    with pytest.raises(asyncio.TimeoutError):
        await manager.acquire("desktop", "task-2", timeout=0.05)

    await lease1.release()
    assert manager.current_owner("desktop") is None


def test_autonomy_policy_queries_capability_index():
    # Register custom tool with destructive risk
    reg = ToolRegistry()
    reg.register_tool(
        name="danger_tool",
        description="Dangerous operation",
        schema={"type": "object"},
        owner="tools",
        risk_class="destructive",
    )(lambda: "done")

    risk, reason = classify_action("danger_tool", {})
    assert risk == RiskClass.DESTRUCTIVE

    req, r_class, r_reason = evaluate("danger_tool", {})
    assert req == Requirement.APPROVE
    assert r_class == RiskClass.DESTRUCTIVE


def test_tool_registry_compatibility_sync():
    reg = ToolRegistry()

    @reg.register_tool(
        name="custom_op",
        description="Custom operation",
        schema={"type": "object", "properties": {"param": {"type": "string"}}},
        owner="system",
        risk_class="reversible",
    )
    def my_op(param: str = ""):
        return f"result: {param}"

    # ToolRegistry methods work
    assert "custom_op" in reg.get_tool_names()
    assert reg.get_owner("custom_op") == "system"
    assert reg.get_risk_class("custom_op") == "reversible"
    assert reg.execute_tool("custom_op", {"param": "hello"}) == "result: hello"

    # CapabilityIndex has the operation
    op = capability_index.get_operation("custom_op")
    assert op is not None
    assert op.name == "custom_op"
    assert op.risk_class == "reversible"

    # Unregister syncs to CapabilityIndex
    reg.unregister_tool("custom_op")
    assert "custom_op" not in reg.get_tool_names()
    assert capability_index.get_operation("custom_op") is None


def test_mcp_client_registration_in_capability_index():
    client = MCPClient()
    client._servers = ["test_server"]
    client._tools = {
        "test_server:echo": MCPTool(
            server_name="test_server",
            name="echo",
            description="Echo message",
            input_schema={"type": "object", "properties": {"msg": {"type": "string"}}},
        )
    }

    reg = ToolRegistry()
    registered = client.register_tools_into(reg)
    assert "mcp_test_server_echo" in registered

    op = capability_index.get_operation("mcp_test_server_echo")
    assert op is not None
    assert op.name == "mcp_test_server_echo"

    cap = capability_index.get_capability("mcp")
    assert cap is not None
    assert cap.provenance == "mcp"

    client.unregister_server_tools(reg, "test_server")
    assert "mcp_test_server_echo" not in reg.get_tool_names()
    assert capability_index.get_operation("mcp_test_server_echo") is None


def test_skill_extension_registration_in_capability_index():
    manifest = SkillManifest(
        name="test_skill",
        description="A test skill",
        metadata={},
        instructions="Do something",
        scripts=["run.py"],
    )
    reg = ToolRegistry()
    registered = register_skill_scripts(reg, manifest, lambda script, args: f"Ran {script}")

    assert len(registered) == 1
    tool_name = registered[0]

    op = capability_index.get_operation(tool_name)
    assert op is not None
    assert op.name == tool_name

    cap = capability_index.get_capability("extensions")
    assert cap is not None
    assert cap.provenance == "extension"

    reg.unregister_tool(tool_name)
    assert capability_index.get_operation(tool_name) is None
