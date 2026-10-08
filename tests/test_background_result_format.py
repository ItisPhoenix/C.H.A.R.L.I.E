from charlie.background_task import _compact_research_speech
from main import _clean_background_result, _compact_catchup_speech


def test_shell_fact_background_result_is_compact_and_deduplicated():
    raw = (
        "Python version: 3.14.6. Done. Here's what I found: **Python version:** 3.14.6 "
        "**First heading:** `TASKKILL [/S system [/U username [/P [password]]]]` "
        "Done. Here's the summary: Python version: 3.14.6."
    )

    assert _clean_background_result(raw) == (
        "Python version: 3.14.6\n"
        "Taskkill help heading: TASKKILL [/S system [/U username [/P [password]]]]"
    )


def test_research_failure_speech_keeps_dashboard_detail_out_of_voice():
    assert _compact_research_speech(
        "I couldn't verify product options matching your requirements from the available sources."
    ) == "Research finished without enough verified evidence."


def test_idle_catchup_speech_does_not_repeat_background_task_text():
    message = (
        "While you were away: Background task 'Research the best laptops' failed. "
        "Result: I couldn't verify product options matching your requirements from the available sources."
    )
    assert _compact_catchup_speech(message) == (
        "Research finished without enough verified evidence. Check the dashboard."
    )
