"""Evidence extraction and bounded prompt formatting."""

from __future__ import annotations

import hashlib
import re
from typing import Iterable, List, Tuple

from charlie.research.models import (
    EvidenceItem,
    Fact,
    SearchResult,
    SourceClass,
    SourceDocument,
)
from charlie.research.ranking import BRIEFING_INTENT_TERMS, search_result_matches_query
from charlie.research.router import is_briefing_query

_SEGMENT_RE = re.compile(r"(?<=[.!?])\s+|[\r\n]+")
_LIST_ITEM_RE = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+")
_DOTTED_VERSION_RE = re.compile(r"(?<!\d)\d+(?:\.\d+){1,3}(?!\d)")
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
    relevance_queries: Iterable[str] | None = None,
    per_source_max: int = DEFAULT_PER_SOURCE_MAX,
    max_items: int = DEFAULT_MAX_ITEMS,
    policy: str = "official_required",
) -> List[EvidenceItem]:
    """Extract grounded sentences and spec rows under a per-source budget.

    Splits on sentences and newlines to preserve spec-sheet rows and key: value lines.
    Excludes forum/social sources when policy is official_required.
    Preserves spec rows that match query terms OR contain fact-shaped tokens.
    """
    additional_queries = list(dict.fromkeys(relevance_queries or []))
    evidence_queries = list(dict.fromkeys([query, *additional_queries]))
    query_versions = list(dict.fromkeys(_DOTTED_VERSION_RE.findall(query)))
    planned_versions = {
        candidate: list(dict.fromkeys(_DOTTED_VERSION_RE.findall(candidate)))
        for candidate in additional_queries
    }
    query_term_sets = []
    for candidate in evidence_queries:
        terms = {
            normalize_research_term(term)
            for term in re.findall(r"[a-z0-9]{3,}", candidate.lower())
            if normalize_research_term(term) not in _STOPWORDS
        }
        if is_briefing_query(candidate):
            terms -= BRIEFING_INTENT_TERMS
        query_term_sets.append(terms)
    topicless_briefing = is_briefing_query(query) and not any(query_term_sets)
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

        raw_segments = []
        for paragraph_index, paragraph in enumerate(re.split(r"\n\s*\n", document.content)):
            for line in paragraph.splitlines():
                if not line.strip():
                    raw_segments.append(("", paragraph_index))
                elif _LIST_ITEM_RE.match(line):
                    raw_segments.append((line, paragraph_index))
                else:
                    raw_segments.extend(
                        (segment, paragraph_index) for segment in _SEGMENT_RE.split(line)
                    )
        scored: List[Tuple[float, int, str, int, int, str]] = []
        cursor = 0
        list_context_score = 0.0
        current_paragraph = -1
        version_anchors: dict[str, int] = {}

        for position, (raw, paragraph_index) in enumerate(raw_segments):
            if paragraph_index != current_paragraph:
                current_paragraph = paragraph_index
                version_anchors.clear()
                list_context_score = 0.0
            if not raw.strip():
                version_anchors.clear()
                continue
            active_version_anchors = set(version_anchors)
            version_anchors = {
                candidate: remaining - 1
                for candidate, remaining in version_anchors.items()
                if remaining > 1
            }
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
            sentence_result = SearchResult(
                document.title,
                document.url,
                sentence,
                published_at=document.published_at,
                domain=document.domain,
            )
            original_full_match = search_result_matches_query(query, sentence_result)
            original_context_match = (
                len(query_versions) == 1
                and query in active_version_anchors
                and search_result_matches_query(query, sentence_result, require_version=False)
            )
            planned_full_matches = [
                candidate for candidate in additional_queries
                if search_result_matches_query(candidate, sentence_result)
            ]
            planned_context_match = any(
                len(planned_versions[candidate]) == 1
                and candidate in active_version_anchors
                and search_result_matches_query(candidate, sentence_result, require_version=False)
                for candidate in additional_queries
            )
            if not (original_full_match or original_context_match) or (
                additional_queries and not (planned_full_matches or planned_context_match)
            ):
                continue

            sentence_terms = {
                normalize_research_term(term)
                for term in re.findall(r"[a-z0-9]{3,}", sentence.lower())
                if normalize_research_term(term) not in _STOPWORDS
            }

            term_overlap = max(
                min(1.0, len(query_terms & sentence_terms) / max(1, len(query_terms)))
                for query_terms in query_term_sets
            )
            is_list_item = bool(_LIST_ITEM_RE.match(sentence))
            is_list_heading = (
                not is_list_item
                and sentence.rstrip().endswith(":")
                and len(sentence.split()) <= 14
            )
            is_list_section = is_list_item and sentence.rstrip().endswith(":")
            if is_list_section or is_list_heading:
                list_context_score = term_overlap
            elif is_list_item:
                # shortcut: 14-word headings only; upgrade when fetchers preserve heading hierarchy.
                if list_context_score:
                    term_overlap = max(term_overlap, min(1.0, list_context_score + 0.25))
            else:
                list_context_score = 0.0
            # Model-quoted findings are retained independently through fact_evidence.
            if term_overlap == 0 and not (topicless_briefing and document.published_at):
                continue

            if original_full_match and (not additional_queries or planned_full_matches):
                sentence_versions = set(_DOTTED_VERSION_RE.findall(sentence))
                if len(query_versions) == 1 and query_versions[0] in sentence_versions:
                    version_anchors[query] = 2
                version_anchors.update(
                    {
                        candidate: 2 for candidate in planned_full_matches
                        if len(planned_versions[candidate]) == 1
                        and planned_versions[candidate][0] in sentence_versions
                    }
                )

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
