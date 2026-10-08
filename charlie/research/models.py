"""Structured contracts for Charlie's public-web research pipeline.

Research data stays typed until the final prompt boundary.  This keeps search,
fetching, evidence handling, and citations from passing fragile
formatted strings between one another.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import List, Optional
from urllib.parse import urlparse


class ResearchMode(StrEnum):
    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"


class SourceClass(StrEnum):
    """What kind of publisher a source is, relative to the research brief."""

    OFFICIAL = "official"
    OFFICIAL_UNVERIFIED = "official_unverified"
    OFFICIAL_STORE = "official_store"
    RETAILER = "retailer"
    NEWS = "news"
    REVIEW = "review"
    REFERENCE = "reference"
    FORUM_SOCIAL = "forum_social"
    UNKNOWN = "unknown"


class EvidenceCompleteness(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    NONE = "none"


class ResearchTermination(StrEnum):
    COMPLETED = "completed"
    DEADLINE = "deadline"
    CANCELLED = "cancelled"
    PROVIDER_EXHAUSTED = "provider_exhausted"
    ERROR = "error"


class ResearchDelivery(StrEnum):
    DELIVERED = "delivered"
    NOT_DELIVERED = "not_delivered"
    DELIVERY_FAILED = "delivery_failed"


@dataclass
class Subquestion:
    id: str
    question: str
    required_fields: List[str] = field(default_factory=list)
    evidence_requirements: List[str] = field(default_factory=list)
    status: str = "unresolved"
    supporting_claim_ids: List[str] = field(default_factory=list)
    refuting_claim_ids: List[str] = field(default_factory=list)
    missing_evidence: List[str] = field(default_factory=list)
    conflict: bool = False


@dataclass
class ResearchBrief:
    """The user's actual requirements, kept separate from search strings."""

    topic: str
    entity_kind: str = "general"  # "product" | "release" | "general"
    option_count: Optional[int] = None
    budget: Optional[float] = None
    currency: Optional[str] = None
    market: Optional[str] = None
    source_policy: str = "any_reputable"  # official_required | official_preferred | any_reputable
    aspects: List[str] = field(default_factory=list)
    priority: List[str] = field(default_factory=list)
    stable_only: bool = False
    explicit_domains: List[str] = field(default_factory=list)
    original_request: str = ""
    objective: str = ""
    strategy: str = "general"
    depth: str = "standard"
    required_subquestions: List[Subquestion] = field(default_factory=list)
    requested_version: Optional[str] = None


@dataclass(frozen=True)
class Candidate:
    """A named option discovered in fetched text, never from model memory."""

    name: str
    brand: Optional[str]
    quote: str
    source_id: str
    mentions: int = 1
    access: Optional[str] = None
    release_date: Optional[str] = None


@dataclass(frozen=True)
class Fact:
    """One verified value with the verbatim span that supports it."""

    candidate: Optional[str]
    aspect: str
    value: str
    quote: str
    source_id: str
    source_class: str
    extractor: str
    url: str = ""
    fetched_at: str = ""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    provider: str = "unknown"
    rank: int = 0
    published_at: Optional[str] = None
    domain: str = ""
    canonical_url: str = ""

    def __post_init__(self) -> None:
        if not self.domain:
            object.__setattr__(self, "domain", urlparse(self.url).netloc.lower())
        if not self.canonical_url:
            object.__setattr__(self, "canonical_url", self.url)


@dataclass
class SourceDocument:
    source_id: str = ""
    url: str = ""
    canonical_url: str = ""
    title: str = ""
    domain: str = ""
    content: str = ""
    extraction_method: str = ""
    fetched_at: datetime = field(default_factory=utc_now)
    word_count: int = 0
    content_hash: str = ""
    relevance_score: float = 0.0
    quality_score: float = 0.0
    published_at: Optional[str] = None
    error: Optional[str] = None
    source_class: str = SourceClass.UNKNOWN.value
    document_id: str = ""

    def __post_init__(self) -> None:
        if not self.content_hash and self.content:
            self.content_hash = hashlib.sha256(self.content.encode("utf-8", "ignore")).hexdigest()
        if not self.document_id:
            identity = f"{self.canonical_url or self.url}\0{self.content_hash}"
            if identity != "\0":
                self.document_id = hashlib.sha256(identity.encode("utf-8", "ignore")).hexdigest()[:24]

    def refresh_identity(self) -> None:
        if self.content:
            self.content_hash = hashlib.sha256(self.content.encode("utf-8", "ignore")).hexdigest()
        identity = f"{self.canonical_url or self.url}\0{self.content_hash}"
        self.document_id = (
            hashlib.sha256(identity.encode("utf-8", "ignore")).hexdigest()[:24]
            if identity != "\0"
            else ""
        )


