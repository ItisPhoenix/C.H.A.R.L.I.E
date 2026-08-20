from types import SimpleNamespace
import pytest

from charlie.research.engine import ResearchEngine
from charlie.research.models import ResearchMode, SearchResult, SourceDocument
from charlie.research.shopping import extract_products, is_shopping_query, parse_budget


def test_parse_budget_defensive_handles_malformed_and_empty():
    assert parse_budget("") is None
    assert parse_budget("under ,") is None
    assert parse_budget("under ₹,") is None
    assert parse_budget("under ₹2000") == 2000.0
    assert parse_budget("below $1,500.50") == 1500.50
    assert parse_budget("less than 500") == 500.0
    assert parse_budget("within £300") == 300.0
    assert parse_budget("world news headlines August 19 2026") is None


def test_is_shopping_query_distinguishes_shopping_and_news():
    assert not is_shopping_query("world news headlines August 19 2026 major stories")
    assert not is_shopping_query("What's happening today?")
    assert not is_shopping_query("latest developments in quantum computing")
    assert not is_shopping_query("weather in Tokyo")

    assert is_shopping_query("best IEMs under ₹2000 for gaming and music")
    assert is_shopping_query("buy mechanical keyboard under $100")
    assert is_shopping_query("price of RTX 5090")
    assert is_shopping_query("recommend wireless earbuds")
    assert is_shopping_query("cheap 4k monitor")


def test_extract_products_defensive_against_malformed_empty_price_data():
    doc1 = SourceDocument(
        url="https://news.example.com/world",
        title="World News Headlines",
        domain="news.example.com",
        content="Market indices fluctuated: $, reported across regions. Oil at Rs. , while gold steady at ₹.",
    )
    doc2 = SourceDocument(
        url="https://news.example.com/article",
        title="Economy Update",
        domain="news.example.com",
        content="No valid prices here, just text with $ symbol and commas like $, , and $..",
    )
    # Must not raise ValueError or any exception; returns empty list
    products = extract_products([doc1, doc2], "world news headlines August 19 2026")
    assert products == []


def test_extract_products_extracts_valid_prices_for_shopping_query():
    doc1 = SourceDocument(
        url="https://store.example.com/iem1",
        title="Tangzu Wan'er IEM",
        domain="store.example.com",
        content="Tangzu Wan'er Studio Edition available now for ₹1,899 (discounted from ₹2,499). Great clarity.",
    )
    doc2 = SourceDocument(
        url="https://store.example.com/iem2",
        title="Expensive Pro IEM",
        domain="store.example.com",
        content="High-end studio monitor for ₹15,000.",
    )
    doc3 = SourceDocument(
        url="https://store.example.com/iem3",
        title="7Hz Zero IEM",
        domain="store.example.com",
        content="Budget king 7Hz Zero 2 at Rs. 1,999 with clean bass.",
    )
    products = extract_products(
        [doc1, doc2, doc3],
        "best IEMs under ₹2000 for gaming and music",
        currency="INR",
    )
    assert len(products) == 2
    names = [p.name for p in products]
    assert "Tangzu Wan'er IEM" in names
    assert "7Hz Zero IEM" in names
    assert all(p.price <= 2000.0 for p in products)
    assert all(p.currency == "INR" for p in products)


class _MockProvider:
    name = "mock"

    async def search(self, query, *, limit, domain_filters=None):
        return [
            SearchResult(
                title="Global News August 19",
                url="https://news.example.com/story1",
                snippet="World news and major stories for today.",
            )
        ]


@pytest.mark.asyncio
async def test_non_shopping_world_news_research_completes_without_product_extraction(monkeypatch):
    config = SimpleNamespace(
        research_enabled=True,
        research_max_search_queries=2,
        research_max_sources=2,
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
            content="Headline: Major world event occurs. Indices report $ , while inflation stays flat.",
            quality_score=0.8,
        )

    monkeypatch.setattr("charlie.research.engine.fetch_document", fake_fetch)
    engine = ResearchEngine(config)
    monkeypatch.setattr(engine, "_providers", lambda: [_MockProvider()])

    report = await engine.run("world news headlines August 19 2026 major stories", "standard")
    assert report.successful
    assert report.stop_reason == "evidence-sufficient"
    assert len(report.sources) == 1
    assert report.products == []


@pytest.mark.asyncio
async def test_optional_product_enrichment_failure_does_not_fail_research_engine(monkeypatch):
    config = SimpleNamespace(
        research_enabled=True,
        research_max_search_queries=2,
        research_max_sources=2,
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
            content="Review of keyboard for $80.",
            quality_score=0.8,
        )

    monkeypatch.setattr("charlie.research.engine.fetch_document", fake_fetch)
    engine = ResearchEngine(config)
    monkeypatch.setattr(engine, "_providers", lambda: [_MockProvider()])

    # Simulate catastrophic exception in extract_products
    def exploding_extract(*args, **kwargs):
        raise RuntimeError("Catastrophic parser crash in third-party library")

    monkeypatch.setattr("charlie.research.engine.extract_products", exploding_extract)

    report = await engine.run("buy mechanical keyboard under $100", "standard")
    # Must still succeed and return report with sources and citations
    assert report.successful
    assert report.stop_reason == "evidence-sufficient"
    assert len(report.sources) == 1
    assert report.products == []
