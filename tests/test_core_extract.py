"""Tests for _extract_tool_calls bare-pattern gating.

Native-tool providers must NOT match bare tool names in prose.
Text-mode (local models) must still match bare patterns.
"""

from unittest.mock import MagicMock, patch

from charlie.core import Brain


def _make_brain(use_native_tools: bool) -> Brain:
    """Build a Brain stub with the desired tool-calling mode."""
    cfg = MagicMock()
    cfg.llm_url = "https://example.com/v1"
    cfg.llm_key = "test-key"
    cfg.llm_model = "test-model"
    cfg.soul = "You are a test assistant."
    cfg.memory_file = "/dev/null"
    cfg.user_file = "/dev/null"
    cfg.opinions_file = "/dev/null"
    cfg.prompt_memory_max = 2200
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

    def test_graph_add_fact_multi_param_no_longer_drift(self):
        brain = _make_brain(use_native_tools=False)
        calls = brain._extract_tool_calls(
            'TOOL: graph_add_fact("user", "prefers", "dark mode")'
        )
        assert calls[0]["name"] == "graph_add_fact"
        assert calls[0]["arguments"] == {
            "subject": "user",
            "predicate": "prefers",
            "object": "dark mode",
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
        assert "graph_add_fact" in tier

    def test_volatile_tier_omits_idle_time_by_default(self):
        from datetime import datetime

        from charlie.core import _build_volatile_tier
        now = datetime(2026, 1, 15, 10, 30)
        tier = _build_volatile_tier("voice", now, 10)
        assert "idle" not in tier.lower()

    def test_volatile_tier_includes_idle_time_when_provided(self):
        from datetime import datetime

        from charlie.core import _build_volatile_tier
        now = datetime(2026, 1, 15, 10, 30)
        tier = _build_volatile_tier("voice", now, 10, idle_seconds=42.0)
        assert "idle time: 42s" in tier


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
    """Helm persona text and auto-detect (Task A4 see-act-verify hardening)."""

    def test_helm_persona_mentions_verify_and_raw_input(self):
        from charlie.core import _HELM_PERSONA_TEXT
        for phrase in ("desktop_click_at", "re-observe", "verify"):
            assert phrase in _HELM_PERSONA_TEXT

    def test_helm_persona_mentions_window_management(self):
        from charlie.core import _HELM_PERSONA_TEXT
        for phrase in ("desktop_windows", "desktop_focus"):
            assert phrase in _HELM_PERSONA_TEXT

    def test_operator_persona_auto_activates_on_desktop_intent(self):
        from charlie.core import _detect_operator_persona
        assert _detect_operator_persona("helm, open my editor")
        assert _detect_operator_persona("click the save button on screen")
        assert not _detect_operator_persona("what's the weather")

    def test_operator_persona_avoids_drag_idiom_false_positives(self):
        from charlie.core import _detect_operator_persona
        assert not _detect_operator_persona("what's a good drag queen show")
        assert not _detect_operator_persona("drag racing is fun")
        assert not _detect_operator_persona("the meeting will drag on forever")

    def test_operator_persona_still_detects_real_drag_intent(self):
        from charlie.core import _detect_operator_persona
        assert _detect_operator_persona("drag the file to the trash")
        assert _detect_operator_persona("drag this to the folder")
        assert _detect_operator_persona("drag and drop this icon")


class TestCapabilitiesBlock:
    """Stable-tier capability roster (Task A4b): stop Charlie from falsely
    refusing when a tool/agent for the request already exists, and make a
    live capability roster override any stale claim (e.g. in SOUL.md)."""

    def test_capabilities_block_overrides_stale_claims(self):
        from charlie.config import Config
        from charlie.core import _build_capabilities_block
        cfg = Config()
        block = _build_capabilities_block(cfg)
        assert "conversation memory" in block
        assert "overrides any conflicting claim" in block

    def test_stable_tier_includes_capabilities_block(self):
        from charlie.config import Config
        from charlie.core import _build_capabilities_block, _build_stable_tier
        cfg = Config()
        block = _build_capabilities_block(cfg)
        stable = _build_stable_tier("Test soul text.", block)
        assert "conversation memory" in stable
        assert "Test soul text." in stable

    def test_capabilities_block_omits_desktop_control_when_disabled(self):
        from charlie.config import Config
        from charlie.core import _build_capabilities_block
        cfg = Config()
        cfg.desktop_control_enabled = False
        block = _build_capabilities_block(cfg)
        assert "Desktop control" not in block

    def test_stable_tier_default_second_arg_matches_old_single_arg_call(self):
        """Existing callers passing only soul_text must still work unchanged."""
        from charlie.core import _build_stable_tier
        stable = _build_stable_tier("Test soul text.")
        assert "Test soul text." in stable

    def test_capabilities_block_omits_desktop_control_when_unavailable(self):
        """desktop_control_enabled=True alone isn't enough -- must also check
        the real _DESKTOP_AVAILABLE import-success flag, mirroring tools.py's
        _desktop_ready() double-check, or the prompt claims a capability that
        actual tool execution will refuse."""
        import charlie.core as core_module
        from charlie.config import Config
        cfg = Config()
        cfg.desktop_control_enabled = True
        with patch.object(core_module, "_DESKTOP_AVAILABLE", False):
            block = core_module._build_capabilities_block(cfg)
        assert "Desktop control" not in block


class TestRebuildStableTier:
    """Task A4b fix 2: the dashboard's system_restart reload flow mutates
    config in place but only rebuilt _context_tier -- capability claims baked
    into _stable_tier kept describing the OLD config until process restart."""

    def test_rebuild_stable_tier_reflects_new_config(self):
        import charlie.core as core_module
        brain = _make_brain(use_native_tools=True)
        brain.config.desktop_control_enabled = False
        brain.config.mcp_enabled = False
        brain.config.plugins_enabled = False
        brain.rebuild_stable_tier()
        before = brain._stable_tier
        assert "Desktop control" not in before

        brain.config.desktop_control_enabled = True
        with patch.object(core_module, "_DESKTOP_AVAILABLE", True):
            brain.rebuild_stable_tier()
        after = brain._stable_tier

        assert after != before
        assert "Desktop control" in after


class TestVisualContentQueryFastPath:
    """Task D1: ambient "what am I looking at" fast-path queues a vision
    screenshot for graphical content OCR/UIA marks can't describe, without
    duplicating _SCREEN_QUERY_RE's existing text-based fast-path."""

    def test_visual_content_query_regex_matches_expected_phrases(self):
        from charlie.core import _VISUAL_CONTENT_QUERY_RE
        assert _VISUAL_CONTENT_QUERY_RE.search("what am I looking at")
        assert _VISUAL_CONTENT_QUERY_RE.search("describe this image")
        assert _VISUAL_CONTENT_QUERY_RE.search("describe the picture")
        assert _VISUAL_CONTENT_QUERY_RE.search("what does this look like")
        assert not _VISUAL_CONTENT_QUERY_RE.search("what time is it")

    def test_visual_content_query_regex_matches_what_do_you_see(self):
        """Previously only OCR-only _SCREEN_QUERY_RE matched; no real screenshot was ever queued for vision."""
        from charlie.core import _VISUAL_CONTENT_QUERY_RE
        assert _VISUAL_CONTENT_QUERY_RE.search("what do you see on my screen right now")
        assert _VISUAL_CONTENT_QUERY_RE.search("what can you see")

    def test_visual_content_query_regex_matches_who_is_this_phrasing(self):
        """"who is this person" previously fell through both regexes: no real
        screenshot ever got queued for the vision model, so Charlie only had
        OCR/UIA text to work with and (correctly, given what she actually saw)
        said she couldn't see a face -- she never saw an image at all."""
        from charlie.core import _VISUAL_CONTENT_QUERY_RE
        assert _VISUAL_CONTENT_QUERY_RE.search("who is this person")
        assert _VISUAL_CONTENT_QUERY_RE.search("who is this")
        assert _VISUAL_CONTENT_QUERY_RE.search("who is that guy")
        assert _VISUAL_CONTENT_QUERY_RE.search("who is he")
        assert _VISUAL_CONTENT_QUERY_RE.search("who is she")

    def test_visual_content_query_regex_does_not_duplicate_screen_query_re(self):
        """"read my screen" is already covered by _SCREEN_QUERY_RE; the new
        regex should not re-match it (kept out to avoid a redundant alternative)."""
        from charlie.core import _SCREEN_QUERY_RE, _VISUAL_CONTENT_QUERY_RE
        assert _SCREEN_QUERY_RE.search("read my screen")
        assert not _VISUAL_CONTENT_QUERY_RE.search("read my screen")

    def test_visual_content_query_regex_matches_widened_phrasing(self):
        """Decision 9: the old 5-phrase whitelist missed common ways to ask
        about a visible error/popup -- widened without touching the automatic
        grounding-fallback tier, which stays regex-independent."""
        from charlie.core import _VISUAL_CONTENT_QUERY_RE
        assert _VISUAL_CONTENT_QUERY_RE.search("what's the error on my screen")
        assert _VISUAL_CONTENT_QUERY_RE.search("what's wrong with this")
        assert _VISUAL_CONTENT_QUERY_RE.search("what's going on on my screen")
        assert _VISUAL_CONTENT_QUERY_RE.search("what is this popup")
        assert _VISUAL_CONTENT_QUERY_RE.search("what's this")
        assert _VISUAL_CONTENT_QUERY_RE.search("help me fix this")
        assert not _VISUAL_CONTENT_QUERY_RE.search("what's for dinner")

    def test_should_queue_visual_screenshot_true_when_fully_enabled(self):
        from charlie.core import _should_queue_visual_screenshot
        cfg = MagicMock()
        cfg.vision_enabled = True
        cfg.desktop_control_enabled = True
        assert _should_queue_visual_screenshot("what am I looking at", cfg)

    def test_should_queue_visual_screenshot_false_without_vision_enabled(self):
        from charlie.core import _should_queue_visual_screenshot
        cfg = MagicMock()
        cfg.vision_enabled = False
        cfg.desktop_control_enabled = True
        assert not _should_queue_visual_screenshot("what am I looking at", cfg)

    def test_should_queue_visual_screenshot_false_without_desktop_control(self):
        from charlie.core import _should_queue_visual_screenshot
        cfg = MagicMock()
        cfg.vision_enabled = True
        cfg.desktop_control_enabled = False
        assert not _should_queue_visual_screenshot("what am I looking at", cfg)

    def test_should_queue_visual_screenshot_false_when_phrase_does_not_match(self):
        from charlie.core import _should_queue_visual_screenshot
        cfg = MagicMock()
        cfg.vision_enabled = True
        cfg.desktop_control_enabled = True
        assert not _should_queue_visual_screenshot("what time is it", cfg)


class TestMaybeInjectVisualScreenshotCall:
    """Regression coverage for the ordering bug: desktop_screenshot must be
    queued via a synthetic tool call injected AFTER the initial completion's
    tool_calls are known, not via an eager pre-call before the initial
    payload is built (which let _build_payload's pop_pending_vision_image()
    consume the image before any vision-routed follow-up ever ran)."""

    def test_no_injection_when_not_queuing(self):
        from charlie.core import _maybe_inject_visual_screenshot_call
        calls = [{"id": "1", "name": "web_search", "arguments": {}}]
        assert _maybe_inject_visual_screenshot_call(calls, False) is calls

    def test_injects_synthetic_call_when_queuing_and_empty(self):
        from charlie.core import _maybe_inject_visual_screenshot_call
        result = _maybe_inject_visual_screenshot_call([], True)
        assert len(result) == 1
        call = result[0]
        assert call["name"] == "desktop_screenshot"
        assert call["arguments"] == {}
        # Downstream code (_exec_one, tool_call_id formatting) reads call["id"]
        # via .get("id") to decide native vs text-based tool-call formatting --
        # it must be present and non-None so a synthetic-only turn is treated
        # the same as a native model-issued call.
        assert call.get("id") is not None

    def test_injects_alongside_existing_real_calls(self):
        from charlie.core import _maybe_inject_visual_screenshot_call
        real_calls = [{"id": "abc", "name": "web_search", "arguments": {"query": "x"}}]
        result = _maybe_inject_visual_screenshot_call(real_calls, True)
        assert len(result) == 2
        assert result[0] == real_calls[0]
        assert result[1]["name"] == "desktop_screenshot"

    def test_no_duplicate_when_model_already_called_desktop_screenshot(self):
        from charlie.core import _maybe_inject_visual_screenshot_call
        real_calls = [{"id": "abc", "name": "desktop_screenshot", "arguments": {}}]
        result = _maybe_inject_visual_screenshot_call(real_calls, True)
        assert result == real_calls
        assert len(result) == 1
