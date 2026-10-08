from charlie.background_task import _validate_citation_support, _validate_numeric_grounding, assemble_answer
from charlie.research.models import (
    Candidate,
    Citation,
    EvidenceItem,
    EvidencePassage,
    Fact,
    ResearchBrief,
    ResearchMode,
    ResearchReport,
    SourceDocument,
    Subquestion,
)
from charlie.research.releases import is_stable_version, pick_stable


def test_assemble_answer_formats_priority_cleanly_without_run_on_sentence():
    # Simulate a brief with 20 priorities planned by LLM
    brief = ResearchBrief(
        topic="best laptop for local AI",
        entity_kind="product",
        budget=100000.0,
        currency="INR",
        priority=[
            "gpu", "vram", "ram", "ram_upgradeable", "cpu", "thermal_design",
            "power_delivery", "storage", "display_resolution", "display_refresh_rate",
            "weight", "battery_capacity", "build_quality", "port_selection",
            "keyboard_layout", "audio_output", "wifi_standard", "bluetooth_version",
            "os_support", "warranty",
        ],
    )
    cands = [
        Candidate(name="HP Victus 15-FA2381TX Gaming", brand="HP", quote="HP Victus 15 gaming", source_id="S1"),
    ]
    facts = [
        Fact(candidate="HP Victus 15-FA2381TX Gaming", aspect="gpu", value="RTX 4050", quote="RTX 4050", source_id="S1", source_class="official", extractor="regex"),
        Fact(candidate="HP Victus 15-FA2381TX Gaming", aspect="vram", value="6 GB", quote="6 GB VRAM", source_id="S1", source_class="official", extractor="regex"),
        Fact(candidate="HP Victus 15-FA2381TX Gaming", aspect="price", value="₹78,584", quote="₹78,584", source_id="S1", source_class="retailer", extractor="regex"),
    ]
    report = ResearchReport(
        query="Charlie, research best laptop under ₹100,000",
        mode=ResearchMode.DEEP,
        brief=brief,
        candidates=cands,
        facts=facts,
    )
    result = assemble_answer(report)

    # Must NOT have the absurd run-on "then..., then..., then..."
    assert ", then " not in result
    assert "then vram, then ram, then ram_upgradeable" not in result
    # Must format top priorities cleanly
    assert "Recommended for highest GPU, VRAM, and RAM within budget." in result
    # Must populate table columns
    assert "| HP Victus 15-FA2381TX Gaming | RTX 4050 | 6 GB | — | — | ₹78,584 | [S1] |" in result


def test_general_research_fallback_uses_cited_evidence_without_product_table():
    report = ResearchReport(
        query="How does Alpha handle retries?",
        mode=ResearchMode.DEEP,
        brief=ResearchBrief(topic="Alpha retries", entity_kind="general"),
        evidence=[EvidenceItem("S1", "Alpha retries transient failures with exponential backoff.")],
        citations=[Citation("S1", "https://alpha.example/docs", "Retry guide", "alpha.example")],
    )

    answer = assemble_answer(report)

    assert "Alpha retries transient failures with exponential backoff. [S1]" in answer
    assert "Recommendation" not in answer
    assert "| Option |" not in answer


def test_assemble_answer_does_not_borrow_family_or_unassigned_source_facts():
    brief = ResearchBrief(
        topic="gaming laptops",
        entity_kind="product",
        priority=["vram", "gpu", "price"],
    )
    cands = [
        Candidate(name="Lenovo LOQ 15IAX9", brand="Lenovo", quote="Lenovo LOQ 15", source_id="S2"),
    ]
    # A family name and shared page do not bind facts to this exact variant.
    facts = [
        Fact(candidate="Lenovo LOQ", aspect="gpu", value="RTX 4060", quote="RTX 4060", source_id="S2", source_class="official", extractor="regex"),
        Fact(candidate="Lenovo LOQ", aspect="vram", value="8 GB", quote="8 GB", source_id="S2", source_class="official", extractor="regex"),
        Fact(candidate=None, aspect="price", value="₹82,990", quote="₹82,990", source_id="S2", source_class="retailer", extractor="regex"),
    ]
    report = ResearchReport(
        query="gaming laptops under ₹1,00,000",
        mode=ResearchMode.DEEP,
        brief=brief,
        candidates=cands,
        facts=facts,
    )
    result = assemble_answer(report)
    assert "| Lenovo LOQ 15IAX9 | — | — | — | — | — | [S2] |" in result
    assert "**Recommendation**" not in result


