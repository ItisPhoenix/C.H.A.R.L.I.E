"""Tests for registry-derived presentation awareness in Brain's stable prompt."""

import asyncio
from copy import deepcopy
from datetime import datetime

from charlie.config import Config
from charlie.core import Brain
from charlie.presentation_registry import PresentationRegistry, get_presentation_registry
from charlie.prompt_builder import (
    _TEXT_TOOL_INSTRUCTIONS,
    assemble_system_prompt,
    build_stable_tier,
    build_volatile_tier,
)


def test_model_awareness_block_is_compact_deterministic_and_secret_free():
    registry = get_presentation_registry()

    block = registry.build_model_awareness_block()

    assert block == registry.build_model_awareness_block()
    assert len(block) <= 1200
    for name in registry.list_workspaces() + registry.list_widgets() + registry.list_overlays():
        assert name in block
    for primitive in registry.list_surface_primitives():
        assert primitive in block
    for action in registry.list_actions():
        assert action not in block
    assert registry.list_actions()
    assert registry.to_dict()["actions"]
    assert "Presentation lifecycle and layout are system-managed." in block
    assert "center" in block
    assert "dock_bottom_right" in block
    assert "frontend/" not in block
    assert ".tsx" not in block
    assert "ResearchWorkspace" not in block
    assert "SettingsModal" not in block
    assert "api_key" not in block.lower()
    assert "session_id" not in block.lower()
    assert "timestamp" not in block.lower()


def test_model_awareness_tracks_added_and_removed_registry_workspaces():
    contract = deepcopy(get_presentation_registry().to_dict())
    contract["workspaces"]["future_workspace"] = {
        "aliases": [],
        "implemented": True,
        "renderer": "FutureRenderer",
        "renderer_module": "frontend/src/workspaces/Future.tsx",
        "spatial": False,
        "core_position": "dock_bottom_right",
        "dismiss_policy": "persistent",
        "description": "Future workspace",
    }
    added_registry = PresentationRegistry.from_dict(contract)
    added_block = added_registry.build_model_awareness_block()

    assert "future_workspace" in added_block
    assert "FutureRenderer" not in added_block
    assert "Future.tsx" not in added_block

    del contract["workspaces"]["research"]
    removed_block = PresentationRegistry.from_dict(contract).build_model_awareness_block()
    assert "research" not in removed_block


def test_stable_tier_includes_presentation_block_and_preserves_tool_modes():
    block = get_presentation_registry().build_model_awareness_block()
    capability_block = "[CAPABILITY ROSTER]\nMemory: memory_add"

    text_stable = build_stable_tier(
        "soul",
        capability_block,
        use_native_tools=False,
        presentation_block=block,
    )
    native_stable = build_stable_tier(
        "soul",
        capability_block,
        use_native_tools=True,
        presentation_block=block,
    )

    assert block in text_stable
    assert block in native_stable
    assert capability_block in text_stable
    assert capability_block in native_stable
    assert _TEXT_TOOL_INSTRUCTIONS in text_stable
    assert _TEXT_TOOL_INSTRUCTIONS not in native_stable


def test_actual_brain_stable_prompt_receives_registry_awareness(tmp_path):
    config = Config()
    config.llm_url = "https://example.invalid/v1"
    config.llm_key = "test-key"
    config.soul = "You are a test Charlie."
    config.memory_file = str(tmp_path / "MEMORY.md")
    config.user_file = str(tmp_path / "USER.md")
    config.opinions_file = str(tmp_path / "OPINIONS.md")
    config.native_tool_calling = True

    brain = Brain(config, register_panic_hotkey=False)
    try:
        block = get_presentation_registry().build_model_awareness_block()
        assert block in brain._stable_tier
        system_prompt = assemble_system_prompt(
            brain._stable_tier,
            brain._context_tier,
            build_volatile_tier("web", datetime(2026, 1, 1), 10),
        )
        assert block in system_prompt
        assert all(
            token in system_prompt
            for token in ("system_metric", "map", "briefing", "research", "tasks", "chart")
        )
        assert "show CPU usage" not in system_prompt
        assert "open map" not in system_prompt
    finally:
        async def close_clients():
            await brain.client.aclose()
            if brain._vision_client is not None:
                await brain._vision_client.aclose()

        asyncio.run(close_clients())
