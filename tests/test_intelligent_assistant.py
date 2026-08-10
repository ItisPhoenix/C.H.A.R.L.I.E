"""Tests for the 6 intelligent assistant upgrade features."""

import os
import tempfile

from charlie.core import (
    _apply_correction_to_memory,
    _assess_tool_result_relevance,
    _detect_correction,
    _detect_forget_rule,
    _detect_operator_persona,
    _detect_review_rules,
    _detect_set_goal,
    _detect_standing_instruction,
    _detect_verbosity_feedback,
    _is_followup,
    _strip_vocatives,
)
from charlie.prompt_builder import build_volatile_tier as _build_volatile_tier

# ---------------------------------------------------------------------------
# Step 1: Correction Detection & Auto-Learning
# ---------------------------------------------------------------------------

class TestCorrectionDetection:
    """Verify _detect_correction catches common correction patterns."""

    def test_no_meaning(self):
        assert _detect_correction("no, I mean blue") is True

    def test_no_comma(self):
        assert _detect_correction("no. that's wrong") is True

    def test_thats_wrong(self):
        assert _detect_correction("that's wrong") is True

    def test_thats_not_right(self):
        assert _detect_correction("that's not right") is True

    def test_actually_prefix(self):
        assert _detect_correction("actually, the answer is 42") is True

    def test_not_quite(self):
        assert _detect_correction("not quite, I meant the other one") is True

    def test_i_said(self):
        assert _detect_correction("I said Paris, not London") is True

    def test_i_meant(self):
        assert _detect_correction("I meant the Python one") is True

    def test_no_correction(self):
        assert _detect_correction("what's the weather in Paris") is False

    def test_normal_question(self):
        assert _detect_correction("tell me a joke") is False

    def test_positive_feedback(self):
        assert _detect_correction("that's great") is False


