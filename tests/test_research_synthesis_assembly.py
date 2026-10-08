import pytest
from charlie.background_task import assemble_answer, _validate_numeric_grounding
from charlie.research.models import Candidate, Fact, ResearchBrief, ResearchMode, ResearchReport, SourceDocument
from charlie.research.releases import pick_stable, is_stable_version


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
    # Text mentions user budget 100,000 and price 78,584 and year 2025
    good_text = "Under your ₹100,000 budget, the best option for 2025 is HP Victus at ₹78,584 [S1]."
    assert _validate_numeric_grounding(good_text, report) is True

    # Text contains hallucinated price 45,000 not in facts, query, or budget
    bad_text = "You can also buy it for ₹45,000 at a discount [S1]."
    assert _validate_numeric_grounding(bad_text, report) is False


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
