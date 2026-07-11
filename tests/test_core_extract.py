"""Tests for _extract_tool_calls bare-pattern gating.

Native-tool providers must NOT match bare tool names in prose.
Text-mode (local models) must still match bare patterns.
"""

from unittest.mock import MagicMock

from charlie.core import Brain


def _make_brain(use_native_tools: bool) -> Brain:
    """Build a Brain stub with the desired tool-calling mode."""
    cfg = MagicMock()
    cfg.small_llm_url = "https://example.com/v1"
    cfg.small_llm_key = "test-key"
    cfg.small_llm_model = "test-model"
    cfg.soul = "You are a test assistant."
    cfg.memory_file = "/dev/null"
    cfg.user_file = "/dev/null"
    cfg.opinions_file = "/dev/null"
    cfg.prompt_memory_max = 2200
    cfg.big_llm_url = ""
    cfg.big_llm_key = "no-key"
    cfg.big_llm_model = ""
    cfg.native_tool_calling = use_native_tools
    cfg.llm_disable_reasoning = True
    cfg.iteration_budget_max = 12

    brain = Brain.__new__(Brain)
    brain.config = cfg
    brain._use_native_tools = use_native_tools
    brain.client = MagicMock()
    brain._chat_generation = 0
    brain._tool_locks = {}
    brain.history = []
    brain._history_max_turns = 5
    brain._turns_since_nudge = 0
    brain._stable_tier = ""
    brain._context_tier = ""
    brain._big_client = None
    brain.on_thought_callback = None
    brain.session_store = None
    brain.memory_store = None
    brain.on_tool_call = None
    brain.on_tool_result = None
    brain.on_thinking_update = None
    return brain


class TestBarePatternGating:
    """Bare-pattern extraction (web_search(...) in prose) must be gated to text-mode."""

    def test_native_mode_ignores_bare_in_prose(self):
        """Cloud/native providers: prose mentioning tool names must NOT extract calls."""
        brain = _make_brain(use_native_tools=True)
        text = (
            "I think I could use web_search to find the answer. "
            "Let me try memory to recall what you said."
        )
        calls = brain._extract_tool_calls(text)
        tool_names = {c["name"] for c in calls}
        assert "web_search" not in tool_names, (
            "Bare web_search in prose must NOT be extracted in native mode"
        )
        assert "memory" not in tool_names, (
            "Bare memory in prose must NOT be extracted in native mode"
        )

    def test_text_mode_extracts_bare_calls(self):
        """Text-mode (local models): bare tool patterns must still extract."""
        brain = _make_brain(use_native_tools=False)
        text = 'web_search("latest news")'
        calls = brain._extract_tool_calls(text)
        assert len(calls) >= 1
        assert calls[0]["name"] == "web_search"
        assert calls[0]["arguments"]["query"] == "latest news"

    def test_explicit_tool_prefix_works_in_both_modes(self):
        """TOOL: prefix format must work regardless of native/text mode."""
        for native in (True, False):
            brain = _make_brain(use_native_tools=native)
            text = 'TOOL: web_search("latest news")'
            calls = brain._extract_tool_calls(text)
            assert len(calls) >= 1, f"TOOL: prefix failed in native={native}"
            assert calls[0]["name"] == "web_search"

    def test_empty_input(self):
        brain = _make_brain(use_native_tools=True)
        assert brain._extract_tool_calls("") == []
        assert brain._extract_tool_calls(None) == []

    def test_multiple_bare_calls_text_mode(self):
        brain = _make_brain(use_native_tools=False)
        text = 'web_search("weather")\nThen shell_execute("dir")'
        calls = brain._extract_tool_calls(text)
        names = {c["name"] for c in calls}
        assert "web_search" in names
        assert "shell_execute" in names

class TestGroundingRules:
    """Grounding rules must be present in the system prompt stable tier."""

    def test_grounding_contract_in_tool_rules(self):
        from charlie.core import _TOOL_RULES
        assert "GROUNDING CONTRACT" in _TOOL_RULES
        assert "Answer ONLY from" in _TOOL_RULES

    def test_anti_fabrication_in_tool_rules(self):
        from charlie.core import _TOOL_RULES
        assert "ANTI-FABRICATION" in _TOOL_RULES
        assert "Do not invent" in _TOOL_RULES

    def test_tool_result_trust_in_tool_rules(self):
        from charlie.core import _TOOL_RULES
        assert "TOOL-RESULT TRUST" in _TOOL_RULES
        assert "ground truth" in _TOOL_RULES

    def test_memory_humility_in_tool_rules(self):
        from charlie.core import _TOOL_RULES
        assert "MEMORY HUMILITY" in _TOOL_RULES
        assert "outdated" in _TOOL_RULES

    def test_soul_has_grounding_line(self):
        from charlie.config import config
        assert "grounded" in config.soul.lower()

    def test_volatile_tier_shows_evidence_blocks(self):
        from datetime import datetime

        from charlie.core import _build_volatile_tier
        now = datetime(2026, 1, 15, 10, 30)
        tier = _build_volatile_tier(
            "voice", now, 10,
            has_search=True, has_memory=False,
            has_user=True, has_opinions=False,
        )
        assert "Evidence blocks present" in tier
        assert "[SEARCH RESULTS]" in tier
        assert "[USER]" in tier
        assert "[Relevant memories]" not in tier
        assert "[OPINIONS]" not in tier

    def test_volatile_tier_no_evidence(self):
        from datetime import datetime

        from charlie.core import _build_volatile_tier
        now = datetime(2026, 1, 15, 10, 30)
        tier = _build_volatile_tier("voice", now, 10)
        assert "none" in tier


class TestCancelGeneration:
    """Barge-in / cancel must bump _chat_generation so the tool loop breaks."""

    def test_cancel_chat_bumps_generation(self):
        brain = _make_brain(use_native_tools=True)
        before = brain._chat_generation
        brain.cancel_chat()
        assert brain._chat_generation == before + 1

    def test_tool_loop_breaks_on_stale_generation(self):
        """Mirror the exact top-of-loop guard used by chat_stream.

        If generation is captured at turn start, a cancel (which increments
        _chat_generation) must make the guard `self._chat_generation != generation`
        True, breaking the loop before another tool cycle runs.
        """
        brain = _make_brain(use_native_tools=True)
        generation_at_loop_top = brain._chat_generation
        brain.cancel_chat()
        assert brain._chat_generation != generation_at_loop_top
