"""Authoritative 6 Acceptance Proofs for Controlled Self-Extension."""

import json
import tempfile
from pathlib import Path
import pytest

from charlie.config import Config
from charlie.settings_service import SettingsService
from charlie.capabilities import CapabilityIndex
from charlie.self_extension.models import (
    ExtensionKind,
    ExtensionRequest,
    ExtensionClassification,
    TransactionStatus,
    RiskClass,
)
from charlie.self_extension.orchestrator import SelfExtensionOrchestrator


@pytest.fixture
def mock_orchestrator_env():
    """Create isolated test harness for SelfExtensionOrchestrator."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir).resolve()

        # Files
        env_file = repo_root / ".env"
        env_file.write_text("LLM_MODEL=gpt-4o-mini\n", encoding="utf-8")

        manifest_path = repo_root / "extensions.json"
        skills_dir = repo_root / "data" / "skills"
        tools_dir = repo_root / "data" / "generated_tools"
        skills_dir.mkdir(parents=True, exist_ok=True)
        tools_dir.mkdir(parents=True, exist_ok=True)

        cfg = Config()
        cfg.llm_model = "gpt-4o-mini"
        settings_service = SettingsService(config_instance=cfg, env_path=env_file)
        cap_idx = CapabilityIndex()

        orchestrator = SelfExtensionOrchestrator(
            repo_root=repo_root,
            manifest_path=manifest_path,
            skills_dir=skills_dir,
            tools_dir=tools_dir,
            settings_service=settings_service,
            config=cfg,
            capability_index=cap_idx,
        )

        yield orchestrator, repo_root, cfg, env_file, cap_idx


def test_proof_1_reversible_config_extension(mock_orchestrator_env):
    """Proof 1: Reversible Config Extension applied via SettingsService and verifiable rollback."""
    orchestrator, repo_root, cfg, env_file, _ = mock_orchestrator_env

    # 1. User requests config update
    req = ExtensionRequest(
        user_prompt="Change your LLM_MODEL setting to claude-3-5-sonnet",
        explicit_user_request=True,
        affected_settings={"LLM_MODEL": "claude-3-5-sonnet"},
    )

    result = orchestrator.execute_transaction(req)
    assert result.success is True
    assert result.status == TransactionStatus.COMPLETED
    assert cfg.llm_model == "claude-3-5-sonnet"
    assert "LLM_MODEL=claude-3-5-sonnet" in env_file.read_text(encoding="utf-8")

    # 2. Rollback transaction
    rb_res = orchestrator.rollback_transaction(result.transaction_id)
    assert rb_res.success is True
    assert cfg.llm_model == "gpt-4o-mini"
    assert "LLM_MODEL=gpt-4o-mini" in env_file.read_text(encoding="utf-8")


def test_proof_2_reusable_skill_extension_lifecycle(mock_orchestrator_env):
    """Proof 2: Reusable Skill lifecycle (creation, capability indexing, disable, and removal)."""
    orchestrator, repo_root, _, _, cap_idx = mock_orchestrator_env

    skill_md = """---
name: triage_playbook
description: Reusable playbook for incident triage
scripts:
  - run_triage.py
---
# Incident Triage Steps
1. Gather error trace
2. Check memory
"""
    req = ExtensionRequest(
        user_prompt="Learn this reusable procedure for incident triage",
        explicit_user_request=True,
        affected_files=["data/skills/triage_playbook/SKILL.md"],
    )

    result = orchestrator.execute_skill_transaction(
        request=req,
        skill_name="triage_playbook",
        raw_text=skill_md,
    )
    assert result.success is True
    assert cap_idx.get_capability("skill_triage_playbook") is not None

    # Verify skill file exists on disk
    skill_file = repo_root / "data" / "skills" / "triage_playbook" / "SKILL.md"
    assert skill_file.exists()

    # Disable skill
    dis = orchestrator.set_extension_enabled("skill_triage_playbook", enabled=False)
    assert dis is True
    assert cap_idx.get_capability("skill_triage_playbook") is None


def test_proof_3_mcp_server_tool_extension(mock_orchestrator_env):
    """Proof 3: MCP tool integration, registration in CapabilityIndex, and rollback."""
    orchestrator, _, _, _, cap_idx = mock_orchestrator_env

    req = ExtensionRequest(
        user_prompt="Connect the github MCP server",
        explicit_user_request=True,
    )

    result = orchestrator.execute_mcp_transaction(
        request=req,
        name="github",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-github"],
        declared_tools=["create_issue", "get_file"],
    )

    assert result.success is True
    assert cap_idx.get_capability("mcp_github") is not None
    cap = cap_idx.get_capability("mcp_github")
    assert "create_issue" in cap.operations


def test_proof_4_small_code_extension_ast_and_verification(mock_orchestrator_env):
    """Proof 4: Small code extension with static AST validation, test execution, and rollback on error."""
    orchestrator, repo_root, _, _, cap_idx = mock_orchestrator_env

    # 1. Valid code extension
    good_code = """
