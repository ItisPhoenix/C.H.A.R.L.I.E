"""Evidence extraction and bounded prompt formatting."""

from __future__ import annotations

import hashlib
import re
from typing import Iterable, List, Tuple

from charlie.research.models import EvidenceItem, Fact, SourceClass, SourceDocument

_SEGMENT_RE = re.compile(r"(?<=[.!?])\s+|[\r\n]+")
_INJECTION_RE = re.compile(r"\b(ignore|disregard)\s+(?:all\s+)?(?:previous|prior|system)\s+instructions?\b", re.I)


def fact_evidence(documents: Iterable[SourceDocument], facts: Iterable[Fact]) -> List[EvidenceItem]:
    """Retain exact cited fact spans even when topical lexical scoring misses them."""
    sources = {source.source_id: source for source in documents}
    retained = {}
    for fact in facts:
        source = sources.get(fact.source_id)
        if source is None or not fact.quote or _INJECTION_RE.search(fact.quote):
            continue
        pattern = r"\s+".join(re.escape(word) for word in fact.quote.split())
        match = re.search(pattern, source.content, re.I)
        if match is None:
            continue
        start, end = match.span()
        passage_id = hashlib.sha256(
            f"{source.document_id}\0{source.content_hash}\0{start}\0{end}".encode("utf-8")
        ).hexdigest()[:24]
        retained[passage_id] = EvidenceItem(
            source.source_id, match.group(), 1.0, min(1.0, source.quality_score),
            passage_id=passage_id, start_offset=start, end_offset=end,
            document_hash=source.content_hash,
        )
    return list(retained.values())


_NEGATED_RELEVANCE_RE = re.compile(
    r"\b(?:unrelated\s+to|not\s+(?:about|relevant\s+to|related\s+to)|no\s+(?:relevance|relation)\s+to)\b",
    re.I,
)
_STOPWORDS = {
    "about", "and", "are", "for", "from", "how", "is", "it", "its", "the", "this",
    "to", "use", "used", "what", "when", "where", "which", "with",
}

DEFAULT_PER_SOURCE_MAX = 6
DEFAULT_MAX_ITEMS = 120
MAX_STATEMENT_CHARS = 700


def normalize_research_term(value: str) -> str:
    token = value.casefold()
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("s") and len(token) > 3 and not token.endswith("ss"):
        return token[:-1]
    return token


def _safe_sentence(sentence: str) -> str:
    if _INJECTION_RE.search(sentence):
        return "[untrusted instruction-like text omitted]"
    return sentence.strip()


def build_evidence(
    documents: Iterable[SourceDocument],
    query: str,
    *,
    per_source_max: int = DEFAULT_PER_SOURCE_MAX,
    max_items: int = DEFAULT_MAX_ITEMS,
    policy: str = "official_required",
) -> List[EvidenceItem]:
    """Extract grounded sentences and spec rows under a per-source budget.

    Splits on sentences and newlines to preserve spec-sheet rows and key: value lines.
    Excludes forum/social sources when policy is official_required.
    Preserves spec rows that match query terms OR contain fact-shaped tokens.
    """
    base_query_terms = {
        normalize_research_term(term)
        for term in re.findall(r"[a-z0-9]{3,}", query.lower())
        if normalize_research_term(term) not in _STOPWORDS
    }
    query_terms = base_query_terms
    per_source = max(1, per_source_max)
    ceiling = max(1, max_items)
    evidence: List[EvidenceItem] = []
    seen: set[tuple[str, str]] = set()

    for document in documents:
        if len(evidence) >= ceiling:
            break

        # Forum/social sources produce no evidence when policy is official_required
        doc_class = getattr(document, "source_class", None)
        if policy == "official_required" and doc_class == SourceClass.FORUM_SOCIAL.value:
            continue

        raw_segments = _SEGMENT_RE.split(document.content)
        scored: List[Tuple[float, int, str, int, int, str]] = []
        cursor = 0

        for position, raw in enumerate(raw_segments):
            start = document.content.find(raw, cursor)
            if start < 0:
                start = cursor
            cursor = start + len(raw)
            if _INJECTION_RE.search(raw):
                continue
            sentence = _safe_sentence(raw)
            if not sentence or len(sentence) < 4:
                continue
            if _NEGATED_RELEVANCE_RE.search(sentence):
                continue

            sentence_terms = {
                normalize_research_term(term)
                for term in re.findall(r"[a-z0-9]{3,}", sentence.lower())
                if normalize_research_term(term) not in _STOPWORDS
            }

            term_overlap = min(
                1.0,
                len(query_terms & sentence_terms) / max(1, len(base_query_terms)),
            )
            # Model-quoted findings are retained independently through fact_evidence.
            if term_overlap == 0:
                continue

            key = (document.source_id, sentence.casefold())
            if key in seen:
                continue
            seen.add(key)

            score = term_overlap
            start_offset = start + raw.find(sentence)
            statement = sentence[:MAX_STATEMENT_CHARS]
            end_offset = start_offset + len(statement)
            passage_id = hashlib.sha256(
                f"{document.document_id}\0{document.content_hash}\0{start_offset}\0{end_offset}".encode("utf-8")
            ).hexdigest()[:24]
            scored.append((score, position, statement, start_offset, end_offset, passage_id))

        if not scored:
            continue

        confidence = min(1.0, document.quality_score)
        for score, _position, statement, start_offset, end_offset, passage_id in sorted(
            scored, key=lambda item: (-item[0], item[1])
        )[:per_source]:
            evidence.append(
                EvidenceItem(
                    document.source_id,
                    statement,
                    score,
                    confidence,
                    passage_id=passage_id,
                    start_offset=start_offset,
                    end_offset=end_offset,
                    document_hash=document.content_hash,
                )
            )

    return evidence
