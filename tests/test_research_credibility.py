"""Credibility scoring, semantic relevance, and confidence-blending contracts.

These are deterministic-logic tests: they use synthetic documents and fakes, so
per tests/AGENTS.md they are TEST/MOCK evidence and never prove live provider,
network, or embedding-service behaviour.
"""

from __future__ import annotations

import pytest

from charlie.research.credibility import rank_prior, score_credibility
from charlie.research.engine import compute_confidence
from charlie.research.models import EvidenceItem, SourceDocument

RESEARCH_REPORT = """
Methodology

We surveyed 4,812 enterprise deployments across 19 industries between January
and June 2025. Median reported cost reduction was 23%, with an interquartile
range of 11% to 38%.

Findings

- 62% of respondents reported a production deployment in at least one function.
- Median time to first production use fell from 9 months in 2023 to 4 months.
- Organisations with a named executive owner reached production 2.1x faster.
- Accuracy on structured extraction tasks reached a mean of 94.2%.

Discussion

The observed acceleration is concentrated in organisations that restructured
ownership rather than those that only increased tooling budget.
"""

CONTENT_FARM = """
Subscribe

Sign up for our newsletter to get more updates like this. Click here to read
more. Subscribe now.

- Top 10 AI tools you must try in 2026
- Amazing AI trends that will blow your mind
- Best AI website generators of the year
- Cheap AI chatbots you can use today
- How to make money with artificial intelligence
- The ultimate guide to machine learning

Subscribe

Our content is brought to you by our sponsors. Click here to read more.
Sign up for our newsletter today. Subscribe now. Click here to read more.
Sign up for our newsletter today. Subscribe now. Click here to read more.
"""


# --------------------------------------------------------------------------
# Credibility signals
# --------------------------------------------------------------------------


def test_data_rich_report_outranks_content_farm():
    report = score_credibility(RESEARCH_REPORT, "hai.stanford.edu")
    farm = score_credibility(CONTENT_FARM, "blog.buildfastwithai.com")
    assert report.score > farm.score
    assert report.factual > farm.factual


def test_padding_does_not_inflate_score():
    """The original defect: quality was len(text)/4000, so padding won."""
    short_factual = score_credibility(
        "Revenue rose 27% to $4.1 billion across 512 customers in 2025.", "example.com"
    )
    padded = score_credibility(
        ("Lorem ipsum dolor sit amet consectetur adipiscing elit. " * 120),
        "example.com",
    )
    assert short_factual.score > padded.score


def test_boilerplate_fraction_lowers_substance():
    """Adding marketing chrome around real content must lower the signal."""
    body = (
        "Revenue rose 27% to $4.1 billion across 512 customers in 2025. "
        "Median latency fell to 40ms across 9 regions. "
        "Churn declined 4 points among 88 enterprise accounts."
    )
    clean = score_credibility(body, "a.com")
    padded = score_credibility(
        body + " Subscribe to our newsletter for weekly updates. " * 15, "a.com"
    )
    assert clean.substance > padded.substance
    assert padded.boilerplate_count > clean.boilerplate_count
    assert clean.score > padded.score


def test_publisher_prior_is_weak_not_an_oracle():
    """Authority correlates only moderately with citation, so bad content on a
    strong domain must still score badly."""
    padded_filler = "Subscribe. Click here to read more. " * 40
    gov = score_credibility(padded_filler, "nasa.gov")
    assert gov.publisher == 1.0
    assert gov.score < 0.35


def test_empty_and_whitespace_score_zero():
    for value in ("", "   \n\t  "):
        signals = score_credibility(value, "example.com")
        assert signals.score == 0.0
        assert signals.fact_count == 0


def test_score_is_always_bounded():
    for text in (RESEARCH_REPORT, CONTENT_FARM, "a", "x" * 50000):
        signals = score_credibility(text, "example.com")
        assert 0.0 <= signals.score <= 1.0
        for field in ("factual", "substance", "completeness", "structure", "publisher"):
            assert 0.0 <= getattr(signals, field) <= 1.0


