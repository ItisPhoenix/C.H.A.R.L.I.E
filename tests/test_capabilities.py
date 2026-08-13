from charlie.capabilities import build_capability_roster
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
