"""Evidence extraction and bounded prompt formatting."""

from __future__ import annotations

import re
from typing import Iterable, List

from charlie.research.models import EvidenceItem, SourceDocument

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_INJECTION_RE = re.compile(r"\b(ignore|disregard)\s+(?:all\s+)?(?:previous|prior|system)\s+instructions?\b", re.I)


def _safe_sentence(sentence: str) -> str:
    if _INJECTION_RE.search(sentence):
        return "[untrusted instruction-like text omitted]"
    return sentence.strip()


def build_evidence(documents: Iterable[SourceDocument], query: str, max_items: int = 40) -> List[EvidenceItem]:
    query_terms = {term.lower() for term in re.findall(r"[a-z0-9]{3,}", query.lower())}
    evidence: List[EvidenceItem] = []
    for document in documents:
        sentences = [_safe_sentence(item) for item in _SENTENCE_RE.split(document.content)]
        relevant = [item for item in sentences if item and query_terms & set(re.findall(r"[a-z0-9]{3,}", item.lower()))]
        selected = (relevant or sentences)[:4]
        for sentence in selected:
            score = len(query_terms & set(re.findall(r"[a-z0-9]{3,}", sentence.lower()))) / max(1, len(query_terms))
            evidence.append(EvidenceItem(document.source_id, sentence[:700], score, min(1.0, document.quality_score)))
            if len(evidence) >= max_items:
                return evidence
    return evidence
