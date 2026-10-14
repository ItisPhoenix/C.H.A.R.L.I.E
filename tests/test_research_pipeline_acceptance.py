from types import SimpleNamespace

import pytest

from charlie.research.citations import assign_citations, validate_claim_support
from charlie.research.engine import ResearchEngine
from charlie.research.models import (
    ResearchBrief,
    ResearchMode,
    ResearchProgress,
    ResearchReport,
    SearchResult,
    SourceDocument,
)


@pytest.mark.asyncio
async def test_progress_counts_fetched_sources_before_evidence_is_known(monkeypatch):
    observed = []
    engine = ResearchEngine(SimpleNamespace(), progress=observed.append)
    engine._configure_budget(ResearchMode.STANDARD, sustained=False)
    result = SearchResult("Guide", "https://alpha.example/guide", provider="test")
    document = SourceDocument(
        source_id="S1", url=result.url, title=result.title,
        domain="alpha.example", content="Fetched readable guide content.",
    )

    async def fetch(*args, **kwargs):
        return document

    monkeypatch.setattr(engine, "_fetch_one", fetch)
    assert await engine._fetch_sources([result], ResearchMode.STANDARD) == [document]
    assert observed[-1].source_count == 1
    assert observed[-1].finding_count is None
    engine._configure_budget(ResearchMode.STANDARD, sustained=False)
    await engine._notify(ResearchProgress("searching", "Searching"))
    assert observed[-1].source_count == 0


@pytest.mark.asyncio
async def test_quick_research_reads_source_instead_of_only_search_snippets(monkeypatch):
    engine = ResearchEngine(SimpleNamespace(research_max_sources=4))
    engine._configure_budget(ResearchMode.QUICK, sustained=False)
    result = SearchResult("Alpha retry guide", "https://alpha.example/retries", provider="test")
    document = SourceDocument(
        source_id="S1", url=result.url, title=result.title, domain="alpha.example",
        content="Alpha retries failed requests with exponential backoff.",
    )

    async def search(plan):
        return [result]

    async def fetch(*args, **kwargs):
        return [document]

    monkeypatch.setattr(engine, "_search", search)
    monkeypatch.setattr(engine, "_fetch_sources", fetch)
    report = await engine._run_inner("How does Alpha retry failed requests?", ResearchMode.QUICK, None)
    assert report.sources == [document]
    assert report.evidence and report.passages
    assert report.stop_reason != "search-snippets-only"


def test_quick_budget_limits_atomic_lookup_to_one_query_and_two_sources():
    engine = ResearchEngine(SimpleNamespace())
    engine._configure_budget(ResearchMode.QUICK, sustained=False)
    assert engine._query_limit == 1
    assert engine._source_limit == 2


@pytest.mark.parametrize(
    ("title", "content", "expected_complete"),
    [
        (
            "Gold Rate Today in India",
            "Gold price in India today is 15142 INR per gram.",
            False,
        ),
        (
            "Gold Rate Today in Kanpur",
            "Gold price in Kanpur today is 15142 INR per gram.",
            True,
        ),
    ],
)
def test_quick_research_requires_fetched_evidence_for_requested_scope(title, content, expected_complete):
    from charlie.research.engine import _research_brief, _update_report_coverage

    query = "What is the current gold price in Kanpur today?"
    brief = _research_brief(query, ResearchMode.QUICK)
    document = SourceDocument(
        source_id="S1", url="https://rates.example/current", title=title,
        domain="rates.example", content=content,
    )
    engine = ResearchEngine(object())
    report = ResearchReport(
        query=query,
        mode=ResearchMode.QUICK,
        brief=brief,
        coverage=brief.required_subquestions,
        sources=[document],
        citations=assign_citations([document]),
        evidence=engine._extract_evidence([document], query, policy=brief.source_policy),
    )
    report.bind_passages()

    assert report.evidence and report.passages
    assert _update_report_coverage(report) is expected_complete
    scope = next(item for item in report.coverage if item.required_fields[0] == "scope:Kanpur")
    assert (scope.status == "supported") is expected_complete


