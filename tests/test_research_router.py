from types import SimpleNamespace

import pytest

from charlie.research.citations import assign_citations, strip_invalid_citations, validate_citations
from charlie.research.engine import ResearchEngine
from charlie.research.fetch import validate_public_url
from charlie.research.models import ResearchMode, ResearchReport, SearchResult, SourceDocument
from charlie.research.router import route
from charlie.research.search import build_plan, clean_query


def test_research_router_distinguishes_stable_and_fresh_requests():
    assert route("what is a Python list comprehension").should_research is False
    current = route("what's trending on X right now")
    assert current.should_research is True
    assert current.mode is ResearchMode.STANDARD
    assert route("deep research open source browser agents").mode is ResearchMode.DEEP


def test_current_request_asking_for_sources_fetches_documents():
    decision = route("latest major AI developments today with sources")
    assert decision.should_research is True
    assert decision.mode is ResearchMode.STANDARD


def test_research_router_routes_factual_and_comparison_queries():
    assert route("What is WebAssembly and what is it used for?").mode is ResearchMode.STANDARD
    assert route("Compare Playwright and Selenium for browser automation.").mode is ResearchMode.STANDARD
    assert route("What is the WebAssembly component model proposal status?").mode is ResearchMode.STANDARD


def test_current_queries_use_extraction_capable_standard_mode():
    assert route("What is the latest Python release?").mode is ResearchMode.STANDARD


def test_document_ranking_prefers_newer_evidence_when_relevance_matches():
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

    assert [item.url for item in rank_documents([older, newer], plan, 2)] == [newer.url, older.url]


def test_clean_query_removes_instruction_and_format_noise():
    cleaned = clean_query("Do a web search and tell me what's currently trending in AI & tech. Be short under 60 words")
    assert cleaned == "trending in AI & tech"


def test_standard_plan_does_not_split_on_conjunctions():
    plan = build_plan("best IEMs under ₹2000 for gaming and music", ResearchMode.STANDARD)
    assert plan.queries[0].text == "best IEMs under ₹2000 for gaming and music"
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
    monkeypatch.setattr(engine, "_providers", lambda: [_Provider()])
    report = await engine.run("latest Python release", "quick")
    assert report.successful
    assert report.mode is ResearchMode.QUICK
    assert report.citations[0].source_id == "S1"


@pytest.mark.asyncio
async def test_standard_research_fetches_and_cites_sources(monkeypatch):
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
    report = await engine.run("research current Python release", "standard")
    assert report.sources
    assert report.citations[0].source_id == "S1"
    assert "[S1]" in report.prompt_context()


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


def test_numeric_esoteric_identifier_requires_matching_source_evidence():
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

    assert rank_documents(docs, plan, 4) == []


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
    assert report.stop_reason == "insufficient-evidence"
    assert report.citations == []
    assert report.prompt_context() == ""
