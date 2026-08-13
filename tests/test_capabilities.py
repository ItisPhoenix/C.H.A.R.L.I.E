import asyncio

import charlie.web_server as web_server
from charlie.capabilities import build_capability_roster, build_capability_snapshot
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


def test_capabilities_endpoint_uses_live_registry(monkeypatch):
    reg = ToolRegistry()
    reg.register_tool("live", "Live tool", {"type": "object", "properties": {}}, owner="tools")(lambda: "ok")
    monkeypatch.setattr("charlie.tools.registry", reg)

    snapshot = asyncio.run(web_server.get_capabilities())

    assert [tool["name"] for tool in snapshot["tools"]] == ["live"]
