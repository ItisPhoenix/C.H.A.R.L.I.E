"""Tests for charlie.personality — emotion detection and voice commands."""

from charlie.personality import get_emotion_for_context, parse_voice_command

# ── get_emotion_for_context ────────────────────────────────────────────────


class TestGetEmotionForContext:
    def test_urgent_keywords(self):
        assert get_emotion_for_context("system crash! help ASAP") == "energetic"

    def test_emergency_keyword(self):
        assert (
            get_emotion_for_context("emergency in the server room") == "energetic"
        )

    def test_broken_keyword(self):
        assert get_emotion_for_context("my code is broken") == "energetic"

    def test_sad_keywords(self):
        assert get_emotion_for_context("I feel terrible today") == "calm"

    def test_sad_keyword_miss(self):
        assert get_emotion_for_context("I miss my old setup") == "calm"

    def test_sad_keyword_depressed(self):
        assert get_emotion_for_context("feeling depressed") == "calm"

    def test_happy_keywords(self):
        assert get_emotion_for_context("that's amazing news!") == "energetic"

    def test_happy_keyword_excited(self):
        assert get_emotion_for_context("I'm so excited") == "energetic"

    def test_happy_keyword_love_it(self):
        assert get_emotion_for_context("love this feature") == "energetic"

    def test_frustrated_keywords(self):
        assert get_emotion_for_context("this is so frustrating") == "calm"

    def test_frustrated_keyword_hate(self):
        assert get_emotion_for_context("I hate this error") == "calm"

    def test_frustrated_keyword_stupid(self):
        assert get_emotion_for_context("stupid compiler won't work") == "calm"

    def test_neutral_default(self):
        assert get_emotion_for_context("what time is it") == "neutral"

    def test_neutral_factual(self):
        assert get_emotion_for_context("tell me about python") == "neutral"

    def test_case_insensitive(self):
        assert get_emotion_for_context("EMERGENCY HELP NOW") == "energetic"

    def test_empty_text(self):
        assert get_emotion_for_context("") == "neutral"


# ── parse_voice_command ────────────────────────────────────────────────────


class TestParseVoiceCommand:
    def test_be_energetic(self):
        assert parse_voice_command("be energetic") == "energetic"

    def test_speak_faster(self):
        assert parse_voice_command("speak faster") == "energetic"

    def test_cheer_up(self):
        assert parse_voice_command("cheer up") == "energetic"

    def test_calm_down(self):
        assert parse_voice_command("calm down") == "calm"

    def test_speak_slower(self):
        assert parse_voice_command("speak slower") == "calm"

    def test_easy(self):
        assert parse_voice_command("easy") == "calm"

    def test_no_command_returns_none(self):
        assert parse_voice_command("what's the weather") is None

    def test_normal_question_returns_none(self):
        assert parse_voice_command("tell me about python") is None

    def test_case_insensitive(self):
        assert parse_voice_command("Be Energetic") == "energetic"

    def test_mixed_case_calm_down(self):
        assert parse_voice_command("CALM DOWN") == "calm"
