"""Deterministic source ranking and deduplication."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterable, List

from charlie.research.fetch import canonicalize_url
from charlie.research.models import ResearchPlan, SearchResult, SourceDocument

_TOKEN_RE = re.compile(r"[a-z0-9]{3,}", re.I)
_PRIMARY_HINTS = (".gov", ".edu", "github.com", "python.org", "openai.com", "x.com", "twitter.com")
_STOPWORDS = {
    "about", "and", "are", "for", "from", "how", "is", "it", "its", "the", "this", "to",
    "use", "used", "what", "when", "where", "which", "with", "briefing", "daily", "intelligence",
}
_IDENTIFIER_RE = re.compile(r"\b[a-z]{2,}-\d+[a-z0-9-]*\b", re.I)


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _freshness_timestamp(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError):
        return 0.0


def _fresh_query(query: str) -> bool:
    return bool(re.search(r"\b(today|current|latest|daily briefing|intelligence briefing|news)\b", query, re.I))


def _score(query: str, result: SearchResult) -> float:
    query_tokens = _tokens(query) - _STOPWORDS
    result_tokens = _tokens(f"{result.title} {result.snippet} {result.domain}")
    overlap = len(query_tokens & result_tokens) / max(1, len(query_tokens))
    primary = 0.12 if any(hint in result.domain for hint in _PRIMARY_HINTS) else 0.0
    freshness = 0.0
    if _fresh_query(query) and _freshness_timestamp(result.published_at):
        age_days = max(0.0, (datetime.now(timezone.utc).timestamp() - _freshness_timestamp(result.published_at)) / 86400)
        freshness = max(-0.35, 0.35 - min(age_days, 30) * 0.02)
    return overlap + primary + freshness + max(0.0, 0.04 - (result.rank * 0.005))


def rank_search_results(results: Iterable[SearchResult], plan: ResearchPlan, limit: int) -> List[SearchResult]:
    best: dict[str, SearchResult] = {}
    for result in results:
        key = canonicalize_url(result.url)
        if key not in best or _score(plan.goal, result) > _score(plan.goal, best[key]):
            best[key] = result
    candidates = list(best.values())
    if _fresh_query(plan.goal):
        dated = [item for item in candidates if _freshness_timestamp(item.published_at)]
        if dated:
            newest = max(_freshness_timestamp(item.published_at) for item in dated)
            candidates = [item for item in candidates if not _freshness_timestamp(item.published_at) or newest - _freshness_timestamp(item.published_at) <= 45 * 86400]
    ranked = sorted(candidates, key=lambda item: _score(plan.goal, item), reverse=True)
    return ranked[: max(1, limit)]


def rank_documents(documents: Iterable[SourceDocument], plan: ResearchPlan, limit: int) -> List[SourceDocument]:
    unique: dict[str, SourceDocument] = {}
    for document in documents:
        key = document.canonical_url or document.url
        if key not in unique or document.quality_score > unique[key].quality_score:
            unique[key] = document
    query_tokens = _tokens(plan.goal) - _STOPWORDS
    numeric_tokens = {token for token in query_tokens if any(char.isdigit() for char in token)}
    requires_identifier = bool(_IDENTIFIER_RE.search(plan.goal))
    for document in unique.values():
        content_tokens = _tokens(f"{document.title} {document.content}")
        if requires_identifier and numeric_tokens and not numeric_tokens.intersection(content_tokens):
            document.relevance_score = 0.0
            continue
        overlap = len(query_tokens & content_tokens) / max(1, len(query_tokens))
        document.relevance_score = overlap
        document.quality_score = min(1.0, document.quality_score + overlap * 0.4)
    return sorted(
        (item for item in unique.values() if item.relevance_score > 0),
        key=lambda item: (
            item.relevance_score,
            _freshness_timestamp(item.published_at),
            item.quality_score,
        ),
        reverse=True,
    )[:limit]
