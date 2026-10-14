import pytest

from charlie.research.citations import assign_citations, validate_claim_support, validate_numeric_grounding
from charlie.research.engine import _is_candidate_complete, _matches_candidate_variant, _update_report_coverage
from charlie.research.facts import extract_facts_from_document
from charlie.research.models import (
    Candidate,
    EvidenceItem,
    Fact,
    ResearchBrief,
    ResearchMode,
    ResearchReport,
    SourceDocument,
    Subquestion,
)


@pytest.mark.parametrize("sku,other", [("83DV00BEIN", "83DV018FIN"), ("ZX1234AB", "ZX1234CD")])
def test_exact_variant_does_not_accept_shared_family(sku, other):
    name = f"Example Model 15IRX9 {sku}"
    assert not _matches_candidate_variant(
        name, f"Example Model 15IRX9 {other}", f"https://shop.example/{other}", "16 GB DDR5 RAM"
    )
    assert _matches_candidate_variant(name, name, "https://shop.example/item", "16 GB DDR5 RAM")


def test_facts_stay_in_product_section_and_ignore_installments():
    name = "Example Model ZX1234AB"
    document = SourceDocument(
        source_id="S1",
        url="https://shop.example/ZX1234AB",
        title=name,
        source_class="retailer",
        content=(
            f"{name}\nPrice: INR 89000\n16 GB DDR5 RAM\n8 GB GDDR6 VRAM\n"
            "INR 26559 x 3m\nRelated products\nExample Model ZX1234CD\n"
            "Price: INR 120000\n32 GB DDR5 RAM\n4 GB GDDR6 VRAM"
        ),
    )
    facts = extract_facts_from_document(document, candidate_name=name, policy="any_reputable")
    assert [(f.aspect, f.value) for f in facts] == [("price", "₹89,000"), ("ram", "16 GB"), ("vram", "8 GB")]


def product_report(values):
    brief = ResearchBrief(
        topic="Compare products", entity_kind="product", source_policy="any_reputable", aspects=["ram"]
    )
    coverage = [Subquestion("ram", "Product RAM", required_fields=["ram"])]
    sources = [
        SourceDocument(
            source_id=f"S{i}",
            url=f"https://vendor{i}.example/item",
            title=name,
            domain=f"vendor{i}.example",
            source_class="retailer",
            content=f"{name}: {value} DDR5 RAM.",
        )
        for i, (name, value) in enumerate(values, 1)
    ]
    return ResearchReport(
        query=brief.topic,
        mode=ResearchMode.DEEP,
        brief=brief,
        coverage=coverage,
        sources=sources,
        citations=assign_citations(sources),
        evidence=[
            EvidenceItem(
                s.source_id,
                s.content,
                passage_id=f"p{i}",
                start_offset=0,
                end_offset=len(s.content),
                document_hash=s.content_hash,
            )
            for i, s in enumerate(sources)
        ],
        facts=[
            Fact(name, "ram", value, s.content, s.source_id, "retailer", "fixture")
            for s, (name, value) in zip(sources, values)
        ],
    )


def test_different_products_are_not_conflicting_and_partial_answer_is_grounded():
    report = product_report([("Model ZX1234AB", "16 GB"), ("Model ZX1234CD", "32 GB")])
    report.bind_passages()
    _update_report_coverage(report)
    assert not report.coverage[0].conflict
    report.gaps.append("Upgradeability was not verified.")
    report.gaps.append("Found 0 of 1 options meeting the required specifications and budget.")
    answer = report.deterministic_answer()
    assert validate_claim_support(answer, report)
    assert validate_numeric_grounding(answer, report)


def test_same_variant_disagreement_still_fails_closed():
    report = product_report([("Model ZX1234AB", "16 GB"), ("Model ZX1234AB", "32 GB")])
    report.bind_passages()
    _update_report_coverage(report)
    assert report.coverage[0].conflict
    candidate = Candidate("Model ZX1234AB", "Example", "Model ZX1234AB", "S1")
    assert not _is_candidate_complete(candidate, report.facts, report.brief)


def test_unrequested_metadata_does_not_disqualify_verified_requirement():
    report = product_report([("Widget ZX1234AB", "16 GB")])
    report.facts.extend(
        [
            Fact("Widget ZX1234AB", "version", "5.1", "Bluetooth 5.1", "S1", "retailer", "fixture"),
            Fact("Widget ZX1234AB", "version", "6.0", "Wireless 6.0", "S2", "retailer", "fixture"),
        ]
    )
    candidate = Candidate("Widget ZX1234AB", "Example", "Widget ZX1234AB", "S1")
    assert _is_candidate_complete(candidate, report.facts, report.brief)


def test_explicit_retailer_domain_does_not_become_official():
    from charlie.research.sources import classify

    brief = ResearchBrief(topic="Product", explicit_domains=["flipkart.com"])
    assert classify("https://www.flipkart.com/item", brief=brief).value == "retailer"