@pytest.mark.asyncio
async def test_static_miss_browser_fallback_produces_bound_research_evidence(monkeypatch):
    import charlie.research.engine as engine_module

    result = SearchResult("Alpha retry guide", "https://alpha.example/docs/retries", provider="test")
    document = SourceDocument(
        source_id="S1",
        url=result.url,
        title=result.title,
        domain="alpha.example",
        source_class="official",
        content="Alpha retries failed requests with exponential backoff.",
    )
    outcomes = {"static": 0, "browser": 0}

    async def static_miss(_result, *, status_out, **_kwargs):
        outcomes["static"] += 1
        status_out.append(403)
        return None

    async def browser_read(_result):
        outcomes["browser"] += 1
        return document

    engine = ResearchEngine(object(), browser_fetch=browser_read)

    monkeypatch.setattr(engine_module, "fetch_document", static_miss)

    fetched = await engine._fetch_one(result, ResearchMode.STANDARD)
    assert fetched is document
    query = "How does Alpha retry failed requests?"
    report = ResearchReport(
        query=query,
        mode=ResearchMode.STANDARD,
        brief=ResearchBrief(topic="Alpha retries", entity_kind="general"),
        sources=[fetched],
        citations=assign_citations([fetched]),
        evidence=engine._extract_evidence([fetched], query, policy="any_reputable"),
    )
    report.bind_passages()
    answer = report.deterministic_answer()

    counts = {
        "search_result_count": 1,
        "document_count": len(report.sources),
        "evidence_count": len(report.evidence),
        "passage_count": len(report.passages),
        "fact_count": len(report.facts),
    }
    assert outcomes == {"static": 1, "browser": 1}, counts
    assert counts["document_count"] == 1, counts
    assert counts["evidence_count"] > 0, counts
    assert counts["passage_count"] == counts["evidence_count"], counts
    assert "exponential backoff" in answer
    assert "[S1]" in answer
    assert validate_claim_support(answer, report)


@pytest.mark.asyncio
async def test_static_readable_source_produces_verified_cited_answer():
    query = "How does Alpha retry failed requests?"
    document = SourceDocument(
        source_id="S1",
        url="https://alpha.example/docs/retries",
        title="Alpha retry guide",
        domain="alpha.example",
        source_class="official",
        content="Alpha retries failed requests with exponential backoff.",
    )
    engine = ResearchEngine(object())
    report = ResearchReport(
        query=query,
        mode=ResearchMode.STANDARD,
        brief=ResearchBrief(topic="Alpha retries", entity_kind="general"),
        sources=[document],
        citations=assign_citations([document]),
        evidence=engine._extract_evidence([document], query, policy="any_reputable"),
    )
    report.bind_passages()

    answer = report.deterministic_answer()

    assert "exponential backoff" in answer
    assert "[S1]" in answer
    assert validate_claim_support(answer, report)


@pytest.mark.asyncio
async def test_optional_query_planner_failure_keeps_deterministic_plan(monkeypatch):
    async def broken_planner(_query):
        raise TimeoutError("planner unavailable")

    engine = ResearchEngine(
        SimpleNamespace(research_max_search_queries=8), query_planner=broken_planner
    )
    observed = []

    async def search(plan):
        observed.append(plan)
        return [SearchResult("Alpha docs", "https://alpha.example/docs", provider="test")]

    async def no_documents(*_args, **_kwargs):
        return []

    monkeypatch.setattr(engine, "_search", search)
    monkeypatch.setattr(engine, "_fetch_sources", no_documents)

    report = await engine._run_inner("Investigate Alpha API behavior", ResearchMode.STANDARD, None)

    assert observed and observed[0].queries
    assert report.search_results
    assert report.stop_reason == "insufficient-evidence"


