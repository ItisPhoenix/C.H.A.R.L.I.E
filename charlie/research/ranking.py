"""Deterministic source ranking and deduplication."""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone
from typing import Iterable, List, Optional
from urllib.parse import urlparse

from charlie.research.credibility import organisational_domain, rank_prior
from charlie.research.fetch import canonicalize_url
from charlie.research.models import ResearchPlan, SearchResult, SourceDocument
from charlie.research.semantics import SemanticRelevance
from charlie.research.sources import SourceClass, classify

logger = logging.getLogger("charlie.research.ranking")

_TOKEN_RE = re.compile(r"[a-z0-9]{3,}", re.I)
_STOPWORDS = {
    "about", "and", "are", "for", "from", "how", "is", "it", "its", "the", "this", "to",
    "use", "used", "what", "when", "where", "which", "with", "briefing", "daily", "intelligence",
    "best", "latest", "stable", "current", "official", "source", "sources", "using", "give", "tell",
    "version", "versions", "release", "releases", "date", "dates", "two", "three", "sentences",
    "under", "below", "within", "less", "than", "india", "inr", "rupees", "price", "prices",
}
_IDENTIFIER_RE = re.compile(r"\b[a-z]{2,}-\d+[a-z0-9-]*\b", re.I)

_SOURCE_WEIGHTS = {
    SourceClass.OFFICIAL: 0.35,
    SourceClass.OFFICIAL_STORE: 0.30,
    SourceClass.OFFICIAL_UNVERIFIED: 0.20,
    SourceClass.REFERENCE: 0.15,
    SourceClass.NEWS: 0.12,
    SourceClass.REVIEW: 0.10,
    SourceClass.RETAILER: 0.05,
    SourceClass.UNKNOWN: 0.0,
    SourceClass.FORUM_SOCIAL: -0.50,
}


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def search_result_matches_query(query: str, result: SearchResult) -> bool:
    """Reject pages that only match generic request wording.

    SearXNG can return HTTP 200 with a thematically unrelated page. Requiring
    one subject token for short queries and two for longer queries prevents
    those pages from becoming research evidence while retaining concise
    release and product searches.
    """
    terms = _tokens(query) - _STOPWORDS
    content = _tokens(f"{result.title} {result.snippet} {result.domain}")
    if not terms:
        return True
    required_overlap = 1 if len(terms) <= 3 else 2
    return len(terms & content) >= required_overlap


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

    # Only boost official sources when the user explicitly asked for official sources or specifications
    is_official_requested = bool(re.search(r"\b(official|specifications?|specs?|manufacturer)\b", query, re.I))
    s_class = classify(result.url)
    class_weight = 0.0
    if is_official_requested:
        if s_class in (SourceClass.OFFICIAL, SourceClass.OFFICIAL_STORE):
            class_weight = 0.20
        elif s_class == SourceClass.OFFICIAL_UNVERIFIED:
            class_weight = 0.10

    freshness = 0.0
    if _fresh_query(query) and _freshness_timestamp(result.published_at):
        age_days = max(
            0.0,
            (datetime.now(timezone.utc).timestamp() - _freshness_timestamp(result.published_at)) / 86400,
        )
        freshness = max(-0.35, 0.35 - min(age_days, 30) * 0.02)
    return overlap + class_weight + freshness + rank_prior(result.rank)


def rank_search_results(results: Iterable[SearchResult], plan: ResearchPlan, limit: int) -> List[SearchResult]:
    best: dict[str, SearchResult] = {}
    for result in results:
        if not search_result_matches_query(plan.goal, result):
            continue
        key = canonicalize_url(result.url)
        if key not in best or _score(plan.goal, result) > _score(plan.goal, best[key]):
            best[key] = result
    candidates = list(best.values())
    if _fresh_query(plan.goal):
        dated = [item for item in candidates if _freshness_timestamp(item.published_at)]
        if dated:
            newest = max(_freshness_timestamp(item.published_at) for item in dated)
            candidates = [
                item
                for item in candidates
                if not _freshness_timestamp(item.published_at)
                or newest - _freshness_timestamp(item.published_at) <= 45 * 86400
            ]
    ranked = sorted(candidates, key=lambda item: _score(plan.goal, item), reverse=True)
    return ranked[: max(1, limit)]


