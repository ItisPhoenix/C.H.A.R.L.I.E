import asyncio
from types import SimpleNamespace

import pytest

from charlie import core
from charlie.config import Config
from charlie.research.citations import (
    assign_citations,
    strip_citation_markers,
    strip_invalid_citations,
    validate_citations,
)
from charlie.research.engine import ResearchEngine
from charlie.research.fetch import validate_public_url
from charlie.research.models import (
    Citation,
    EvidenceItem,
    ResearchMode,
    ResearchReport,
    SearchResult,
    SourceDocument,
)
from charlie.research.router import is_briefing_query, is_sustained_research_query, route
from charlie.research.releases import canonical_release_urls, pick_stable
from charlie.research.search import build_plan, clean_query, discovery_queries, parse_brief


def test_research_router_distinguishes_stable_and_fresh_requests():
    assert route("what is a Python list comprehension").should_research is False
    current = route("what's trending on X right now")
    assert current.should_research is True
    assert current.mode is ResearchMode.QUICK
    assert route("deep research open source browser agents").mode is ResearchMode.DEEP


def test_explicit_research_keyword_keeps_full_mode_when_tool_requests_quick():
    assert route("Research the latest Python release", ResearchMode.QUICK).mode is ResearchMode.STANDARD
    assert route("Deep research current browser agent security", ResearchMode.QUICK).mode is ResearchMode.DEEP


def test_fresh_lookup_stays_quick_even_when_configured_for_full_research():
    decision = route("What is the current stock price of NVIDIA?", ResearchMode.STANDARD)
    assert decision.should_research is True
    assert decision.mode is ResearchMode.QUICK


def test_local_notepad_write_does_not_research_words_inside_the_note():
    query = (
        "In the open Notepad window, write this short note: I help with research, reminders, and weather. "
        "Then save it on the Desktop as About Charlie.txt."
    )
    decision = route(query)
    assert decision.should_research is False
    assert decision.reason == "local desktop or file action"


def test_current_local_ui_controls_stay_out_of_web_research():
    for query in (
        "Use the currently open Save As dialog. Type C:\\Users\\me\\Desktop\\About Charlie.txt "
        "into the observed File name field, then click Save.",
        "Use the current file picker dialog and keyboard to enter a filename, then press Save.",
    ):
        decision = route(query)
        assert decision.should_research is False
        assert decision.reason == "local desktop or file action"


def test_explicit_research_then_save_still_uses_web_research():
    decision = route("Research how Charlie can save notes in Notepad, then save the findings on the Desktop.")
    assert decision.should_research is True


@pytest.mark.parametrize(
    "query",
    [
        "What's the price of gold in Kanpur right now?",
        "What is the current stock price of NVIDIA?",
    ],
)
def test_current_fact_uses_quick_search_without_an_atomic_query_gate(query):
    decision = route(query)
    assert decision.should_research is True
    assert decision.mode is ResearchMode.QUICK


def test_broad_current_topic_keeps_quick_search_budget():
    assert route("What's trending in India right now?").mode is ResearchMode.QUICK


@pytest.mark.parametrize(
    ("query", "domains"),
    [
        ("Search GitHub for Charlie plugins", ["github.com"]),
        ("Search Bilibili for Charlie assistants", ["bilibili.com", "b23.tv"]),
        ("What's trending on Twitter right now?", ["x.com", "twitter.com"]),
        ("Search Reddit for local assistant reviews", ["reddit.com"]),
    ],
)
def test_agent_reach_platform_queries_scope_public_domain_search(query, domains):
    assert build_plan(query, ResearchMode.STANDARD).domain_filters == domains


def test_installed_local_version_and_help_request_does_not_start_web_research():
    decision = route(
        "Charlie, I'm checking what's installed on this PC. Could you tell me the Python version "
        "and the first heading in Windows' taskkill help? Please don't stop or change any process."
    )
    assert decision.should_research is False
    assert decision.mode is None
    assert decision.reason == "local installed-version lookup"


def test_explicit_background_action_is_not_preempted_by_a_version_signal():
    action = route(
        "Charlie, start a background task with two steps: run python --version, then run taskkill /? for help."
    )
    assert action.should_research is False
    assert action.reason == "explicit background task request"

    research = route("Charlie, start a background task to research the latest stable Python version.")
    assert research.should_research is True
    assert research.mode is ResearchMode.STANDARD


