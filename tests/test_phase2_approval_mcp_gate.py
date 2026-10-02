"""MCP enablement must be enforced at every self-extension spawn site.

``MCP_ENABLED=false`` was honoured at ``charlie.extensions.install`` and at
main's ``enable`` gate, but the two self-extension fallbacks that call
``MCPClient.add_server`` / ``enable_server`` directly -- and therefore start a
subprocess -- had no check at all.

These tests assert the invariant in the two owned self-extension files, and
assert that the canonical main-owned seam is still the single authority for the
path it owns.

Evidence class: TEST/MOCK (nothing is installed or started).
"""

from __future__ import annotations

from charlie.self_extension.adapters.mcp_adapter import MCPAdapter
from charlie.self_extension.models import ExtensionKind
from charlie.self_extension.registry import ExtensionRegistry, mcp_enablement_allowed


class _FakeToolRegistry:
    def __init__(self) -> None:
        self.tools: dict[str, str] = {}

    def register_tool(self, name, description, schema, **kwargs):
        def decorator(func):
            self.tools[name] = name
            return func

        return decorator

    def unregister_tool(self, name) -> None:
        self.tools.pop(name, None)


class _FakeMcpClient:
    """Records the spawn calls; never starts anything."""

    def __init__(self) -> None:
        self.added: list[str] = []
        self.enabled: list[str] = []
        self.removed: list[str] = []
        self._tools: list = []

    def add_server(self, config) -> None:
        self.added.append(config.name)

    def enable_server(self, registry, name):
        self.enabled.append(name)

        from charlie.mcp_client import MCPTool

        tool = MCPTool(name="probe", description="probe")
        tool.server_name = name
        self._tools = [tool]
        registry.register_tool(
            name=f"mcp_{name}_probe", description="probe", schema={"type": "object"}
        )
        return [f"mcp_{name}_probe"]

    def list_tools(self):
        return self._tools

    def health_check(self):
        return {name: True for name in self.added}

    def tool_policy(self, _tool_id):
        return "ask"

    def remove_server(self, registry, name) -> None:
        self.removed.append(name)


def _adapter(tmp_path, *, mcp_enabled=None, with_client=True):
    registry = ExtensionRegistry(manifest_path=tmp_path / "extensions.json")
    client = _FakeMcpClient() if with_client else None
    tool_registry = _FakeToolRegistry()
    kwargs = {} if mcp_enabled is None else {"mcp_enabled": mcp_enabled}
    adapter = MCPAdapter(
        registry=registry,
        mcp_client=client,
        tool_registry=tool_registry,
        **kwargs,
    )
    return adapter, client, tool_registry


class TestTheEnablementInvariant:
    def test_the_canonical_predicate_defaults_to_disabled_in_test_mode(self):
        """`MCP_ENABLED` is unset under pytest, so the canonical value is off."""
        assert mcp_enablement_allowed(None) is False

    def test_the_canonical_predicate_honours_an_explicit_value(self):
        assert mcp_enablement_allowed(True) is True
        assert mcp_enablement_allowed(False) is False