def test_validate_numeric_grounding_allows_budget_and_query_numbers():
    brief = ResearchBrief(topic="laptop under 100,000", budget=100000.0, currency="INR")
    facts = [
        Fact(candidate="HP Victus", aspect="gpu", value="RTX 4050", quote="RTX 4050", source_id="S1", source_class="official", extractor="regex"),
        Fact(candidate="HP Victus", aspect="price", value="₹78,584", quote="₹78,584", source_id="S1", source_class="retailer", extractor="regex"),
    ]
    report = ResearchReport(
        query="Charlie, research laptops under ₹100,000 and recommend one [S1].",
        mode=ResearchMode.DEEP,
        brief=brief,
        facts=facts,
    )
    # User budget and verified price are allowed; an unsupported date is not.
    good_text = "Under your ₹100,000 budget, HP Victus costs ₹78,584 [S1]."
    assert _validate_numeric_grounding(good_text, report) is True
    assert _validate_numeric_grounding("HP Victus is best in 2025 [S1].", report) is False

    # Text contains hallucinated price 45,000 not in facts, query, or budget
    bad_text = "You can also buy it for ₹45,000 at a discount [S1]."
    assert _validate_numeric_grounding(bad_text, report) is False


def test_validate_numeric_grounding_binds_numbers_to_cited_source():
    report = ResearchReport(
        query="Compare Alpha and Beta retry behavior",
        mode=ResearchMode.DEEP,
        sources=[
            SourceDocument(source_id="S1", url="https://alpha.example", content="Alpha retries 5 times."),
            SourceDocument(source_id="S2", url="https://beta.example", content="Beta retries 7 times."),
        ],
        evidence=[
            EvidenceItem("S1", "Alpha retries 5 times."),
            EvidenceItem("S2", "Beta retries 7 times."),
        ],
    )

    assert _validate_numeric_grounding("Alpha retries 5 times [S1].", report) is True
    assert _validate_numeric_grounding("Alpha retries 7 times [S1].", report) is False


def test_citation_must_support_the_attached_claim():
    report = ResearchReport(
        query="What does Alpha document about licensing?",
        mode=ResearchMode.STANDARD,
        sources=[SourceDocument(source_id="S1", url="https://alpha.example/docs", content="Alpha's license is MIT.")],
        evidence=[EvidenceItem("S1", "Alpha's license is MIT.")],
        citations=[Citation("S1", "https://alpha.example/docs", "Alpha docs", "alpha.example")],
    )

    assert _validate_citation_support("Alpha's license is MIT [S1].", report) is True
    assert _validate_citation_support("Alpha retries use exponential backoff [S1].", report) is False
    assert _validate_citation_support("Alpha's license is MIT.", report) is False