@pytest.mark.parametrize(
    "query",
    [
        "Deep research the latest Windows security changes",
        "Research current browser agent security thoroughly",
        "Research and compare Playwright and Selenium",
        "Research local LLM runtimes across multiple sources",
    ],
)
def test_explicit_sustained_research_is_task_routed(query):
    decision = route(query)
    assert decision.should_research is True
    assert is_sustained_research_query(query, decision) is True


def test_short_explicit_research_request_stays_on_foreground_research_turn():
    query = "Charlie, research the latest stable Python documentation version. Cite only sources you fetched."
    decision = route(query)
    assert decision.should_research is True
    assert is_sustained_research_query(query, decision) is False


def test_three_subject_multi_dimension_research_is_sustained_with_keyword():
    query = (
        "Research the architecture, advantages and limitations of SearXNG, Crawl4AI and Scrapling. "
        "Use their official project documentation and identify missing evidence."
    )
    decision = route(query)

    assert decision.should_research is True
    assert is_sustained_research_query(query, decision) is True


def test_current_lookup_is_not_automatically_backgrounded():
    query = "What's the latest Python release?"
    decision = route(query)
    assert decision.should_research is True
    assert is_sustained_research_query(query, decision) is False


def test_memory_and_reminder_updates_with_relative_time_are_not_research():
    decision = route(
        "Correct the NIST task notes from the failed attempt. Update structured memory and remove "
        "the retry notes. Update the existing reminder to fire five minutes from now in Asia/Kolkata."
    )
    assert decision.should_research is False
    assert decision.reason == "local state update"

    mixed = route("Research the official NIST publication and then update the existing reminder.")
    assert mixed.should_research is True


def test_relabeling_an_existing_timer_is_local_state_work_not_research():
    decision = route("Relabel my existing timer to Take Medicine.")
    assert decision.should_research is False
    assert decision.reason == "local state update"


def test_personal_calendar_questions_do_not_trigger_web_research():
    decision = route("What events are on my calendar today?")
    assert decision.should_research is False
    assert decision.reason == "personal calendar lookup"

    explicit = route("Research calendar apps with the best event reminders.")
    assert explicit.should_research is True


def test_current_request_asking_for_sources_uses_quick_search():
    decision = route("latest major AI developments today with sources")
    assert decision.should_research is True
    assert decision.mode is ResearchMode.QUICK


def test_official_publication_query_stays_on_quick_budget():
    decision = route("NIST AI Risk Management Framework official publication page", "quick")
    assert decision.should_research is True
    assert decision.mode is ResearchMode.QUICK


def test_action_report_phrase_does_not_trigger_research():
    decision = route("run taskkill /? and report the first help heading")
    assert decision.should_research is False


@pytest.mark.parametrize(
    "query",
    [
        "Give me today's intelligence briefing.",
        "Prepare a news roundup for today.",
        "Create a daily summary.",
    ],
)
def test_briefing_intent_is_shared_by_research_and_runtime(query):
    assert is_briefing_query(query)
    assert route(query).should_research


def test_research_router_routes_factual_and_comparison_queries():
    assert route("What is WebAssembly and what is it used for?").should_research is False
    assert route("Compare Playwright and Selenium for browser automation.").mode is ResearchMode.QUICK
    assert route("What is the WebAssembly component model proposal status?").should_research is False


def test_current_facts_do_not_depend_on_query_length_or_topic_whitelists():
    assert route("What is the latest Python release?").mode is ResearchMode.QUICK
    assert route(
        "Could you tell me what the current population estimate is for Kanpur, including the district boundary estimate?"
    ).mode is ResearchMode.QUICK


@pytest.mark.parametrize(
    "query, expected_mode",
    [
        ("Research the current Python release", ResearchMode.STANDARD),
        ("Deep research current browser agent security", ResearchMode.DEEP),
        ("What is WebAssembly and what is it used for?", ResearchMode.QUICK),
        ("latest Python release", ResearchMode.QUICK),
    ],
)
def test_explicit_research_keyword_controls_full_mode(query, expected_mode):
    assert route(query, ResearchMode.QUICK).mode is expected_mode


@pytest.mark.parametrize(
    "query",
    [
        "How are you today?",
        "How are you doing today?",
        "How have you been lately?",
        "Are you okay today?",
        "What's up with you today?",
        "How are things going lately?",
    ],
)
def test_social_freshness_words_stay_conversation(query):
    decision = route(query)
    assert decision.should_research is False
    assert decision.mode is None