def test_distinct_retailer_offers_are_not_specification_conflicts():
    from charlie.research.facts import conflicting_facts

    facts = [
        Fact("Widget ZX1234AB", "price", price, price, sid, "retailer", "fixture", url=url)
        for sid, price, url in (
            ("S1", "INR 80000", "https://shop1.example/item"),
            ("S2", "INR 85000", "https://shop2.example/item"),
        )
    ]
    assert not conflicting_facts(facts)


def test_non_citable_review_price_does_not_block_retailer_evidence():
    brief = ResearchBrief(
        topic="Example Laptop price",
        entity_kind="product",
        source_policy="official_required",
        aspects=["price"],
    )
    review = SourceDocument(
        source_id="S1",
        url="https://review.example/laptop",
        title="Example Laptop price review",
        source_class="review",
        content="Example Laptop ZX1234 price: ₹90,000.",
    )
    retailer = SourceDocument(
        source_id="S2",
        url="https://store.example/laptop",
        title="Example Laptop ZX1234 listing",
        source_class="retailer",
        content="Example Laptop ZX1234 price: ₹95,000.",
    )
    sources = [review, retailer]
    report = ResearchReport(
        query=brief.topic,
        mode=ResearchMode.STANDARD,
        brief=brief,
        coverage=[Subquestion("price", "Example Laptop price", required_fields=["price"])],
        sources=sources,
        citations=assign_citations(sources),
        evidence=[
            EvidenceItem(
                source.source_id,
                source.content,
                passage_id=f"p{index}",
                start_offset=0,
                end_offset=len(source.content),
                document_hash=source.content_hash,
            )
            for index, source in enumerate(sources, start=1)
        ],
        facts=[
            Fact("Example Laptop ZX1234", "price", "₹90,000", review.content, "S1", "review", "fixture"),
            Fact("Example Laptop ZX1234", "price", "₹95,000", retailer.content, "S2", "retailer", "fixture"),
        ],
    )
    report.bind_passages()

    _update_report_coverage(report)
    answer = report.deterministic_answer()

    assert not report.coverage[0].conflict
    assert report.coverage[0].supporting_claim_ids == ["p2"]
    assert "₹95,000 [S2]" in answer
    assert "₹90,000 [S1]" not in answer
    assert validate_claim_support(answer, report)
    assert validate_numeric_grounding(answer, report)


def test_retained_fact_passages_work_without_query_word_overlap():
    from charlie.research.evidence import fact_evidence

    source = SourceDocument(
        source_id="S1",
        url="https://vendor.example/api",
        source_class="official",
        content="Requests are retained for\n  72 hours.",
    )
    fact = Fact(
        "Example API", "retention", "72 hours", "Requests are retained for 72 hours.", "S1", "official", "fixture"
    )
    report = ResearchReport(
        query="Explain lifecycle guarantees",
        mode=ResearchMode.STANDARD,
        sources=[source],
        facts=[fact],
        citations=assign_citations([source]),
        evidence=fact_evidence([source], [fact]),
    )
    report.bind_passages()
    assert len(report.passages) == 1
    assert validate_claim_support(report.deterministic_answer(), report)
    assert not validate_claim_support("Requests are retained for 96 hours. [S1]", report)


def test_multisentence_requirement_gap_does_not_erase_verified_findings():
    report = product_report([("Widget ZX1234AB", "16 GB")])
    report.bind_passages()
    report.gaps = ["Investigate memory. Verify capacity and update policy: update policy"]
    answer = report.deterministic_answer()
    assert validate_claim_support(answer, report)
    assert validate_numeric_grounding(answer, report)
    assert not validate_claim_support(answer + "\nIt includes 128 GB RAM. [S1]", report)


def test_family_name_is_not_an_exact_variant():
    assert not _matches_candidate_variant(
        "Example Widget", "Example Widget ZX1234AB", "https://shop.example/item", "16 GB DDR5 RAM"
    )


def test_numeric_model_heading_is_cited_and_category_facets_are_not_facts():
    report = product_report([("Widget Series 7", "16 GB")])
    report.bind_passages()
    assert validate_numeric_grounding(report.deterministic_answer(), report)
    source = SourceDocument(
        source_id="S1",
        title="Widget Series 7",
        url="https://shop.example/category",
        source_class="retailer",
        content="Widget Series 7\nUp to INR 86000\nUp to 159 GB",
    )
    assert not extract_facts_from_document(source, candidate_name="Widget Series 7", policy="any_reputable")


def test_fact_value_after_old_quote_limit_keeps_its_actual_source_span():
    name = "Widget ZX1234AB"
    source = SourceDocument(
        source_id="S1",
        title=name,
        url="https://vendor.example/item",
        source_class="official",
        content="Description " * 30 + "RTX 5090 graphics",
    )
    facts = extract_facts_from_document(source, candidate_name=name)
    assert len(facts) == 1
    assert "RTX 5090" in facts[0].quote
    from charlie.research.facts import verify_quote

    assert verify_quote(facts[0].quote, source)