def test_contradicted_proposition_is_rendered_and_must_not_be_reversed():
    from charlie.research.citations import validate_claim_support
    from charlie.research.engine import _research_brief, _update_report_coverage

    query = "Is Alpha licensed under GPL-3.0?"
    brief = _research_brief(query, ResearchMode.DEEP)
    content = "Alpha is licensed under the MIT License."
    source = SourceDocument(
        source_id="S1", url="https://alpha.example/license", title="Alpha license",
        domain="alpha.example", content=content, source_class="official", quality_score=0.9,
    )
    report = ResearchReport(
        query=query,
        mode=ResearchMode.DEEP,
        brief=brief,
        sources=[source],
        citations=[Citation("S1", source.url, source.title, source.domain)],
        evidence=[EvidenceItem("S1", content, passage_id="p1")],
        passages=[EvidencePassage(
            "p1", "S1", source.url, source.canonical_url, source.content_hash,
            content, 0, len(content), "now", None, "official",
        )],
        coverage=brief.required_subquestions,
        stop_reason="evidence-sufficient",
    )
    assert _update_report_coverage(report) is True

    answer = report.deterministic_answer()

    assert "contradicts" in answer
    assert "MIT" in answer and "GPL-3.0" in answer
    assert validate_claim_support(answer, report) is True
    assert _validate_numeric_grounding(answer, report) is True
    assert validate_claim_support("Yes, Alpha is licensed under GPL-3.0 [S1].", report) is False
    bound_claim = next(claim for claim in report.claims if claim.claim_id.startswith("answer-"))
    assert bound_claim.relationship == "refutes"
    assert bound_claim.evidence_passage_ids == ["p1"]


def test_conflict_answer_must_retain_both_sources_and_refuse_to_choose():
    from charlie.research.citations import validate_claim_support

    source_a = SourceDocument(
        source_id="S1",
        url="https://alpha.example/release",
        content="Alpha 2.0 was released on January 1, 2024.",
    )
    source_b = SourceDocument(
        source_id="S2",
        url="https://beta.example/alpha",
        content="Alpha 2.0 was released on January 2, 2024.",
    )
    report = ResearchReport(
        query="On what date was Alpha 2.0 released?",
        mode=ResearchMode.DEEP,
        sources=[source_a, source_b],
        citations=[
            Citation("S1", "https://alpha.example/release", "Alpha release", "alpha.example"),
            Citation("S2", "https://beta.example/alpha", "Alpha history", "beta.example"),
        ],
        evidence=[
            EvidenceItem("S1", "Alpha 2.0 was released on January 1, 2024.", passage_id="p1"),
            EvidenceItem("S2", "Alpha 2.0 was released on January 2, 2024.", passage_id="p2"),
        ],
        passages=[
            EvidencePassage(
                "p1", "S1", "https://alpha.example/release", "https://alpha.example/release",
                source_a.content_hash, source_a.content, 0, len(source_a.content), "now", None, "official",
            ),
            EvidencePassage(
                "p2", "S2", "https://beta.example/alpha", "https://beta.example/alpha",
                source_b.content_hash, source_b.content, 0, len(source_b.content), "now", None, "official",
            ),
        ],
        coverage=[Subquestion(
            id="q1", question="Alpha 2.0 release date", required_fields=["release_date:2.0"],
            status="unresolved", supporting_claim_ids=["p1", "p2"], conflict=True,
            missing_evidence=["Credible sources report different dates for this exact version"],
        )],
        gaps=["Unresolved: Alpha 2.0 release date (conflicting evidence)"],
    )

    answer = report.deterministic_answer()

    assert "January 1, 2024. [S1]" in answer
    assert "January 2, 2024. [S2]" in answer
    assert validate_claim_support(answer, report) is True
    assert validate_claim_support("Alpha 2.0 was released on January 1, 2024 [S1].", report) is False
    bound_claims = [claim for claim in report.claims if claim.claim_id.startswith("answer-")]
    assert {claim.evidence_passage_ids[0] for claim in bound_claims} == {"p1", "p2"}