@pytest.mark.parametrize(
    "query",
    [
        "What's happening in AI today?",
        "What's the weather today?",
        "What happened in the market today?",
        "Latest cybersecurity news today",
        "What changed in OpenAI recently?",
    ],
)
def test_domain_freshness_words_still_research(query):
    decision = route(query)
    assert decision.should_research is True
    assert decision.mode is ResearchMode.QUICK


@pytest.mark.asyncio
async def test_document_ranking_prefers_newer_evidence_when_relevance_matches():
    plan = build_plan("latest Python release", ResearchMode.STANDARD)
    older = SourceDocument(
        url="https://example.com/old",
        title="Python release",
        content="Python release information and current version details. " * 4,
        published_at="2025-01-01T00:00:00Z",
        quality_score=0.8,
    )
    newer = SourceDocument(
        url="https://example.com/new",
        title="Python release",
        content="Python release information and current version details. " * 4,
        published_at="2026-01-01T00:00:00Z",
        quality_score=0.8,
    )
    from charlie.research.ranking import rank_documents

    assert [item.url for item in await rank_documents([older, newer], plan, 2)] == [newer.url, older.url]


def test_clean_query_removes_instruction_and_format_noise():
    cleaned = clean_query("Do a web search and tell me what's currently trending in AI & tech. Be short under 60 words")
    assert cleaned == "trending in AI & tech"


def test_open_source_reimplementation_is_not_misrouted_as_release_research():
    from charlie.research.search import parse_brief

    query = "Brainstorm ideas for building a free open-source version of an app feature."
    assert route(query).should_research is False
    assert parse_brief(query).entity_kind == "general"
    assert route("What is the latest stable Python version?").should_research is True


def test_release_lookup_only_requires_a_date_when_the_user_asks_for_one():
    version_only = parse_brief("What is the latest stable Node.js LTS release?")
    with_date = parse_brief("What is the release date of Node.js v24.21.0?")

    assert version_only.aspects == ["version"]
    assert with_date.aspects == ["version", "release_date"]


def test_lts_release_lookup_selects_the_lts_version_not_the_current_channel():
    brief = parse_brief("What is the latest stable Node.js LTS release?")
    source = SourceDocument(
        source_id="S1",
        content="v26.11.1 Current\nv24.21.0 Latest LTS\nv22.23.3 LTS",
    )

    assert pick_stable([source], brief) == ("24.21.0", "", "S1")


def test_canonical_release_urls_preserve_the_registered_official_domain():
    urls = canonical_release_urls(parse_brief("What is the latest stable Node.js release?"))

    assert urls == [
        "https://nodejs.org/en/download",
        "https://nodejs.org/en/about/previous-releases",
    ]


def test_llm_release_query_normalizes_closed_source_typo_and_searches_both_release_types():
    query = "Research what's the latest close source and open source LLM released."
    brief = parse_brief(query)

    assert brief.topic == "the latest closed-source and open-source LLM released"
    queries = discovery_queries(brief)
    assert any("closed-source" in item.casefold() for item in queries)
    assert any("open-source" in item.casefold() for item in queries)
    assert any("proprietary" in item.casefold() for item in queries)
    assert any("open-weight" in item.casefold() for item in queries)
    assert not is_sustained_research_query(query, route(query))


def test_clean_query_removes_assistant_and_citation_instructions_but_preserves_quotes_and_domain():
    query = (
        'Charlie, research the exact phrase "cite only sources you fetched" on docs.python.org. '
        "Cite only sources you fetched."
    )
    assert clean_query(query) == 'the exact phrase "cite only sources you fetched" on docs.python.org'


def test_standard_plan_keeps_explicit_domain_as_a_filter():
    plan = build_plan("research Python docs on docs.python.org", ResearchMode.STANDARD)
    assert plan.domain_filters == ["docs.python.org"]
    assert all(query.domain_filters == ["docs.python.org"] for query in plan.queries)


def test_clean_query_removes_insufficient_evidence_reply_instruction():
    query = (
        "Charlie, research the exact phrase CHARLIE-G5-EMPTY-20260923-7F6C on example.invalid. "
        "If you find no fetched evidence, say so."
    )
    assert clean_query(query) == "the exact phrase CHARLIE-G5-EMPTY-20260923-7F6C on example.invalid"


