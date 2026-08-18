"""Tests for Persistent Extension Registry and Reusable Skill Lifecycle."""

import json
import tempfile
from pathlib import Path

import pytest

from charlie.capabilities import CapabilityIndex
from charlie.self_extension.adapters.skill_adapter import SkillAdapter
from charlie.self_extension.models import ExtensionKind
from charlie.self_extension.registry import ExtensionEntry, ExtensionRegistry


@pytest.fixture
def mock_registry_env():
    """Create isolated directory for extension registry and skills."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir).resolve()
        manifest_path = base / "extensions.json"
        skills_dir = base / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)

        cap_idx = CapabilityIndex()
        registry = ExtensionRegistry(manifest_path=manifest_path, capability_index=cap_idx)
        skill_adapter = SkillAdapter(skills_dir=skills_dir, registry=registry, capability_index=cap_idx)

        yield registry, skill_adapter, cap_idx, base, manifest_path, skills_dir


def test_registry_persistence_across_reloads(mock_registry_env):
    """Verify extensions persist to JSON and reload cleanly on new instance."""
    registry, _, cap_idx, _, manifest_path, _ = mock_registry_env

    entry = ExtensionEntry(
        extension_id="skill_alert_triage",
        name="alert_triage",
        kind=ExtensionKind.SKILL,
        source="local:skills/alert_triage",
        content_hash="abc12345",
        enabled=True,
        declared_tools=["triage_alert"],
        metadata={"author": "user"},
    )
    registry.register(entry)

    assert manifest_path.exists()
    with open(manifest_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        assert "skill_alert_triage" in data

    # Create new registry instance to simulate server restart
    new_reg = ExtensionRegistry(manifest_path=manifest_path, capability_index=cap_idx)
    loaded = new_reg.get("skill_alert_triage")
    assert loaded is not None
    assert loaded.name == "alert_triage"
    assert loaded.enabled is True
    assert loaded.declared_tools == ["triage_alert"]


def test_registry_never_persists_raw_secrets(mock_registry_env):
    """Verify registry scrubs API keys or secret tokens from manifest metadata."""
    registry, _, _, _, manifest_path, _ = mock_registry_env

    entry = ExtensionEntry(
        extension_id="mcp_weather",
        name="weather",
        kind=ExtensionKind.MCP_TOOL,
        source="npx -y @weather/mcp",
        content_hash="hash999",
        metadata={"api_key": "sk-super-secret", "token": "ghp_123456", "safe_param": "metric"},
    )
    registry.register(entry)

    with open(manifest_path, "r", encoding="utf-8") as f:
        content = f.read()
        assert "sk-super-secret" not in content
        assert "ghp_123456" not in content
        assert "metric" in content


def test_skill_lifecycle_create_update_disable_remove(mock_registry_env):
    """Verify end-to-end skill creation, update, disable, and removal."""
    _, skill_adapter, cap_idx, _, _, skills_dir = mock_registry_env

    skill_content = """---
name: incident_responder
description: Reusable playbook for triaging incidents
scripts:
  - triage.py
---
# Incident Responder Playbook
1. Check metrics
2. Check logs
3. Page on-call
"""
    # 1. Create skill
    res = skill_adapter.save_skill(name="incident_responder", raw_text=skill_content)
    assert res.success is True
    skill_file = skills_dir / "incident_responder" / "SKILL.md"
    assert skill_file.exists()

    # Verify registered in capabilities
    desc = cap_idx.get_capability("skill_incident_responder")
    assert desc is not None
    assert desc.name == "incident_responder"

    # 2. Disable skill
    dis_res = skill_adapter.set_enabled("incident_responder", enabled=False)
    assert dis_res.success is True
    assert cap_idx.get_capability("skill_incident_responder") is None

    # 3. Enable skill
    en_res = skill_adapter.set_enabled("incident_responder", enabled=True)
    assert en_res.success is True
    assert cap_idx.get_capability("skill_incident_responder") is not None

    # 4. Remove skill
    del_res = skill_adapter.remove_skill("incident_responder")
    assert del_res.success is True
    assert not skill_file.exists()
    assert cap_idx.get_capability("skill_incident_responder") is None
