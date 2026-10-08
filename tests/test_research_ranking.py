from charlie.research.models import ResearchMode, ResearchPlan, ResearchQuery, SearchResult
from charlie.research.ranking import rank_search_results


def test_current_briefing_prioritizes_fresh_published_story():
    plan = ResearchPlan(
        goal="today's intelligence briefing",
        mode=ResearchMode.STANDARD,
        queries=[ResearchQuery("today's intelligence briefing")],
    )
    old = SearchResult(
        "Today's intelligence briefing",
        "https://old.example/story",
        "global update",
        published_at="2026-05-01T08:00:00Z",
        rank=0,
    )
    fresh = SearchResult(
        "Regional market update",
        "https://fresh.example/story",
        "market update",
        published_at="2026-08-27T07:00:00Z",
        rank=4,
    )

    ranked = rank_search_results([old, fresh], plan, 2)

    assert ranked[0] is fresh


def test_briefing_word_overlap_does_not_make_unrelated_page_headline():
    plan = ResearchPlan(
        goal="today's intelligence briefing on space science",
        mode=ResearchMode.STANDARD,
        queries=[ResearchQuery("today's intelligence briefing on space science")],
    )
    unrelated = SearchResult(
        "Daily intelligence briefing",
        "https://unrelated.example/story",
        "briefing and intelligence",
        published_at="2026-08-27T06:00:00Z",
        rank=0,
    )
    relevant = SearchResult(
        "Space science update",
        "https://science.example/story",
        "space science telescope findings",
        published_at="2026-08-27T05:00:00Z",
        rank=1,
    )

    ranked = rank_search_results([unrelated, relevant], plan, 2)

    assert ranked[0] is relevant


def test_compound_release_ranks_results_against_each_specific_search_query():
    plan = ResearchPlan(
        goal="research what's the latest close source and open source LLM released",
        mode=ResearchMode.STANDARD,
        queries=[
            ResearchQuery("latest closed-source large language model release developer announcement"),
            ResearchQuery("latest open-source large language model release developer announcement"),
        ],
    )
    closed = SearchResult(
        "OpenAI's new model",
        "https://openai.example/model",
        "Announcement of the latest closed-source large language model.",
    )
    open_weights = SearchResult(
        "Mistral announces a new model",
        "https://mistral.example/model",
        "Open-weight large language model release announcement.",
    )

    ranked = rank_search_results([closed, open_weights], plan, 2)

    assert {result.url for result in ranked} == {closed.url, open_weights.url}


def test_llm_release_filter_rejects_dictionary_and_unrelated_language_results():
    from charlie.research.ranking import search_result_matches_query

    query = "latest closed-source and open-source LLM released"
    dictionary = SearchResult(
        "Closed definition",
        "https://dictionary.example/closed",
        "A word meaning not open or shut.",
    )
    language_reference = SearchResult(
        "Python data model",
        "https://python.example/reference",
        "An open-source reference for the Python language and data model.",
    )
    release = SearchResult(
        "New LLM release",
        "https://models.example/release",
        "Announcement for a new open-weight large language model.",
    )

    assert not search_result_matches_query(query, dictionary)
    assert not search_result_matches_query(query, language_reference)
    assert search_result_matches_query(query, release)