def test_explain_is_diagnostic():
    text = score_credibility(RESEARCH_REPORT, "hai.stanford.edu").explain()
    for token in ("score=", "factual=", "facts=", "boilerplate="):
        assert token in text


def test_rank_prior_decays_but_never_vanishes():
    """The old bonus was max(0, 0.04 - rank*0.005): zero past rank 8."""
    assert rank_prior(1) > rank_prior(5) > rank_prior(20) > rank_prior(100)
    assert rank_prior(100) > 0.0
    assert rank_prior(0) == rank_prior(1)


# --------------------------------------------------------------------------
# Confidence blending
# --------------------------------------------------------------------------


def _document(source_id: str, domain: str, quality: float) -> SourceDocument:
    return SourceDocument(
        source_id=source_id, domain=domain, url=f"https://{domain}/x",
        quality_score=quality,
    )


def test_confidence_requires_corroboration_not_source_count():
    single = compute_confidence(
        [EvidenceItem("a", "claim", confidence=0.9)], [_document("a", "one.com", 0.9)]
    )
    many = compute_confidence(
        [EvidenceItem(f"s{i}", "claim") for i in range(12)],
        [_document(f"s{i}", f"site{i}.com", 0.9) for i in range(12)],
    )
    assert single is not None and many is not None
    assert many > single


def test_confidence_penalises_many_low_credibility_domains():
    """Quality-blind counting scored 12 content farms the same as 12 credible
    sources. Credibility now participates."""
    credible = compute_confidence(
        [EvidenceItem(f"s{i}", "claim") for i in range(12)],
        [_document(f"s{i}", f"site{i}.com", 0.85) for i in range(12)],
    )
    farms = compute_confidence(
        [EvidenceItem(f"s{i}", "claim") for i in range(12)],
        [_document(f"s{i}", f"farm{i}.com", 0.10) for i in range(12)],
    )
    assert credible is not None and farms is not None
    assert credible > farms


def test_confidence_is_none_when_unmeasurable():
    assert compute_confidence([], []) is None
    assert compute_confidence([EvidenceItem("missing", "claim")], []) is None


# --------------------------------------------------------------------------
# Soft-404 rejection
# --------------------------------------------------------------------------


def test_soft_404_page_returned_with_200_is_discarded():
    """A branded error page clears a length gate and was being cited as a source."""
    from charlie.research.fetch import document_from_content
    from charlie.research.models import SearchResult

    chrome = (
        "IBV website IBM Institute for Business Value left arrow button Featured "
        "Technologies Industries Roles/Functions More Benchmarking right arrow "
        "button Subscribe Subscribe Our data shows... this page is missing. "
        "You can find plenty of research waiting for you on the IBV homepage."
    )
    result = SearchResult("Report", "https://www.ibm.com/gone", domain="www.ibm.com")
    assert document_from_content(result, chrome, extraction_method="trafilatura") is None


def test_real_content_with_the_same_host_is_still_kept():
    from charlie.research.fetch import document_from_content
    from charlie.research.models import SearchResult

    body = (
        "Median revenue rose 27% to $4.1 billion across 512 customers in 2025. "
        "Deployment time fell from 9 months to 4 months. "
        "Accuracy on extraction tasks reached 94.2%."
    )
    result = SearchResult("Report", "https://www.ibm.com/real", domain="www.ibm.com")
    document = document_from_content(result, body, extraction_method="trafilatura")
    assert document is not None
    assert document.quality_score > 0.0


# --------------------------------------------------------------------------
# Document ranking
# --------------------------------------------------------------------------


def _plan():
    from charlie.research.models import ResearchMode, ResearchPlan

    return ResearchPlan(
        goal="latest ai trends 2026", mode=ResearchMode.STANDARD, queries=[]
    )


