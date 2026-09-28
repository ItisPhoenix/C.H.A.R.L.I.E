"""Tests for Persistent Extension Registry and Reusable Skill Lifecycle."""

import json
import tempfile
from pathlib import Path

import pytest

from charlie.capabilities import CapabilityIndex
from charlie.self_extension.adapters.skill_adapter import SkillAdapter
from charlie.self_extension.models import (
    ExtensionClassification,
    ExtensionKind,
    ExtensionPlan,
    ExtensionRequest,
    TransactionStatus,
)
from charlie.self_extension.registry import ExtensionEntry, ExtensionRegistry


def _candidate_skill(name: str, instruction: str, scripts: str = "") -> str:
    return (
        f"---\nname: {name}\ndescription: Test candidate\n{scripts}---\n\n"
        f"# {name}\n\n{instruction}\n"
    )


def test_skill_candidate_instructions_exclude_staging_commands():
    from charlie.self_extension.orchestrator import SelfExtensionOrchestrator

    prompt = (
        "Create an inactive, instructions-only skill named receipt_evidence. Purpose: "
        "In future acceptance reports, clearly distinguish between Telegram API acceptance "
        "and owner-confirmed receipt. The skill should provide guidance on reporting each "
        "status separately. This skill is for review only — do not activate it."
    )

    instructions = SelfExtensionOrchestrator._skill_instruction_text(prompt)

    assert instructions.startswith("In future acceptance reports")
    assert "Telegram API acceptance" in instructions
    assert "owner-confirmed receipt" in instructions
    assert "Create an inactive" not in instructions
    assert "for review only" not in instructions
    assert "do not activate" not in instructions


def test_pending_candidate_review_token_survives_registry_restart(tmp_path):
    skills_dir = tmp_path / "skills"
    manifest = tmp_path / "extensions.json"
    registry = ExtensionRegistry(manifest_path=manifest)
    adapter = SkillAdapter(skills_dir=skills_dir, registry=registry)
    content = _candidate_skill("restart_candidate", "Separate submitted and verified states.")
    staged = adapter.stage_skill_candidate("restart_candidate", content)
    old_token = adapter.list_skill_candidates()[0]["review_token"]
    adapter.mark_skill_candidate_review_submitted("restart_candidate", staged.content_hash)

    document = json.loads(manifest.read_text(encoding="utf-8"))
    extension_id = f"skill_candidate_restart_candidate_{staged.content_hash}"
    document[extension_id]["metadata"]["review_token"] = "[REDACTED]"
    manifest.write_text(json.dumps(document), encoding="utf-8")

    reloaded_registry = ExtensionRegistry(manifest_path=manifest)
    reloaded_adapter = SkillAdapter(skills_dir=skills_dir, registry=reloaded_registry)
    candidate = reloaded_adapter.list_skill_candidates()[0]

    assert candidate["status"] == "pending"
    assert candidate["enabled"] is False
    assert candidate["matches_hash"] is True
    assert candidate["review_submitted"] is False
    assert len(candidate["review_token"]) == 16
    assert candidate["review_token"] != old_token

    persisted = ExtensionRegistry(manifest_path=manifest).get(extension_id)
    assert persisted.metadata["review_token"] == candidate["review_token"]


def test_skill_candidate_rejects_sensitive_content(tmp_path):
    registry = ExtensionRegistry(manifest_path=tmp_path / "extensions.json")
    adapter = SkillAdapter(skills_dir=tmp_path / "skills", registry=registry)
    candidate = _candidate_skill("sensitive_candidate", "Never store the password is hunter2.")

    result = adapter.stage_skill_candidate("sensitive_candidate", candidate)

    assert result.success is False
    assert "cannot contain credentials" in result.message
    assert adapter.list_skill_candidates() == []
    assert not (tmp_path / "skills" / ".candidates").exists()


def test_durable_direct_correction_stages_once_and_stays_inactive(tmp_path):
    from charlie.self_extension.guard import AuthorizationGuard
    from charlie.self_extension.orchestrator import SelfExtensionOrchestrator

    registry = ExtensionRegistry(manifest_path=tmp_path / "extensions.json")
    adapter = SkillAdapter(skills_dir=tmp_path / "skills", registry=registry)
    orchestrator = SelfExtensionOrchestrator.__new__(SelfExtensionOrchestrator)
    orchestrator._transactions = {}
    orchestrator._event_bus = None
    orchestrator._guard = AuthorizationGuard()
    orchestrator._registry = registry
    orchestrator._skill_adapter = adapter
    correction = (
        "Actually, for future acceptance reports, distinguish Telegram API acceptance "
        "from owner-confirmed receipt."
    )

    result = orchestrator.stage_reusable_correction_candidate(correction)

    assert result is not None and result.success is True
    assert result.status is TransactionStatus.PENDING_REVIEW
    assert result.details["activated"] is False
    candidate = adapter.list_skill_candidates()[0]
    assert candidate["status"] == "pending"
    assert candidate["enabled"] is False
    assert "owner-confirmed receipt" in candidate["content"]
    assert "Actually" not in candidate["content"]

    assert orchestrator.stage_reusable_correction_candidate(correction) is None
    assert len(adapter.list_skill_candidates()) == 1