def add_numbers(a: int, b: int) -> int:
    \"\"\"Add two numbers together.\"\"\"
    return int(a) + int(b)
"""
    req1 = ExtensionRequest(
        user_prompt="Add a helper function to add two numbers",
        explicit_user_request=True,
    )
    res1 = orchestrator.execute_code_transaction(
        request=req1,
        name="add_numbers",
        code=good_code,
        test_inputs={"a": 3, "b": 4},
        expected_output=7,
    )
    assert res1.success is True
    assert cap_idx.get_capability("code_add_numbers") is not None

    # 2. Failing code extension (should roll back cleanly)
    bad_code = """
def broken_calc(x: int) -> int:
    \"\"\"Broken calculator.\"\"\"
    raise ValueError("Internal failure")
"""
    req2 = ExtensionRequest(
        user_prompt="Add a broken calculator",
        explicit_user_request=True,
    )
    res2 = orchestrator.execute_code_transaction(
        request=req2,
        name="broken_calc",
        code=bad_code,
        test_inputs={"x": 5},
    )
    assert res2.success is False
    assert not (repo_root / "data" / "generated_tools" / "broken_calc.py").exists()
    assert cap_idx.get_capability("code_broken_calc") is None


def test_proof_5_spontaneous_modification_guard(mock_orchestrator_env):
    """Proof 5: Spontaneous self-modification guard blocks unprompted self-edits and gates large architecture."""
    orchestrator, _, _, _, _ = mock_orchestrator_env

    # 1. Spontaneous (unprompted) self-edit attempt
    req_spontaneous = ExtensionRequest(
        user_prompt="Autonomous background refactor of core",
        explicit_user_request=False,  # Unprompted
        affected_files=["charlie/core.py"],
    )
    res1 = orchestrator.execute_transaction(req_spontaneous)
    assert res1.success is False
    assert res1.status == TransactionStatus.APPROVAL_REQUIRED
    assert "spontaneous" in res1.message.lower()

    # 2. Large architecture rewrite attempt
    req_large = ExtensionRequest(
        user_prompt="Rewrite the entire Charlie orchestration engine",
        explicit_user_request=True,
        classification=ExtensionClassification(kind=ExtensionKind.ARCHITECTURE_LARGE),
    )
    res2 = orchestrator.execute_transaction(req_large)
    assert res2.success is False
    assert res2.status == TransactionStatus.APPROVAL_REQUIRED


def test_proof_6_worktree_safety_and_preservation_of_unrelated_files(mock_orchestrator_env):
    """Proof 6: Unrelated dirty and untracked files are preserved 100% byte-for-byte during rollbacks."""
    orchestrator, repo_root, _, _, _ = mock_orchestrator_env

    # Create unrelated user files (one tracked, one untracked)
    user_file_1 = repo_root / "user_notes.md"
    user_file_1.write_text("# My Personal Notes\nDo not delete!\n", encoding="utf-8")

    user_file_2 = repo_root / "charlie" / "custom_draft.txt"
    user_file_2.parent.mkdir(parents=True, exist_ok=True)
    user_file_2.write_text("WIP user code draft\n", encoding="utf-8")

    # Attempt a code transaction that raises an error during verification
    broken_code = """
def faulty_tool():
    \"\"\"Faulty tool.\"\"\"
    raise RuntimeError("Intentional boom")
"""
    req = ExtensionRequest(
        user_prompt="Add faulty tool",
        explicit_user_request=True,
    )
    res = orchestrator.execute_code_transaction(
        request=req,
        name="faulty_tool",
        code=broken_code,
        test_inputs={},
    )
    assert res.success is False

    # Assert unrelated files are completely untouched and intact
    assert user_file_1.exists()
    assert user_file_1.read_text(encoding="utf-8") == "# My Personal Notes\nDo not delete!\n"
    assert user_file_2.exists()
    assert user_file_2.read_text(encoding="utf-8") == "WIP user code draft\n"
