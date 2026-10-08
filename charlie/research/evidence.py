"""Evidence extraction and bounded prompt formatting."""

from __future__ import annotations

import re
from typing import Iterable, List, Optional, Tuple

from charlie.research.models import EvidenceItem, SourceClass, SourceDocument

_SEGMENT_RE = re.compile(r"(?<=[.!?])\s+|[\r\n]+")
_INJECTION_RE = re.compile(r"\b(ignore|disregard)\s+(?:all\s+)?(?:previous|prior|system)\s+instructions?\b", re.I)
_STOPWORDS = {
    "about", "and", "are", "for", "from", "how", "is", "it", "its", "the", "this",
    "to", "use", "used", "what", "when", "where", "which", "with",
}

_FACT_SHAPED_RE = re.compile(
    r"(?:₹|rs\.?|inr|\b\d+\s*gb\b|\brtx\b|\bgpu\b|\bvram\b|\bram\b|\bso-dimm\b|\bddr[45]\b|"
    r"\bssd\b|\bintel\b|\bamd\b|\bryzen\b|\bcore\s+i[3579]\b|\bnvidia\b|\bversion\b|\brelease\b|"
    r"\bpython\s*\d+\.\d+\b|\bcamera\b|\bdisplay\b|\bspecifications?\b)",
    re.I,
)

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
    policy: str = "official_required",
) -> List[EvidenceItem]:
    """Extract grounded sentences and spec rows under a per-source budget.

    Splits on sentences and newlines to preserve spec-sheet rows and key: value lines.
    Excludes forum/social sources when policy is official_required.
    Preserves spec rows that match query terms OR contain fact-shaped tokens.
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

        # Forum/social sources produce no evidence when policy is official_required
        doc_class = getattr(document, "source_class", None)
        if policy == "official_required" and doc_class == SourceClass.FORUM_SOCIAL.value:
            continue

        raw_segments = _SEGMENT_RE.split(document.content)
        scored: List[Tuple[float, int, str]] = []

        for position, raw in enumerate(raw_segments):
            sentence = _safe_sentence(raw)
            if not sentence or len(sentence) < 4:
                continue

            sentence_terms = {
                term.lower()
                for term in re.findall(r"[a-z0-9]{3,}", sentence.lower())
                if term.lower() not in _STOPWORDS
            }

            term_overlap = len(query_terms & sentence_terms) / max(1, len(query_terms))
            has_fact_shape = bool(_FACT_SHAPED_RE.search(sentence))

            # Keep segment if it shares query terms OR has relevant fact shape (like specs/prices)
            if term_overlap == 0 and not has_fact_shape:
                continue

            key = (document.source_id, sentence.casefold())
            if key in seen:
                continue
            seen.add(key)

            # Score combines term overlap and fact presence
            score = term_overlap + (0.35 if has_fact_shape else 0.0)
            scored.append((score, position, sentence))

        if not scored:
            continue

        confidence = min(1.0, document.quality_score)
        for score, _position, sentence in sorted(scored, key=lambda item: (-item[0], item[1]))[:per_source]:
            evidence.append(EvidenceItem(document.source_id, sentence[:MAX_STATEMENT_CHARS], score, confidence))

    return evidence
