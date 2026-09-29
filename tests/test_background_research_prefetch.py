from charlie.background_task import _step_needs_research_prefetch


def test_research_step_allows_bounded_prefetch():
    assert _step_needs_research_prefetch(
        "Search the NIST website for the AI Risk Management Framework 1.0 publication page."
    ) is True


def test_action_steps_keep_prefetch_disabled():
    assert _step_needs_research_prefetch("Open the publication page in the browser.") is False
    assert _step_needs_research_prefetch("Download the official PDF to Downloads.") is False
