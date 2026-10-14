"""Tests for _TEXT_TOOL_INSTRUCTIONS gating: it must not be injected once
native tools are on (it used to be unconditional, creating two competing
tool-instruction channels for native-tool-capable models).

Note: an earlier version of this fix also tried to let an explicit
native_tool_calling config value override the localhost-URL text-mode
heuristic in Brain.__init__, gated on whether NATIVE_TOOL_CALLING was
present in os.environ. Reverted -- .env.example ships that same key with
the same default value, so "key present in environ" can't distinguish a
deliberate override from a copied default, and the naive version broke
local-model text-mode fallback on any machine whose .env was set up the
normal way (copied from .env.example). A real fix needs the settings layer
to track which fields were user-modified vs default, not something this
lives on the Config field itself.
"""

from charlie.prompt_builder import _TEXT_TOOL_INSTRUCTIONS
from charlie.prompt_builder import build_stable_tier as _build_stable_tier


class TestTextToolInstructionsGating:
    def test_included_in_text_mode(self):
        stable = _build_stable_tier("soul", use_native_tools=False)
        assert _TEXT_TOOL_INSTRUCTIONS in stable

    def test_excluded_in_native_mode(self):
        stable = _build_stable_tier("soul", use_native_tools=True)
        assert _TEXT_TOOL_INSTRUCTIONS not in stable

    def test_native_payload_keeps_live_tool_schemas_without_prompt_roster(self):
        from charlie.config import Config
        from charlie.core import Brain
        from charlie import tools as _registered_tools  # noqa: F401

        brain = Brain.__new__(Brain)
        brain.config = Config()
        brain.config.vision_enabled = False
        brain._use_native_tools = True
        brain._pending_vision_image_url = None
        payload = brain._build_payload([{"role": "user", "content": "Explain recursion."}])

        names = {item["function"]["name"] for item in payload["tools"]}
        assert "weather_get" in names
        assert "YOUR ACTUAL CAPABILITIES" not in _build_stable_tier("soul", use_native_tools=True)