def test_discovery_keeps_sku_from_the_quoted_product_title():
    from charlie.research.candidates import extract_candidates

    source = SourceDocument(
        source_id="D1",
        url="https://vendor.example/item",
        source_class="official",
        content="Lenovo IdeaPad Slim 3, 16GB RAM, 512GB SSD, 83UR009QIN",
    )
    candidates = extract_candidates([source], ResearchBrief(topic="laptop", entity_kind="product"))
    assert candidates
    assert "83UR009QIN" in candidates[0].name
    facts = extract_facts_from_document(source, candidate_name=candidates[0].name)
    assert any(fact.aspect == "ram" and fact.value == "16GB" for fact in facts)


def test_multiple_verified_fields_from_one_quote_all_appear_in_answer():
    from charlie.research.evidence import fact_evidence

    name = "Widget ZX1234AB"
    source = SourceDocument(
        source_id="S1",
        url="https://vendor.example/item",
        title=name,
        source_class="official",
        content=f"{name}: RTX 5090, 32 GB GDDR6 VRAM, 64 GB DDR5 RAM.",
    )
    facts = extract_facts_from_document(source, candidate_name=name)
    report = ResearchReport(
        query="Compare specifications",
        mode=ResearchMode.DEEP,
        brief=ResearchBrief(topic="Widget", entity_kind="product", aspects=["gpu", "vram", "ram"]),
        sources=[source],
        facts=facts,
        citations=assign_citations([source]),
        evidence=fact_evidence([source], facts),
    )
    report.bind_passages()
    answer = report.deterministic_answer()
    assert "32 GB" in answer and "64 GB" in answer and "RTX 5090" in answer
    assert validate_claim_support(answer, report)
    assert validate_numeric_grounding(answer, report)


def test_one_unsupported_financing_price_does_not_erase_verified_findings():
    from charlie.research.evidence import fact_evidence

    source = SourceDocument(
        source_id="S1",
        title="Widget ZX1234AB",
        url="https://vendor.example/item",
        source_class="official",
        content="Price INR 189990\nNo cost EMI INR 5699\n32 GB DDR5 RAM",
    )
    facts = [
        Fact("Widget ZX1234AB", aspect, value, quote, "S1", "official", "fixture")
        for aspect, value, quote in (
            ("price", "INR 189990", "Price INR 189990"),
            ("price", "INR 5699", "No cost EMI INR 5699"),
            ("ram", "32 GB", "32 GB DDR5 RAM"),
        )
    ]
    report = ResearchReport(
        query="Compare device specifications",
        mode=ResearchMode.DEEP,
        brief=ResearchBrief(topic="Device", entity_kind="product", aspects=["price", "ram"]),
        sources=[source],
        facts=facts,
        citations=assign_citations([source]),
        evidence=fact_evidence([source], facts),
    )
    report.bind_passages()
    answer = report.deterministic_answer()
    assert "189990" in answer and "32 GB" in answer
    assert "5699" not in answer
    assert len(report.facts) == 2
    assert validate_claim_support(answer, report)
    assert validate_numeric_grounding(answer, report)


@pytest.mark.parametrize(
    "text,expected", [("₹2 lakh", "2 lakh rupees"), ("₹2.5 crore", "2.5 crore rupees"), ("₹1,00,000", "100000 rupees")]
)
def test_currency_scale_precedes_currency_unit_in_search_and_speech(text, expected):
    from charlie.research.search import clean_query
    from charlie.text_utils import normalize_rupee_amounts

    assert normalize_rupee_amounts(text) == expected
    assert clean_query(text) == expected


def test_registry_research_progress_preserves_actual_query(monkeypatch):
    import charlie.research.engine as engine_module
    import charlie.tools as tools_module
    from charlie.execution_context import ExecutionContext, activate_execution_context, reset_execution_context
    from charlie.research.models import ResearchProgress

    received = []
    context = ExecutionContext()
    context.research_progress_callback = lambda progress, query: received.append((progress.message, query))

    class Engine:
        def __init__(self, config, progress):
            self.progress = progress

        def run_sync(self, query, mode, **kwargs):
            self.progress(ResearchProgress("reading", "Reading source 2/4"))
            return "result"

    monkeypatch.setattr(engine_module, "ResearchEngine", Engine)
    token = activate_execution_context(context)
    try:
        assert tools_module._run_research_report("web_research", {"query": "Compare API clients"}) == "result"
    finally:
        reset_execution_context(token)
    assert received == [("Reading source 2/4", "Compare API clients")]


def test_equivalent_gpu_names_do_not_conflict():
    from charlie.research.facts import conflicting_facts

    assert not conflicting_facts(
        [
            Fact("Widget ZX1234AB", "gpu", "NVIDIA GeForce RTX 5090", "RTX 5090", "S1", "retailer", "fixture"),
            Fact("Widget ZX1234AB", "gpu", "RTX5090", "RTX5090", "S2", "retailer", "fixture"),
        ]
    )
@pytest.mark.parametrize("query", ["What is the gold price today in Kanpur?", "Current stock price", "Silver price today"])
def test_market_price_lookup_does_not_require_product_specifications(query):
    from charlie.research.search import parse_brief

    brief = parse_brief(query)
    assert brief.entity_kind == "general"
    assert "specifications" not in brief.aspects