def test_direct_correction_candidate_skips_one_off_and_sensitive_content(tmp_path):
    from charlie.self_extension.guard import AuthorizationGuard
    from charlie.self_extension.orchestrator import SelfExtensionOrchestrator

    registry = ExtensionRegistry(manifest_path=tmp_path / "extensions.json")
    adapter = SkillAdapter(skills_dir=tmp_path / "skills", registry=registry)
    orchestrator = SelfExtensionOrchestrator.__new__(SelfExtensionOrchestrator)
    orchestrator._transactions = {}
    orchestrator._event_bus = None
    orchestrator._guard = AuthorizationGuard()
    orchestrator._registry = registry
    orchestrator._skill_adapter = adapter

    assert orchestrator.stage_reusable_correction_candidate("Actually, I meant Python.") is None
    assert orchestrator.stage_reusable_correction_candidate(
        "Actually, for future reports, include my password is hunter2."
    ) is None
    assert adapter.list_skill_candidates() == []


def test_repeated_success_audit_metadata_requires_safe_telegram_verification():
    from dataclasses import replace

    from charlie.self_extension.orchestrator import repeated_success_audit_metadata
    from charlie.turn_contracts import ResultEnvelope

    envelope = ResultEnvelope(
        request="Open the official Python documentation page and report its title.",
        turn_id="turn-1",
        capability="browser",
        operation=None,
        status="completed",
        verification_status="verified_success",
        risk_class="safe",
    )
    metadata = repeated_success_audit_metadata(envelope, "telegram", "open_url")

    assert metadata["turn_id"] == "turn-1"
    assert metadata["verification_status"] == "verified_success"
    assert metadata["risk_class"] == "safe"
    assert metadata["requires_approval"] is False
    assert len(metadata["repeated_success_signature"]) == 64
    assert repeated_success_audit_metadata(envelope, "voice", "open_url") == {}
    assert repeated_success_audit_metadata(
        replace(envelope, verification_status="executed_unverified"), "telegram", "open_url"
    ) == {}
    assert repeated_success_audit_metadata(
        replace(envelope, risk_class="reversible"), "telegram", "open_url"
    ) == {}
    assert repeated_success_audit_metadata(
        replace(envelope, requires_approval=True), "telegram", "open_url"
    ) == {}
    assert repeated_success_audit_metadata(
        replace(envelope, data={"persistence_status": "failed"}), "telegram", "open_url"
    ) == {}


def test_repeated_success_requires_two_distinct_verified_turns_and_stages_inactive(tmp_path):
    from charlie.self_extension.orchestrator import (
        SelfExtensionOrchestrator,
        repeated_success_signature,
        verified_success_turn_ids,
    )

    request = "Charlie, open https://docs.python.org/3/ and tell me the page title."
    signature = repeated_success_signature(request, "browser", "open_url")
    assert signature == repeated_success_signature(
        "open https://docs.python.org/3/ and tell me the page title", "browser", "OPEN_URL"
    )
    assert signature is not None
    previous = {
        "turn_id": "turn-1",
        "verification_status": "verified_success",
        "risk_class": "safe",
        "requires_approval": False,
        "repeated_success_signature": signature,
    }
    current = {**previous, "turn_id": "turn-2"}
    audit_entries = [
        {"arguments": json.dumps(previous), "outcome": "completed"},
        {"arguments": json.dumps(current), "outcome": "completed"},
        {"arguments": json.dumps(current), "outcome": "completed"},
        {
            "arguments": json.dumps({**previous, "turn_id": "turn-3", "verification_status": "executed_unverified"}),
            "outcome": "completed",
        },
    ]
    assert verified_success_turn_ids(audit_entries, signature) == {"turn-1", "turn-2"}

    registry = ExtensionRegistry(manifest_path=tmp_path / "extensions.json")
    adapter = SkillAdapter(skills_dir=tmp_path / "skills", registry=registry)
    orchestrator = SelfExtensionOrchestrator.__new__(SelfExtensionOrchestrator)
    orchestrator._transactions = {}
    orchestrator._event_bus = None
    orchestrator._registry = registry
    orchestrator._skill_adapter = adapter

    result = orchestrator.stage_repeated_success_candidate(
        request,
        "browser",
        "open_url",
        audit_entries,
        current_turn_id="turn-2",
        expected_signature=signature,
        risk_class="safe",
        requires_approval=False,
    )

    assert result is not None and result.success is True
    assert result.details["trigger"] == "two_distinct_verified_successes"
    candidate = adapter.list_skill_candidates()[0]
    assert candidate["status"] == "pending"
    assert candidate["enabled"] is False
    assert candidate["matches_hash"] is True
    assert "browser.open_url" in candidate["content"]
    assert "ResultEnvelope" in candidate["content"]
    assert orchestrator.stage_repeated_success_candidate(
        request,
        "browser",
        "open_url",
        audit_entries,
        current_turn_id="turn-2",
        expected_signature=signature,
        risk_class="safe",
        requires_approval=False,
    ) is None


