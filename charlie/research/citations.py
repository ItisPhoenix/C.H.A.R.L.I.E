"""Citation IDs and post-synthesis validation."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, List, Set

from charlie.research.models import Claim, Citation, SearchResult

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


def validate_numeric_grounding(text: str, facts_or_report: Any) -> bool:
    """Require each numeric claim to match its cited source or a verified fact."""
    if hasattr(facts_or_report, "facts"):
        report = facts_or_report
        facts = getattr(report, "facts", [])
    elif isinstance(facts_or_report, list):
        report = None
        facts = facts_or_report
    else:
        return True

    number_re = re.compile(r"(?<![A-Za-z0-9_])\d[\d,]*(?:\.\d+)*(?![A-Za-z0-9_])")

    def numbers(value: str) -> set[str]:
        return {item.replace(",", "") for item in number_re.findall(value or "")}

    if report is None:
        fact_numbers = set().union(
            *(numbers(f"{getattr(fact, 'value', '')} {getattr(fact, 'quote', '')}") for fact in facts)
        )
        return numbers(re.sub(r"\[S\d+\]", "", text)).issubset(fact_numbers)

    sources_by_id = {
        source.source_id: numbers(f"{source.title} {source.content[:25000]}")
        for source in getattr(report, "sources", [])
        if source.source_id
    }
    facts_by_source: dict[str, set[str]] = {}
    for fact in facts:
        facts_by_source.setdefault(getattr(fact, "source_id", ""), set()).update(
            numbers(f"{getattr(fact, 'value', '')} {getattr(fact, 'quote', '')}")
        )
    query_numbers = numbers(getattr(report, "query", ""))
    passages_by_id = {
        passage.passage_id: passage for passage in getattr(report, "passages", [])
    }
    contradicted_numbers: set[str] = set()
    contradicted_sources: set[str] = set()
    for question in getattr(report, "coverage", []):
        if question.status != "contradicted":
            continue
        for requirement in question.required_fields:
            if requirement.startswith("license_proposition:"):
                contradicted_numbers.update(numbers(requirement.split(":", 1)[1]))
        contradicted_sources.update(
            passages_by_id[passage_id].source_id
            for passage_id in question.refuting_claim_ids
            if passage_id in passages_by_id
        )
    for sentence in re.split(r"(?<=[!?])\s+(?!\[S\d+\])|(?<=\.)\s+(?!\[S\d+\])|\n+", text):
        sentence_numbers = numbers(re.sub(r"\[S\d+\]", "", sentence))
        if not sentence_numbers:
            continue
        cited_ids = _CITATION_RE.findall(sentence)
        for value in sentence_numbers:
            if value in query_numbers and re.search(r"\b(?:budget|under|within)\b", sentence, re.I):
                continue
            if (
                value in contradicted_numbers
                and contradicted_sources.intersection(cited_ids)
                and re.search(r"\b(?:no|not|false|contradict(?:s|ed)?|rather than|instead)\b", sentence, re.I)
            ):
                continue
            if not cited_ids or not any(
                value in (sources_by_id.get(source_id, set()) | facts_by_source.get(source_id, set()))
                for source_id in cited_ids
            ):
                return False
    return True


def validate_claim_support(text: str, report: Any) -> bool:
    """Bind every factual sentence to cited, retained evidence passages."""
    stopwords = {
        "a", "an", "and", "are", "as", "at", "be", "by", "can", "does", "for", "from",
        "has", "have", "how", "in", "is", "it", "its", "of", "on", "or", "that", "the",
        "their", "this", "to", "use", "used", "uses", "was", "were", "what", "when", "which",
        "with", "according", "documentation", "docs", "project", "source",
    }
    evidence_by_source: dict[str, list[str]] = {}
    for item in getattr(report, "evidence", []):
        evidence_by_source.setdefault(item.source_id, []).append(item.statement)
    for fact in getattr(report, "facts", []):
        evidence_by_source.setdefault(getattr(fact, "source_id", ""), []).append(
            f"{getattr(fact, 'candidate', '')} {getattr(fact, 'aspect', '')} "
            f"{getattr(fact, 'value', '')} {getattr(fact, 'quote', '')}"
        )
    valid_ids = {citation.source_id for citation in getattr(report, "citations", [])}
    passages_by_id = {
        passage.passage_id: passage for passage in getattr(report, "passages", [])
    }
    passages_by_source: dict[str, list[Any]] = {}
    sources_by_id = {source.source_id: source for source in getattr(report, "sources", [])}
    for passage in passages_by_id.values():
        source = sources_by_id.get(passage.source_id)
        if (
            source is None
            or source.content_hash != passage.document_hash
            or source.content[passage.start_offset:passage.end_offset] != passage.text
        ):
            return False
        passages_by_source.setdefault(passage.source_id, []).append(passage)
    cited_ids_in_answer = referenced_ids(text) & valid_ids

    for question in getattr(report, "coverage", []):
        passage_ids = question.refuting_claim_ids if question.status == "contradicted" else (
            question.supporting_claim_ids + question.refuting_claim_ids if question.conflict else []
        )
        required_sources = {
            passages_by_id[passage_id].source_id
            for passage_id in passage_ids
            if passage_id in passages_by_id
        }
        if question.status == "contradicted":
            if not required_sources or not required_sources.issubset(cited_ids_in_answer):
                return False
            if not re.search(r"\b(?:no|not|false|contradict(?:s|ed)?|rather than|instead)\b", text, re.I):
                return False
        elif question.conflict:
            if not required_sources or not required_sources.issubset(cited_ids_in_answer):
                return False
            conflict_terms = r"\b(?:conflict(?:ing)?|disagree|different|differ|inconsistent|unresolved|whereas|both)\b"
            if not re.search(conflict_terms, text, re.I):
                return False

    def terms(value: str) -> set[str]:
        return {
            token.casefold()
            for token in re.findall(r"[A-Za-z0-9]+", value)
            if token.casefold() not in stopwords
        }

    number_re = re.compile(r"(?<![A-Za-z0-9_])\d[\d,]*(?:\.\d+)*(?![A-Za-z0-9_])")

    def numbers(value: str) -> set[str]:
        return {item.replace(",", "") for item in number_re.findall(value or "")}

    def normalized(value: str) -> str:
        return " ".join(value.split()).casefold()

    refuted_numbers = {
        number
        for question in getattr(report, "coverage", [])
        if question.status == "contradicted"
        for requirement in question.required_fields
        if requirement.startswith("license_proposition:")
        for number in numbers(requirement.split(":", 1)[1])
    }

    protected_text = re.sub(
        r"\b(Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.\s",
        r"\1<MONTH_DOT> ",
        text or "",
        flags=re.I,
    )
    answer_claims: list[Claim] = []
    sentences = re.split(r"(?<=[.!?])\s+(?!\[S\d+\])|\n+", protected_text)
    for sentence in sentences:
        sentence = sentence.replace("<MONTH_DOT>", ".")
        stripped = sentence.strip()
        if (
            not stripped
            or stripped.startswith("#")
            or stripped.casefold().startswith(("unresolved:", "verified findings:"))
            or stripped.casefold().startswith("- unresolved:")
            or re.fullmatch(r"[-| :]+", stripped)
        ):
            continue
        cited_ids = [
            source_id for source_id in _CITATION_RE.findall(stripped) if source_id in valid_ids
        ]
        claim_text = re.sub(r"\[S\d+\]", "", stripped).strip()
        claim_terms = terms(claim_text)
        claim_numbers = numbers(claim_text)
        if not claim_terms:
            continue
        if not cited_ids:
            return False
        minimum_overlap = min(2, len(claim_terms))
        relationship = "supports"
        passage_ids: list[str] = []
        for question in getattr(report, "coverage", []):
            if question.status != "contradicted":
                continue
            target = next(
                (
                    field.split(":", 1)[1]
                    for field in question.required_fields
                    if field.startswith("license_proposition:")
                ),
                "",
            )
            target_is_negated = bool(
                target
                and target.casefold() in stripped.casefold()
                and re.search(r"\b(?:no|not|false|contradict(?:s|ed)?|rather than|instead)\b", stripped, re.I)
            )
            refuting_ids = [
                passage_id
                for passage_id in question.refuting_claim_ids
                if passage_id in passages_by_id
                and passages_by_id[passage_id].source_id in cited_ids
            ]
            if target and target.casefold() in stripped.casefold() and not target_is_negated:
                return False
            if target_is_negated and refuting_ids:
                relationship = "refutes"
                passage_ids.extend(refuting_ids)

        if relationship == "refutes":
            claim_numbers.difference_update(refuted_numbers)

        normalized_claim = normalized(claim_text)
        fact_passages_by_source: dict[str, list[str]] = {}
        fact_predicates: set[str] = set()
        for fact in getattr(report, "facts", []):
            source_id = getattr(fact, "source_id", "")
            if source_id not in cited_ids:
                continue
            values = [
                normalized(value)
                for value in (getattr(fact, "value", ""), getattr(fact, "quote", ""))
                if value
            ]
            if not values or not any(value in normalized_claim for value in values):
                continue
            fact_matches = [
                passage.passage_id
                for passage in passages_by_source.get(source_id, [])
                if any(value in normalized(passage.text) for value in values)
            ]
            if not fact_matches:
                return False
            fact_passages_by_source.setdefault(source_id, []).extend(fact_matches)
            if getattr(fact, "aspect", ""):
                fact_predicates.add(fact.aspect)

        supported_numbers: set[str] = set()
        for source_id in cited_ids:
            source_passages = passages_by_source.get(source_id, [])
            if source_passages:
                if source_id in fact_passages_by_source:
                    fact_passage_ids = list(dict.fromkeys(fact_passages_by_source[source_id]))
                    passage_ids.extend(fact_passage_ids)
                    supported_numbers.update(
                        number
                        for passage_id in fact_passage_ids
                        for number in numbers(passages_by_id[passage_id].text)
                    )
                    continue
                supported = [
                    passage
                    for passage in source_passages
                    if len(claim_terms & terms(passage.text)) >= minimum_overlap
                    and (not claim_numbers or claim_numbers.intersection(numbers(passage.text)))
                ]
                if not supported:
                    return False
                passage_ids.extend(passage.passage_id for passage in supported)
                supported_numbers.update(
                    number for passage in supported for number in numbers(passage.text)
                )
            elif len(claim_terms & terms(" ".join(evidence_by_source.get(source_id, [])))) < minimum_overlap:
                return False

        if claim_numbers and not claim_numbers.issubset(supported_numbers):
            return False

        if passage_ids:
            passage_ids = list(dict.fromkeys(passage_ids))
            subject = getattr(getattr(report, "brief", None), "topic", "")
            matching_question = next(
                (
                    question
                    for question in getattr(report, "coverage", [])
                    if set(passage_ids).intersection(
                        question.supporting_claim_ids + question.refuting_claim_ids
                    )
                ),
                None,
            )
            predicate = (
                next(iter(fact_predicates))
                if len(fact_predicates) == 1
                else "supported_statement"
            )
            if not fact_predicates and matching_question and matching_question.required_fields:
                predicate = matching_question.required_fields[0].split(":", 1)[0]
            claim_id = "answer-" + hashlib.sha256(
                f"{claim_text}\0{relationship}\0{'|'.join(sorted(passage_ids))}".encode("utf-8")
            ).hexdigest()[:24]
            answer_claims.append(
                Claim(
                    claim_id=claim_id,
                    text=claim_text,
                    subject=subject,
                    predicate=predicate,
                    value=claim_text,
                    evidence_passage_ids=passage_ids,
                    relationship=relationship,
                    verification_status="bound_to_validated_passage",
                )
            )

    if hasattr(report, "claims"):
        retained = [claim for claim in report.claims if not claim.claim_id.startswith("answer-")]
        report.claims = retained + answer_claims
    return True


def strip_citation_markers(text: str) -> str:
    """Remove visual source markers from speech while retaining answer text."""
    return _CITATION_RE.sub("", text or "").replace("  ", " ").strip()