def _document_score(document: SourceDocument) -> float:
    """Balance topical relevance against content credibility.

    Ranking on relevance alone put a cybersecurity page at the top of an AI
    query, because bag-of-words overlap cannot tell a page about the topic from
    a page that merely shares vocabulary. Ranking on credibility alone would
    prefer a credible page that never answers the question. The geometric mean
    requires both to be decent: a page cannot win on one axis alone, and a
    perfect score on one axis cannot mask a poor score on the other.
    """
    relevance = max(0.0, min(1.0, document.relevance_score))
    credibility = max(0.0, min(1.0, document.quality_score))
    return math.sqrt(relevance * credibility)


def _normalise_semantic(scores: dict) -> dict:
    """Map raw cosine similarities onto [0, 1].

    Embedding backends cluster most related pairs above ~0.5, so a raw cosine
    of 0.55 is already a good match and must not be discarded as "no overlap".
    """
    if not scores:
        return {}
    low = min(scores.values())
    high = max(scores.values())
    if high - low < 1e-6:
        return {key: 1.0 for key in scores}
    span = high - low
    return {key: (value - low) / span for key, value in scores.items()}


def _token_relevance(query_tokens: set, document: SourceDocument) -> float:
    content_tokens = _tokens(f"{document.title} {document.content}")
    return len(query_tokens & content_tokens) / max(1, len(query_tokens))


async def rank_documents(
    documents: Iterable[SourceDocument],
    plan: ResearchPlan,
    limit: int,
    *,
    max_per_domain: int = 2,
    semantic: Optional[SemanticRelevance] = None,
) -> List[SourceDocument]:
    unique: dict[str, SourceDocument] = {}
    for document in documents:
        key = document.canonical_url or document.url
        if key not in unique or document.quality_score > unique[key].quality_score:
            unique[key] = document
    query_tokens = _tokens(plan.goal) - _STOPWORDS
    numeric_tokens = {token for token in query_tokens if any(char.isdigit() for char in token)}
    requires_identifier = bool(_IDENTIFIER_RE.search(plan.goal))

    semantic_scores = _normalise_semantic(semantic.scores) if semantic else {}
    semantic_mode = semantic.mode if semantic else "disabled"

    for document in unique.values():
        if not getattr(document, "source_class", None) or document.source_class == SourceClass.UNKNOWN.value:
            document.source_class = classify(document.url).value
        content_tokens = _tokens(f"{document.title} {document.content}")
        if requires_identifier and numeric_tokens and not numeric_tokens.intersection(content_tokens):
            document.relevance_score = 0.0
            continue
        overlap = _token_relevance(query_tokens, document)
        # Take whichever signal is more generous. Token overlap cannot detect a
        # shared topic with different vocabulary, and cosine cannot detect an
        # exact rare-token match, so the stronger evidence wins per document.
        semantic_score = semantic_scores.get(document.source_id)
        document.relevance_score = (
            max(overlap, semantic_score) if semantic_score is not None else overlap
        )

    candidates = [item for item in unique.values() if item.relevance_score > 0]
    candidates.sort(
        key=lambda item: (
            _document_score(item),
            _freshness_timestamp(item.published_at),
        ),
        reverse=True,
    )
    logger.debug(
        "Document ranking used %s relevance across %d candidate(s).",
        semantic_mode,
        len(candidates),
    )

    # Independence: one publisher cannot fill the citation list with its own
    # pages and masquerade as corroboration while alternatives remain. The cap
    # is relaxed only when the whole candidate set is smaller than the limit,
    # because returning fewer sources is worse than a repeated publisher, and
    # confidence counts distinct publishers so extra pages cannot inflate it.
    capped: List[SourceDocument] = []
    per_domain: dict[str, int] = {}
    deferred: List[SourceDocument] = []
    for document in candidates:
        host = organisational_domain(document.domain) or organisational_domain(
            urlparse(document.url).netloc
        )
        used = per_domain.get(host, 0)
        if host and used >= max_per_domain:
            deferred.append(document)
            continue
        if host:
            per_domain[host] = used + 1
        capped.append(document)
        if len(capped) >= limit:
            return capped
    if len(candidates) < limit:
        capped.extend(deferred[: max(0, limit - len(capped))])
    return capped[:limit]