def _text(*terms: str) -> str:
    return " ".join(terms) + ". This article discusses these topics at length."


def _doc(source_id, domain, url, relevance, quality, content=None):
    return SourceDocument(
        source_id=source_id,
        domain=domain,
        url=url,
        canonical_url=url,
        relevance_score=relevance,
        quality_score=quality,
        content=content or _text("latest", "ai", "trends", "2026"),
    )


@pytest.mark.asyncio
async def test_semantic_relevance_beats_token_overlap_for_off_topic_pages():
    """The measured failure: a cybersecurity page scored perfect token overlap
    (1.000) and outranked the on-topic AI Index report (0.667). Sweeping every
    weighting of relevance against credibility shows no weighting fixes both
    that case and its inverse, so the relevance signal itself must change.
    """
    from charlie.research.ranking import rank_documents
    from charlie.research.semantics import SemanticRelevance

    # Mirrors the observed run: the off-topic page carries every query term,
    # the on-topic page carries three of four.
    off_topic = _doc(
        "off", "fortinet.com", "https://fortinet.com/a", 0.0, 0.546,
        _text("latest", "ai", "trends", "2026", "cybersecurity"),
    )
    on_topic = _doc(
        "on", "hai.stanford.edu", "https://hai.stanford.edu/a", 0.0, 0.657,
        _text("ai", "trends", "2026", "index", "report"),
    )

    token_only = await rank_documents([off_topic, on_topic], _plan(), 5)
    assert off_topic.relevance_score == pytest.approx(1.0)
    assert on_topic.relevance_score < 1.0
    assert [d.source_id for d in token_only][0] == "off"

    semantic = SemanticRelevance({"off": 0.31, "on": 0.92}, "semantic")
    with_semantics = await rank_documents(
        [off_topic, on_topic], _plan(), 5, semantic=semantic
    )
    assert [d.source_id for d in with_semantics][0] == "on"


@pytest.mark.asyncio
async def test_semantic_relevance_admits_a_page_tokens_alone_would_drop():
    """Promotion path: a page sharing no query token would be filtered out by
    the relevance>0 gate, and survives only because semantics score it."""
    from charlie.research.ranking import rank_documents
    from charlie.research.semantics import SemanticRelevance

    token_match = _doc("tm", "a.com", "https://a.com/1", 0.0, 0.50,
                       _text("latest", "ai", "trends", "2026"))
    unrelated = _doc("un", "b.com", "https://b.com/2", 0.0, 0.50,
                     _text("machine", "learning", "adoption", "survey"))

    token_only = await rank_documents([token_match, unrelated], _plan(), 5)
    assert [d.source_id for d in token_only] == ["tm"]

    semantic = SemanticRelevance({"tm": 0.18, "un": 0.95}, "semantic")
    promoted = await rank_documents(
        [token_match, unrelated], _plan(), 5, semantic=semantic
    )
    assert "un" in [d.source_id for d in promoted]


@pytest.mark.asyncio
async def test_semantics_never_demote_a_strong_token_match():
    """Documented limitation, asserted so it cannot regress silently.

    Semantic relevance is promote-only: it takes the maximum of token overlap
    and cosine similarity. A credible page that happens to contain every query
    term can therefore still rank first even when semantically off-topic.
    Demoting it would need a calibrated absolute threshold, and an uncalibrated
    one would risk discarding genuinely relevant pages.
    """
    from charlie.research.ranking import rank_documents
    from charlie.research.semantics import SemanticRelevance

    credible_off_topic = _doc(
        "ci", "nature.com", "https://nature.com/a", 0.0, 0.95,
        _text("latest", "ai", "trends", "2026", "protein"),
    )
    weaker = _doc(
        "wk", "blog.example", "https://blog.example/a", 0.0, 0.42,
        _text("ai", "trends", "2026", "adoption"),
    )
    semantic = SemanticRelevance({"ci": 0.05, "wk": 1.0}, "semantic")
    ranked = await rank_documents(
        [credible_off_topic, weaker], _plan(), 5, semantic=semantic
    )
    # Token overlap keeps the credible page; promotion-only semantics never demote.
    assert credible_off_topic.relevance_score == pytest.approx(1.0)
    assert [d.source_id for d in ranked][0] == "ci"


