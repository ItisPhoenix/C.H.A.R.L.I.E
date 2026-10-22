"""Deterministic source ranking and deduplication."""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable, List, Optional
from urllib.parse import urlparse

from charlie.research.credibility import organisational_domain, rank_prior
from charlie.research.fetch import canonicalize_url
from charlie.research.models import ResearchPlan, SearchResult, SourceDocument
from charlie.research.router import is_briefing_query
from charlie.research.semantics import SemanticRelevance
from charlie.research.sources import SourceClass, classify

logger = logging.getLogger("charlie.research.ranking")

_TOKEN_RE = re.compile(r"[a-z0-9]{2,}", re.I)
_STOPWORDS = {
    "about", "and", "are", "as", "at", "be", "by", "do", "for", "from", "how", "in", "is", "it", "its", "of", "on", "or", "the", "this", "to", "vs",
    "use", "used", "what", "when", "where", "which", "with", "briefing", "daily", "intelligence",
    "best", "official", "source", "sources", "using", "give", "tell",
    "two", "three", "sentences",
    "compare",
    "under", "below", "within", "less", "than",
}
BRIEFING_INTENT_TERMS = {
    "daily", "intelligence", "latest", "current", "today", "news", "week", "weekly",
}
_IDENTIFIER_RE = re.compile(r"\b[a-z]{2,}-\d+[a-z0-9-]*\b", re.I)
_VERSION_RE = re.compile(r"(?<!\d)\d+(?:\.\d+){1,3}(?!\d)")
_CAPITALIZED_ENTITY_PHRASE_RE = re.compile(r"\b[A-Z][a-z0-9]+(?:\s+[A-Z][a-z0-9]+)+\b")
_ACRONYM_RE = re.compile(r"\b[A-Z]{2,}\b")

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


def _query_entity_tokens(query: str) -> set[str]:
    words = query.split()
    phrases = _CAPITALIZED_ENTITY_PHRASE_RE.findall(query)
    acronyms = _ACRONYM_RE.findall(query)
    # A lone capitalised word is an entity too; skip the sentence-initial one.
    singles = [w for i, w in enumerate(words) if i and w[:1].isupper() and w[1:].islower()]
    return {
        token
        for value in [*phrases, *acronyms, *singles]
        for token in _tokens(value)
        if token not in _STOPWORDS
    }


def search_result_matches_query(
    query: str, result: SearchResult, *, require_version: bool = True,
) -> bool:
    """Reject pages that only match generic request wording.

    SearXNG can return HTTP 200 with a thematically unrelated page. Requiring
    enough of a multiword entity prevents incidental mentions from becoming
    research evidence while retaining single-name product searches.
    """
    briefing = is_briefing_query(query)
    week_bounded = bool(re.search(r"\b(?:this|last|past) week\b", query, re.I))
    if (briefing or week_bounded) and not _freshness_timestamp(result.published_at):
        return False
    terms = _tokens(query) - _STOPWORDS
    if briefing:
        terms -= BRIEFING_INTENT_TERMS
    content_text = f"{result.title} {result.snippet} {result.domain} {result.url}"
    versions = _VERSION_RE.findall(query)
    if require_version and versions and not any(
        re.search(rf"(?<!\d){re.escape(version)}(?!\d)", content_text)
        for version in versions
    ):
        return False
    content = _tokens(content_text)
    if not terms:
        return briefing
    entity_terms = _query_entity_tokens(query)
    acronym_terms = {
        token
        for value in _ACRONYM_RE.findall(query)
        for token in _tokens(value)
        if token not in _STOPWORDS
    }
    if acronym_terms and not (acronym_terms & content):
        return False
    entity_phrases = [
        _tokens(value) - _STOPWORDS
        for value in _CAPITALIZED_ENTITY_PHRASE_RE.findall(query)
    ]
    entity_phrases = [phrase for phrase in entity_phrases if phrase]
    if entity_phrases:
        if not any(
            len(phrase & content) >= min(2, len(phrase))
            for phrase in entity_phrases
        ):
            return False
    elif entity_terms and not (entity_terms & content):
        return False
    if len(terms) == 1 and len(next(iter(terms))) == 2:
        identity = _tokens(f"{result.title} {result.domain or urlparse(result.url).hostname or ''}")
        return bool(terms & identity)
    return len(terms & content) >= 1


