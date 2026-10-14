from charlie.research.search import parse_brief
from charlie.research.sources import get_official_domains, resolve_brands


def test_named_public_authorities_scope_searches_to_each_official_domain():
    assert resolve_brands("official FAA and TSA guidance") == ["faa", "tsa"]
    assert resolve_brands("official CPSC carbon monoxide guidance") == ["cpsc"]
    assert get_official_domains("cpsc") == ["cpsc.gov"]

    brief = parse_brief(
        "Research official FAA and TSA guidance: how must spare lithium-ion batteries be packed, "
        "and what are the carry-on limits?"
    )

    assert brief.source_policy == "official_required"
    assert {"faa.gov", "tsa.gov"}.issubset(brief.explicit_domains)