@pytest.mark.asyncio
async def test_missing_semantics_falls_back_to_token_overlap():
    from charlie.research.ranking import rank_documents
    from charlie.research.semantics import SemanticRelevance

    doc = _doc("only", "example.com", "https://example.com/a", 0.0, 0.7)
    for unavailable in (None, SemanticRelevance({}, "unavailable")):
        ranked = await rank_documents([doc], _plan(), 5, semantic=unavailable)
        assert len(ranked) == 1
        assert ranked[0].relevance_score > 0.0


@pytest.mark.asyncio
async def test_one_publisher_cannot_fill_the_citation_list():
    """While alternative publishers exist, the cap holds."""
    from charlie.research.ranking import rank_documents

    docs = [_doc(f"x{i}", "x.com", f"https://x.com/{i}", 1.0, 0.9) for i in range(4)]
    docs += [
        _doc(f"o{i}", f"other{i}.com", f"https://other{i}.com/{i}", 0.9, 0.8)
        for i in range(4)
    ]
    ranked = await rank_documents(docs, _plan(), 8)
    assert len([d for d in ranked if d.domain == "x.com"]) <= 2
    assert len([d for d in ranked if d.domain != "x.com"]) >= 4


@pytest.mark.asyncio
async def test_publisher_cap_backfills_when_no_alternative_publisher_exists():
    """Starving the result set is worse than a repeated publisher.

    Backfilling cannot overstate confidence, because confidence counts distinct
    organisational domains rather than sources, so the extra pages never read as
    corroboration.
    """
    from charlie.research.ranking import rank_documents

    docs = [_doc(f"x{i}", "x.com", f"https://x.com/{i}", 1.0, 0.9) for i in range(3)]
    ranked = await rank_documents(docs, _plan(), 4)
    assert len(ranked) == 3


@pytest.mark.asyncio
async def test_publisher_cap_holds_even_when_the_limit_is_reachable():
    """The cap wins over filling the limit from an over-represented publisher."""
    from charlie.research.ranking import rank_documents

    docs = [_doc(f"a{i}", "same.com", f"https://same.com/{i}", 1.0, 0.9) for i in range(3)]
    docs += [
        _doc(f"b{i}", f"other{i}.com", f"https://other{i}.com/{i}", 0.8, 0.7)
        for i in range(3)
    ]
    ranked = await rank_documents(docs, _plan(), 6)
    assert len([d for d in ranked if d.domain == "same.com"]) == 2
    assert len(ranked) == 5


# --------------------------------------------------------------------------
# Semantic relevance transport
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_semantic_service_failure_is_not_treated_as_zero_similarity():
    from charlie.research.semantics import gather_semantic_scores

    result = await gather_semantic_scores(
        "query",
        [("a", "text")],
        base_url="http://127.0.0.1:1/api/embed",
        model="nope",
    )
    assert result.mode == "unavailable"
    assert result.scores == {}
    assert result.score_for("a") is None


@pytest.mark.asyncio
async def test_semantic_relevance_unavailable_without_endpoint_or_model():
    from charlie.research.semantics import compute_semantic_relevance

    assert (await compute_semantic_relevance("q", [("a", "t")], base_url="")).mode == "unavailable"
    assert (
        await compute_semantic_relevance(
            "q", [("a", "t")], base_url="http://x", model=""
        )
    ).mode == "unavailable"


def test_cosine_is_bounded_and_symmetric():
    from charlie.research.semantics import _cosine

    assert _cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert _cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)
    assert _cosine([], [1.0]) == 0.0
    assert _cosine([1.0], [1.0, 2.0]) == 0.0