def _freshness_timestamp(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        return parsed.timestamp()
    except (OverflowError, OSError, ValueError):
        return 0.0


def _fresh_query(query: str) -> bool:
    return bool(re.search(
        r"\b(today|current|latest|daily briefing|intelligence briefing|news|"
        r"(?:this|last|past) week)\b",
        query,
        re.I,
    ))


def _freshness_bounds(query: str) -> tuple[float, float]:
    now = datetime.now(timezone.utc)
    week_start = now.replace(
        hour=0, minute=0, second=0, microsecond=0
    ) - timedelta(days=now.weekday())
    if re.search(r"\bthis week\b", query, re.I):
        return week_start.timestamp(), now.timestamp()
    if re.search(r"\blast week\b", query, re.I):
        return (week_start - timedelta(days=7)).timestamp(), week_start.timestamp()
    if re.search(r"\bpast week\b", query, re.I):
        return (now - timedelta(days=7)).timestamp(), now.timestamp()
    return (now - timedelta(days=45)).timestamp(), now.timestamp()


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
    queries = [item.text for item in plan.queries] or [plan.goal]
    for result in results:
        if not search_result_matches_query(plan.goal, result) or not any(
            search_result_matches_query(query, result) for query in queries
        ):
            continue
        key = canonicalize_url(result.url)
        if key not in best or _score(plan.goal, result) > _score(plan.goal, best[key]):
            best[key] = result
    candidates = list(best.values())
    if _fresh_query(plan.goal):
        cutoff, upper_bound = _freshness_bounds(plan.goal)
        week_bounded = bool(re.search(r"\b(?:this|last|past) week\b", plan.goal, re.I))
        last_week = bool(re.search(r"\blast week\b", plan.goal, re.I))
        fresh_candidates = []
        for item in candidates:
            published_at = _freshness_timestamp(item.published_at)
            in_window = (
                cutoff <= published_at < upper_bound
                if last_week
                else cutoff <= published_at <= upper_bound
            )
            if published_at and in_window:
                fresh_candidates.append(item)
            elif not published_at and not week_bounded:
                fresh_candidates.append(item)
        candidates = fresh_candidates
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
    """Clamp cosine scores; orthogonal vectors must not become relevance 0.5."""
    return {key: max(0.0, min(1.0, value)) for key, value in scores.items()}


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

    relevance_queries = [item.text for item in plan.queries] or [plan.goal]
    for document in unique.values():
        if not getattr(document, "source_class", None) or document.source_class == SourceClass.UNKNOWN.value:
            document.source_class = classify(document.url).value
        document_result = SearchResult(
            document.title,
            document.url,
            document.content,
            published_at=document.published_at,
            domain=document.domain,
        )
        if not search_result_matches_query(plan.goal, document_result) or not any(
            search_result_matches_query(query, document_result) for query in relevance_queries
        ):
            document.relevance_score = 0.0
            continue
        content_tokens = _tokens(f"{document.title} {document.content}")
        if requires_identifier and numeric_tokens and not numeric_tokens.intersection(content_tokens):
            document.relevance_score = 0.0
            continue
        overlap = _token_relevance(query_tokens, document)
        # Take whichever signal is more generous. Token overlap cannot detect a
        # shared topic with different vocabulary, and cosine cannot detect an
        # exact rare-token match, so the stronger evidence wins per document.
        semantic_score = semantic_scores.get(document.document_id)
        if semantic_score is None:
            semantic_score = semantic_scores.get(document.source_id)
        if semantic_score is None:
            semantic_score = semantic_scores.get(document.canonical_url or document.url)
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
