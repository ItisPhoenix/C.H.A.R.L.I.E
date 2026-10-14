"""Source classification, official domain registry, and citation policy."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse

from charlie.research.credibility import organisational_domain
from charlie.research.models import ResearchBrief, SourceClass

# Brand -> primary registrable domains and official store URL hints.
# Data lives in shared/official_sources.json; "tld" is shorthand for "<brand>.<tld>".
_REGISTRY_PATH = Path(__file__).resolve().parents[2] / "shared" / "official_sources.json"


def _load_official_registry() -> Dict[str, Dict[str, Any]]:
    with _REGISTRY_PATH.open(encoding="utf-8") as registry_file:
        payload = json.load(registry_file)
    registry: Dict[str, Dict[str, Any]] = {}
    for raw_brand, raw_info in payload.get("official_sources", {}).items():
        brand = str(raw_brand).strip().lower()
        domains = {str(item).strip().lower() for item in raw_info.get("domains") or []}
        tld = str(raw_info.get("tld") or "").strip().lstrip(".")
        if tld:
            domains.add(f"{brand}.{tld}")
        registry[brand] = {
            "domains": domains,
            "store_paths": list(raw_info.get("store_paths") or []),
            "aliases": list(raw_info.get("aliases") or []),
        }
    return registry


OFFICIAL_REGISTRY: Dict[str, Dict[str, Any]] = _load_official_registry()

# Major Indian retailers
PUBLIC_AUTHORITY_DOMAINS: Set[str] = {
    "w3.org",
    "ietf.org",
    "iso.org",
    "ecma-international.org",
}
GOVERNMENT_DOMAIN_SUFFIXES = (
    ".gov", ".gov.uk", ".gc.ca", ".gov.au", ".gov.in", ".govt.nz", ".gouv.fr", ".go.jp",
)

RETAILERS_IN: Set[str] = {
    "amazon.in",
    "flipkart.com",
    "croma.com",
    "reliancedigital.in",
    "vijaysales.com",
}

# Tech review / benchmark / reference sites (informative, not official manufacturer specs)
REVIEW_SITES: Set[str] = {
    "notebookcheck.net",
    "ultrabookreview.com",
    "techspot.com",
    "tomshardware.com",
    "anandtech.com",
    "pcmag.com",
    "gsmarena.com",
    "91mobiles.com",
    "mysmartprice.com",
    "gadgets360.com",
    "smartprix.com",
}

# Forums and Social Media
FORUM_SOCIAL_DOMAINS: Set[str] = {
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "reddit.com",
    "quora.com",
    "news.ycombinator.com",
    "youtube.com",
    "medium.com",
    "blogspot.com",
}

_FORUM_PATH_PATTERNS = [
    re.compile(r"^/blog/[^/]+/[^/]+", re.I),  # user blog posts like huggingface.co/blog/user/...
    re.compile(r"^/groups/", re.I),
    re.compile(r"^/r/[^/]+", re.I),
]


def resolve_brands(query_or_candidate: str) -> List[str]:
    """Find every known publisher or brand named in text."""
    lower = query_or_candidate.lower()
    matches: List[str] = []
    for brand, info in OFFICIAL_REGISTRY.items():
        terms = [brand, *info.get("aliases", [])]
        if any(re.search(rf"\b{re.escape(term)}\b", lower) for term in terms):
            matches.append(brand)
    return matches


def resolve_brand(query_or_candidate: str) -> Optional[str]:
    """Find the first known publisher or brand named in text."""
    return next(iter(resolve_brands(query_or_candidate)), None)


def get_official_domains(brand: str) -> List[str]:
    """Return registered official domains for a brand."""
    info = OFFICIAL_REGISTRY.get(brand.lower())
    if info:
        return sorted(list(info.get("domains", set())))
    return []


def classify(
    url: str,
    brief: Optional[ResearchBrief] = None,
    candidate_brand: Optional[str] = None,
) -> SourceClass:
    """Classify a source URL into a SourceClass relative to the brief/brand."""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.hostname or "").lower()
    org_domain = organisational_domain(host)
    path = parsed.path.lower()

    if not org_domain:
        return SourceClass.UNKNOWN

    if org_domain in PUBLIC_AUTHORITY_DOMAINS or any(
        host.endswith(suffix) for suffix in GOVERNMENT_DOMAIN_SUFFIXES
    ):
        return SourceClass.OFFICIAL

    # Check social / forum / community
    if org_domain in FORUM_SOCIAL_DOMAINS:
        return SourceClass.FORUM_SOCIAL
    if org_domain == "huggingface.co" and path.startswith("/blog/") and len(path.strip("/").split("/")) > 2:
        # Community user blog post
        return SourceClass.FORUM_SOCIAL

    # A requested retailer or review site keeps its publisher identity.
    if org_domain in RETAILERS_IN or host in RETAILERS_IN:
        return SourceClass.RETAILER
    if org_domain in REVIEW_SITES or host in REVIEW_SITES:
        return SourceClass.REVIEW

    # Check explicit domains requested by user
    if brief and brief.explicit_domains:
        for explicit in brief.explicit_domains:
            exp_org = organisational_domain(explicit.lower())
            if org_domain == exp_org or host == explicit.lower() or host.endswith(f".{explicit.lower()}"):
                return SourceClass.OFFICIAL

    # Check against candidate brand or query brand
    target_brand = candidate_brand or (resolve_brand(brief.topic) if brief else None)
    if target_brand and target_brand in OFFICIAL_REGISTRY:
        reg_info = OFFICIAL_REGISTRY[target_brand]
        reg_domains = reg_info.get("domains", set())
        if org_domain in reg_domains or host in reg_domains:
            # Check if official store path
            store_paths = reg_info.get("store_paths", [])
            for sp in store_paths:
                if sp in path or sp in host:
                    return SourceClass.OFFICIAL_STORE
            return SourceClass.OFFICIAL

    # General check: any brand in registry?
    for brand, reg_info in OFFICIAL_REGISTRY.items():
        reg_domains = reg_info.get("domains", set())
        if org_domain in reg_domains or host in reg_domains:
            store_paths = reg_info.get("store_paths", [])
            for sp in store_paths:
                if sp in path or sp in host:
                    return SourceClass.OFFICIAL_STORE
            return SourceClass.OFFICIAL

    # Retailers
    if org_domain in RETAILERS_IN or host in RETAILERS_IN:
        return SourceClass.RETAILER

    # Reviews
    if org_domain in REVIEW_SITES or host in REVIEW_SITES:
        return SourceClass.REVIEW

    # Match the discovered publisher against the registrable host.
    if target_brand:
        generic_brand_terms = {"ai", "lab", "labs", "research", "the", "inc", "llc", "ltd", "company"}
        brand_terms = set(re.findall(r"[a-z0-9]+", target_brand.casefold())) - generic_brand_terms
        domain_terms = set(re.findall(r"[a-z0-9]+", org_domain.casefold())) - {
            "com", "org", "net", "co", "io", "ai", "dev", "app", "tech"
        }
        if brand_terms and brand_terms == domain_terms:
            # The registrable host is the vendor itself, so no registry row is needed.
            return SourceClass.OFFICIAL
        if brand_terms and brand_terms.issubset(domain_terms):
            return SourceClass.OFFICIAL_UNVERIFIED
        compact_brand = "".join(sorted(brand_terms))
        compact_domain = "".join(sorted(domain_terms))
        if compact_brand and compact_brand == compact_domain:
            return SourceClass.OFFICIAL_UNVERIFIED

    return SourceClass.UNKNOWN


def citation_source_class(
    source_class: Optional[str],
    url: str,
    brief: Optional[ResearchBrief] = None,
) -> str:
    if source_class and source_class != SourceClass.UNKNOWN.value:
        return source_class
    return classify(url, brief=brief).value


def citable_for(aspect: str, source_class: SourceClass | str, policy: str = "official_required") -> bool:
    """Apply the requested authority policy uniformly to model-selected evidence fields."""
    s_class = SourceClass(source_class) if isinstance(source_class, str) else source_class

    if s_class == SourceClass.FORUM_SOCIAL:
        return False

    if policy == "any_reputable":
        return True

    # official_required or official_preferred
    # For specs, versions, release dates, hardware facts
    if policy == "official_required":
        return s_class in (SourceClass.OFFICIAL, SourceClass.OFFICIAL_STORE)
    elif policy == "official_preferred":
        return s_class in (
            SourceClass.OFFICIAL,
            SourceClass.OFFICIAL_STORE,
            SourceClass.OFFICIAL_UNVERIFIED,
            SourceClass.REVIEW,
            SourceClass.REFERENCE,
        )

    return True