@pytest.mark.asyncio
async def test_explicit_sustained_general_research_runs_targeted_followup(monkeypatch):
    engine = ResearchEngine(
        SimpleNamespace(
            research_enabled=True,
            research_total_timeout_sustained_s=2,
            research_synthesis_reserve_sustained_s=0.1,
            research_max_search_queries=12,
            research_max_sources=24,
            research_max_iterations=6,
        )
    )
    monkeypatch.setattr(
        engine,
        "decide",
        lambda *_args, **_kwargs: SimpleNamespace(
            should_research=True, mode=ResearchMode.STANDARD, reason="test"
        ),
    )
    searches = []

    async def search(plan):
        searches.append(plan)
        if len(searches) == 1:
            return [SearchResult("Seed", "https://alpha.example/start", provider="test")]
        return [SearchResult("Verification", "https://alpha.example/verify", provider="test")]

    async def fetch(results, _mode, **_kwargs):
        if results[0].title == "Seed":
            return []
        return [
            SourceDocument(
                source_id="S1",
                url=results[0].url,
                title="Alpha API behavior",
                domain="alpha.example",
                source_class="official",
                content="Alpha API behavior uses a bounded queue for request processing.",
            )
        ]

    async def rank(documents, _plan, _mode):
        return list(documents)

    monkeypatch.setattr(engine, "_search", search)
    monkeypatch.setattr(engine, "_fetch_sources", fetch)
    monkeypatch.setattr(engine, "_rank_sources", rank)

    report = await engine.run("Investigate Alpha API behavior", sustained=True)

    assert report.mode is ResearchMode.DEEP
    assert len(searches) >= 2
    assert any(item.purpose == "evidence gap" for item in searches[1].queries)


@pytest.mark.asyncio
async def test_search_results_without_fetchable_evidence_refuse_with_instrumented_counts(monkeypatch):
    engine = ResearchEngine(SimpleNamespace(research_enabled=True))
    monkeypatch.setattr(
        engine,
        "decide",
        lambda *_args, **_kwargs: SimpleNamespace(
            should_research=True, mode=ResearchMode.STANDARD, reason="test"
        ),
    )

    async def search(_plan):
        return [SearchResult("Snippet only", "https://alpha.example/page", snippet="Alpha API details")]

    async def no_documents(*_args, **_kwargs):
        return []

    monkeypatch.setattr(engine, "_search", search)
    monkeypatch.setattr(engine, "_fetch_sources", no_documents)

    report = await engine.run("Investigate Alpha API behavior")
    counts = {
        "search_result_count": len(report.search_results),
        "document_count": len(report.sources),
        "evidence_count": len(report.evidence),
        "passage_count": len(report.passages),
        "fact_count": len(report.facts),
        "stop_reason": report.stop_reason,
    }

    assert counts == {
        "search_result_count": 1,
        "document_count": 0,
        "evidence_count": 0,
        "passage_count": 0,
        "fact_count": 0,
        "stop_reason": "insufficient-evidence",
    }, counts
    assert "Unresolved:" in report.deterministic_answer()


@pytest.mark.asyncio
async def test_fetched_source_survives_when_evidence_heuristic_misses(monkeypatch):
    engine = ResearchEngine(SimpleNamespace(research_enabled=True))
    monkeypatch.setattr(
        engine,
        "decide",
        lambda *_args, **_kwargs: SimpleNamespace(
            should_research=True, mode=ResearchMode.STANDARD, reason="test"
        ),
    )

    async def search(_plan):
        return [SearchResult("Alpha guide", "https://alpha.example/guide", provider="test")]

    async def fetch(*_args, **_kwargs):
        return [
            SourceDocument(
                source_id="S1",
                url="https://other.example/guide",
                title="General guide",
                domain="other.example",
                content="The application coordinates remote work through an adjustable worker queue.",
            )
        ]

    monkeypatch.setattr(engine, "_search", search)
    monkeypatch.setattr(engine, "_fetch_sources", fetch)

    report = await engine.run("Investigate Alpha crawl concurrency behavior")

    assert len(report.sources) == 1
    assert report.sources[0].relevance_score == 0
    assert report.evidence == []
    assert report.passages == []
    assert report.stop_reason == "insufficient-evidence"
    assert "unresolved:" in report.deterministic_answer().casefold()


