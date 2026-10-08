from charlie.research.candidates import extract_candidates
from charlie.research.models import ResearchBrief, SourceClass, SourceDocument
from charlie.research.search import verification_queries
from charlie.research.sources import classify


def test_llm_release_candidates_are_extracted_from_dated_source_statements():
    brief = ResearchBrief(
        topic="the latest closed-source and open-source LLM released",
        entity_kind="release",
    )
    document = SourceDocument(
        source_id="D1",
        title="Model release updates",
        url="https://tracker.example/changelog",
        content=(
            "- ReleasedReflection AI debuts Beam, a 501B open-weight model. "
            "Reflection AI released Beam on 2026-10-05.\n"
            "OpenAI released GPT-6 Sol on 2026-09-29: a proprietary language model."
        ),
    )

    extracted = extract_candidates([document], brief)
    assert [candidate.access for candidate in extracted] == ["closed", "open"]
    candidates = {candidate.name: candidate for candidate in extracted}

    assert candidates["Beam"].brand == "Reflection AI"
    assert candidates["Beam"].access == "open"
    assert candidates["Beam"].release_date == "2026-10-05"
    assert candidates["GPT-6 Sol"].brand == "OpenAI"
    assert candidates["GPT-6 Sol"].access == "closed"
    assert candidates["GPT-6 Sol"].release_date == "2026-09-29"
    assert verification_queries(candidates["Beam"], brief) == [
        "Reflection AI Beam official model release announcement",
        "Reflection AI Beam official release date",
        "Reflection AI Beam release 2026-10-05",
    ]


def test_llm_release_candidate_can_be_named_before_the_release_verb():
    brief = ResearchBrief(topic="latest open-weight LLM release", entity_kind="release")
    document = SourceDocument(
        source_id="D1",
        title="Model release updates",
        url="https://tracker.example/changelog",
        content=(
            'Mistral Large 4 ("Le Chonk") is the flagship open-weight model, '
            "launched in public preview on 2026-10-06."
        ),
    )

    candidates = extract_candidates([document], brief)

    assert len(candidates) == 1
    assert candidates[0].name == "Mistral Large 4"
    assert candidates[0].brand == "Mistral"
    assert candidates[0].access == "open"
    assert candidates[0].release_date == "2026-10-06"


def test_llm_release_candidates_keep_dates_and_access_labels_with_each_flattened_entry():
    brief = ResearchBrief(
        topic="latest closed-source and open-source LLM releases",
        entity_kind="release",
    )
    document = SourceDocument(
        source_id="D1",
        title="Model release updates",
        url="https://tracker.example/changelog",
        content=(
            "OpenAI released GPT-6 Sol on 2026-09-29 as a proprietary language model. "
            "Reflection AI debuted Beam on 2026-10-05 as its first open-weight model."
        ),
    )

    candidates = {candidate.name: candidate for candidate in extract_candidates([document], brief)}

    assert candidates["GPT-6 Sol"].access == "closed"
    assert candidates["GPT-6 Sol"].release_date == "2026-09-29"
    assert candidates["Beam"].access == "open"
    assert candidates["Beam"].release_date == "2026-10-05"


def test_llm_candidate_with_future_weights_is_not_yet_classified_as_open():
    brief = ResearchBrief(topic="latest open-weight LLM release", entity_kind="release")
    document = SourceDocument(
        source_id="D1",
        title="Model release update",
        url="https://tracker.example/model",
        content=(
            "Mistral Large 4 launched in public preview on 2026-10-06, "
            "open weights are promised by the end of October."
        ),
    )

    candidates = extract_candidates([document], brief)

    assert len(candidates) == 1
    assert candidates[0].name == "Mistral Large 4"
    assert candidates[0].access is None


def test_discovered_developer_domain_is_only_unverified_official_without_registry_entry():
    assert classify(
        "https://reflection.ai/news/beam",
        candidate_brand="Reflection AI",
    ) is SourceClass.OFFICIAL_UNVERIFIED