def test_citation_binding_keeps_dotted_month_release_dates_in_one_sentence():
    from charlie.research.citations import validate_claim_support
    from charlie.research.engine import _research_brief, _update_report_coverage
    from charlie.research.models import EvidencePassage, Fact
    from charlie.research.releases import stable_release_span

    content = "Python 3.14.6 June 10, 2026.\nPython 3.14.8 Sept. 30, 2026."
    source = SourceDocument(
        source_id="S1", url="https://www.python.org/downloads/", title="Python releases",
        domain="python.org", content=content, source_class="official",
    )
    query = "What is the latest stable Python release and its release date? Verify using python.org."
    brief = _research_brief(query, ResearchMode.DEEP)
    p2_start, p2_end = stable_release_span(source, "3.14.8", "Sept. 30, 2026")
    p1_end = content.index("\n")
    report = ResearchReport(
        query=query,
        mode=ResearchMode.DEEP,
        brief=brief,
        coverage=brief.required_subquestions,
        sources=[source],
        citations=[Citation("S1", source.url, source.title, source.domain)],
        evidence=[
            EvidenceItem(
                "S1", content[:p1_end], passage_id="p1", start_offset=0, end_offset=p1_end,
                document_hash=source.content_hash,
            ),
            EvidenceItem(
                "S1", content[p2_start:p2_end], passage_id="p2", start_offset=p2_start,
                end_offset=p2_end, document_hash=source.content_hash,
            ),
        ],
        passages=[
            EvidencePassage(
                "p1", "S1", source.url, source.canonical_url, source.content_hash,
                content[:p1_end], 0, p1_end, "now", None, "official",
            ),
            EvidencePassage(
                "p2", "S1", source.url, source.canonical_url, source.content_hash,
                content[p2_start:p2_end], p2_start, p2_end, "now", None, "official",
            ),
        ],
        facts=[
            Fact(
                "Python 3.14.8", "version", "3.14.8", "3.14.8", "S1", "official",
                "releases:pick_stable", source.url,
            ),
            Fact(
                "Python 3.14.8", "release_date", "Sept. 30, 2026", "Sept. 30, 2026",
                "S1", "official", "releases:pick_stable", source.url,
            ),
        ],
    )
    assert _update_report_coverage(report) is True

    answer = (
        "The latest stable release of Python is 3.14.8 [S1]. "
        "It was released on Sept. 30, 2026 [S1]."
    )

    assert validate_claim_support(answer, report) is True
    claims = {claim.predicate: claim for claim in report.claims if claim.claim_id.startswith("answer-")}
    assert set(claims) == {"version", "release_date"}
    assert claims["version"].evidence_passage_ids == ["p2"]
    assert claims["release_date"].evidence_passage_ids == ["p2"]

    missing_fact_passage = ResearchReport(
        query=query,
        mode=ResearchMode.DEEP,
        brief=brief,
        sources=[source],
        citations=report.citations,
        evidence=[report.evidence[0]],
        passages=[report.passages[0]],
        facts=report.facts,
    )
    assert validate_claim_support(answer, missing_fact_passage) is False


def test_numeric_grounding_keeps_citation_after_sentence_final_period():
    from charlie.research.citations import validate_numeric_grounding

    content = "HTTPX supports HTTP/1.1 and HTTP/2."
    source = SourceDocument(
        source_id="S1", url="https://www.python-httpx.org/", content=content,
    )
    report = ResearchReport(
        query="How does HTTPX handle HTTP versions?",
        mode=ResearchMode.DEEP,
        sources=[source],
        citations=[Citation("S1", source.url, "HTTPX", "www.python-httpx.org")],
    )

    assert validate_numeric_grounding("HTTPX supports HTTP/1.1 and HTTP/2. [S1]", report) is True


