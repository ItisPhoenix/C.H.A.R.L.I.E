from main import _clean_background_result


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