def test_standard_plan_does_not_split_on_conjunctions():
    plan = build_plan("best IEMs under ₹2000 for gaming and music", ResearchMode.STANDARD)
    assert plan.queries[0].text == "IEMs under 2000 rupees for gaming and music"
    assert any("price" in item for item in plan.constraints)


def test_public_url_validation_rejects_local_targets(monkeypatch):
    with pytest.raises(ValueError):
        validate_public_url("http://localhost:8080/search")
    with pytest.raises(ValueError):
        validate_public_url("file:///C:/secret.txt")


def test_citation_validation_removes_unknown_source_ids():
    source = SourceDocument(
        source_id="",
        url="https://example.com",
        title="Example",
        domain="example.com",
        content="Useful content " * 30,
    )
    citations = assign_citations([source])
    assert validate_citations("Claim [S1]", citations)
    assert not validate_citations("Claim [S99]", citations)
    assert strip_invalid_citations("Claim [S1] [S99]", citations) == "Claim [S1] "
    assert strip_citation_markers("Claim [S1] [S99]") == "Claim"


class _Provider:
    name = "fake"

    async def search(self, query, *, limit, domain_filters=None):
        return [SearchResult("Example source", "https://example.com/article", "Current useful evidence about " + query)]


@pytest.mark.asyncio
async def test_quick_research_does_not_launch_browser(monkeypatch):
    config = SimpleNamespace(
        research_enabled=True,
        research_max_search_queries=2,
        research_max_sources=3,
        research_max_concurrency=2,
        research_market="IN",
        research_locale="en-IN",
        research_fetch_timeout_s=1,
        research_total_timeout_quick_s=5,
        research_total_timeout_standard_s=5,
        research_total_timeout_deep_s=5,
    )
    engine = ResearchEngine(config, browser_fetch=lambda result: pytest.fail("quick research launched browser"))
    async def search(_plan):
        return [SearchResult("Python release", "https://www.python.org/downloads/release/python-3130/")]

    async def fetch(result, **_kwargs):
        return SourceDocument(
            url=result.url, title=result.title, domain="www.python.org",
            content="Python 3.13.0 is a stable Python release. Release date: October 7, 2024.",
            quality_score=0.8,
        )

    monkeypatch.setattr(engine, "_search", search)
    monkeypatch.setattr("charlie.research.engine.fetch_document", fetch)
    report = await engine.run("latest Python release", "quick")
    assert report.successful
    assert report.mode is ResearchMode.QUICK
    assert report.search_results[0].url.rstrip("/") == "https://python.org/downloads"
    assert report.citations[0].source_id == "S1"
    assert report.sources and report.evidence


@pytest.mark.asyncio
async def test_explicit_research_overrides_quick_and_fetches_cites_sources(monkeypatch):
    config = SimpleNamespace(
        research_enabled=True,
        research_max_search_queries=2,
        research_max_sources=3,
        research_max_concurrency=2,
        research_market="IN",
        research_locale="en-IN",
        research_fetch_timeout_s=1,
        research_crawl_enabled=False,
        research_total_timeout_quick_s=5,
        research_total_timeout_standard_s=5,
        research_total_timeout_deep_s=5,
        research_currency="INR",
    )
    async def fake_fetch(result, **_kwargs):
        return SourceDocument(
            url=result.url,
            canonical_url=result.url,
            title=result.title,
            domain=result.domain,
            content="Current evidence. " * 40,
            quality_score=0.8,
        )

    monkeypatch.setattr("charlie.research.engine.fetch_document", fake_fetch)
    engine = ResearchEngine(config)
    monkeypatch.setattr(engine, "_providers", lambda: [_Provider()])
    report = await engine.run("research current Python release", "quick")
    assert report.mode is ResearchMode.STANDARD
    assert report.sources
    assert report.citations[0].source_id == "S1"
    assert "[S1]" in report.prompt_context()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["quick", "standard"])
