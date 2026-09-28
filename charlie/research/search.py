"""Query normalization, bounded planning, and concurrent search execution."""

from __future__ import annotations

import asyncio
import re
from typing import Callable, List, Optional

from charlie.research.models import ResearchMode, ResearchPlan, ResearchQuery, SearchResult
from charlie.research.providers import SearchProvider, search_with_fallback

_INSTRUCTION_RE = re.compile(
    r"\b(?:do\s+a\s+web\s+search|search\s+the\s+web|please|could\s+you|can\s+you|"
    r"tell\s+me|show\s+me|find\s+me|i\s+want\s+to\s+know|right\s+now|currently)\b",
    re.IGNORECASE,
)
_FORMAT_RE = re.compile(r"\b(?:be\s+short|under\s+\d+\s+words?|in\s+\d+\s+words?)\b", re.IGNORECASE)
_QUOTED_RE = re.compile(r'"[^"\n]*"|“[^”\n]*”|‘[^’\n]*’|(?<!\w)\'[^\'\n]*\'')
_ASSISTANT_PREFIX_RE = re.compile(r"^\s*(?:hey\s+)?charlie(?:\s*[,!:—-]\s*|\s+)", re.IGNORECASE)
_RESEARCH_PREFIX_RE = re.compile(r"^(?:research|investigate|look\s+into)\s+", re.IGNORECASE)
_FORMAT_INSTRUCTION_RE = re.compile(
    r"^\s*(?:please\s+)?(?:cite\b|include\s+(?:citations?|sources?)\b|"
    r"use\s+only\b|only\s+(?:cite|use)\b|provide\s+only\b|"
    r"answer\s+in\b|respond\s+in\b|keep\s+the\s+answer\b|"
    r"if\s+(?:you\s+)?find\s+(?:no|zero)\s+(?:fetched\s+)?(?:evidence|sources?)\b|"
    r"if\s+(?:you\s+)?(?:cannot|can't|do\s+not|don't)\s+find\s+(?:any\s+)?"
    r"(?:fetched\s+)?(?:evidence|sources?)\b)",
    re.IGNORECASE,
)
_EXPLICIT_DOMAIN_RE = re.compile(
    r"\b(?:site\s*:\s*|(?:on|from|at)\s+)(?:https?://)?(?:www\.)?"
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
                    ResearchQuery(f"{cleaned} reviews", "independent reviews"),
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
