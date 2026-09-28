"""Tests for owner-channel tool approval and fail-closed behavior."""

import asyncio

import pytest

from charlie import core as core_module
from charlie.config import Config
from charlie.core import (
    ApprovalDecision,
    Brain,
    get_active_voice_approval,
    pending_tool_approvals,
    resolve_tool_approval,
)


@pytest.fixture
def brain_config():
    return Config(llm_url="http://localhost:11434/v1", llm_key="no-key", llm_model="dummy")


def test_resolve_tool_approval_unknown_id_returns_false():
    assert resolve_tool_approval("not-a-real-id", True) is False


@pytest.mark.asyncio
async def test_request_tool_approval_declines_safely_with_no_channel(brain_config):
    brain = Brain(brain_config)
    approved = await brain.request_tool_approval(
        "shell_execute", {"command": "rm -rf foo"}, "risky keyword 'rm -rf'"
    )
    assert approved is False
    await brain.close()


@pytest.mark.asyncio
async def test_request_tool_approval_voice_fallback_approved(brain_config):
    spoken = []
    brain = Brain(brain_config, on_thought_callback=spoken.append)

    async def approve_shortly():
        await asyncio.sleep(0.05)
        request_id = get_active_voice_approval()
        assert request_id is not None
        assert resolve_tool_approval(request_id, True) is True

    approve_task = asyncio.create_task(approve_shortly())
    approved = await brain.request_tool_approval(
        "file_write", {"path": ".env"}, "sensitive path '.env'"
    )
    await approve_task

    assert approved is True
    assert spoken and ".env" in spoken[0]
    assert get_active_voice_approval() is None
    assert pending_tool_approvals == {}
    await brain.close()


@pytest.mark.asyncio
async def test_request_tool_approval_voice_fallback_declined(brain_config):
    brain = Brain(brain_config, on_thought_callback=lambda _text: None)

    async def decline_shortly():
        await asyncio.sleep(0.05)
        request_id = get_active_voice_approval()
        assert request_id is not None
        assert resolve_tool_approval(request_id, False) is True

    decline_task = asyncio.create_task(decline_shortly())
    approved = await brain.request_tool_approval(
        "shell_execute", {"command": "taskkill /IM notepad.exe /F"}, "risky keyword 'taskkill'"
    )
    await decline_task

    assert approved is False
    assert get_active_voice_approval() is None
    await brain.close()


@pytest.mark.asyncio
async def test_telegram_approval_works_without_voice_and_uses_telegram_timeout(brain_config, monkeypatch):
    monkeypatch.setattr(core_module, "_TELEGRAM_TOOL_APPROVAL_TIMEOUT_SEC", 0.02, raising=False)
    brain = Brain(
        brain_config,
        on_tool_approval_request=lambda *_args, **_kwargs: True,
        approval_timeout=core_module._TOOL_APPROVAL_TIMEOUT_SEC,
    )
    try:
        decision = await asyncio.wait_for(
            brain._request_tool_approval_decision(
                "shell_execute", {"command": "taskkill /?"}, "help command", platform="telegram"
            ),
            timeout=0.25,
        )
    finally:
        await brain.close()

    assert decision is ApprovalDecision.TIMED_OUT
    assert pending_tool_approvals == {}


@pytest.mark.asyncio
async def test_approval_callback_receives_redacted_shell_command_preview(brain_config):
    received = {}

    def approve(request_id, *_args, operation_preview=None, **_identity):
        received["preview"] = operation_preview
        assert resolve_tool_approval(request_id, True) is True
        return True

    brain = Brain(brain_config, on_tool_approval_request=approve)
    try:
        decision = await brain._request_tool_approval_decision(
            "shell_execute",
            {"command": 'python3 script.py --api-key="secret-value" --password hidden-value'},
            "arbitrary shell command requires explicit approval",
            platform="telegram",
        )
    finally:
        await brain.close()

    assert decision is ApprovalDecision.APPROVED
    assert received["preview"] == "python3 script.py --api-key=[REDACTED] --password [REDACTED]"
    assert "secret-value" not in received["preview"]
    assert "hidden-value" not in received["preview"]
