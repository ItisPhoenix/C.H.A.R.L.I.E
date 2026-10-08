"""Query normalization, bounded planning, and concurrent search execution."""

from __future__ import annotations

import asyncio
import re
from typing import Callable, List, Optional

from charlie.research.models import (
    Candidate,
    ResearchBrief,
    ResearchMode,
    ResearchPlan,
    ResearchQuery,
    SearchResult,
)
from charlie.research.providers import SearchProvider, search_with_fallback
from charlie.research.shopping import is_shopping_query
from charlie.research.sources import OFFICIAL_REGISTRY, get_official_domains, resolve_brand

_INSTRUCTION_RE = re.compile(
    r"\b(?:do\s+a\s+web\s+search|search\s+the\s+web|please|could\s+you|can\s+you|"
    r"tell\s+me|show\s+me|find\s+me|i\s+want\s+to\s+know|right\s+now|currently)\b",
    re.IGNORECASE,
)
_FORMAT_RE = re.compile(r"\b(?:be\s+short|under\s+\d+\s+words?|in\s+\d+\s+words?|in\s+\d+\s+sentences?)\b", re.IGNORECASE)
_QUOTED_RE = re.compile(r'"[^"\n]*"|“[^”\n]*”|‘[^’\n]*’|(?<!\w)\'[^\'\n]*\'')
_ASSISTANT_PREFIX_RE = re.compile(r"^\s*(?:hey\s+)?charlie(?:\s*[,!:—-]\s*|\s+)", re.IGNORECASE)
_RESEARCH_PREFIX_RE = re.compile(r"^(?:research|investigate|look\s+into)\s+", re.IGNORECASE)
_FORMAT_INSTRUCTION_RE = re.compile(
    r"^\s*(?:please\s+)?(?:cite\b|include\s+(?:citations?|sources?)\b|"
    r"give\b|state\b|tell\s+me\b|"
    r"use\s+only\b|only\s+(?:cite|use)\b|provide\s+only\b|"
    r"answer\s+in\b|respond\s+in\b|keep\s+the\s+answer\b|"
    r"compare\s+(?:\d+|two|three|four|five)\s+(?:options?|products?|models?)\b|recommend\b|"
    r"if\s+(?:you\s+)?find\s+(?:no|zero)\s+(?:fetched\s+)?(?:evidence|sources?)\b|"
    r"if\s+(?:you\s+)?(?:cannot|can't|do\s+not|don't)\s+find\s+(?:any\s+)?"
    r"(?:fetched\s+)?(?:evidence|sources?)\b)",
    re.IGNORECASE,
)
_EXPLICIT_DOMAIN_RE = re.compile(
    r"\b(?:site\s*:\s*|(?:on|from|at|using(?:\s+official)?)\s+)(?:https?://)?(?:www\.)?"
    r"((?:[a-z0-9-]+\.)+[a-z0-9-]{2,})\b",
    re.IGNORECASE,
)
_SPACE_RE = re.compile(r"\s+")


def _protect_quoted_phrases(query: str) -> tuple[str, list[str]]:
    phrases: list[str] = []

    def replace(match: re.Match[str]) -> str:
        phrases.append(match.group(0))
        return f"__quoted_phrase_{len(phrases) - 1}__"

    return _QUOTED_RE.sub(replace, query), phrases


def _restore_quoted_phrases(query: str, phrases: list[str]) -> str:
    for index, phrase in enumerate(phrases):
        query = query.replace(f"__quoted_phrase_{index}__", phrase)
    return query


def explicit_domain_filters(query: str) -> List[str]:
    """Extract only domains explicitly scoped by site:, on, from, or at."""
    unquoted, _phrases = _protect_quoted_phrases(query)
    return list(dict.fromkeys(match.group(1).rstrip(".").lower() for match in _EXPLICIT_DOMAIN_RE.finditer(unquoted)))