def test_repeated_success_ignores_same_turn_unverified_or_sensitive_runs(tmp_path):
    from charlie.self_extension.orchestrator import SelfExtensionOrchestrator, repeated_success_signature

    request = "Open the official Python documentation page and report its title."
    signature = repeated_success_signature(request, "browser", "open_url")
    assert signature is not None
    one_turn_twice = {
        "turn_id": "turn-1",
        "verification_status": "verified_success",
        "risk_class": "safe",
        "requires_approval": False,
        "repeated_success_signature": signature,
    }
    unverified = {**one_turn_twice, "turn_id": "turn-2", "verification_status": "executed_unverified"}
    entries = [
        {"arguments": json.dumps(one_turn_twice), "outcome": "completed"},
        {"arguments": json.dumps(one_turn_twice), "outcome": "completed"},
        {"arguments": json.dumps(unverified), "outcome": "completed"},
    ]
    registry = ExtensionRegistry(manifest_path=tmp_path / "extensions.json")
    adapter = SkillAdapter(skills_dir=tmp_path / "skills", registry=registry)
    orchestrator = SelfExtensionOrchestrator.__new__(SelfExtensionOrchestrator)
    orchestrator._transactions = {}
    orchestrator._event_bus = None
    orchestrator._registry = registry
    orchestrator._skill_adapter = adapter

    assert orchestrator.stage_repeated_success_candidate(
        request,
        "browser",
        "open_url",
        entries,
        current_turn_id="turn-1",
        expected_signature=signature,
        risk_class="safe",
        requires_approval=False,
    ) is None
    assert orchestrator.stage_repeated_success_candidate(
        "Open this and save my password as hunter2.",
        "browser",
        "open_url",
        entries,
        current_turn_id="turn-1",
        expected_signature=signature,
        risk_class="safe",
        requires_approval=False,
    ) is None
    assert adapter.list_skill_candidates() == []


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


def test_skill_candidate_hash_mismatch_and_scripts_are_rejected(mock_registry_env):
    registry, adapter, cap_idx, _, _, skills_dir = mock_registry_env
    content = _candidate_skill("answer_style", "Use concise answers.")

    staged = adapter.stage_skill_candidate("answer_style", content)
    assert staged.success
    candidate = adapter.list_skill_candidates()[0]
    assert candidate["enabled"] is False
    assert candidate["matches_hash"] is True
    assert cap_idx.get_capability("skill_answer_style") is None

    mismatch = adapter.approve_skill_candidate("answer_style", "0" * 64)
    assert mismatch.success is False
    assert adapter.reject_skill_candidate("answer_style", "0" * 64).success is False
    assert cap_idx.get_capability("skill_answer_style") is None
    assert not (skills_dir / "answer_style" / "SKILL.md").exists()

    with_scripts = _candidate_skill("unsafe_candidate", "Read these instructions.", "scripts:\n  - run.py\n")
    refused = adapter.stage_skill_candidate("unsafe_candidate", with_scripts)
    assert refused.success is False
    assert registry.get("skill_candidate_unsafe_candidate_" + "0" * 64) is None


def test_pending_skill_candidate_survives_restart_without_activation(mock_registry_env):
    registry, adapter, cap_idx, _, manifest_path, skills_dir = mock_registry_env
    content = _candidate_skill("restart_candidate", "Keep the user informed.")
    assert adapter.stage_skill_candidate("restart_candidate", content).success

    restarted_registry = ExtensionRegistry(manifest_path=manifest_path, capability_index=cap_idx)
    restarted_adapter = SkillAdapter(skills_dir=skills_dir, registry=restarted_registry, capability_index=cap_idx)
    candidate = restarted_adapter.list_skill_candidates()[0]
    report = restarted_registry.rehydrate(cap_idx)

    assert candidate["status"] == "pending"
    assert candidate["content"] == content
    assert candidate["matches_hash"] is True
    assert report.skipped_disabled == 1
    assert cap_idx.get_capability("skill_restart_candidate") is None