def test_general_deterministic_answer_covers_supported_comparison_fields():
    from charlie.research.citations import validate_claim_support
    from charlie.research.models import ResearchBrief, Subquestion

    contents = {
        "S1": "HTTPX supports asynchronous requests through a context-managed AsyncClient.",
        "S2": "aiohttp supports asynchronous requests through ClientSession.",
    }
    sources = [
        SourceDocument(
            source_id=source_id,
            url=url,
            title=f"{name} documentation",
            domain=domain,
            content=content,
            source_class="official",
        )
        for (source_id, content), name, url, domain in zip(
            contents.items(),
            ("HTTPX", "aiohttp"),
            ("https://www.python-httpx.org/", "https://docs.aiohttp.org/"),
            ("www.python-httpx.org", "docs.aiohttp.org"),
        )
    ]
    brief = ResearchBrief(topic="HTTPX and aiohttp", entity_kind="general")
    passages = [
        EvidencePassage(
            f"p{index}", source.source_id, source.url, source.canonical_url,
            source.content_hash, source.content, 0, len(source.content), "now", None, "official",
        )
        for index, source in enumerate(sources, start=1)
    ]
    report = ResearchReport(
        query="Compare HTTPX and aiohttp asynchronous requests.",
        mode=ResearchMode.DEEP,
        brief=brief,
        sources=sources,
        citations=[Citation(source.source_id, source.url, source.title, source.domain) for source in sources],
        evidence=[
            EvidenceItem(source.source_id, source.content, passage_id=f"p{index}")
            for index, source in enumerate(sources, start=1)
        ],
        passages=passages,
        coverage=[
            Subquestion("q1", "HTTPX: asynchronous requests", ["HTTPX", "asynchronous requests"],
                status="supported", supporting_claim_ids=["p1"]),
            Subquestion("q2", "aiohttp: asynchronous requests", ["aiohttp", "asynchronous requests"],
                status="supported", supporting_claim_ids=["p2"]),
            Subquestion("q3", "HTTPX: client lifecycle", ["HTTPX", "client lifecycle"],
                status="supported", supporting_claim_ids=["p1"]),
        ],
    )

    answer = report.deterministic_answer()

    assert "HTTPX — asynchronous requests" in answer
    assert "aiohttp — asynchronous requests" in answer
    assert "HTTPX — client lifecycle" in answer
    assert answer.count("HTTPX supports asynchronous requests through a context-managed AsyncClient. [S1]") == 2
    assert "Recommendation" not in answer and "| Option |" not in answer
    assert validate_claim_support(answer, report) is True


def test_pick_stable_python_with_dotted_month():
    # Test dotted month abbreviation like 'Feb. 4, 2025' from python.org
    content = """
    Latest Python Releases
    Python 3.14.0a4 - Jan. 14, 2025
    Python 3.13.2 - Feb. 4, 2025
    Python 3.12.8 - Dec. 3, 2024
    """
    doc = SourceDocument(source_id="S1", url="https://www.python.org/downloads/", content=content)
    result = pick_stable([doc])
    assert result is not None
    ver, dt, sid = result
    assert ver == "3.13.2"
    assert "Feb. 4, 2025" in dt
    assert sid == "S1"


def test_is_stable_version():
    assert is_stable_version("3.13.2") is True
    assert is_stable_version("3.12.0") is True
    assert is_stable_version("3.14.0a4") is False
    assert is_stable_version("3.14.0rc1") is False
    assert is_stable_version("3.13.0b2") is False


def test_assemble_answer_release_topic_cleaning():
    brief = ResearchBrief(
        topic="latest stable Python release using official python.org sources",
        entity_kind="release",
    )
    doc = SourceDocument(source_id="S1", url="https://www.python.org/downloads/", content="Python 3.13.2 - Feb. 4, 2025")
    report = ResearchReport(
        query="Research the latest stable Python release using official python.org sources.",
        mode=ResearchMode.DEEP,
        brief=brief,
        sources=[doc],
    )
    result = assemble_answer(report)
    assert "The latest stable release of Python is **3.13.2** [S1]." in result
    assert "Feb. 4, 2025" in result
    assert "latest stable Python release using official" not in result


