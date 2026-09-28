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


def test_duckduckgo_parser_unwraps_redirect_links_and_rejects_invalid_targets():
    parser = _DuckDuckGoParser()
    parser.feed(
        '<a class="result__a" href="//duckduckgo.com/l/?uddg='
        'https%3A%2F%2Fexample.com%2Farticle%3Fx%3D1%26y%3D2&amp;rut=abc">'
        'Example article</a>'
        '<a class="result__a" href="//duckduckgo.com/l/?uddg=javascript%3Aalert%281%29">'
        'Unsafe target</a>'
        '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2F">'
        'Malformed target</a>'
    )

    assert parser.results == [("Example article", "https://example.com/article?x=1&y=2", "")]


def test_browser_read_accepts_short_page_content():
    from charlie.research.fetch import document_from_content

    page = SearchResult(title="Example Domain", url="https://example.com", provider="browser_read")
    search_result = SearchResult(title="Example Domain", url="https://example.com", provider="search")

    document = document_from_content(page, "Example Domain", extraction_method="html-text")
    short_search_result = document_from_content(
        search_result,
        "Example Domain",
        extraction_method="html-text",
    )

    assert document is not None and document.content == "Example Domain"
    assert short_search_result is None


def test_browser_read_preserves_public_url_validation_error(monkeypatch):
    def reject(_url):
        raise ValueError("Research URL host could not be resolved")

    monkeypatch.setattr("charlie.research.fetch.validate_public_url", reject)

    assert read_url("https://example.invalid") == {"error": "Research URL host could not be resolved"}


def test_browser_read_returns_title_from_short_html_page(monkeypatch):
    import charlie.research.fetch as fetch_module
    import charlie.tools as tools_module

    class FakeResponse:
        url = "https://example.com/"
        text = "<html><head><title>Example Domain</title></head><body><p>Example Domain</p></body></html>"

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def get(self, _url, **_kwargs):
            return FakeResponse()

        async def aclose(self):
            pass

    monkeypatch.setattr(fetch_module, "validate_public_url", lambda url: url)
    monkeypatch.setattr(fetch_module.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(tools_module, "_browser_ready", lambda: True)

    result = tools_module.browser_read("https://example.com")

    assert result.startswith("Title: Example Domain\nURL: https://example.com/")
    assert "Example Domain" in result


@pytest.mark.asyncio
async def test_duckduckgo_provider_prefixes_domain_filter(monkeypatch):
    from charlie.research.providers import DuckDuckGoProvider

    requests = []

    class FakeResponse:
        text = '<a class="result__a" href="https://docs.python.org/3/tutorial/index.html">Python</a>'

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def get(self, url, **kwargs):
            requests.append((url, kwargs["params"]))
            return FakeResponse()

    monkeypatch.setattr("charlie.research.providers.httpx.AsyncClient", FakeClient)
    results = await DuckDuckGoProvider().search(
        "Python list comprehensions", limit=3, domain_filters=["docs.python.org"]
    )

    assert requests == [
        ("https://html.duckduckgo.com/html/", {"q": "site:docs.python.org Python list comprehensions"})
    ]
    assert [result.url for result in results] == ["https://docs.python.org/3/tutorial/index.html"]


def test_web_research_converts_comma_domains_to_filters_without_rewriting_query(monkeypatch):
    import charlie.research.engine as engine_module
    import charlie.tools as tools_module

    calls = []

    class FakeEngine:
        def __init__(self, _config):
            pass

        def run_sync(self, query, mode, *, domain_filters=None):
            calls.append((query, mode, domain_filters))
            return SimpleNamespace(legacy_text=lambda: "ok")

    monkeypatch.setattr(engine_module, "ResearchEngine", FakeEngine)
    report = tools_module._run_research_report(
        "web_research",
        {"query": "Python list comprehensions", "mode": "standard", "domain": "docs.python.org, example.com"},
    )

    assert report.legacy_text() == "ok"
    assert calls == [
        ("Python list comprehensions", "standard", ["docs.python.org", "example.com"])
    ]


@pytest.mark.asyncio
async def test_deep_research_propagates_domain_filters_to_all_queries_and_followup(monkeypatch):
    import charlie.research.engine as engine_module

    config = SimpleNamespace(
        research_enabled=True,
        research_max_search_queries=3,
        research_max_sources=3,
        research_max_concurrency=1,
        research_fetch_timeout_s=1,
        research_crawl_enabled=False,
        research_total_timeout_deep_s=5,
        research_currency="INR",
    )
    engine = ResearchEngine(config)
    result = SearchResult("Python docs", "https://docs.python.org/3/tutorial/", "Python documentation")
    followups = []

    async def initial_search(plan):
        assert all(item.domain_filters == ["docs.python.org"] for item in plan.queries)
        return [result]

    async def no_sources(_results, _mode):
        return []

    async def capture_followup(plan, _providers, **_kwargs):
        followups.append(plan)
        return []

    monkeypatch.setattr(engine, "_search", initial_search)
    monkeypatch.setattr(engine, "_fetch_sources", no_sources)
    monkeypatch.setattr(engine, "_providers", lambda: [])
    monkeypatch.setattr(engine_module, "search_plan", capture_followup)

    report = await engine.run(
        "Python list comprehensions",
        "deep",
        domain_filters=["docs.python.org"],
    )

    assert report.plan.domain_filters == ["docs.python.org"]
    assert followups[0].domain_filters == ["docs.python.org"]
    assert followups[0].queries[0].domain_filters == ["docs.python.org"]


@pytest.mark.asyncio
async def test_search_cache_is_scoped_to_domain_filters(monkeypatch):
    config = SimpleNamespace(research_max_sources=3, research_max_concurrency=1)
    engine = ResearchEngine(config)
    calls = []

    class FilteredProvider:
        name = "filtered"

        async def search(self, query, *, limit, domain_filters=None):
            calls.append(tuple(domain_filters or []))
            domain = (domain_filters or ["example.com"])[0]
            return [SearchResult(domain, f"https://{domain}/", f"Found {query}")]

    monkeypatch.setattr(engine, "_providers", lambda: [FilteredProvider()])
    first = engine.plan("Python docs", ResearchMode.QUICK, domain_filters=["docs.python.org"])
    second = engine.plan("Python docs", ResearchMode.QUICK, domain_filters=["docs.example.com"])

    first_results = await engine._search(first)
    second_results = await engine._search(second)

    assert calls == [("docs.python.org",), ("docs.example.com",)]
    assert first_results[0].domain == "docs.python.org"
    assert second_results[0].domain == "docs.example.com"


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
    assert read_url("http://localhost:8080/private")["error"] == "Research URL targets a private or local host"


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