@dataclass(frozen=True)
class EvidenceItem:
    source_id: str
    statement: str
    relevance: float = 0.0
    confidence: float = 0.0
    contradiction: bool = False
    passage_id: str = ""
    start_offset: int = -1
    end_offset: int = -1
    document_hash: str = ""


@dataclass(frozen=True)
class EvidencePassage:
    passage_id: str
    source_id: str
    source_url: str
    canonical_url: str
    document_hash: str
    text: str
    start_offset: int
    end_offset: int
    retrieved_at: str
    published_at: Optional[str]
    source_class: str


@dataclass(frozen=True)
class Claim:
    claim_id: str
    text: str
    subject: str = ""
    predicate: str = ""
    value: str = ""
    units: str = ""
    time_scope: str = ""
    evidence_passage_ids: List[str] = field(default_factory=list)
    relationship: str = "supports"
    verification_status: str = "unverified"


@dataclass(frozen=True)
class Citation:
    source_id: str
    url: str
    title: str
    domain: str


@dataclass(frozen=True)
class ProductResult:
    name: str
    price: Optional[float]
    currency: str
    store: str
    url: str
    rating: Optional[float] = None
    review_count: Optional[int] = None
    availability: Optional[str] = None
    reason: str = ""


@dataclass(frozen=True)
class MediaResult:
    title: str
    platform: str
    url: str
    channel: Optional[str] = None
    thumbnail_url: Optional[str] = None


@dataclass(frozen=True)
class ResearchQuery:
    text: str
    purpose: str = "primary"
    domain_filters: List[str] = field(default_factory=list)


@dataclass
class ResearchPlan:
    goal: str
    mode: ResearchMode
    queries: List[ResearchQuery]
    constraints: List[str] = field(default_factory=list)
    preferred_sources: List[str] = field(default_factory=list)
    required_freshness: str = "current"
    domain_filters: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ResearchProgress:
    stage: str
    message: str
    current: int = 0
    total: int = 0
    mode: Optional[ResearchMode] = None