def test_assemble_answer_ranks_candidates_by_priority():
    brief = ResearchBrief(
        topic="laptops for local AI",
        entity_kind="product",
        budget=100000.0,
        currency="INR",
        priority=["vram", "gpu", "ram", "price"],
    )
    cands = [
        Candidate(name="Option A RTX 3050", brand="BrandA", quote="Option A", source_id="S1"),
        Candidate(name="Option B RTX 4060", brand="BrandB", quote="Option B", source_id="S2"),
    ]
    facts = [
        Fact(candidate="Option A RTX 3050", aspect="gpu", value="RTX 3050", quote="RTX 3050", source_id="S1", source_class="official", extractor="regex"),
        Fact(candidate="Option A RTX 3050", aspect="vram", value="6 GB", quote="6 GB VRAM", source_id="S1", source_class="official", extractor="regex"),
        Fact(candidate="Option A RTX 3050", aspect="price", value="₹65,000", quote="₹65,000", source_id="S1", source_class="official", extractor="regex"),
        Fact(candidate="Option B RTX 4060", aspect="gpu", value="RTX 4060", quote="RTX 4060", source_id="S2", source_class="official", extractor="regex"),
        Fact(candidate="Option B RTX 4060", aspect="vram", value="8 GB", quote="8 GB VRAM", source_id="S2", source_class="official", extractor="regex"),
        Fact(candidate="Option B RTX 4060", aspect="price", value="₹85,000", quote="₹85,000", source_id="S2", source_class="official", extractor="regex"),
    ]
    report = ResearchReport(
        query="Charlie, research best laptop",
        mode=ResearchMode.DEEP,
        brief=brief,
        candidates=cands,
        facts=facts,
    )
    result = assemble_answer(report)
    # Option B has 8GB VRAM vs Option A's 6GB VRAM, so Option B must be recommended
    assert "**Recommendation**: Option B RTX 4060." in result


def test_pick_stable_python_with_colon_or_text():
    content_colon = "Latest Python Release: Python 3.13.2: Feb. 4, 2025"
    doc1 = SourceDocument(source_id="S1", url="https://www.python.org/downloads/", content=content_colon)
    res1 = pick_stable([doc1])
    assert res1 is not None
    assert res1[0] == "3.13.2"
    assert "Feb. 4, 2025" in res1[1]

    content_text = "Python 3.13.2 was released on February 4, 2025"
    doc2 = SourceDocument(source_id="S2", url="https://www.python.org/downloads/", content=content_text)
    res2 = pick_stable([doc2])
    assert res2 is not None
    assert res2[0] == "3.13.2"
    assert "February 4, 2025" in res2[1]


def test_validate_numeric_grounding_allows_resolutions_and_candidates():
    brief = ResearchBrief(topic="laptop", budget=100000.0, currency="INR")
    cands = [Candidate(name="Dell G15 5530", brand="Dell", quote="Dell G15 5530", source_id="S1")]
    facts = [
        Fact(candidate="Dell G15 5530", aspect="gpu", value="RTX 4060", quote="RTX 4060", source_id="S1", source_class="official", extractor="regex"),
        Fact(candidate="Dell G15 5530", aspect="price", value="₹89,990", quote="₹89,990", source_id="S1", source_class="official", extractor="regex"),
    ]
    doc = SourceDocument(source_id="S1", url="https://www.dell.com", content="Dell G15 5530 features 165Hz FHD 1920x1080 display")
    report = ResearchReport(
        query="laptops",
        mode=ResearchMode.DEEP,
        brief=brief,
        candidates=cands,
        facts=facts,
        sources=[doc],
    )
    # Mentions model number 5530, resolution 1080, refresh rate 165
    text = "The Dell G15 5530 is priced at ₹89,990 [S1] with a 1080p display at 165Hz."
    assert _validate_numeric_grounding(text, report) is True


def test_pick_stable_multiple_facts_same_source():
    # If multiple version/date facts come from the same official source,
    # 3.13.2 must be correctly paired with its own date, not an older version's date.
    facts = [
        Fact(candidate="Python 3.12.8", aspect="version", value="3.12.8", quote="Python 3.12.8 - Dec. 3, 2024", source_id="S1", source_class="official", extractor="regex"),
        Fact(candidate="Python 3.12.8", aspect="release_date", value="Dec. 3, 2024", quote="Python 3.12.8 - Dec. 3, 2024", source_id="S1", source_class="official", extractor="regex"),
        Fact(candidate="Python 3.13.2", aspect="version", value="3.13.2", quote="Python 3.13.2 - Feb. 4, 2025", source_id="S1", source_class="official", extractor="regex"),
        Fact(candidate="Python 3.13.2", aspect="release_date", value="Feb. 4, 2025", quote="Python 3.13.2 - Feb. 4, 2025", source_id="S1", source_class="official", extractor="regex"),
    ]
    res = pick_stable(facts)
    assert res is not None
    ver, dt, sid = res
    assert ver == "3.13.2"
    assert dt == "Feb. 4, 2025"
    assert sid == "S1"


