"""Source classification, official domain registry, and citation policy."""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Set
from urllib.parse import urlparse

from charlie.research.credibility import organisational_domain
from charlie.research.models import ResearchBrief, SourceClass

# Brand -> Primary registrable domains and official store URL hints
# Keys are lower-case brand tokens.
OFFICIAL_REGISTRY: Dict[str, Dict[str, any]] = {
    "lenovo": {
        "domains": {"lenovo.com"},
        "store_paths": ["/in/en/p/", "/in/en/laptops/", "/in/en/d/"],
        "aliases": ["loq", "legion", "thinkpad", "ideapad", "yoga"],
    },
    "asus": {
        "domains": {"asus.com", "rog.asus.com"},
        "store_paths": ["/in/store/", "/in/displays-desktops/", "/in/laptops/"],
        "aliases": ["tuf", "rog", "zenbook", "vivobook"],
    },
    "hp": {
        "domains": {"hp.com"},
        "store_paths": ["/in-en/shop/", "/in-en/products/"],
        "aliases": ["victus", "omen", "pavilion", "envy", "spectre"],
    },
    "dell": {
        "domains": {"dell.com"},
        "store_paths": ["/en-in/shop/"],
        "aliases": ["alienware", "g15", "g16", "xps", "inspiron"],
    },
    "acer": {
        "domains": {"acer.com"},
        "store_paths": ["/in-en/store/"],
        "aliases": ["predator", "nitro", "aspire", "swift"],
    },
    "msi": {
        "domains": {"msi.com"},
        "store_paths": ["/store/"],
        "aliases": ["katana", "cyborg", "bravo", "thin", "stealth", "raider"],
    },
    "apple": {
        "domains": {"apple.com"},
        "store_paths": ["/in/shop/"],
        "aliases": ["macbook", "mac", "iphone", "ipad"],
    },
    "samsung": {
        "domains": {"samsung.com"},
        "store_paths": ["/in/"],
        "aliases": ["galaxy"],
    },
    "google": {
        "domains": {"google.com"},
        "store_paths": ["store.google.com"],
        "aliases": ["pixel"],
    },
    "oneplus": {
        "domains": {"oneplus.in", "oneplus.com"},
        "store_paths": ["/in/"],
        "aliases": [],
    },
    "xiaomi": {
        "domains": {"mi.com"},
        "store_paths": ["/in/"],
        "aliases": ["redmi"],
    },
    "microsoft": {
        "domains": {"microsoft.com"},
        "store_paths": ["/en-in/store/"],
        "aliases": ["surface"],
    },
    "razer": {
        "domains": {"razer.com"},
        "store_paths": ["/razerstore/"],
        "aliases": ["blade"],
    },
    "gigabyte": {
        "domains": {"gigabyte.com", "aorus.com"},
        "store_paths": [],
        "aliases": ["aorus"],
    },
    # Software & Standards
    "python": {
        "domains": {"python.org"},
        "canonical_paths": ["/downloads/", "/downloads/release/"],
        "aliases": [],
    },
    "nodejs": {
        "domains": {"nodejs.org"},
        "canonical_paths": ["/en/about/previous-releases"],
        "aliases": ["node"],
    },
    "rust": {
        "domains": {"rust-lang.org"},
        "canonical_paths": [],
        "aliases": [],
    },
    "httpx": {
        "domains": {"python-httpx.org"},
        "canonical_paths": [],
        "documentation_urls": [
            "https://www.python-httpx.org/async/",
            "https://www.python-httpx.org/quickstart/",
            "https://www.python-httpx.org/compatibility/",
            "https://www.python-httpx.org/advanced/clients/",
        ],
        "aliases": [],
    },
    "aiohttp": {
        "domains": {"aiohttp.org"},
        "canonical_paths": [],
        "documentation_urls": [
            "https://docs.aiohttp.org/en/stable/client_quickstart.html",
            "https://docs.aiohttp.org/en/stable/client_advanced.html",
            "https://docs.aiohttp.org/en/stable/client_reference.html",
        ],
        "aliases": [],
    },
    "owasp": {
        "domains": {"owasp.org"},
        "canonical_paths": [],
        "documentation_urls": [
            "https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html",
        ],
        "aliases": [],
    },
    "portswigger": {
        "domains": {"portswigger.net"},
        "canonical_paths": [],
        "documentation_urls": ["https://portswigger.net/web-security/ssrf"],
        "aliases": [],
    },
    "searxng": {
        "domains": {"searxng.org"},
        "canonical_paths": [],
        "documentation_urls": [
            "https://docs.searxng.org/",
            "https://docs.searxng.org/admin/settings/settings_search.html",
            "https://docs.searxng.org/admin/settings/settings_engines.html",
        ],
        "aliases": [],
    },
    "crawl4ai": {
        "domains": {"crawl4ai.com"},
        "canonical_paths": [],
        "documentation_urls": [
            "https://docs.crawl4ai.com/",
            "https://docs.crawl4ai.com/core/installation/",
        ],
        "aliases": [],
    },
    "scrapling": {
        "domains": {"scrapling.readthedocs.io"},
        "canonical_paths": [],
        "documentation_urls": ["https://scrapling.readthedocs.io/en/latest/"],
        "aliases": [],
    },
}

