"""Truthfulness contracts for research confidence and scraped-text hygiene.

Two defects are encoded here:

1. ``ResearchReport.confidence`` was a count proxy
   (``min(1.0, len(evidence)/8 * 0.6 + len(citations)/5 * 0.4)``) that reported
   1.00 for a run where 12 pages were fetched, 1 survived, and 40 sentences came
   out of that single page.  A single source must never read as high confidence.

2. Extraction junk (``NaN``, ``undefined``, ``Loading...``) survived extraction,
   became an ``EvidenceItem``, and was quoted to the user as if it were content.
   A real delivered answer contained "the growth rate figure shows as `NaN`
   (likely a data issue on the source side) [S2]".

Tests drive the real engine path (``_fetch_one`` -> ``_fetch_sources`` ->
``build_evidence``) rather than only the helper, so they fail if the guarantee
is ever removed from the pipeline and not just from the function.
"""

from types import SimpleNamespace

import pytest

from charlie.research.engine import (
    ResearchEngine,
    compute_confidence,
    grounded_domain_count,
    organisational_domain,
    sanitize_source_text,
)
from charlie.research.models import (
    EvidenceItem,
    ResearchMode,
    ResearchReport,
    SearchResult,
    SourceDocument,
)

QUERY = "quantum battery capacity report"
GOOD_SENTENCE = "The quantum battery capacity reached 120 Wh per kilogram in the trial."

# A single page can be made to yield any number of "evidence" items; that is
# precisely the failure the count proxy mistook for corroboration.
VERBOSE_BODY = "\n".join(
    f"The quantum battery capacity reading {index} was recorded by the laboratory."
    for index in range(40)
)


def count_proxy(evidence_count, citation_count):
    """The removed formula, kept here so tests can assert it is NOT in use."""
    return min(1.0, (evidence_count / 8.0) * 0.6 + (citation_count / 5.0) * 0.4)


