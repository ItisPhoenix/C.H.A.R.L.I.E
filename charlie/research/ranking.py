"""Deterministic source ranking and deduplication."""

from __future__ import annotations

import re
from typing import Iterable, List

from charlie.research.fetch import canonicalize_url
from charlie.research.models import ResearchPlan, SearchResult, SourceDocument

_TOKEN_RE = re.compile(r"[a-z0-9]{3,}", re.I)
_PRIMARY_HINTS = (".gov", ".edu", "github.com", "python.org", "openai.com", "x.com", "twitter.com")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _score(query: str, result: SearchResult) -> float:
    query_tokens = _tokens(query)
    result_tokens = _tokens(f"{result.title} {result.snippet} {result.domain}")
    overlap = len(query_tokens & result_tokens) / max(1, len(query_tokens))
    primary = 0.12 if any(hint in result.domain for hint in _PRIMARY_HINTS) else 0.0
    return overlap + primary + max(0.0, 0.04 - (result.rank * 0.005))


def rank_search_results(results: Iterable[SearchResult], plan: ResearchPlan, limit: int) -> List[SearchResult]:
    best: dict[str, SearchResult] = {}
    for result in results:
        key = canonicalize_url(result.url)
        if key not in best or _score(plan.goal, result) > _score(plan.goal, best[key]):
            best[key] = result
    ranked = sorted(best.values(), key=lambda item: _score(plan.goal, item), reverse=True)
    return ranked[: max(1, limit)]


def rank_documents(documents: Iterable[SourceDocument], plan: ResearchPlan, limit: int) -> List[SourceDocument]:
    unique: dict[str, SourceDocument] = {}
    for document in documents:
        key = document.canonical_url or document.url
        if key not in unique or document.quality_score > unique[key].quality_score:
            unique[key] = document
    query_tokens = _tokens(plan.goal)
    for document in unique.values():
        overlap = len(query_tokens & _tokens(document.content)) / max(1, len(query_tokens))
        document.relevance_score = overlap
        document.quality_score = min(1.0, document.quality_score + overlap * 0.4)
    return sorted(unique.values(), key=lambda item: (item.relevance_score, item.quality_score), reverse=True)[:limit]