def clean_query(query: str) -> str:
    """Remove conversational/formatting noise without splitting user intent."""
    cleaned, phrases = _protect_quoted_phrases(query)
    cleaned = _ASSISTANT_PREFIX_RE.sub("", cleaned).strip()
    cleaned = _RESEARCH_PREFIX_RE.sub("", cleaned).strip()
    cleaned = re.sub(r"\b(?:the\s+)?best\s+(?:current\s+)?", "", cleaned, flags=re.I)
    cleaned = re.sub(r"₹\s*(\d+(?:,\d+)*)", lambda match: match.group(1).replace(",", "") + " rupees", cleaned)
    for match in re.finditer(r"[.!?;,]", cleaned):
        if _FORMAT_INSTRUCTION_RE.match(cleaned[match.end() :]):
            cleaned = cleaned[: match.start()].rstrip()
            break
    cleaned = _INSTRUCTION_RE.sub(" ", cleaned).strip()
    cleaned = _FORMAT_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\bwhat(?:'s| is)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^(?:and|then)\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[?!.,;:]+$", "", cleaned).strip()
    cleaned = re.sub(r"[?!.,;:]+$", "", cleaned).strip()
    cleaned = _SPACE_RE.sub(" ", cleaned)
    return _restore_quoted_phrases(cleaned or query.strip(), phrases)


def _budget_constraint(query: str) -> Optional[str]:
    match = re.search(r"(?:under|below|less than|within)\s*[₹$€£]?\s*([\d,]+)", query, re.I)
    return f"price <= {match.group(1).replace(',', '')}" if match else None


def parse_brief(query: str) -> ResearchBrief:
    """Parse user query into structured requirements (ResearchBrief)."""
    cleaned = clean_query(query)
    lower = query.lower()

    # 1. Entity kind
    if re.search(r"\b(?:releases?|versions?|latest\s+stable)\b", lower):
        entity_kind = "release"
    elif is_shopping_query(query) or any(k in lower for k in ["laptop", "phone", "galaxy", "pixel", "macbook"]):
        entity_kind = "product"
    else:
        entity_kind = "general"

    # 2. Option count
    option_count = None
    count_m = re.search(r"\b(?:compare\s+)?(\d+|two|three|four|five)\s+(?:options?|products?|models?|alternatives?)\b", lower)
    if count_m:
        val = count_m.group(1)
        word_map = {"two": 2, "three": 3, "four": 4, "five": 5}
        option_count = word_map.get(val, int(val) if val.isdigit() else None)
    elif "compare" in lower:
        option_count = 3

    # 3. Budget & Currency
    budget = None
    currency = None
    from charlie.research.facts import parse_inr_price
    parsed_price = parse_inr_price(query)
    if parsed_price is not None:
        budget = parsed_price
        currency = "INR"
    elif "₹" in query or "rupee" in lower:
        currency = "INR"

    # 4. Market
    market = "IN" if ("india" in lower or "₹" in query or "rupee" in lower or "inr" in lower or "lakh" in lower) else None

    # 5. Source policy
    if "official sources" in lower or "official source" in lower or "official manufacturer" in lower:
        source_policy = "official_required"
    elif "official" in lower:
        source_policy = "official_preferred"
    else:
        source_policy = "any_reputable"

    # 6. Stable only
    stable_only = bool(re.search(r"\b(latest\s+stable|stable\s+release|stable\s+version|final\s+release)\b", lower))

    # 7. Explicit domains
    exp_domains = explicit_domain_filters(query)

    # 8. Aspects & Priority
    aspects = []
    priority = []
    if entity_kind == "product":
        if "laptop" in lower or "local ai" in lower or "llm" in lower:
            aspects = ["price", "gpu", "vram", "ram", "ram_upgradeable", "variant"]
            priority = ["vram", "gpu", "ram", "price"]
        elif "phone" in lower or "pixel" in lower or "galaxy" in lower:
            aspects = ["price", "processor", "ram", "camera", "display"]
            priority = ["processor", "ram", "price"]
        else:
            aspects = ["price", "specifications"]
            priority = ["price"]
    topic = cleaned
    if entity_kind == "release":
        aspects = ["version", "release_date"]
        priority = ["version", "release_date"]
        cleaned_topic = re.sub(r"\busing\s+official\s+[\w.-]+\s+sources?\b", "", topic, flags=re.I).strip(" .:,")
        cleaned_topic = re.sub(r"\b(?:give|state|tell)\s+.*", "", cleaned_topic, flags=re.I).strip(" .:,")
        if cleaned_topic:
            topic = cleaned_topic

    return ResearchBrief(
        topic=topic,
        entity_kind=entity_kind,
        option_count=option_count,
        budget=budget,
        currency=currency,
        market=market,
        source_policy=source_policy,
        aspects=aspects,
        priority=priority,
        stable_only=stable_only,
        explicit_domains=exp_domains,
    )


def discovery_queries(brief: ResearchBrief) -> List[str]:
    """Generate search queries to discover candidate options from user requirements without invented models."""
    queries: List[str] = []
    topic = brief.topic
    lower = topic.lower()

    queries.append(topic)

    if brief.entity_kind == "product":
        market_suffix = "India" if brief.market == "IN" else ""
        budget_str = f"under {int(brief.budget)} {brief.currency or ''}" if brief.budget else ""

        if "local ai" in lower or "llm" in lower:
            queries.append(f"best laptops for local AI models RTX {budget_str} {market_suffix}".strip())
            queries.append(f"laptop RTX 4060 8GB {budget_str} {market_suffix}".strip())
        elif "laptop" in lower:
            queries.append(f"best gaming laptops {budget_str} {market_suffix}".strip())
        elif "pixel" in lower and "galaxy" in lower:
            queries.append(f"Google Pixel vs Samsung Galaxy flagship comparison {market_suffix}".strip())
            queries.append("Google Pixel official specifications India")
            queries.append("Samsung Galaxy official specifications India")

    elif brief.entity_kind == "release":
        if "python" in lower:
            queries.append("site:python.org latest stable release")
            queries.append("python.org latest stable release")
            queries.append("site:python.org Python downloads release")
            queries.append("python.org Python downloads release")
        else:
            queries.append(f"{topic} official release notes")

    # Deduplicate while preserving order
    return list(dict.fromkeys(q.strip() for q in queries if q.strip()))


def verification_queries(candidate: Candidate, brief: ResearchBrief) -> List[str]:
    """Generate targeted verification queries on official and store domains for a candidate."""
    cand_name = candidate.name
    brand = candidate.brand or resolve_brand(cand_name)
    official_domains = get_official_domains(brand) if brand else []

    queries: List[str] = []
    # A bare exact-name query is the stable bridge to general web indexes.
    # Domain-scoped specification queries are retained below for provenance,
    # but some engines return no result when both the model and site: filter
    # are present.
    queries.append(cand_name)

    if official_domains:
        primary_domain = official_domains[0]
        # One query per evidence need; duplicated site/plain queries exhausted engines.
        queries.append(f"{cand_name} specifications site:{primary_domain}")
        # 2. Official price query
        queries.append(f"{cand_name} price site:{primary_domain}")
    else:
        queries.append(f"{cand_name} official specifications")
        queries.append(f"{cand_name} official price")

    # 3. Labelled retailer query for price verification in India
    if brief.market == "IN":
        queries.append(f"{cand_name} price amazon.in")
        queries.append(f"{cand_name} price flipkart.com")

    return list(dict.fromkeys(q.strip() for q in queries if q.strip()))


def build_plan(
    query: str,
    mode: ResearchMode,
    *,
    max_queries: int = 6,
    market: str = "IN",
    locale: str = "en-IN",
    domain_filters: Optional[List[str]] = None,
) -> ResearchPlan:
    cleaned = clean_query(query)
    requested_domains = list(
        dict.fromkeys(
            domain.strip()
            for domain in (domain_filters or explicit_domain_filters(query))
            if domain.strip()
        )
    )
    constraints: List[str] = []
    budget = _budget_constraint(query)
    if budget:
        constraints.append(budget)
    if re.search(r"\b(price|shopping|product|buy|iem|keyboard|laptop|phone)\b", query, re.I):
        constraints.extend([f"market={market}", f"locale={locale}"])

    queries = [ResearchQuery(cleaned, "primary")]
    lower = cleaned.lower()
    if mode in (ResearchMode.STANDARD, ResearchMode.DEEP):
        if "trend" in lower or "twitter" in lower or re.search(r"\bon\s+x\b", lower):
            queries.extend(
                [
                    ResearchQuery(
                        cleaned if requested_domains else f"{cleaned} site:x.com",
                        "platform evidence",
                        requested_domains or ["x.com", "twitter.com"],
                    ),
                    ResearchQuery(f"{cleaned} news", "independent corroboration"),
                ]
            )
        elif constraints and any(item.startswith("price") for item in constraints):
            queries.extend(
                [
                    ResearchQuery(f"{cleaned} {market} price", "market prices"),
                    ResearchQuery(
                        f"{cleaned} official specifications"
                        if re.search(r"\bofficial\s+sources?\b", query, re.I)
                        else f"{cleaned} reviews",
                        "product specifications" if re.search(r"\bofficial\s+sources?\b", query, re.I) else "independent reviews",
                    ),
                ]
            )
        else:
            queries.extend(
                [
                    ResearchQuery(f"{cleaned} official source", "primary source"),
                    ResearchQuery(f"{cleaned} independent reporting", "corroboration"),
                ]
            )
    if mode is ResearchMode.DEEP:
        queries.append(ResearchQuery(f"{cleaned} latest developments", "freshness check"))
    unique: List[ResearchQuery] = []
    seen = set()
    for item in queries:
        key = item.text.casefold()
        if key not in seen:
            unique.append(
                ResearchQuery(
                    item.text,
                    item.purpose,
                    list(requested_domains or item.domain_filters),
                )
            )
            seen.add(key)
    return ResearchPlan(
        goal=query,
        mode=mode,
        queries=unique[:max(1, max_queries)],
        constraints=constraints,
        required_freshness="current" if re.search(r"latest|current|today|now|trend", query, re.I) else "recent",
        domain_filters=requested_domains,
    )


async def search_plan(
    plan: ResearchPlan,
    providers: List[SearchProvider],
    *,
    limit: int,
    max_concurrency: int,
    progress: Optional[Callable[[int, int], None]] = None,
) -> List[SearchResult]:
    semaphore = asyncio.Semaphore(max(1, max_concurrency))
    completed = 0

    async def run(item: ResearchQuery) -> List[SearchResult]:
        nonlocal completed
        async with semaphore:
            result = await search_with_fallback(
                providers, item.text, limit=limit, domain_filters=item.domain_filters
            )
        completed += 1
        if progress:
            progress(completed, len(plan.queries))
        return result

    groups = await asyncio.gather(*(run(item) for item in plan.queries))
    merged: List[SearchResult] = []
    seen = set()
    for group in groups:
        for result in group:
            key = result.canonical_url.casefold()
            if key and key not in seen:
                merged.append(result)
                seen.add(key)
    return merged
