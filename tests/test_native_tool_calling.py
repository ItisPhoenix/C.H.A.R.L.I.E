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
