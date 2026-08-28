import asyncio

import charlie.web_server as web_server
from charlie.capabilities import (
    CapabilityDescriptor,
    CapabilityIndex,
    CapabilityOperation,
    build_capability_roster,
    build_capability_snapshot,
    capability_index,
    register_tool_in_index,
)
from charlie.config import Config
from charlie.tools import ToolRegistry, registry


def test_roster_snapshot_includes_every_registered_tool():
    # A tool registered but missing from the roster is exactly the stale-prompt bug this replaces.
    cfg = Config()
    cfg.desktop_control_enabled = True
    cfg.browser_enabled = True
    block = build_capability_roster(registry, cfg)
    for name in registry.get_tool_names():
        owner = registry.get_owner(name)
        if owner in ("desktop", "browser"):
            continue  # availability-gated separately, not guaranteed present regardless of config
        assert name in block, f"tool '{name}' is registered but missing from the capability roster"


def test_roster_groups_by_owner_label():
    reg = ToolRegistry()
    empty_schema = {"type": "object", "properties": {}}
    reg.register_tool(name="web_search", description="Search.", schema=empty_schema, owner="tools")(lambda: "x")
    reg.register_tool(name="memory", description="Remember.", schema=empty_schema, owner="memory")(lambda: "x")
    block = build_capability_roster(reg, Config())
    assert "General tools: web_search" in block
    assert "Memory: memory" in block


def test_roster_empty_registry_returns_empty_string():
    assert build_capability_roster(ToolRegistry(), Config()) == ""


def test_snapshot_is_machine_readable_and_secret_free():
    reg = ToolRegistry()
    reg.register_tool("safe", "Safe tool", {"type": "object", "properties": {}}, owner="tools")(lambda: "ok")
    cfg = Config()
    cfg.telegram_bot_token = "secret-token"
    cfg.telegram_user_id = 42

    snapshot = build_capability_snapshot(reg, cfg)

    assert snapshot["tools"][0]["name"] == "safe"
    assert snapshot["subsystems"]["telegram"] == {"enabled": cfg.telegram_enabled, "configured": True}
    assert "secret-token" not in str(snapshot)


def test_brain_stable_roster_uses_capability_index_for_init_and_reload(tmp_path, monkeypatch):
    import charlie.core as core

    seen_sources = []
    monkeypatch.setattr(
        core,
        "build_capability_roster",
        lambda source, _config: seen_sources.append(source) or "canonical roster",
    )

    cfg = Config()
    cfg.llm_url = "https://example.com/v1"
    cfg.llm_key = "test-key"
    cfg.llm_model = "test-model"
    cfg.memory_file = str(tmp_path / "memory.md")
    cfg.user_file = str(tmp_path / "user.md")
    cfg.opinions_file = str(tmp_path / "opinions.md")
    cfg.memory_graph_db = str(tmp_path / "memory-graph.db")
    cfg.world_model_db_path = str(tmp_path / "world-model.db")

    brain = core.Brain(cfg, register_panic_hotkey=False)
    try:
        brain.rebuild_stable_tier()
    finally:
        asyncio.run(brain.client.aclose())

    assert seen_sources == [capability_index, capability_index]


def test_capabilities_endpoint_projects_shared_capability_index(monkeypatch):
    isolated_index = CapabilityIndex()
    register_tool_in_index(
        name="live",
        description="Live tool",
        schema={"type": "object", "properties": {}},
        func=lambda: "ok",
        owner="tools",
        risk_class="reversible",
        index=isolated_index,
    )
    monkeypatch.setattr(web_server, "_shared_capability_index", isolated_index)
    monkeypatch.setattr(web_server, "config", Config())

    snapshot = asyncio.run(web_server.get_capabilities())

    assert [tool["name"] for tool in snapshot["tools"]] == ["live"]
    assert snapshot["tools"][0]["owner"] == "tools"
    assert snapshot["tools"][0]["risk_class"] == "reversible"
    assert "runtime" in snapshot


def test_capability_views_hide_operations_requiring_disabled_desktop():
    isolated_index = CapabilityIndex()
    isolated_index.register_capability(
        CapabilityDescriptor(
            id="system",
            name="SystemCapability",
            description="Test system",
            owner="charlie.tools",
            operations={
                "desktop_bound": CapabilityOperation(
                    id="system.desktop_bound",
                    name="desktop_bound",
                    description="Requires desktop",
                    parameters_schema={"type": "object"},
                    required_leases=("desktop",),
                )
            },
        )
    )
    cfg = Config()
    cfg.desktop_control_enabled = False

    assert "desktop_bound" not in build_capability_roster(isolated_index, cfg)
    assert build_capability_snapshot(isolated_index, cfg)["tools"] == []


def test_capability_views_exclude_index_only_and_unavailable_operations():
    isolated_index = CapabilityIndex()
    isolated_index.register_capability(
        CapabilityDescriptor(
            id="metadata_only",
            name="Metadata only",
            description="Not a callable ToolRegistry operation",
            owner="charlie.extensions",
            provenance="extension",
            operations={
                "inspect": CapabilityOperation(
                    id="metadata_only.inspect",
                    name="inspect",
                    description="Inspection metadata",
                    parameters_schema={"type": "object"},
                )
            },
        )
    )
    isolated_index.register_capability(
        CapabilityDescriptor(
            id="offline",
            name="Offline",
            description="Unavailable capability",
            owner="charlie.mcp",
            provenance="mcp",
            availability_check=lambda: False,
            operations={
                "offline_call": CapabilityOperation(
                    id="offline.call",
                    name="offline_call",
                    description="Unavailable operation",
                    parameters_schema={"type": "object"},
                )
            },
        )
    )
    cfg = Config()

    assert build_capability_roster(isolated_index, cfg) == ""
    assert build_capability_snapshot(isolated_index, cfg)["tools"] == []


def test_core_timeout_table_contains_only_dynamic_exception():
    from charlie.capabilities import BUILTIN_TOOL_METADATA
    from charlie.core import _TOOL_TIMEOUTS

    assert set(BUILTIN_TOOL_METADATA).isdisjoint(_TOOL_TIMEOUTS)
    assert _TOOL_TIMEOUTS["plugin_fs_search"] > 15.0


def test_dynamic_plugin_timeout_fallback_survives_capability_registration():
    import charlie.core as core

    isolated_index = CapabilityIndex()
    operation = register_tool_in_index(
        name="plugin_fs_search",
        description="Dynamic filesystem search",
        schema={"type": "object"},
        func=lambda **_: "ok",
        owner="plugins",
        risk_class="security_sensitive",
        index=isolated_index,
    )

    assert core._tool_timeout("plugin_fs_search", operation) == core._TOOL_TIMEOUTS["plugin_fs_search"]


def test_core_com_compatibility_projection_matches_canonical_operations():
    from charlie.core import _DESKTOP_COM_TOOLS

    expected = {
        operation.name
        for operation in capability_index.find_operations(available_only=False)
        if operation.executor_type == "com_thread"
    }
    assert _DESKTOP_COM_TOOLS == expected
