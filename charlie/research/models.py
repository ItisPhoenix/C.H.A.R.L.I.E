"""Structured contracts for Charlie's web research pipeline.

Research data stays typed until the final prompt boundary.  This keeps search,
fetching, evidence handling, citations, and widgets from passing fragile
formatted strings between one another.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import List, Optional
from urllib.parse import urlparse


class ResearchMode(StrEnum):
    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"


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


@dataclass(frozen=True)
class EvidenceItem:
    source_id: str
    statement: str
    relevance: float = 0.0
    confidence: float = 0.0
    contradiction: bool = False


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
    confidence: float = 0.0
    stop_reason: str = ""
    errors: List[str] = field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def successful(self) -> bool:
        return bool(self.search_results or self.sources or self.evidence)

    def structured_payload(self, max_items: int = 8) -> dict:
        """Return canonical bounded presentation data; prompt text stays model-only."""
        from charlie.research.presentation import build_research_workspace_payload

        payload = build_research_workspace_payload(self)
        if max_items < 12:
            payload["sources"] = payload["sources"][:max_items]
            valid = {item["id"] for item in payload["sources"]}
            payload["findings"] = [
                item for item in payload["findings"] if set(item["source_ids"]).issubset(valid)
            ][: max_items * 4]
        return payload

    def presentation_payload(self) -> dict:
        """Explicit name for the presentation-safe research representation."""
        from charlie.research.presentation import build_research_workspace_payload

        return build_research_workspace_payload(self)

    def prompt_context(self, max_chars: int = 12000) -> str:
        """Build bounded, clearly untrusted evidence for the synthesis model."""
        blocks: List[str] = []
        for citation, source in zip(self.citations, self.sources):
            content = source.content.strip() or next(
                (result.snippet for result in self.search_results if result.canonical_url == source.canonical_url),
                "",
            )
            if not content:
                continue
            blocks.append(
                f"[{citation.source_id}] {citation.title}\n"
                f"URL: {citation.url}\n"
                f"UNTRUSTED SOURCE CONTENT:\n{content[:2200]}"
            )
        if not blocks:
            for index, result in enumerate(self.search_results[:8], start=1):
                source_id = f"S{index}"
                blocks.append(
                    f"[{source_id}] {result.title}\nURL: {result.url}\n"
                    f"UNTRUSTED SEARCH SNIPPET:\n{result.snippet[:1000]}"
                )
        return "\n\n".join(blocks)[:max_chars]

    def legacy_text(self, max_chars: int = 12000) -> str:
        """Compatibility representation for existing string-based tool calls."""
        context = self.prompt_context(max_chars=max_chars)
        if not context:
            return "No useful research results found."
        return f"Research mode: {self.mode.value}\n{context}"