# Major Indian retailers
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


def resolve_brand(query_or_candidate: str) -> Optional[str]:
    """Find known brand from text."""
    lower = query_or_candidate.lower()
    for brand, info in OFFICIAL_REGISTRY.items():
        if re.search(rf"\b{re.escape(brand)}\b", lower):
            return brand
        for alias in info.get("aliases", []):
            if re.search(rf"\b{re.escape(alias)}\b", lower):
                return brand
    return None


def get_official_domains(brand: str) -> List[str]:
    """Return registered official domains for a brand."""
    info = OFFICIAL_REGISTRY.get(brand.lower())
    if info:
        return sorted(list(info.get("domains", set())))
    return []


def get_official_document_urls(brand: str) -> List[str]:
    info = OFFICIAL_REGISTRY.get(brand.lower())
    return list(info.get("documentation_urls", [])) if info else []


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

    # Check social / forum / community
    if org_domain in FORUM_SOCIAL_DOMAINS:
        return SourceClass.FORUM_SOCIAL
    if org_domain == "huggingface.co" and path.startswith("/blog/") and len(path.strip("/").split("/")) > 2:
        # Community user blog post
        return SourceClass.FORUM_SOCIAL

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

    # Mark a publisher unverified-official when its registrable host matches the discovered developer name.
    if target_brand:
        generic_brand_terms = {"ai", "lab", "labs", "research", "the", "inc", "llc", "ltd", "company"}
        brand_terms = set(re.findall(r"[a-z0-9]+", target_brand.casefold())) - generic_brand_terms
        domain_terms = set(re.findall(r"[a-z0-9]+", org_domain.casefold())) - {
            "com", "org", "net", "co", "io", "ai", "dev", "app", "tech"
        }
        if brand_terms and brand_terms.issubset(domain_terms):
            return SourceClass.OFFICIAL_UNVERIFIED
        compact_brand = "".join(sorted(brand_terms))
        compact_domain = "".join(sorted(domain_terms))
        if compact_brand and compact_brand == compact_domain:
            return SourceClass.OFFICIAL_UNVERIFIED

    return SourceClass.UNKNOWN


def citable_for(aspect: str, source_class: SourceClass | str, policy: str = "official_required") -> bool:
    """Determine if a source class is allowed to ground a claim for a given aspect.

    Policy rules (Decision 2):
    - Specs must come from OFFICIAL or OFFICIAL_STORE.
    - Prices can come from OFFICIAL_STORE, or RETAILER (labelled as retailer price).
    - FORUM_SOCIAL is NEVER citable for facts.
    """
    s_class = SourceClass(source_class) if isinstance(source_class, str) else source_class

    if s_class == SourceClass.FORUM_SOCIAL:
        return False

    if policy == "any_reputable":
        return s_class not in (SourceClass.FORUM_SOCIAL, SourceClass.UNKNOWN)

    # official_required or official_preferred
    if aspect == "price":
        # Official store is preferred; retailer allowed
        return s_class in (SourceClass.OFFICIAL, SourceClass.OFFICIAL_STORE, SourceClass.RETAILER)

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