def test_is_candidate_complete_rejects_family_facts_for_a_specific_variant():
    from charlie.research.engine import _is_candidate_complete
    brief = ResearchBrief(
        topic="laptop",
        budget=100000.0,
        currency="INR",
        aspects=["price", "gpu", "vram", "ram"],
    )
    # Candidate name has model variant "Lenovo LOQ 15IAX9"
    cand = Candidate(name="Lenovo LOQ 15IAX9", brand="Lenovo", quote="Lenovo LOQ", source_id="S1")
    # Facts extracted with base name "Lenovo LOQ"
    facts = [
        Fact(candidate="Lenovo LOQ", aspect="gpu", value="RTX 4060", quote="RTX 4060", source_id="S1", source_class="official", extractor="regex"),
        Fact(candidate="Lenovo LOQ", aspect="vram", value="8 GB", quote="8 GB", source_id="S1", source_class="official", extractor="regex"),
        Fact(candidate="Lenovo LOQ", aspect="ram", value="16 GB", quote="16 GB", source_id="S1", source_class="official", extractor="regex"),
        Fact(candidate="Lenovo LOQ", aspect="price", value="₹84,990", quote="₹84,990", source_id="S1", source_class="official", extractor="regex"),
    ]
    assert _is_candidate_complete(cand, facts, brief) is False
    from dataclasses import replace
    exact_facts = [replace(fact, candidate="Lenovo LOQ 15IAX9") for fact in facts]
    assert _is_candidate_complete(cand, exact_facts, brief) is True


def test_assemble_answer_release_topic_cleaning_variants():
    # Test cleaning from domain, URL, intent prefixes, and empty citation
    cases = [
        ("latest stable Python release from python.org", "The latest stable release of Python is **3.13.2** [S1]."),
        ("what is the latest stable version of Python", "The latest stable release of Python is **3.13.2** [S1]."),
        ("latest stable release of Python", "The latest stable release of Python is **3.13.2** [S1]."),
    ]
    for topic_query, expected_prefix in cases:
        brief = ResearchBrief(topic=topic_query, entity_kind="release")
        doc = SourceDocument(source_id="S1", url="https://www.python.org/downloads/", content="Python 3.13.2 - Feb. 4, 2025")
        report = ResearchReport(query=topic_query, mode=ResearchMode.DEEP, brief=brief, sources=[doc])
        res = assemble_answer(report)
        assert expected_prefix in res

    # Test without source_id (should not have dangling space before period)
    brief = ResearchBrief(topic="Python", entity_kind="release")
    doc_no_id = SourceDocument(source_id="", url="https://www.python.org/downloads/", content="Python 3.13.2 - Feb. 4, 2025")
    report_no_id = ResearchReport(query="Python", mode=ResearchMode.DEEP, brief=brief, sources=[doc_no_id])
    res_no_id = assemble_answer(report_no_id)
    assert "The latest stable release of Python is **3.13.2**." in res_no_id
    assert " **3.13.2** ." not in res_no_id


def test_assemble_answer_uses_verified_llm_release_facts_not_unrelated_semver():
    brief = ResearchBrief(
        topic="the latest closed-source and open-source LLM released",
        entity_kind="release",
        source_policy="official_preferred",
    )
    report = ResearchReport(
        query=brief.topic,
        mode=ResearchMode.DEEP,
        brief=brief,
        candidates=[
            Candidate(
                "Beam", "Reflection AI", "Reflection AI debuted Beam", "D1",
                access="open", release_date="2026-10-05",
            ),
            Candidate(
                "GPT-6 Sol", "OpenAI", "OpenAI released GPT-6 Sol", "D2",
                access="closed", release_date="2026-09-29",
            ),
        ],
        facts=[
            Fact(
                "Beam", "release_date", "Oct 5, 2026",
                "Reflection AI debuted Beam on Oct 5, 2026", "S1",
                "official_unverified", "regex:release_date",
            ),
            Fact(
                "GPT-6 Sol", "release_date", "Sept 29, 2026",
                "OpenAI released GPT-6 Sol on Sept 29, 2026", "S2",
                "official_unverified", "regex:release_date",
            ),
            Fact(
                None, "version", "1.0.6", "unrelated package version 1.0.6",
                "S3", "unknown", "regex:version",
            ),
        ],
    )

    answer = assemble_answer(report)

    assert "Closed-source: **OpenAI GPT-6 Sol**" in answer
    assert "Open-weight: **Reflection AI Beam**" in answer
    assert "Sept 29, 2026" in answer and "[S2]" in answer
    assert "Oct 5, 2026" in answer and "[S1]" in answer
    assert "1.0.6" not in answer