class TestMCPAdapterRefusesWhenMcpIsDisabled:
    def test_an_unstated_flag_fails_closed(self, tmp_path):
        adapter, client, tool_registry = _adapter(tmp_path)

        result = adapter.register_mcp_server(
            name="weather", command="npx", args=["-y", "@weather/mcp"]
        )

        assert result.success is False
        assert "MCP_ENABLED" in result.message
        assert client.added == [], "no server may be registered while MCP is disabled"
        assert client.enabled == [], "no subprocess may be started while MCP is disabled"
        assert tool_registry.tools == {}

    def test_an_explicit_false_refuses(self, tmp_path):
        adapter, client, _tool_registry = _adapter(tmp_path, mcp_enabled=False)

        result = adapter.register_mcp_server(name="weather", command="npx", args=["-y"])

        assert result.success is False
        assert "MCP_ENABLED" in result.message
        assert client.added == []
        assert client.enabled == []

    def test_an_explicit_true_proceeds(self, tmp_path):
        adapter, client, _tool_registry = _adapter(tmp_path, mcp_enabled=True)

        result = adapter.register_mcp_server(
            name="weather", command="npx", args=["-y", "@weather/mcp"]
        )

        assert result.success is True
        assert client.added == ["weather"]
        assert client.enabled == ["weather"]

    def test_no_client_is_still_reported_as_missing_before_any_enablement_check(self, tmp_path):
        """Pre-existing contract preserved: no MCPClient means no registry-only install."""
        adapter, _client, _tool_registry = _adapter(tmp_path, with_client=False)

        result = adapter.register_mcp_server(name="weather", command="npx", args=["-y"])

        assert result.success is False
        assert "MCPClient unavailable" in result.message

    def test_the_canonical_runtime_seam_is_not_double_gated(self, tmp_path):
        """main owns the flag on the canonical path; the local gate must not shadow it."""
        registry = ExtensionRegistry(manifest_path=tmp_path / "extensions.json")
        seen: list[tuple] = []

        def _runtime_operation(operation, name, command, args, env):
            seen.append((operation, name))
            return {"success": True, "tool_names": ["probe"]}

        adapter = MCPAdapter(
            registry=registry,
            tool_registry=_FakeToolRegistry(),
            runtime_extension_operation=_runtime_operation,
        )

        result = adapter.register_mcp_server(name="weather", command="npx", args=["-y"])

        assert result.success is True
        assert seen == [("install", "weather")]


class TestExtensionRegistryRefusesMcpRehydrationWhenDisabled:
    def _entry(self):
        from charlie.self_extension.registry import ExtensionEntry

        return ExtensionEntry(
            extension_id="mcp_weather",
            name="weather",
            kind=ExtensionKind.MCP_TOOL,
            source="npx -y @weather/mcp",
            content_hash="abc123",
            metadata={"command": "npx", "args": ["-y", "@weather/mcp"]},
        )

    def _manifest(self, tmp_path):
        from charlie.self_extension.registry import ExtensionRegistry as _Registry

        registry = _Registry(manifest_path=tmp_path / "extensions.json")
        registry.register(self._entry())
        return _Registry(manifest_path=tmp_path / "extensions.json")

    def test_disabled_rehydration_starts_nothing(self, tmp_path):
        from charlie.capabilities import CapabilityIndex

        registry = self._manifest(tmp_path)
        client = _FakeMcpClient()
        tool_registry = _FakeToolRegistry()

        report = registry.rehydrate(
            CapabilityIndex(),
            mcp_client=client,
            tool_registry=tool_registry,
            mcp_enabled=False,
        )

        assert report.restored == 0
        assert report.failed == 1
        assert client.added == []
        assert client.enabled == []
        assert tool_registry.tools == {}

    def test_enabled_rehydration_still_works(self, tmp_path):
        from charlie.capabilities import CapabilityIndex

        registry = self._manifest(tmp_path)
        client = _FakeMcpClient()
        tool_registry = _FakeToolRegistry()

        report = registry.rehydrate(
            CapabilityIndex(),
            mcp_client=client,
            tool_registry=tool_registry,
            mcp_enabled=True,
        )

        assert report.restored == 1
        assert client.added == ["weather"]
        assert client.enabled == ["weather"]

    def test_the_registry_remembers_its_configured_enablement(self, tmp_path):
        from charlie.capabilities import CapabilityIndex

        manifest = tmp_path / "extensions.json"
        seed = ExtensionRegistry(manifest_path=manifest, mcp_enabled=True)
        seed.register(self._entry())
        registry = ExtensionRegistry(manifest_path=manifest, mcp_enabled=False)
        client = _FakeMcpClient()

        report = registry.rehydrate(
            CapabilityIndex(),
            mcp_client=client,
            tool_registry=_FakeToolRegistry(),
        )

        assert report.failed == 1
        assert client.added == []