def _config(**overrides):
    base = {
        "research_enabled": True,
        "research_max_search_queries": 2,
        "research_max_sources": 12,
        "research_max_pages_per_domain": 8,
        "research_max_concurrency": 2,
        "research_market": "IN",
        "research_locale": "en-IN",
        "research_fetch_timeout_s": 1,
        "research_crawl_enabled": False,
        "research_total_timeout_quick_s": 10,
        "research_total_timeout_standard_s": 10,
        "research_total_timeout_deep_s": 10,
        "research_currency": "INR",
        # Sized so a single verbose page really can produce 40 evidence items,
        # which is the shape of the run that previously logged confidence 1.00.
        "research_evidence_per_source": 30,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _Provider:
    name = "fake"

    def __init__(self, results):
        self._results = list(results)

    async def search(self, query, *, limit, domain_filters=None):
        return list(self._results[:limit])


def _source(source_id, domain, body=VERBOSE_BODY, url=None):
    return SourceDocument(
        source_id=source_id,
        url=url or f"https://{domain}/{source_id}",
        canonical_url=url or f"https://{domain}/{source_id}",
        title=f"{domain} report",
        domain=domain,
        content=body,
        quality_score=0.8,
    )


async def _run(monkeypatch, results, bodies, **config_overrides):
    """Run the real engine over fake results whose pages have known content."""
    import charlie.research.engine as engine_module

    by_domain = dict(bodies)

    async def fake_fetch(result, **_kwargs):
        body = by_domain.get(result.domain)
        if body is None:
            return None
        return _source("", result.domain, body, url=result.url)

    monkeypatch.setattr(engine_module, "fetch_document", fake_fetch)
    engine = ResearchEngine(_config(**config_overrides))
    monkeypatch.setattr(engine, "_providers", lambda: [_Provider(results)])
    return await engine.run(QUERY, "standard")


# --------------------------------------------------------------------------
# 1. A single source must not produce high confidence
# --------------------------------------------------------------------------


def test_single_source_with_forty_evidence_items_is_not_high_confidence():
    """The exact measured failure: 1 source, 40 evidence, previously 1.00."""
    evidence = [EvidenceItem("S1", GOOD_SENTENCE) for _ in range(40)]

    confidence = compute_confidence(evidence, [_source("S1", "example.com")])

    assert confidence is not None
    assert confidence < 0.5, f"one source reported confidence {confidence}"
    # 40 items from one publisher is still one publisher.
    assert confidence == pytest.approx(1 / 3)


@pytest.mark.asyncio
async def test_engine_run_with_one_surviving_source_reports_low_confidence(monkeypatch):
    results = [SearchResult("Only page", "https://example.com/report")]
    report = await _run(monkeypatch, results, {"example.com": VERBOSE_BODY})

    assert report.evidence, "sanity: the page should still produce evidence"
    assert report.confidence is not None
    assert report.confidence < 0.5, f"single-source run reported {report.confidence}"
    # Independent of how many items were scraped: the count proxy is gone.
    assert report.confidence != count_proxy(len(report.evidence), len(report.citations))


def test_subdomains_of_one_publisher_do_not_count_as_corroboration():
    """blog./www./docs. on one publisher is one source, not three."""
    evidence = [EvidenceItem(source_id, GOOD_SENTENCE) for source_id in ("S1", "S2", "S3")]
    sources = [
        _source("S1", "blog.example.com"),
        _source("S2", "www.example.com"),
        _source("S3", "docs.example.com"),
    ]

    assert compute_confidence(evidence, sources) == pytest.approx(1 / 3)


def test_organisational_domain_collapses_subdomains_and_two_label_suffixes():
    assert organisational_domain("Blog.Example.COM") == "example.com"
    assert organisational_domain("example.com:8443") == "example.com"
    assert organisational_domain("news.bbc.co.uk") == "bbc.co.uk"
    assert organisational_domain("") == ""
    assert organisational_domain("localhost") == "localhost"


# --------------------------------------------------------------------------
# 2. More distinct domains -> higher confidence
# --------------------------------------------------------------------------


def test_three_distinct_domains_outrank_one_domain_all_else_equal():
    evidence = [EvidenceItem("S1", GOOD_SENTENCE) for _ in range(40)]
    single = compute_confidence(evidence, [_source("S1", "one.example")])

    multi_evidence = []
    multi_sources = []
    for index, domain in enumerate(("one.example", "two.example", "three.example"), start=1):
        source_id = f"S{index}"
        multi_evidence.extend(EvidenceItem(source_id, GOOD_SENTENCE) for _ in range(40 // 3 + 1))
        multi_sources.append(_source(source_id, domain))
    multi = compute_confidence(multi_evidence, multi_sources)

    assert single is not None and multi is not None
    assert multi > single
    assert multi == pytest.approx(0.6)


def test_confidence_increases_monotonically_with_independent_domains():
    scores = []
    for count in range(1, 7):
        evidence = [EvidenceItem(f"S{i}", GOOD_SENTENCE) for i in range(count)]
        sources = [_source(f"S{i}", f"host{i}.example") for i in range(count)]
        scores.append(compute_confidence(evidence, sources))

    assert all(a < b for a, b in zip(scores, scores[1:])), scores
    assert scores[-1] < 1.0, "web research can never claim certainty"


@pytest.mark.asyncio
async def test_engine_run_with_three_domains_beats_one_domain(monkeypatch):
    bodies = {domain: VERBOSE_BODY for domain in ("one.example", "two.example", "three.example")}
    results = [SearchResult("Page", f"https://{domain}/report") for domain in bodies]

    report = await _run(monkeypatch, results, bodies)

    assert report.confidence is not None
    assert grounded_domain_count(report.evidence, report.sources) == 3
    assert report.confidence > 1 / 3
    assert report.confidence != count_proxy(len(report.evidence), len(report.citations))


def test_fetched_but_ungrounded_pages_cannot_inflate_confidence(monkeypatch):
    """A page that produced no evidence must not count as a corroborating source."""
    evidence = [EvidenceItem("S1", GOOD_SENTENCE) for _ in range(40)]
    sources = [
        _source("S1", "grounded.example"),
        _source("S2", "fetched-but-unused.example"),
    ]

    assert compute_confidence(evidence, sources) == pytest.approx(1 / 3)


# --------------------------------------------------------------------------
# 3. Unmeasurable is None, not a fabricated float
# --------------------------------------------------------------------------


def test_unmeasurable_returns_none_not_a_float():
    assert compute_confidence([], []) is None
    assert compute_confidence([EvidenceItem("S1", GOOD_SENTENCE)], []) is None
    # Evidence that cannot be traced to any resolvable publisher.
    orphan = SourceDocument(source_id="S1", url="", domain="")
    assert compute_confidence([EvidenceItem("S1", GOOD_SENTENCE)], [orphan]) is None


def test_report_confidence_defaults_to_none():
    """0.0 is a measurement; None is 'unknown'.  The default must say unknown."""
    report = ResearchReport(query=QUERY, mode=ResearchMode.STANDARD)

    assert report.confidence is None


@pytest.mark.asyncio
async def test_engine_run_without_grounded_evidence_leaves_confidence_none(monkeypatch):
    results = [SearchResult("Junk", "https://junk.example/report")]
    report = await _run(
        monkeypatch,
        results,
        {"junk.example": "Gardening tips for spring bulbs are unrelated to batteries."},
    )

    assert report.evidence == []
    assert report.confidence is None
    assert report.stop_reason == "insufficient-evidence"


# --------------------------------------------------------------------------
# 4. Extraction junk must never become an EvidenceItem
# --------------------------------------------------------------------------


def test_sanitize_drops_js_literals_and_placeholders():
    dirty = (
        "Loading...\n"
        "The growth rate figure shows as NaN in the table.\n"
        "Battery capacity was undefined for the second trial.\n"
        "JavaScript is disabled. Please enable JavaScript to continue.\n"
        "&nbsp;\n"
        "{{\n"
        "The measured capacity reached 120 Wh per kilogram.\n"
    )

    cleaned = sanitize_source_text(dirty)

    for junk in ("NaN", "undefined", "Loading", "JavaScript is disabled", "&nbsp;", "{{"):
        assert junk not in cleaned, f"{junk!r} survived: {cleaned!r}"
    assert "120 Wh per kilogram" in cleaned


def test_sanitiser_keeps_legitimate_prose_that_looks_like_junk():
    keep = (
        "The null hypothesis was not rejected for the quantum battery capacity test.\n"
        "The result was a null result for capacity scaling.\n"
        "Undefined behaviour in the capacity model is documented upstream.\n"
        "The capacitor is 40 nanometres wide.\n"
        "Loading the battery model improved capacity estimates by 3 percent.\n"
    )

    cleaned = sanitize_source_text(keep)

    for phrase in ("null hypothesis", "null result", "Undefined behaviour", "40 nanometres", "Loading the battery model"):
        assert phrase in cleaned, f"dropped legitimate prose: {phrase!r} -> {cleaned!r}"


def test_junk_document_yields_no_evidence_item_containing_junk(monkeypatch):
    """Drive the real pipeline: fetch -> sanitise -> build_evidence."""
    from charlie.research.evidence import build_evidence

    body = (
        "The quantum battery capacity figure shows as NaN in this report.\n"
        "The quantum battery capacity field is undefined here.\n"
        "Loading...\n"
        "The quantum battery capacity reached 120 Wh per kilogram in the trial.\n"
    )
    document = _source("S1", "example.com", body)

    fetched = asyncio_run(_fetch_document_probe(document))
    evidence = build_evidence([fetched], QUERY)

    assert evidence, "sanity: the clean sentence should still produce evidence"
    for item in evidence:
        for junk in ("NaN", "undefined", "Loading"):
            assert junk not in item.statement, f"junk {junk!r} leaked into {item.statement!r}"


def _fetch_document_probe(document):
    """Fetch one document through the engine's real _fetch_one seam."""
    import asyncio

    async def run():
        engine = ResearchEngine(_config())
        result = SearchResult("Page", document.url)
        import charlie.research.engine as engine_module

        original = engine_module.fetch_document

        async def fake_fetch(_result, **_kwargs):
            return document

        engine_module.fetch_document = fake_fetch
        try:
            return await engine._fetch_one(result, ResearchMode.STANDARD)
        finally:
            engine_module.fetch_document = original

    return run()


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


@pytest.mark.asyncio
async def test_engine_run_drops_junk_from_delivered_evidence(monkeypatch):
    bodies = {
        "example.com": (
            "The quantum battery capacity figure shows as NaN in the table.\n"
            "Loading...\n"
            "The quantum battery capacity reached 120 Wh per kilogram in the trial.\n"
        )
    }
    results = [SearchResult("Page", "https://example.com/report")]

    report = await _run(monkeypatch, results, bodies)

    assert report.evidence
    for item in report.evidence:
        for junk in ("NaN", "Loading"):
            assert junk not in item.statement, f"junk {junk!r} leaked into {item.statement!r}"
    assert "NaN" not in report.prompt_context()


@pytest.mark.asyncio
async def test_junk_only_page_is_rejected_so_it_cannot_be_cited(monkeypatch):
    bodies = {"junk.example": "NaN undefined Loading... {{}} &nbsp;\n"}
    results = [SearchResult("Junk", "https://junk.example/report")]

    report = await _run(monkeypatch, results, bodies)

    assert report.evidence == []
    assert report.confidence is None