def test_skill_candidate_rejection_stays_inactive(mock_registry_env):
    _, adapter, cap_idx, _, _, _ = mock_registry_env
    content = _candidate_skill("rejected_candidate", "Do not activate me.")
    adapter.stage_skill_candidate("rejected_candidate", content)
    digest = adapter.list_skill_candidates()[0]["content_hash"]

    rejected = adapter.reject_skill_candidate("rejected_candidate", digest)

    assert rejected.success
    candidate = adapter.list_skill_candidates()[0]
    assert candidate["status"] == "rejected"
    assert candidate["enabled"] is False
    assert cap_idx.get_capability("skill_rejected_candidate") is None


def test_approved_skill_candidate_activates_and_disable_restores_previous_version(mock_registry_env):
    registry, adapter, cap_idx, _, _, skills_dir = mock_registry_env
    previous = _candidate_skill("review_helper", "Use the original review checklist.")
    candidate_text = _candidate_skill("review_helper", "Use the improved review checklist.").replace(
        "\n", "\r\n"
    )
    assert adapter.save_skill("review_helper", previous).success
    staged = adapter.stage_skill_candidate("review_helper", candidate_text)
    digest = adapter.list_skill_candidates()[0]["content_hash"]
    assert staged.success
    assert cap_idx.get_capability("skill_review_helper") is not None
    assert (skills_dir / "review_helper" / "SKILL.md").read_bytes() == previous.encode("utf-8")

    approved = adapter.approve_skill_candidate("review_helper", digest)
    assert approved.success
    assert (skills_dir / "review_helper" / "SKILL.md").read_bytes() == candidate_text.encode("utf-8")
    assert registry.get("skill_review_helper").enabled is True
    assert cap_idx.get_capability("skill_review_helper") is not None

    disabled = adapter.disable_skill_candidate("review_helper", digest)
    assert disabled.success
    assert (skills_dir / "review_helper" / "SKILL.md").read_bytes() == previous.encode("utf-8")
    assert registry.get("skill_review_helper").enabled is True
    assert cap_idx.get_capability("skill_review_helper") is not None
    assert adapter.list_skill_candidates()[0]["status"] == "disabled"


def test_explicit_skill_extension_request_stages_pending_candidate_without_activation(mock_registry_env):
    from types import SimpleNamespace

    from charlie.self_extension.orchestrator import SelfExtensionOrchestrator

    registry, adapter, cap_idx, _, _, skills_dir = mock_registry_env
    orchestrator = SelfExtensionOrchestrator.__new__(SelfExtensionOrchestrator)
    orchestrator._transactions = {}
    orchestrator._emit = lambda *_args, **_kwargs: None
    orchestrator._guard = SimpleNamespace(
        evaluate=lambda _request: SimpleNamespace(
            requires_approval=False, is_authorized=True, reason="explicit request"
        )
    )
    orchestrator._skill_adapter = adapter
    raw_text = _candidate_skill("explicit_recipe", "Use a short ordered checklist.")
    request = ExtensionRequest(
        user_prompt="Create this reusable skill",
        explicit_user_request=True,
        classification=ExtensionClassification(kind=ExtensionKind.SKILL),
        plan=ExtensionPlan(
            plan_id="plan-test",
            kind=ExtensionKind.SKILL,
            description="Stage explicit skill request",
            raw_text=raw_text,
        ),
        affected_capabilities=["skill_explicit_recipe"],
    )

    result = orchestrator.execute_transaction(request)

    assert result.success is True
    assert result.status is TransactionStatus.PENDING_REVIEW
    assert result.details["review_status"] == "pending"
    assert result.details["activated"] is False
    assert result.details["verified_as_installed"] is False
    candidate = adapter.list_skill_candidates()[0]
    assert candidate["name"] == "explicit_recipe"
    assert candidate["content_hash"] == result.details["content_hash"]
    assert candidate["enabled"] is False
    assert registry.get("skill_explicit_recipe") is None
    assert cap_idx.get_capability("skill_explicit_recipe") is None
    assert not (skills_dir / "explicit_recipe" / "SKILL.md").exists()

    implicit_request = ExtensionRequest(
        user_prompt="An inferred reusable procedure",
        explicit_user_request=False,
        classification=ExtensionClassification(kind=ExtensionKind.SKILL),
        plan=ExtensionPlan(
            plan_id="plan-implicit",
            kind=ExtensionKind.SKILL,
            description="Implicit skill suggestion",
            raw_text=_candidate_skill("implicit_recipe", "This must not be staged."),
        ),
        affected_capabilities=["skill_implicit_recipe"],
    )
    implicit = orchestrator.execute_transaction(implicit_request)
    assert implicit.success is False
    assert implicit.status is TransactionStatus.APPROVAL_REQUIRED
    assert all(item["name"] != "implicit_recipe" for item in adapter.list_skill_candidates())
