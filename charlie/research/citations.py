"""Citation IDs and post-synthesis validation."""

from __future__ import annotations

import re
from typing import Iterable, List, Set

from charlie.research.models import Citation, SearchResult

_CITATION_RE = re.compile(r"\[(S\d+)\]")


def assign_citations(documents) -> List[Citation]:
    citations: List[Citation] = []
    for index, document in enumerate(documents, start=1):
        document.source_id = f"S{index}"
        citations.append(Citation(document.source_id, document.url, document.title, document.domain))
    return citations


def assign_search_citations(results: Iterable[SearchResult], limit: int = 8) -> List[Citation]:
    """Give snippet-only QUICK results stable source IDs too."""
    return [
        Citation(f"S{index}", result.url, result.title, result.domain)
        for index, result in enumerate(list(results)[:limit], start=1)
    ]


def referenced_ids(text: str) -> Set[str]:
    return set(_CITATION_RE.findall(text or ""))


def validate_citations(text: str, citations: Iterable[Citation]) -> bool:
    valid = {item.source_id for item in citations}
    return referenced_ids(text).issubset(valid)


def strip_invalid_citations(text: str, citations: Iterable[Citation]) -> str:
    valid = {item.source_id for item in citations}
    return _CITATION_RE.sub(lambda match: match.group(0) if match.group(1) in valid else "", text or "")


def strip_citation_markers(text: str) -> str:
    """Remove visual source markers from speech while retaining answer text."""
    return _CITATION_RE.sub("", text or "").replace("  ", " ").strip()