async def test_product_extraction_uses_selected_budget_without_forcing_deep(monkeypatch, mode):
    config = SimpleNamespace(
        research_enabled=True,
        research_total_timeout_standard_s=5,
        research_total_timeout_sustained_s=5,
    )
    engine = ResearchEngine(config)
    configured = []
    sustained_called = []
    inner_called = []

    def configure(mode, *, sustained):
        configured.append((mode, sustained))
        return 5

    async def run_sustained(query, *, mode, **kwargs):
        sustained_called.append((query, mode))
        return ResearchReport(query=query, mode=mode)

    async def run_inner(query, mode, *args, **kwargs):
        inner_called.append((query, mode))
        return ResearchReport(query=query, mode=mode)

    monkeypatch.setattr(engine, "_configure_budget", configure)
    monkeypatch.setattr(engine, "_run_sustained", run_sustained)
    monkeypatch.setattr(engine, "_run_inner", run_inner)
    await engine.run("Recommend headphones under ₹2000 for music", mode)

    assert configured == [(ResearchMode.QUICK, False)]
    assert sustained_called == []
    assert inner_called == [("Recommend headphones under ₹2000 for music", ResearchMode.QUICK)]


@pytest.mark.asyncio
async def test_explicit_product_research_keeps_full_research_path(monkeypatch):
    engine = ResearchEngine(SimpleNamespace(research_enabled=True, research_total_timeout_standard_s=5))
    configured = []
    sustained_called = []

    monkeypatch.setattr(engine, "_configure_budget", lambda mode, *, sustained: configured.append((mode, sustained)) or 5)

    async def run_sustained(query, *, mode, **kwargs):
        sustained_called.append((query, mode))
        return ResearchReport(query=query, mode=mode)

    monkeypatch.setattr(engine, "_run_sustained", run_sustained)
    await engine.run("Research headphones under ₹2000 for music", "quick")

    assert configured == [(ResearchMode.STANDARD, True)]
    assert sustained_called == [("Research headphones under ₹2000 for music", ResearchMode.STANDARD)]


def test_standard_prompt_context_contains_only_grounded_evidence():
    source = SourceDocument(
        source_id="S1",
        url="https://example.com/article",
        title="Article",
        domain="example.com",
        content="WebAssembly is a binary instruction format. Gardening tips are unrelated.",
    )
    report = ResearchReport(
        query="What is WebAssembly?",
        mode=ResearchMode.STANDARD,
        sources=[source],
    )
    report.citations = assign_citations(report.sources)
    from charlie.research.evidence import build_evidence

    report.evidence = build_evidence(report.sources, report.query)
    context = report.prompt_context()
    assert "WebAssembly is a binary instruction format." in context
    assert "Gardening tips are unrelated." not in context


@pytest.mark.asyncio
async def test_numeric_esoteric_identifier_requires_matching_source_evidence():
    plan = build_plan("What is the QZ-4819 quantum moss protocol?", ResearchMode.STANDARD)
    docs = [
        SourceDocument(
            url="https://example.com",
            title="Quantum protocol overview",
            content="Quantum protocol research without the requested identifier. " * 5,
            quality_score=0.9,
        )
    ]
    from charlie.research.ranking import rank_documents

    assert await rank_documents(docs, plan, 4) == []


@pytest.mark.asyncio
async def test_standard_research_reports_insufficient_evidence_without_extracted_sources(monkeypatch):
    config = SimpleNamespace(
        research_enabled=True,
        research_max_search_queries=1,
        research_max_sources=2,
        research_max_concurrency=1,
        research_market="IN",
        research_locale="en-IN",
        research_fetch_timeout_s=1,
        research_crawl_enabled=False,
        research_total_timeout_standard_s=5,
    )

    class Provider:
        name = "fake"

        async def search(self, query, *, limit, domain_filters=None):
            return [SearchResult("Unrelated", "https://example.com", "keyword only")]

    async def no_document(result, **_kwargs):
        return None

    monkeypatch.setattr("charlie.research.engine.fetch_document", no_document)
    engine = ResearchEngine(config)
    monkeypatch.setattr(engine, "_providers", lambda: [Provider()])
    report = await engine.run("research current WebAssembly capabilities", "standard")
    assert report.stop_reason == "no-results"
    assert report.citations == []
    assert report.prompt_context() == ""


_BUDGET_QUERY = "current browser agent security"


def _verbose_document(source_id: str, url: str, matching: int = 45) -> SourceDocument:
    """A source with more unique matching sentences than the old global budget."""
    body = " ".join(
        f"Browser agent security finding {index} describes the current browser agent "
        f"security posture for this deployment."
        for index in range(matching)
    )
    return SourceDocument(
        source_id=source_id,
        url=url,
        canonical_url=url,
        title="Browser agent security review",
        domain=url.split("//", 1)[1].split("/", 1)[0],
        content=f"{body} Gardening tips are unrelated to this topic.",
        quality_score=0.8,
    )


