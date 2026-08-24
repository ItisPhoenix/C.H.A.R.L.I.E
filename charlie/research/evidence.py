"""Evidence extraction and bounded prompt formatting."""

from __future__ import annotations

import re
from typing import Iterable, List

from charlie.research.models import EvidenceItem, SourceDocument

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_INJECTION_RE = re.compile(r"\b(ignore|disregard)\s+(?:all\s+)?(?:previous|prior|system)\s+instructions?\b", re.I)
_STOPWORDS = {
    "about", "and", "are", "for", "from", "how", "is", "it", "its", "the", "this",
    "to", "use", "used", "what", "when", "where", "which", "with",
}


def _safe_sentence(sentence: str) -> str:
    if _INJECTION_RE.search(sentence):
        return "[untrusted instruction-like text omitted]"
    return sentence.strip()


def build_evidence(documents: Iterable[SourceDocument], query: str, max_items: int = 40) -> List[EvidenceItem]:
    query_terms = {
        term.lower()
        for term in re.findall(r"[a-z0-9]{3,}", query.lower())
        if term.lower() not in _STOPWORDS
    }
    evidence: List[EvidenceItem] = []
    seen: set[tuple[str, str]] = set()
    for document in documents:
        sentences = [_safe_sentence(item) for item in _SENTENCE_RE.split(document.content)]
        for sentence in sentences:
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
            evidence.append(EvidenceItem(document.source_id, sentence[:700], score, min(1.0, document.quality_score)))
            if len(evidence) >= max_items:
                return evidence
    return evidence
