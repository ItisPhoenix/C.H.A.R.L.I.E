from charlie.research.models import ResearchMode
from charlie.research.router import route


def test_natural_background_shell_facts_do_not_route_to_research():
    decision = route(
        "Charlie, please check the Python version and the first heading in Windows "
        "taskkill help in the background, then message me here when you're done."
    )

    assert decision.should_research is False
    assert decision.mode is None


def test_explicit_research_still_wins_for_shell_named_topic():
    decision = route("Research taskkill help documentation and cite official sources")

    assert decision.should_research is True
    assert decision.mode is ResearchMode.STANDARD
