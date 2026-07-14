"""Tests for the gated-tool approve/decline flow (charlie.core.Brain.request_tool_approval).

Covers the voice fallback path (no web dashboard connected) since the web
path (WebSocket "tool_approval_request" -> "tool_approve"/"tool_reject")
mirrors the already-tested charlie.recovery proposal flow (see
tests/test_ws_recovery.py) almost exactly.
"""

import asyncio

import pytest

from charlie import recovery
from charlie.config import Config
from charlie.core import (
    Brain,
    get_active_voice_approval,
    pending_tool_approvals,
    resolve_tool_approval,
)


@pytest.fixture
def brain_config():
    return Config(
        small_llm_url="http://localhost:11434",
        small_llm_key="no-key",
        small_llm_model="dummy",
        iteration_budget_max=3,
    )


@pytest.fixture(autouse=True)
def _no_active_ws(monkeypatch):
    """Deterministic no-dashboard-connected state regardless of test order."""
    monkeypatch.setattr(recovery, "_active_ws_count", 0)
    monkeypatch.setattr(recovery, "_event_bus", None)
    yield
    pending_tool_approvals.clear()


def test_resolve_tool_approval_unknown_id_returns_false():
    assert resolve_tool_approval("not-a-real-id", True) is False


@pytest.mark.asyncio
async def test_request_tool_approval_declines_safely_with_no_channel(brain_config):
    """No web dashboard and no on_thought_callback (voice) wired -- there's
    no way to ask, so the gated call must fail safe (declined), not hang or
    silently proceed."""
    brain = Brain(brain_config)
    approved = await brain.request_tool_approval(
        "shell_execute", {"command": "rm -rf foo"}, "risky keyword 'rm -rf'"
    )
    assert approved is False


@pytest.mark.asyncio
async def test_request_tool_approval_voice_fallback_approved(brain_config):
    """No dashboard connected -- falls back to speaking the prompt via
    on_thought_callback and exposes the request id via
    get_active_voice_approval() for main.py's speech handler to resolve."""
    spoken = []
    brain = Brain(brain_config, on_thought_callback=spoken.append)

    async def approve_shortly():
        # Let request_tool_approval register the pending future first.
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
    # Resolved -- must not still be flagged as pending.
    assert get_active_voice_approval() is None


@pytest.mark.asyncio
async def test_request_tool_approval_voice_fallback_declined(brain_config):
    brain = Brain(brain_config, on_thought_callback=lambda text: None)

    async def decline_shortly():
        await asyncio.sleep(0.05)
        request_id = get_active_voice_approval()
        resolve_tool_approval(request_id, False)

    decline_task = asyncio.create_task(decline_shortly())
    approved = await brain.request_tool_approval(
        "shell_execute", {"command": "taskkill /IM notepad.exe /F"}, "risky keyword 'taskkill'"
    )
    await decline_task

    assert approved is False
