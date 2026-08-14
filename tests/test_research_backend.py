from types import SimpleNamespace

import pytest

from charlie.browser.actions import read_url
from charlie.core import Brain
from charlie.research.citations import assign_search_citations, strip_invalid_citations
from charlie.research.engine import ResearchEngine
from charlie.research.models import ResearchMode, ResearchProgress, SearchResult, SourceDocument
from charlie.research.providers import _DuckDuckGoParser, search_with_fallback


def test_duckduckgo_parser_extracts_structured_results():
    parser = _DuckDuckGoParser()
    parser.feed(
        '<a class="result__a" href="https://example.com/a">Example title</a>'
        '<a class="result__snippet">Useful snippet</a>'
    )
    assert parser.results == [("Example title", "https://example.com/a", "Useful snippet")]


@pytest.mark.asyncio
async def test_provider_fallback_continues_after_failure():
    class BrokenProvider:
        name = "broken"

        async def search(self, query, *, limit, domain_filters=None):
            raise RuntimeError("provider unavailable")

    class WorkingProvider:
        name = "working"

        async def search(self, query, *, limit, domain_filters=None):
            return [SearchResult("Working", "https://example.com", "evidence")]

    results = await search_with_fallback(
        [BrokenProvider(), WorkingProvider()],
        "test query",
        limit=3,
    )
    assert results[0].provider == "unknown"
    assert results[0].url == "https://example.com"


def test_read_url_rejects_private_target_before_playwright(monkeypatch):
    monkeypatch.setattr(
        "charlie.browser.actions.controller.run",
        lambda *args, **kwargs: pytest.fail("browser launched"),
    )
    assert read_url("http://localhost:8080/private")["error"] == "Only public HTTP(S) URLs can be read."


def test_quick_report_citations_are_validated():
    results = [SearchResult("Source", "https://example.com", "snippet")]
    citations = assign_search_citations(results)
    assert strip_invalid_citations("Claim [S1] [S9]", citations) == "Claim [S1] "


def test_research_progress_keeps_session_scope():
    updates = []
    brain = Brain.__new__(Brain)
    brain.on_thinking_update = lambda name, payload: updates.append((name, payload))
    brain._on_research_progress(ResearchProgress("searching", "Searching"), "session-1")
    assert updates == [
        (
            "research",
            {
                "stage": "searching",
                "message": "Searching",
                "current": 0,
                "total": 0,
                "mode": None,
                "session_id": "session-1",
            },
        )
    ]


@pytest.mark.asyncio
async def test_standard_research_caps_pages_per_domain(monkeypatch):
    config = SimpleNamespace(
        research_enabled=True,
        research_max_search_queries=2,
        research_max_sources=4,
        research_max_pages_per_domain=1,
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
    engine = ResearchEngine(config)
    results = [
        SearchResult("A", "https://example.com/a", "evidence"),
        SearchResult("B", "https://example.com/b", "evidence"),
        SearchResult("C", "https://other.example/c", "evidence"),
    ]
    fetched: list[str] = []

    async def fake_fetch(result, mode):
        fetched.append(result.url)
        return SourceDocument(
            url=result.url,
            canonical_url=result.url,
            title=result.title,
            domain=result.domain,
            content="Evidence content. " * 20,
        )

    monkeypatch.setattr(engine, "_fetch_one", fake_fetch)
    documents = await engine._fetch_sources(results, ResearchMode.STANDARD)
    assert len(documents) == 2
    assert fetched == ["https://example.com/a", "https://other.example/c"]
