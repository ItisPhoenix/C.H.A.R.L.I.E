from types import SimpleNamespace

import pytest

from charlie.research.citations import assign_citations, strip_invalid_citations, validate_citations
from charlie.research.engine import ResearchEngine
from charlie.research.fetch import validate_public_url
from charlie.research.models import ResearchMode, SearchResult, SourceDocument
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
