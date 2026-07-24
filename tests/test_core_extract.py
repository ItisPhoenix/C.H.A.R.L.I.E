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
    brain._installed_skill_blocks = {}
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

    def test_desktop_tool_param_names_no_longer_drift(self):
        """Previously _TOOL_PARAM_NAMES only covered 6 of 19+ tools -- any
        desktop_* call parsed in text mode got a bogus `query` kwarg and
        crashed with a TypeError. Params are now read live from the
        registry, so a previously-uncovered tool like desktop_click parses
        onto its real `mark_id` parameter."""
        brain = _make_brain(use_native_tools=False)
        calls = brain._extract_tool_calls('TOOL: desktop_click("3")')
        assert calls[0]["name"] == "desktop_click"
        assert calls[0]["arguments"] == {"mark_id": "3"}

    def test_delegate_to_agent_multi_param_no_longer_drift(self):
        brain = _make_brain(use_native_tools=False)
        calls = brain._extract_tool_calls(
            'TOOL: delegate_to_agent("E.D.I.T.H.", "research quantum computing")'
        )
        assert calls[0]["name"] == "delegate_to_agent"
        assert calls[0]["arguments"] == {
            "agent_name": "E.D.I.T.H.",
            "task_description": "research quantum computing",
        }

    def test_zero_arg_tool_gets_empty_arguments(self):
        """graph_consolidate takes no parameters -- an empty-parens call must
        not synthesize a bogus `query` kwarg that would crash the call."""
        brain = _make_brain(use_native_tools=False)
        calls = brain._extract_tool_calls("TOOL: graph_consolidate()")
        assert calls[0]["name"] == "graph_consolidate"
        assert calls[0]["arguments"] == {}

    def test_unknown_tool_name_falls_back_to_query(self):
        brain = _make_brain(use_native_tools=False)
        calls = brain._extract_tool_calls('TOOL: totally_made_up_tool("something")')
        assert calls[0]["arguments"] == {"query": "something"}

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
        assert "never guess" in config.soul.lower()

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

    def test_volatile_tier_omits_tool_catalog_by_default(self):
        from datetime import datetime

        from charlie.core import _build_volatile_tier
        now = datetime(2026, 1, 15, 10, 30)
        tier = _build_volatile_tier("voice", now, 10)
        assert "AVAILABLE TOOLS" not in tier

    def test_volatile_tier_includes_tool_catalog_when_provided(self):
        """Text-mode (local model) turns pass the live registry's tool
        catalog here every turn -- this is what makes Charlie aware of
        desktop control, memory/graph tools, MCP, and plugins in text mode,
        instead of only the 3 tools _TEXT_TOOL_INSTRUCTIONS shows examples
        for."""
        from datetime import datetime

        from charlie.core import _build_volatile_tier
        from charlie.tools import registry as tool_registry
        now = datetime(2026, 1, 15, 10, 30)
        tier = _build_volatile_tier(
            "voice", now, 10, tool_catalog=tool_registry.build_tool_prompt()
        )
        assert "AVAILABLE TOOLS" in tier
        assert "desktop_click" in tier
        assert "delegate_to_agent" in tier


class TestInstalledSkillBlocks:
    """A "skill" kind extension installed via the web dashboard is mirrored
    into the voice process over the EventBus (see main.py's
    "extension_installed" handler) -- add_installed_skill_block() is how its
    instructions actually reach the context tier the chat loop uses."""

    def test_add_installed_skill_block_appears_in_context_tier(self):
        brain = _make_brain(use_native_tools=True)
        brain.add_installed_skill_block("demo-skill", "[SKILL: demo-skill]\nDo the thing.")
        assert "[SKILL: demo-skill]" in brain._context_tier
        assert "Do the thing." in brain._context_tier

    def test_remove_installed_skill_block_drops_it(self):
        brain = _make_brain(use_native_tools=True)
        brain.add_installed_skill_block("demo-skill", "[SKILL: demo-skill]\nDo the thing.")
        brain.remove_installed_skill_block("demo-skill")
        assert "[SKILL: demo-skill]" not in brain._context_tier

    def test_remove_unknown_skill_block_is_a_no_op(self):
        brain = _make_brain(use_native_tools=True)
        before = brain._context_tier
        brain.remove_installed_skill_block("never-installed")
        assert brain._context_tier == before


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


class TestHelmPersona:
    """H.E.L.M. persona text and auto-detect (Task A4 see-act-verify hardening)."""

    def test_helm_persona_mentions_verify_and_raw_input(self):
        from charlie.core import _HELM_PERSONA_TEXT
        for phrase in ("desktop_click_at", "re-observe", "verify"):
            assert phrase in _HELM_PERSONA_TEXT

    def test_operator_persona_auto_activates_on_desktop_intent(self):
        from charlie.core import _detect_operator_persona
        assert _detect_operator_persona("helm, open my editor")
        assert _detect_operator_persona("click the save button on screen")
        assert not _detect_operator_persona("what's the weather")
