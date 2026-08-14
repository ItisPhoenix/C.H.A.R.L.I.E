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
_SPACE_RE = re.compile(r"\s+")


def clean_query(query: str) -> str:
    """Remove conversational/formatting noise without splitting user intent."""
    cleaned = _INSTRUCTION_RE.sub(" ", query).strip()
    cleaned = _FORMAT_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\bwhat(?:'s| is)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^(?:and|then)\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[?!.,;:]+$", "", cleaned).strip()
    cleaned = re.sub(r"[?!.,;:]+$", "", cleaned).strip()
    cleaned = _SPACE_RE.sub(" ", cleaned)
    return cleaned or query.strip()


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
) -> ResearchPlan:
    cleaned = clean_query(query)
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
                    ResearchQuery(f"{cleaned} site:x.com", "platform evidence", ["x.com", "twitter.com"]),
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
            unique.append(item)
            seen.add(key)
    return ResearchPlan(
        goal=query,
        mode=mode,
        queries=unique[:max(1, max_queries)],
        constraints=constraints,
        required_freshness="current" if re.search(r"latest|current|today|now|trend", query, re.I) else "recent",
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