def test_evidence_budget_is_per_source_not_first_document_wins():
    from charlie.research.evidence import build_evidence

    documents = [
        _verbose_document(f"S{index}", f"https://site{index}.example/report")
        for index in range(1, 7)
    ]

    evidence = build_evidence(documents, _BUDGET_QUERY)

    grounded = {item.source_id for item in evidence}
    assert grounded == {f"S{index}" for index in range(1, 7)}
    counts = {source_id: sum(1 for item in evidence if item.source_id == source_id) for source_id in grounded}
    # One verbose page must not consume a budget the other sources never see.
    assert max(counts.values()) <= 6
    assert min(counts.values()) >= 1


def test_evidence_keeps_best_matching_sentences_per_source():
    from charlie.research.evidence import build_evidence

    url = "https://site1.example/report"
    document = SourceDocument(
        source_id="S1",
        url=url,
        canonical_url=url,
        title="Browser agent security review",
        domain="site1.example",
        content=(
            "A passing note that mentions browser once. "
            "Browser agent security is the current browser agent security topic. "
            "Another low signal that says agent only. "
            "Current security guidance for deployments. "
            "Gardening tips are unrelated to this topic. "
        ),
        quality_score=0.8,
    )

    evidence = build_evidence([document], _BUDGET_QUERY, per_source_max=2)

    assert [item.relevance for item in evidence] == [1.0, 0.5]
    assert evidence[0].statement.startswith("Browser agent security is the current")
    assert evidence[1].statement.startswith("Current security guidance")


def test_evidence_dedupes_repeated_sentences_within_a_source():
    from charlie.research.evidence import build_evidence

    document = _verbose_document("S1", "https://site1.example/report")
    document.content = f"{document.content} {document.content}"

    evidence = build_evidence([document], _BUDGET_QUERY, per_source_max=50)

    statements = [item.statement for item in evidence]
    assert len(statements) == 45
    assert len(statements) == len(set(statements))


@pytest.mark.asyncio
async def test_standard_research_stops_at_coverage_and_keeps_fetched_evidence(monkeypatch):
    config = SimpleNamespace(
        research_enabled=True,
        research_max_search_queries=3,
        research_max_sources=6,
        research_max_pages_per_domain=6,
        research_max_concurrency=3,
        research_market="IN",
        research_locale="en-IN",
        research_fetch_timeout_s=1,
        research_crawl_enabled=False,
        research_total_timeout_standard_s=10,
        research_currency="INR",
    )

    class Provider:
        name = "fake"

        async def search(self, query, *, limit, domain_filters=None):
            return [
                SearchResult(
                    f"Browser agent security {index}",
                    f"https://site{index}.example/report",
                    "Current browser agent security review",
                )
                for index in range(1, 7)
            ]

    fetched = []

    async def fake_fetch(result, **_kwargs):
        fetched.append(result.url)
        return _verbose_document("", result.url)

    monkeypatch.setattr("charlie.research.engine.fetch_document", fake_fetch)
    engine = ResearchEngine(config)
    monkeypatch.setattr(engine, "_providers", lambda: [Provider()])

    report = await engine.run("research current browser agent security", "standard")

    assert len(report.search_results) == 6
    assert 0 < len(fetched) < len(report.search_results)
    assert len(report.sources) == len(report.citations) == len(fetched)
    assert {item.source_id for item in report.evidence} == {
        citation.source_id for citation in report.citations
    }
    assert {source.url for source in report.sources} == set(fetched)
    assert report.stop_reason == "evidence-sufficient"




# --- Research stop-reason refusal contract -----------------------------------------
# A research turn that produced no usable fetched evidence must refuse, never answer
# from parametric memory. Only a terminal reason that means evidence was actually
# fetched may be synthesized; everything else fails closed.

_FRESH_QUERY = "what is the latest AI trend right now"
# No citation marker on purpose: an ungrounded turn has its invented [Sn] markers
# silently stripped, so the surviving text is the bare confident claim. Matching the
# marker would hide the exact hallucination this guards against.
_UNGROUNDED_MODEL_ANSWER = "The latest AI trend is agentic coding"


