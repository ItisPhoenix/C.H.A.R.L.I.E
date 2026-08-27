from charlie.research.models import ResearchMode, ResearchPlan, ResearchQuery, SearchResult
from charlie.research.ranking import rank_search_results


def test_current_briefing_prioritizes_fresh_published_story():
    plan = ResearchPlan(
        goal="today's intelligence briefing",
        mode=ResearchMode.STANDARD,
        queries=[ResearchQuery("today's intelligence briefing")],
    )
    old = SearchResult("Today's intelligence briefing", "https://old.example/story", "global update", published_at="2026-05-01T08:00:00Z", rank=0)
    fresh = SearchResult("Regional market update", "https://fresh.example/story", "market update", published_at="2026-08-27T07:00:00Z", rank=4)

    ranked = rank_search_results([old, fresh], plan, 2)

    assert ranked[0] is fresh


def test_briefing_word_overlap_does_not_make_unrelated_page_headline():
    plan = ResearchPlan(
        goal="today's intelligence briefing on space science",
        mode=ResearchMode.STANDARD,
        queries=[ResearchQuery("today's intelligence briefing on space science")],
    )
    unrelated = SearchResult("Daily intelligence briefing", "https://unrelated.example/story", "briefing and intelligence", published_at="2026-08-27T06:00:00Z", rank=0)
    relevant = SearchResult("Space science update", "https://science.example/story", "space science telescope findings", published_at="2026-08-27T05:00:00Z", rank=1)

    ranked = rank_search_results([unrelated, relevant], plan, 2)

    assert ranked[0] is relevant