@dataclass
class ResearchReport:
    query: str
    mode: ResearchMode
    plan: Optional[ResearchPlan] = None
    search_results: List[SearchResult] = field(default_factory=list)
    sources: List[SourceDocument] = field(default_factory=list)
    evidence: List[EvidenceItem] = field(default_factory=list)
    citations: List[Citation] = field(default_factory=list)
    products: List[ProductResult] = field(default_factory=list)
    media: List[MediaResult] = field(default_factory=list)
    answer: str = ""
    suggested_open_url: Optional[str] = None
    # None means "not measurable", which is not the same claim as 0.0.
    # Nothing in the pipeline may populate this from item counts; see
    # charlie.research.engine.compute_confidence for what is actually measurable.
    confidence: Optional[float] = None
    stop_reason: str = ""
    errors: List[str] = field(default_factory=list)
    duration_ms: float = 0.0
    # Discover→verify outputs. Empty for the inline single-pass path.
    brief: Optional[ResearchBrief] = None
    candidates: List[Candidate] = field(default_factory=list)
    facts: List[Fact] = field(default_factory=list)
    gaps: List[str] = field(default_factory=list)
    partial: bool = False
    phase_ms: dict = field(default_factory=dict)
    synthesis_kind: Optional[str] = None  # "model" | "auto_assembled"
    passages: List[EvidencePassage] = field(default_factory=list)
    claims: List[Claim] = field(default_factory=list)
    coverage: List[Subquestion] = field(default_factory=list)
    completeness: Optional[EvidenceCompleteness] = None
    termination_reason: Optional[ResearchTermination] = None
    delivery_status: ResearchDelivery = ResearchDelivery.NOT_DELIVERED
    llm_calls_used: int = 0
    llm_call_limit: Optional[int] = None
    provider_outcomes: List[dict[str, object]] = field(default_factory=list)

    def finalize_outcome(self) -> None:
        grounded = bool(self.evidence or self.facts)
        if self.stop_reason == "evidence-sufficient" and grounded and not self.partial and not self.gaps:
            self.completeness = EvidenceCompleteness.COMPLETE
        elif grounded:
            self.completeness = EvidenceCompleteness.PARTIAL
        else:
            self.completeness = EvidenceCompleteness.NONE

        self.termination_reason = {
            "timeout": ResearchTermination.DEADLINE,
            "deadline": ResearchTermination.DEADLINE,
            "cancelled": ResearchTermination.CANCELLED,
            "error": ResearchTermination.ERROR,
            "no-results": ResearchTermination.PROVIDER_EXHAUSTED,
            "provider-exhausted": ResearchTermination.PROVIDER_EXHAUSTED,
        }.get(self.stop_reason, ResearchTermination.COMPLETED)

    def bind_passages(self) -> None:
        sources_by_id = {source.source_id: source for source in self.sources}
        passages: List[EvidencePassage] = []
        claims: List[Claim] = []
        valid_evidence: List[EvidenceItem] = []
        for item in self.evidence:
            source = sources_by_id.get(item.source_id)
            if (
                source is None
                or not item.passage_id
                or item.document_hash != source.content_hash
                or item.start_offset < 0
                or item.end_offset > len(source.content)
                or source.content[item.start_offset:item.end_offset] != item.statement
            ):
                self.errors.append(f"Evidence passage provenance failed for {item.source_id or 'unknown source'}")
                continue
            passages.append(
                EvidencePassage(
                    passage_id=item.passage_id,
                    source_id=source.source_id,
                    source_url=source.url,
                    canonical_url=source.canonical_url,
                    document_hash=source.content_hash,
                    text=item.statement,
                    start_offset=item.start_offset,
                    end_offset=item.end_offset,
                    retrieved_at=source.fetched_at.isoformat(),
                    published_at=source.published_at,
                    source_class=source.source_class,
                )
            )
            claims.append(
                Claim(
                    claim_id=item.passage_id,
                    text=item.statement,
                    predicate="source_statement",
                    value=item.statement,
                    evidence_passage_ids=[item.passage_id],
                    verification_status="exact_source_span",
                )
            )
            valid_evidence.append(item)
        self.evidence = valid_evidence
        self.passages = passages
        self.claims = claims

    @property
    def successful(self) -> bool:
        return bool(self.search_results or self.sources or self.evidence)

    def coverage_summary(self) -> str:
        lines = []
        for question in self.coverage:
            passage_ids = list(dict.fromkeys(
                question.supporting_claim_ids + question.refuting_claim_ids
            ))
            citations = f" (evidence passages: {', '.join(passage_ids)})" if passage_ids else ""
            if question.conflict:
                lines.append(
                    f"UNRESOLVED CONFLICT: {question.question}{citations}. "
                    "Retain both positions; do not choose a winner."
                )
            elif question.status == "contradicted":
                lines.append(
                    f"CONTRADICTED: {question.question}{citations}. "
                    "State that the proposition is contradicted by the cited evidence."
                )
            elif question.status == "unresolved":
                missing = "; ".join(question.missing_evidence)
                suffix = f" Missing evidence: {missing}." if missing else ""
                lines.append(f"UNRESOLVED: {question.question}.{suffix}")
            else:
                lines.append(f"SUPPORTED: {question.question}{citations}.")
        return "\n".join(lines)

    def deterministic_answer(self) -> str:
        allowed_ids = {citation.source_id for citation in self.citations}
        lines: List[str] = []
        seen: set[tuple[str, str]] = set()
        for question in self.coverage:
            if question.status != "contradicted":
                continue
            proposition = question.question.rstrip(" ?.")
            if question.required_fields and question.required_fields[0].startswith("license_proposition:"):
                license_id = question.required_fields[0].split(":", 1)[1]
                subject = (
                    question.required_fields[1].split(":", 1)[1]
                    if len(question.required_fields) > 1 and question.required_fields[1].startswith("subject:")
                    else self.brief.topic if self.brief else "The project"
                )
                proposition = f"{subject} is licensed under {license_id}"
            for item in self.evidence:
                if item.passage_id not in question.refuting_claim_ids or item.source_id not in allowed_ids:
                    continue
                statement = " ".join(item.statement.split()).rstrip()
                lines.append(
                    f"The evidence contradicts the proposition that {proposition}: "
                    f"{statement} [{item.source_id}]"
                )
                seen.add((item.source_id, statement.casefold()))
        if self.brief and self.brief.entity_kind == "general" and self.coverage:
            evidence_by_passage = {
                item.passage_id: item for item in self.evidence if item.passage_id
            }

            def append_passages(passage_ids: List[str], limit: Optional[int] = None) -> None:
                items = [
                    evidence_by_passage[passage_id]
                    for passage_id in passage_ids
                    if passage_id in evidence_by_passage
                    and evidence_by_passage[passage_id].source_id in allowed_ids
                ]
                items.sort(key=lambda item: item.relevance, reverse=True)
                question_seen: set[tuple[str, str]] = set()
                for item in items[:limit]:
                    statement = re.sub(r"\[S\d+\]", "", " ".join(item.statement.split())).strip()
                    key = (item.source_id, statement.casefold())
                    if statement and key not in question_seen:
                        lines.append(f"- {statement} [{item.source_id}]")
                        question_seen.add(key)
                        seen.add(key)

            for question in self.coverage:
                fields = question.required_fields
                if fields and fields[0].startswith(("source:", "independent_sources:")):
                    if question.status == "unresolved":
                        lines.append(f"Unresolved: {question.question}")
                    continue
                if question.status == "contradicted":
                    continue
                if question.conflict:
                    passage_ids = list(dict.fromkeys(
                        question.supporting_claim_ids + question.refuting_claim_ids
                    ))
                    if passage_ids:
                        lines.append(f"### Conflicting evidence: {question.question}")
                        append_passages(passage_ids)
                    continue
                if question.status == "supported":
                    label = " — ".join(fields) if fields else question.question
                    if question.supporting_claim_ids:
                        lines.append(f"### {label}")
                        append_passages(question.supporting_claim_ids, limit=2)
                elif question.status == "unresolved":
                    lines.append(f"Unresolved: {question.question}")
            if lines:
                return "\n".join(lines)
        for item in self.evidence:
            statement = " ".join(item.statement.split()).rstrip()
            key = (item.source_id, statement.casefold())
            if item.source_id in allowed_ids and statement and key not in seen:
                lines.append(f"- {statement} [{item.source_id}]")
                seen.add(key)
        unresolved = self.gaps or [
            f"Unresolved: {question.question}"
            for question in self.coverage
            if question.status == "unresolved"
        ]
        if unresolved:
            if lines:
                lines.append("")
            for gap in unresolved:
                value = str(gap)
                if value.casefold().startswith("unresolved:"):
                    value = value[len("unresolved:"):].strip()
                lines.append(f"Unresolved: {value}")
        return "\n".join(lines) or "I couldn't verify a useful answer from the available sources."

    def prompt_context(self, max_chars: int = 12000) -> str:
        """Build bounded, clearly untrusted evidence for the synthesis model."""
        blocks: List[str] = []
        coverage = self.coverage_summary()
        if coverage and (self.search_results or self.sources or self.evidence or self.facts):
            blocks.append("RUNTIME COVERAGE DECISIONS (preserve contradicted and unresolved states):\n" + coverage)
        if self.facts:
            from charlie.research.facts import fact_table
            blocks.append("VERIFIED FACTS (keep each value bound to its candidate and cited source):\n" + fact_table(self))
        sources_by_id = {source.source_id: source for source in self.sources}
        evidence_by_source = {}
        for item in self.evidence:
            evidence_by_source.setdefault(item.source_id, []).append(item.statement)
        for citation in self.citations:
            statements = evidence_by_source.get(citation.source_id, [])
            content = " ".join(statements)
            if not content:
                continue
            source = sources_by_id.get(citation.source_id)
            freshness = source.published_at if source and source.published_at else "unknown"
            retrieved = source.fetched_at.isoformat() if source else "unknown"
            passage_records = [item for item in self.passages if item.source_id == citation.source_id]
            passage_lines = "\n".join(
                f"PASSAGE {item.passage_id} [{item.start_offset}:{item.end_offset}] {item.text}"
                for item in passage_records
            )
            blocks.append(
                f"[{citation.source_id}] {citation.title}\n"
                f"URL: {citation.url}\n"
                f"Published: {freshness}\nRetrieved: {retrieved}\n"
                f"DOCUMENT HASH: {source.content_hash if source else 'unknown'}\n"
                f"UNTRUSTED SOURCE CONTENT:\n{passage_lines or content[:2200]}"
            )
        if not blocks and self.mode is ResearchMode.QUICK:
            for index, result in enumerate(self.search_results[:8], start=1):
                if not result.snippet.strip():
                    continue
                source_id = f"S{index}"
                blocks.append(
                    f"[{source_id}] {result.title}\nURL: {result.url}\n"
                    f"Published: {result.published_at or 'unknown'}\n"
                    f"UNTRUSTED SEARCH SNIPPET:\n{result.snippet[:1000]}"
                )
        return "\n\n".join(blocks)[:max_chars]

    def legacy_text(self, max_chars: int = 12000) -> str:
        """Compatibility text form for existing string-based tool calls."""
        context = self.prompt_context(max_chars=max_chars)
        if not context:
            return (
                "Error: No useful research evidence was retrieved. "
                "Do not claim that the requested facts were verified."
            )
        return f"Research mode: {self.mode.value}\n{context}"