class TestApplyCorrectionToMemory:
    """Verify corrections get written to OPINIONS.md."""

    def test_writes_correction(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            path = f.name
        try:
            result = _apply_correction_to_memory(
                "no, I meant blue", "The sky is green", opinions_path=path
            )
            assert result is not None
            assert "Correction" in result
            assert "blue" in result
            with open(path, encoding="utf-8") as f:
                content = f.read()
            assert "no, I meant blue" in content
        finally:
            os.unlink(path)

    def test_deduplicates(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("Correction by user: no, I meant blue. Previous answer: 'The sky is green'.\n")
            path = f.name
        try:
            result = _apply_correction_to_memory(
                "no, I meant blue", "The sky is green", opinions_path=path
            )
            assert result is None  # Already exists, skip
        finally:
            os.unlink(path)

    def test_non_correction_returns_none(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            path = f.name
        try:
            result = _apply_correction_to_memory(
                "what's the weather", "I don't know", opinions_path=path
            )
            assert result is None
        finally:
            os.unlink(path)

    def test_writes_structural_rule_when_world_model_given(self):
        from charlie.world_model import WorldModel
        wm = WorldModel(db_path=":memory:")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            path = f.name
        try:
            _apply_correction_to_memory("no, I meant blue", "The sky is green", opinions_path=path, world_model=wm)
            rules = wm.active_rules()
            assert any("no, I meant blue" in text for _id, text in rules)
        finally:
            os.unlink(path)

    def test_no_rule_written_without_world_model(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            path = f.name
        try:
            result = _apply_correction_to_memory("no, I meant blue", "The sky is green", opinions_path=path)
            assert result is not None
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Step 4: Correction Fast-Path in _needs_web_search
# ---------------------------------------------------------------------------

class TestCorrectionFastPath:
    """Verify _is_followup matches correction phrases and _strip_vocatives works."""

    def test_followup_no_comma(self):
        assert _is_followup("no, I meant Paris") is True

    def test_followup_thats_wrong(self):
        assert _is_followup("that's wrong") is True

    def test_followup_actually(self):
        assert _is_followup("actually") is True

    def test_followup_i_meant(self):
        assert _is_followup("I meant the other one") is True

    def test_followup_too_long_rejected(self):
        # Exceeds _FOLLOWUP_MAX_LEN
        assert _is_followup("no, I meant the weather in Paris today and tomorrow") is False

    def test_vocative_stripped(self):
        # "what, Charlie?" should match "what" after stripping
        assert _is_followup("what, Charlie?") is True

    def test_vocative_stripped_with_hey(self):
        assert _is_followup("what, hey Charlie?") is True

    def test_strip_vocatives(self):
        assert _strip_vocatives("hello, Charlie") == "hello"

    def test_strip_vocatives_none(self):
        assert _strip_vocatives("hello there") == "hello there"

    def test_followup_what_was_that(self):
        assert _is_followup("what was that") is True


# ---------------------------------------------------------------------------
# Step 3: Post-Tool Confidence Gate
# ---------------------------------------------------------------------------

class TestConfidenceGate:
    """Verify _assess_tool_result_relevance filters low-quality results."""

    def test_short_result_rejected(self):
        assert _assess_tool_result_relevance("web_search", "Error") is False

    def test_empty_result_rejected(self):
        assert _assess_tool_result_relevance("web_search", "") is False

    def test_error_prefix_rejected(self):
        assert _assess_tool_result_relevance(
            "web_search", "Error: timeout connecting to upstream"
        ) is False

    def test_html_result_rejected(self):
        assert _assess_tool_result_relevance(
            "web_search", "<html><head><title>404</title></head></html>"
        ) is False

    def test_no_results_rejected(self):
        assert _assess_tool_result_relevance(
            "web_search", "No results found"
        ) is False

    def test_good_result_accepted(self):
        result = (
            "According to recent data, the population of France is approximately "
            "68 million people. The country spans 640,679 square kilometers."
        )
        assert _assess_tool_result_relevance("web_search", result) is True

    def test_tool_error_result_rejected(self):
        assert _assess_tool_result_relevance(
            "web_search", "Error: Tool 'web_search' timed out after 15s"
        ) is False


# ---------------------------------------------------------------------------
# Step 2: Proactive Memory Recall (already implemented; test dedup logic)
# ---------------------------------------------------------------------------

class TestMemoryRecall:
    """Verify memory recall existing logic + follow-up skip."""

    def test_followup_skips_memory_search(self):
        """Follow-up queries should not trigger memory search."""
        assert _is_followup("what was that") is True
        assert _is_followup("elaborate") is True

    def test_non_followup_allows_search(self):
        assert _is_followup("tell me about the latest stock price for Tesla") is False


# ---------------------------------------------------------------------------
# Step 5: Adaptive Verbosity Preference
# ---------------------------------------------------------------------------

class TestVerbosityDetection:
    """Verify _detect_verbosity_feedback catches explicit feedback."""

    def test_too_long(self):
        assert _detect_verbosity_feedback("that's too long") == "short"

    def test_shorter(self):
        assert _detect_verbosity_feedback("shorter") == "short"

    def test_be_brief(self):
        assert _detect_verbosity_feedback("be brief") == "short"

    def test_more_detail(self):
        assert _detect_verbosity_feedback("more detail please") == "long"

    def test_elaborate(self):
        assert _detect_verbosity_feedback("elaborate on this") == "long"

    def test_tell_me_more(self):
        assert _detect_verbosity_feedback("tell me more about that") == "long"

    def test_no_feedback(self):
        assert _detect_verbosity_feedback("what's the weather") is None


class TestVolatilityTierVerbosity:
    """Verify _build_volatile_tier injects verbosity hint."""

    def test_no_verbosity(self):
        from datetime import datetime
        tier = _build_volatile_tier("voice", datetime.now(), 5)
        assert "Answer style:" not in tier

    def test_with_short_verbosity(self):
        from datetime import datetime
        tier = _build_volatile_tier(
            "voice", datetime.now(), 5, verbosity_hint="short"
        )
        assert "Answer style: short" in tier

    def test_with_long_verbosity(self):
        from datetime import datetime
        tier = _build_volatile_tier(
            "voice", datetime.now(), 5, verbosity_hint="long"
        )
        assert "Answer style: long" in tier


# ---------------------------------------------------------------------------
# Step 6: Conversation Goal Memory
# ---------------------------------------------------------------------------

class TestGoalDetection:
    """Verify _detect_set_goal parses goal commands."""

    def test_set_goal_basic(self):
        assert _detect_set_goal("set goal: plan vacation") == "plan vacation"

    def test_set_goal_with_charlie(self):
        assert _detect_set_goal("Charlie, set goal: build a website") == "build a website"

    def test_set_goal_with_hey(self):
        assert _detect_set_goal("hey charlie, set goal: fix the bug") == "fix the bug"

    def test_set_goal_no_match(self):
        assert _detect_set_goal("what's the weather") is None

    def test_set_goal_strips_period(self):
        assert _detect_set_goal("set goal: plan vacation.") == "plan vacation"


class TestVolatilityTierGoal:
    """Verify _build_volatile_tier injects active goal."""

    def test_no_goal(self):
        from datetime import datetime
        tier = _build_volatile_tier("voice", datetime.now(), 5)
        assert "Current goal:" not in tier

    def test_with_goal(self):
        from datetime import datetime
        tier = _build_volatile_tier(
            "voice", datetime.now(), 5, active_goal="plan vacation"
        )
        assert "Current goal: plan vacation" in tier
        assert "Stay focused" in tier


class TestStandingInstructionDetection:
    """Verify _detect_standing_instruction catches behavior-rule teaching."""

    def test_always_prefix(self):
        assert _detect_standing_instruction("always reply short on Telegram") == "always reply short on Telegram"

    def test_from_now_on(self):
        assert _detect_standing_instruction("from now on use metric units") == "from now on use metric units"

    def test_in_the_future(self):
        assert _detect_standing_instruction("in the future skip the small talk") is not None

    def test_whenever_i(self):
        assert _detect_standing_instruction("whenever I ask for news, include security") is not None

    def test_strips_vocative_prefix(self):
        assert _detect_standing_instruction("Charlie, always be brief") == "always be brief"

    def test_strips_trailing_period(self):
        assert _detect_standing_instruction("always be brief.") == "always be brief"

    def test_no_match(self):
        assert _detect_standing_instruction("what's the weather") is None

    def test_opinion_teaching_not_captured(self):
        assert _detect_standing_instruction("you should like jazz") is None


class TestReviewRulesDetection:
    """Verify _detect_review_rules catches the 'what have you learned' command."""

    def test_what_have_you_learned(self):
        assert _detect_review_rules("what have you learned about me") is True

    def test_what_do_you_know(self):
        assert _detect_review_rules("what do you know about me") is True

    def test_list_rules(self):
        assert _detect_review_rules("list your rules") is True

    def test_no_match(self):
        assert _detect_review_rules("what's the weather") is False


class TestForgetRuleDetection:
    """Verify _detect_forget_rule extracts the search text."""

    def test_forget_that(self):
        assert _detect_forget_rule("forget that I like short replies") == "I like short replies"

    def test_forget_what_you_learned_about(self):
        assert _detect_forget_rule("forget what you learned about Telegram") == "Telegram"

    def test_forget_the_rule_about(self):
        assert _detect_forget_rule("forget the rule about spotify") == "spotify"

    def test_strips_trailing_period(self):
        assert _detect_forget_rule("forget that Telegram thing.") == "Telegram thing"

    def test_no_match(self):
        assert _detect_forget_rule("what's the weather") is None


# Phase 4: Helm desktop-control operator persona
class TestOperatorPersonaDetection:
    """Verify _detect_operator_persona catches Helm address."""

    def test_helm_with_comma(self):
        assert _detect_operator_persona("Helm, open my email") is True

    def test_helm_lowercase_no_punctuation(self):
        assert _detect_operator_persona("helm open my email") is True

    def test_helm_not_at_start(self):
        assert _detect_operator_persona("ask helm to open my email") is False

    def test_no_match(self):
        assert _detect_operator_persona("what's the weather") is False


class TestVolatileTierOperatorPersona:
    """Verify _build_volatile_tier injects the Helm persona block.

    A bare "Helm" mention is no longer sufficient to prove the (token-
    heavy) narration persona is active -- these tests check for the persona
    block's own marker ("[Helm MODE]") instead of the bare name.
    """

    def test_no_persona(self):
        from datetime import datetime
        tier = _build_volatile_tier("voice", datetime.now(), 5)
        assert "[Helm MODE]" not in tier

    def test_with_persona(self):
        from datetime import datetime
        tier = _build_volatile_tier(
            "voice", datetime.now(), 5, operator_persona=True
        )
        assert "[Helm MODE]" in tier
        assert "desktop_observe" in tier
        assert "desktop_observe" in tier
