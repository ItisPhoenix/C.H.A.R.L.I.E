"""Supporting contracts only; live behavior is the acceptance gate."""

from charlie import prompt_builder


def test_general_knowledge_and_observed_action_rules_do_not_conflict():
    rules = prompt_builder._TOOL_RULES
    assert "Answer ONLY from" not in rules
    assert "NEVER use tools for:" not in rules
    assert "trust that result -- never redo" not in rules
    assert "stable general knowledge" in rules
    assert "verified postcondition" in rules


def test_default_output_rules_keep_simple_answers_short_and_direct():
    rules = prompt_builder._DEFAULT_OUTPUT_RULES

    assert "at most 3 sentences and 60 words" in rules
    assert "turn verified tool results into the answer" in rules
    assert "Avoid stock openings and filler" in rules
    assert "use no heading, list, example, or follow-up offer" in rules
    assert "Expand only when the user requests detail" in rules


def test_voice_output_rules_keep_greetings_short_without_memory_dumps():
    rules = prompt_builder._PLATFORM_OUTPUT_RULES["voice"]

    assert "1-2 short spoken sentences and at most 35 words" in rules
    assert "do not dump remembered tasks or details" in rules
