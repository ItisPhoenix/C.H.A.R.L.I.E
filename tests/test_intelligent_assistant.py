"""Tests for the 6 intelligent assistant upgrade features."""

import os
import tempfile

from charlie.core import (
    _apply_correction_to_memory,
    _assess_tool_result_relevance,
    _build_volatile_tier,
    _detect_correction,
    _detect_set_goal,
    _detect_verbosity_feedback,
    _is_followup,
    _strip_vocatives,
)

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
