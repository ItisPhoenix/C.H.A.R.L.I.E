"""Tests for Config and MCP extension adapters."""

import tempfile
from pathlib import Path
import pytest

from charlie.config import Config
from charlie.settings_service import SettingsService
from charlie.capabilities import CapabilityIndex
from charlie.self_extension.registry import ExtensionRegistry
from charlie.self_extension.adapters.config_adapter import ConfigAdapter
from charlie.self_extension.adapters.mcp_adapter import MCPAdapter


@pytest.fixture
def mock_config_mcp_env():
    """Create isolated settings and MCP test environment."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir).resolve()
        env_file = base / ".env"
        env_file.write_text("ASSISTANT_NAME=C.H.A.R.L.I.E.\nLLM_PROVIDER=openai\n", encoding="utf-8")

        cfg = Config()
        settings_service = SettingsService(config_instance=cfg, env_path=env_file)
        cap_idx = CapabilityIndex()
        registry = ExtensionRegistry(manifest_path=base / "extensions.json", capability_index=cap_idx)

        config_adapter = ConfigAdapter(settings_service=settings_service, config=cfg)
        mcp_adapter = MCPAdapter(registry=registry, capability_index=cap_idx)

        yield config_adapter, mcp_adapter, settings_service, cfg, cap_idx, env_file


def test_config_adapter_apply_and_rollback(mock_config_mcp_env):
    """Verify ConfigAdapter applies validated updates and restores previous values on rollback."""
    config_adapter, _, _, cfg, _, env_file = mock_config_mcp_env

    # 1. Capture preimage
    preimage = config_adapter.capture_preimage(["LLM_MODEL"])
    assert preimage["LLM_MODEL"] == cfg.llm_model

    # 2. Apply update
    res = config_adapter.apply_updates({"LLM_MODEL": "gpt-4o"})
    assert res.success is True
    assert cfg.llm_model == "gpt-4o"
    assert "LLM_MODEL=gpt-4o" in env_file.read_text(encoding="utf-8")

    # 3. Rollback
    rb_res = config_adapter.rollback(preimage)
    assert rb_res.success is True
    assert cfg.llm_model == preimage["LLM_MODEL"]


def test_mcp_adapter_register_and_rollback(mock_config_mcp_env):
    """Verify MCPAdapter registers MCP server config and rolls back cleanly on failure."""
    _, mcp_adapter, _, _, cap_idx, _ = mock_config_mcp_env

    spec = {
        "name": "sqlite_tool",
        "command": "python",
        "args": ["-m", "mcp_server_sqlite", "--db", "test.db"],
        "declared_tools": ["query_db", "list_tables"],
    }

    # Apply MCP registration
    res = mcp_adapter.register_mcp_server(
        name=spec["name"],
        command=spec["command"],
        args=spec["args"],
        declared_tools=spec["declared_tools"],
    )
    assert res.success is True
    assert cap_idx.get_capability("mcp_sqlite_tool") is not None

    # Rollback MCP registration
    rb_res = mcp_adapter.rollback_mcp_server("sqlite_tool")
    assert rb_res.success is True
    assert cap_idx.get_capability("mcp_sqlite_tool") is None