@pytest.fixture
def brain_config(tmp_path):
    return Config(
        llm_url="http://localhost:11434",
        llm_key="no-key",
        llm_model="dummy",
        native_tool_calling=False,
        router_classifier_enabled=False,
        session_db_path=str(tmp_path / "sessions.db"),
        world_model_db_path=str(tmp_path / "world.db"),
    )


async def _run_research_turn(brain, text=_FRESH_QUERY):
    return [
        chunk
        async for chunk in brain.chat_stream(
            text,
            platform="web",
            session_id="session-research-stop",
            task_id="task-research-stop",
            turn_id="turn-research-stop",
            skip_pre_search=False,
        )
    ]


def _stub_ungrounded_completion(seen):
    async def fake_completion(payload, _generation):
        seen["prompt"] = payload
        return f"{_UNGROUNDED_MODEL_ANSWER} [S1]", []

    return fake_completion


@pytest.mark.asyncio
async def test_research_timeout_refuses_instead_of_answering_from_memory(monkeypatch, brain_config):
    """Real engine, real timeout: the bare question must not reach the model ungrounded."""
    brain_config.research_total_timeout_standard_s = 0.05
    seen: dict = {}
    brain = core.Brain(brain_config, register_panic_hotkey=False)

    async def hanging_search(_self, _plan):
        await asyncio.sleep(30)
        return []

    monkeypatch.setattr(ResearchEngine, "_search", hanging_search)
    monkeypatch.setattr(brain, "_stream_completion", _stub_ungrounded_completion(seen))
    try:
        chunks = await _run_research_turn(brain)
    finally:
        await brain.close()

    assert chunks, "a research turn must still produce an answer chunk"
    joined = "".join(chunks)
    assert _UNGROUNDED_MODEL_ANSWER not in joined, (
        f"an ungrounded answer escaped a timeout refusal: {joined!r}"
    )
    assert "time" in joined.lower(), f"timeout refusal must name the timeout, got: {joined!r}"
    assert "sufficient reliable evidence" not in joined, (
        "a timeout must not be reported as 'no sources exist'"
    )
    assert "[S1]" not in joined, "invented citations must not survive a refusal"


@pytest.mark.asyncio
async def test_research_stop_reasons_refuse_or_answer(monkeypatch, brain_config):
    """Every non-evidence stop reason refuses; evidence-sufficient is answered."""
    cases = [
        ("no-results", True),
        ("insufficient-evidence", True),
        ("search-snippets-only", True),
        ("error", True),
        ("cancelled", True),
        ("evidence-sufficient", False),
    ]
    for stop_reason, expect_refusal in cases:
        seen: dict = {}
        brain = core.Brain(brain_config, register_panic_hotkey=False)
        citations = []
        if stop_reason == "evidence-sufficient":
            citations = [Citation(source_id="S1", url="https://example.com/a", title="A", domain="example.com")]
        report = ResearchReport(
            query=_FRESH_QUERY,
            mode=ResearchMode.STANDARD,
            sources=[SourceDocument(
                source_id="S1",
                url="https://example.com/a",
                title="A",
                content=_UNGROUNDED_MODEL_ANSWER,
            )]
            if stop_reason == "evidence-sufficient"
            else [],
            evidence=[EvidenceItem(source_id="S1", statement=_UNGROUNDED_MODEL_ANSWER)]
            if stop_reason == "evidence-sufficient"
            else [],
            citations=citations,
            stop_reason=stop_reason,
        )

        async def fake_research(*_args, **_kwargs):
            return report

        monkeypatch.setattr(brain, "_run_research_for_turn", fake_research)
        monkeypatch.setattr(brain, "_stream_completion", _stub_ungrounded_completion(seen))
        try:
            chunks = await _run_research_turn(brain)
        finally:
            await brain.close()

        joined = "".join(chunks)
        assert joined.strip(), f"stop_reason={stop_reason!r} produced no answer at all"
        refused = _UNGROUNDED_MODEL_ANSWER not in joined
        assert refused is expect_refusal, (
            f"stop_reason={stop_reason!r} expected refuse={expect_refusal}, got chunks={chunks!r}"
        )
        if stop_reason == "evidence-sufficient":
            assert "sufficient reliable evidence" not in joined
        if stop_reason in {"error", "cancelled"}:
            assert "sufficient reliable evidence" not in joined, (
                f"{stop_reason} must not claim there were no sources, got: {joined!r}"
            )
