"""Supporting acquisition-stop contracts, not live research acceptance."""

import asyncio
from types import SimpleNamespace

import pytest

from charlie.research.engine import ResearchEngine
from charlie.research.models import ResearchMode, ResearchReport, SearchResult, SourceDocument


@pytest.mark.asyncio
async def test_stops_fetching_when_existing_evidence_covers_request(monkeypatch):
    engine = ResearchEngine(SimpleNamespace(research_max_pages_per_domain=4))
    report = ResearchReport(query="How does a bicycle stay balanced?", mode=ResearchMode.QUICK)
    results = [SearchResult("Balance", f"https://example.com/{i}") for i in range(8)]
    fetched = []

    async def fetch(batch, mode, **kwargs):
        fetched.extend(batch)
        return [SourceDocument(
            source_id="S1", url=batch[0].url, title="Balance", domain="example.com",
            content="A bicycle stays balanced because steering keeps the center of mass above its support.",
        )]

    monkeypatch.setattr(engine, "_fetch_sources", fetch)
    monkeypatch.setattr("charlie.research.engine._update_report_coverage", lambda r: bool(r.evidence))
    documents = await engine._fetch_until_covered(results, ResearchMode.QUICK, report)
    assert len(fetched) == len(documents) == 1
    assert report.evidence


@pytest.mark.asyncio
async def test_cancelled_acquisition_does_not_fetch(monkeypatch):
    engine = ResearchEngine(SimpleNamespace(research_max_pages_per_domain=4))
    report = ResearchReport(query="A question", mode=ResearchMode.STANDARD)
    cancelled = asyncio.Event()
    cancelled.set()

    async def unexpected_fetch(*args, **kwargs):
        raise AssertionError("Cancelled work must not fetch")

    monkeypatch.setattr(engine, "_fetch_sources", unexpected_fetch)
    assert await engine._fetch_until_covered(
        [SearchResult("Source", "https://example.com")], ResearchMode.STANDARD, report, cancelled,
    ) == []
