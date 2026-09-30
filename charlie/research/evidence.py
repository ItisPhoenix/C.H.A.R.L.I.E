"""Evidence extraction and bounded prompt formatting."""

from __future__ import annotations

import re
from typing import Iterable, List, Tuple

from charlie.research.models import EvidenceItem, SourceDocument

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_INJECTION_RE = re.compile(r"\b(ignore|disregard)\s+(?:all\s+)?(?:previous|prior|system)\s+instructions?\b", re.I)
_STOPWORDS = {
    "about", "and", "are", "for", "from", "how", "is", "it", "its", "the", "this",
    "to", "use", "used", "what", "when", "where", "which", "with",
}

DEFAULT_PER_SOURCE_MAX = 6
DEFAULT_MAX_ITEMS = 120
MAX_STATEMENT_CHARS = 700


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
) -> List[EvidenceItem]:
    """Extract grounded sentences under a per-source budget, not a global one.

    The budget is deliberately per source.  A single global cap behaves as a
    first-document-wins policy: one verbose page consumes the whole budget and
    every later document contributes nothing, which then drops those documents
    from the grounded source list downstream.  Each source therefore keeps up
    to ``per_source_max`` of its own best-matching sentences, and ``max_items``
    is only an overall ceiling.  Keep the ceiling at least
    ``per_source_max * len(documents)`` or starvation returns.

    Within a source, sentences are deduplicated by ``(source_id, sentence)`` and
    ranked by relevance to the query, with ties resolved by document order so
    extraction stays deterministic.  Documents are emitted in input order so
    evidence stays aligned with the source list and its citation IDs.
    """
    query_terms = {
        term.lower()
        for term in re.findall(r"[a-z0-9]{3,}", query.lower())
        if term.lower() not in _STOPWORDS
    }
    per_source = max(1, per_source_max)
    ceiling = max(1, max_items)
    evidence: List[EvidenceItem] = []
    seen: set[tuple[str, str]] = set()
    for document in documents:
        if len(evidence) >= ceiling:
            break
        sentences = [_safe_sentence(item) for item in _SENTENCE_RE.split(document.content)]
        scored: List[Tuple[float, int, str]] = []
        for position, sentence in enumerate(sentences):
            sentence_terms = {
                term.lower()
                for term in re.findall(r"[a-z0-9]{3,}", sentence.lower())
                if term.lower() not in _STOPWORDS
            }
            if not sentence or not query_terms.intersection(sentence_terms):
                continue
            key = (document.source_id, sentence.casefold())
            if key in seen:
                continue
            seen.add(key)
            score = len(query_terms & sentence_terms) / max(1, len(query_terms))
            scored.append((score, position, sentence))
        if not scored:
            continue
        confidence = min(1.0, document.quality_score)
        for score, _position, sentence in sorted(scored, key=lambda item: (-item[0], item[1]))[:per_source]:
            evidence.append(EvidenceItem(document.source_id, sentence[:MAX_STATEMENT_CHARS], score, confidence))
    return evidence
