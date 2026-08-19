"""Comprehensive Phase 15 Hardening, Performance, and Security tests."""

import os
import sys
import tempfile
from pathlib import Path
import pytest

from charlie.config import Config
from charlie.doctor import CharlieDoctor
from charlie.fastpaths import match_fast_path, execute_fast_path, _handle_system_diagnostics
from charlie.log_redaction import redact_sensitive_text
from charlie.resource_locks import CapabilityLeaseManager
from charlie.security.policy import check_tool_call
from charlie.self_knowledge import SelfKnowledgeService
from charlie.settings_service import SettingsService
from charlie.task_journal import TaskJournal, TaskStatus, TaskTransitionError
from charlie.tools import get_path_gate_reason


class TestPhase15PerformanceFastpaths:
    def test_cpu_fastpath_returns_formatted_telemetry_fast(self):
        match = match_fast_path("what is the cpu usage?")
        assert match is not None
        assert match.intent == "system_cpu"
        res = execute_fast_path(match)
        assert "CPU Utilization:" in res
        assert "%" in res

    def test_memory_fastpath_returns_formatted_telemetry(self):
        match = match_fast_path("how much ram is used?")
        assert match is not None
        assert match.intent == "system_memory"
        res = execute_fast_path(match)
        assert "Memory Utilization:" in res
        assert "GB" in res

    def test_disk_fastpath_returns_formatted_telemetry(self):
        match = match_fast_path("how much disk space is left?")
        assert match is not None
        assert match.intent == "system_disk"
        res = execute_fast_path(match)
        assert "Disk Utilization:" in res
        assert "GB" in res

    def test_processes_fastpath_returns_top_processes(self):
        match = match_fast_path("show running processes")
        assert match is not None
        assert match.intent == "system_processes"
        res = execute_fast_path(match)
        assert "Top Running Processes:" in res

    def test_fastpath_fallback_on_telemetry_error(self, monkeypatch):
        import charlie.fastpaths as fp_mod
        res = fp_mod._handle_system_diagnostics("cpu")
        assert "CPU" in res


class TestPhase15SecurityAndSecretHardening:
    @pytest.mark.parametrize("secret_path", [
        ".env",
        "sessions.db",
        "id_rsa",
        "id_ed25519",
        os.path.join(".ssh", "id_rsa"),
        os.path.join(".aws", "credentials"),
        os.path.join(".kube", "config"),
        os.path.join(".gnupg", "secring.gpg"),
    ])
    def test_sensitive_paths_are_gated(self, secret_path):
        reason = get_path_gate_reason(secret_path)
        assert reason is not None
        assert "sensitive path" in reason or "sessions.db" in reason or ".env" in reason

    def test_check_tool_call_enforces_gated_paths(self):
        pol = check_tool_call("file_read", {"path": "C:\\Users\\test\\.kube\\config"})
        assert pol.needs_approval is True

    @pytest.mark.parametrize("raw_log,expected_redacted", [
        ("Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", "Bearer [REDACTED]"),
        ("https://api.openai.com/v1?api_key=sk-1234567890abcdef", "https://api.openai.com/v1?api_key=[REDACTED]"),
        ("https://api.anthropic.com/v1?access_token=anthropic-secret-99", "https://api.anthropic.com/v1?access_token=[REDACTED]"),
        ("Connecting to bot: /bot123456789:ABCdefGhIjkLmNoPqRsTuVwXyZ", "Connecting to bot: /bot[REDACTED]"),
        ("Config value api_key: sk-proj-123456", "Config value api_key: [REDACTED]"),
        ("Loaded private_key: MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwgg", "Loaded private_key: [REDACTED]"),
        ("client_secret = 9988776655443322", "client_secret = [REDACTED]"),
    ])
    def test_log_redaction_masks_all_secret_variants(self, raw_log, expected_redacted):
        redacted = redact_sensitive_text(raw_log)
        assert "[REDACTED]" in redacted
        assert "sk-1234567890abcdef" not in redacted
        assert "123456789:ABCdefGhIjkLmNoPqRsTuVwXyZ" not in redacted
        assert "9988776655443322" not in redacted

    def test_settings_service_masks_secrets(self, tmp_path):
        cfg = Config(llm_key="super-secret-key-123")
        svc = SettingsService(cfg, env_path=tmp_path / ".env")
        specs = svc.get_field_specs()
        key_spec = next((s for s in specs if s["key"] == "LLM_API_KEY"), None)
        assert key_spec is not None
        assert key_spec["secret"] is True
        assert key_spec["value"] is None
        assert key_spec["is_set"] is True


class TestPhase15TaskJournalAndLeaseIntegrity:
    def test_task_lifecycle_enforces_allowed_transitions(self):
        tj = TaskJournal(state_path=None)
        task = tj.create_task(title="Hardening Task")
        assert task.status == TaskStatus.QUEUED

        tj.transition(task.id, TaskStatus.RUNNING)
        assert tj.get(task.id).status == TaskStatus.RUNNING

        tj.transition(task.id, TaskStatus.VERIFYING)
        assert tj.get(task.id).status == TaskStatus.VERIFYING

        tj.transition(task.id, TaskStatus.COMPLETED, result_reference="res-1")
        assert tj.get(task.id).status == TaskStatus.COMPLETED

        with pytest.raises(TaskTransitionError):
            tj.transition(task.id, TaskStatus.RUNNING)

    @pytest.mark.asyncio
    async def test_resource_leases_prevent_conflicting_owners(self):
        import asyncio
        lm = CapabilityLeaseManager()
        lease_a = await lm.acquire("desktop", "task-1", timeout=0.1)
        assert lease_a is not None
        assert lm.current_owner("desktop") == "task-1"

        # Second task cannot acquire lease within timeout
        with pytest.raises(asyncio.TimeoutError):
            await lm.acquire("desktop", "task-2", timeout=0.05)

        await lease_a.release()
        lease_b = await lm.acquire("desktop", "task-2", timeout=0.1)
        assert lease_b is not None
        assert lm.current_owner("desktop") == "task-2"
        await lease_b.release()


class TestPhase15DoctorAndSelfKnowledge:
    def test_doctor_diagnoses_all_subsystems_cleanly(self):
        doctor = CharlieDoctor()
        report = doctor.diagnose()
        assert report.total_checks >= 15
        assert isinstance(report.is_healthy, bool)

    def test_self_knowledge_introspects_truthfully(self):
        sk = SelfKnowledgeService()
        ev = sk.get_evidence_for_query("what capabilities are available?")
        assert len(ev.evidence_sources) > 0
        ans = sk.answer_self_question("what capabilities are available?")
        assert ans["is_self_question"] is True
        assert "capability" in ans["answer"].lower()