def test_assemble_answer_reports_supported_model_mentions_when_latest_date_is_unresolved():
    from charlie.research.citations import validate_claim_support

    brief = ResearchBrief(
        topic="the latest closed-source and open-source LLM released",
        entity_kind="release",
        source_policy="official_preferred",
    )
    document = SourceDocument(
        source_id="S1",
        url="https://reflection.ai/news/beam",
        title="Beam release",
        domain="reflection.ai",
        content="Reflection describes Beam as its first open-weight model.",
        source_class="official_unverified",
    )
    report = ResearchReport(
        query=brief.topic,
        mode=ResearchMode.DEEP,
        brief=brief,
        sources=[document],
        candidates=[Candidate("Beam", "Reflection AI", document.content, "S1", access="open")],
        evidence=[
            EvidenceItem(
                "S1",
                document.content,
                passage_id="beam-open-weight",
                start_offset=0,
                end_offset=len(document.content),
                document_hash=document.content_hash,
            )
        ],
        citations=[Citation("S1", document.url, document.title, document.domain)],
        coverage=[
            Subquestion("closed", "Latest closed-source release", ["llm_release:closed"], status="unresolved"),
            Subquestion("open", "Latest open-weight release", ["llm_release:open"], status="unresolved"),
        ],
    )
    report.bind_passages()

    answer = assemble_answer(report)

    assert "Beam** is described as open-weight [S1]" in answer
    assert "Unresolved: latest open-weight model and release date." in answer
    assert "Unresolved: latest closed-source model and release date." in answer
    assert "released on" not in answer
    assert validate_claim_support(answer, report)


def test_assemble_answer_candidate_scoring_edge_cases():
    # Candidates with missing aspect facts, non-string priority elements, and ₹0 price
    brief = ResearchBrief(
        topic="budget laptop",
        entity_kind="product",
        budget=50000.0,
        currency="INR",
        priority=["vram", None, "gpu", 123, "price"],  # Non-string priorities should not crash
    )
    cands = [
        Candidate(name="Laptop A", brand="BrandA", quote="Laptop A", source_id="S1"),
        Candidate(name="Laptop B", brand="BrandB", quote="Laptop B", source_id="S2"),
    ]
    facts = [
        # Laptop A has no vram, no gpu fact, price 0
        Fact(candidate="Laptop A", aspect="price", value="₹0", quote="₹0", source_id="S1", source_class="official", extractor="regex"),
        # Laptop B has vram 8 GB, gpu RTX 4050, price 45,000
        Fact(candidate="Laptop B", aspect="gpu", value="RTX 4050", quote="RTX 4050", source_id="S2", source_class="official", extractor="regex"),
        Fact(candidate="Laptop B", aspect="vram", value="8 GB", quote="8 GB", source_id="S2", source_class="official", extractor="regex"),
        Fact(candidate="Laptop B", aspect="price", value="₹45,000", quote="₹45,000", source_id="S2", source_class="official", extractor="regex"),
    ]
    report = ResearchReport(
        query="budget laptop",
        mode=ResearchMode.DEEP,
        brief=brief,
        candidates=cands,
        facts=facts,
    )
    res = assemble_answer(report)
    assert "**Recommendation**: Laptop B." in res