def test_retained_passage_accepts_valid_paraphrase_and_rejects_unsupported_claim():
    query = "How does Alpha retry failures?"
    document = SourceDocument(
        source_id="S1",
        url="https://alpha.example/retries",
        title="Retry behavior",
        domain="alpha.example",
        source_class="official",
        content="Alpha retries failed requests following intermittent errors.",
    )
    engine = ResearchEngine(object())
    report = ResearchReport(
        query=query,
        mode=ResearchMode.STANDARD,
        brief=ResearchBrief(topic="Alpha retries", entity_kind="general"),
        sources=[document],
        citations=assign_citations([document]),
        evidence=engine._extract_evidence([document], query, policy="any_reputable"),
    )
    report.bind_passages()

    assert report.passages
    assert validate_claim_support(
        "Alpha makes repeated attempts after transient failures [S1].", report
    )
    assert not validate_claim_support(
        "Alpha requires authentication for every request [S1].", report
    )
    assert not validate_claim_support(
        "Alpha does not retry failed requests [S1].", report
    )


@pytest.mark.asyncio
async def test_browser_egress_proxy_pins_public_peer(monkeypatch):
    import charlie.research.crawler as crawler

    connections = []

    async def resolve(url, *, timeout_s):
        return "https://docs.example/guide", ("93.184.216.34",)

    class Writer:
        closed = False

        def get_extra_info(self, name):
            return ("93.184.216.34", 443) if name == "peername" else None

        def close(self):
            self.closed = True

        async def wait_closed(self):
            pass

    writer = Writer()

    async def open_connection(host, port):
        connections.append((host, port))
        return object(), writer

    monkeypatch.setattr(crawler, "_resolve_public_url_async", resolve)
    monkeypatch.setattr(crawler.asyncio, "open_connection", open_connection)

    canonical, reader, connected_writer = await crawler._open_pinned_target(
        "https://docs.example/guide"
    )

    assert canonical == "https://docs.example/guide"
    assert reader is not None
    assert connected_writer is writer
    assert connections == [("93.184.216.34", 443)]


@pytest.mark.asyncio
async def test_browser_egress_proxy_rejects_private_url_and_rebound_peer(monkeypatch):
    import charlie.research.crawler as crawler

    connects = []

    async def reject_private(_url, *, timeout_s):
        raise ValueError("Research URL targets a private or local network")

    async def unexpected_connect(host, port):
        connects.append((host, port))
        raise AssertionError("private destinations must be rejected before connect")

    monkeypatch.setattr(crawler, "_resolve_public_url_async", reject_private)
    monkeypatch.setattr(crawler.asyncio, "open_connection", unexpected_connect)

    with pytest.raises(ValueError, match="private"):
        await crawler._open_pinned_target("http://127.0.0.1/admin")
    assert connects == []

    async def resolve_public(url, *, timeout_s):
        return url, ("93.184.216.34",)

    class ReboundWriter:
        closed = False

        def get_extra_info(self, name):
            return ("10.0.0.8", 443) if name == "peername" else None

        def close(self):
            self.closed = True

        async def wait_closed(self):
            pass

    rebound = ReboundWriter()

    async def rebound_connect(_host, _port):
        return object(), rebound

    monkeypatch.setattr(crawler, "_resolve_public_url_async", resolve_public)
    monkeypatch.setattr(crawler.asyncio, "open_connection", rebound_connect)

    with pytest.raises(ValueError, match="validated public address"):
        await crawler._open_pinned_target("https://docs.example/guide")
    assert rebound.closed
