"""Bounded research orchestration: search, fetch, extract, rank, cite, iterate."""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
import time
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Sequence, Set
from urllib.parse import urlparse

from charlie.research.cache import TTLCache
from charlie.research.citations import assign_citations, assign_search_citations
from charlie.research.crawler import crawl_document
from charlie.research.credibility import (
    organisational_domain as _organisational_domain,
)
from charlie.research.evidence import build_evidence
from charlie.research.facts import (
    extract_facts_from_document,
    fact_table,
    model_facts,
    parse_inr_price,
)
from charlie.research.fetch import fetch_document
from charlie.research.media import media_results
from charlie.research.models import (
    Candidate,
    EvidenceItem,
    Fact,
    ResearchBrief,
    ResearchMode,
    ResearchPlan,
    ResearchProgress,
    ResearchQuery,
    ResearchReport,
    SearchResult,
    SourceClass,
    SourceDocument,
)
from charlie.research.ranking import rank_documents, rank_search_results
from charlie.research.releases import canonical_release_urls, pick_stable
from charlie.research.router import ResearchDecision, is_sustained_research_query, route
from charlie.research.candidates import extract_candidates
from charlie.research.search import (
    clean_query,
    build_plan,
    discovery_queries,
    parse_brief,
    search_plan,
    verification_queries,
)
from charlie.research.semantics import gather_semantic_scores
from charlie.research.shopping import extract_products, is_shopping_query
from charlie.research.sources import OFFICIAL_REGISTRY, classify, citable_for

logger = logging.getLogger("charlie.research.engine")

_MAX_BROWSER_ESCALATIONS = 3

ProgressCallback = Callable[[ResearchProgress], Any]
BrowserFetchCallback = Callable[[SearchResult], Awaitable[Optional[SourceDocument]]]
BriefPlannerCallback = Callable[[str, ResearchBrief], Awaitable[Dict[str, Any]]]
CandidateExtractorCallback = Callable[[ResearchBrief, List[SourceDocument]], Awaitable[List[Dict[str, Any]]]]


_CONFIDENCE_HALF_WEIGHT = 2.0
CREDIBILITY_FLOOR = 0.4


def organisational_domain(host: str) -> str:
    return _organisational_domain(host)


def compute_confidence(
    evidence: Sequence[EvidenceItem],
    sources: Iterable[SourceDocument],
) -> Optional[float]:
    documents = list(sources)
    domain_by_source: dict[str, str] = {}
    quality_by_source: dict[str, float] = {}
    for document in documents:
        host = organisational_domain(document.domain) or organisational_domain(
            urlparse(document.url).netloc
        )
        if host and document.source_id:
            domain_by_source[document.source_id] = host
            quality_by_source[document.source_id] = max(
                0.0, min(1.0, document.quality_score)
            )

    grounded = {
        domain_by_source[item.source_id]
        for item in evidence
        if domain_by_source.get(item.source_id)
    }
    if not grounded:
        return None

    count = float(len(grounded))
    diversity = count / (count + _CONFIDENCE_HALF_WEIGHT)

    contributing = [
        quality_by_source[item.source_id]
        for item in evidence
        if item.source_id in quality_by_source
    ]
    if not contributing:
        return None
    credibility = sum(contributing) / len(contributing)

    combined = diversity * (
        CREDIBILITY_FLOOR + (1.0 - CREDIBILITY_FLOOR) * credibility
    )
    return min(1.0, max(0.0, combined))


def grounded_domain_count(
    evidence: Sequence[EvidenceItem],
    sources: Iterable[SourceDocument],
) -> int:
    domain_by_source: dict[str, str] = {}
    for document in sources:
        host = organisational_domain(document.domain) or organisational_domain(
            urlparse(document.url).netloc
        )
        if host and document.source_id:
            domain_by_source[document.source_id] = host
    return len(
        {
            domain_by_source[item.source_id]
            for item in evidence
            if domain_by_source.get(item.source_id)
        }
    )


_ENTITY_RE = re.compile(r"&(?:nbsp|ensp|emsp|thinsp|#160|#8194|#8195|#8201);", re.I)
_ZERO_WIDTH_RE = re.compile("[\u00ad\u200b-\u200f\u2060\ufeff]")
_TEMPLATE_RE = re.compile(r"\{[{%][^}%]{0,400}[}%]\\}")
_RAW_TAG_RE = re.compile(r"</?[A-Za-z][^<>]{0,400}>")
_OBJECT_RE = re.compile(r"\[object\s+Object\]", re.I)
_SEGMENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_ALNUM_RE = re.compile(r"[A-Za-z0-9\u00c0-\u024f]")

_NAN_RE = re.compile(r"(?<![A-Za-z0-9_])(?:NaN|[+-]?Infinity)(?![A-Za-z0-9_])")
_UNDEFINED_RE = re.compile(r"(?<![A-Za-z0-9_])undefined(?![A-Za-z0-9_])", re.I)
_UNDEFINED_LEGIT_RE = re.compile(r"undefined\s+(?:behaviour|behaviors|behavior)s?", re.I)
_NULL_JUNK_RE = re.compile(r"(?:=|:|\(|\[)\s?null\b|\bnull\s?(?:\)|;|\])", re.I)
_LOADING_ONLY_RE = re.compile(
    r"^(?:please\s+)?(?:loading|please\s+wait|wait|working|fetching|one\s+moment)"
    r"(?:[\s.\u2026!]*|please\b.*)$",
    re.I,
)
_JS_DISABLED_RE = re.compile(
    r"javascript\s+(?:is|are)\s+(?:disabled|not\s+enabled|required)", re.I
)


def _is_junk_segment(segment: str) -> bool:
    text = segment.strip()
    if not text:
        return True
    if not _ALNUM_RE.search(text):
        return True
    if _NAN_RE.search(text):
        return True
    if _UNDEFINED_RE.search(_UNDEFINED_LEGIT_RE.sub(" ", text)):
        return True
    if _NULL_JUNK_RE.search(text):
        return True
    if _OBJECT_RE.search(text):
        return True
    if _JS_DISABLED_RE.search(text):
        return True
    if len(text) <= 80 and _LOADING_ONLY_RE.match(text):
        return True
    return False


def sanitize_source_text(content: str) -> str:
    if not content:
        return ""
    cleaned = _ENTITY_RE.sub(" ", content)
    cleaned = _ZERO_WIDTH_RE.sub(" ", cleaned)
    cleaned = _TEMPLATE_RE.sub(" ", cleaned)
    cleaned = _RAW_TAG_RE.sub(" ", cleaned)
    kept = [
        segment.strip()
        for segment in _SEGMENT_SPLIT_RE.split(cleaned)
        if not _is_junk_segment(segment)
    ]
    return "\n".join(kept)


def _matches_candidate_variant(candidate_name: str, doc_title: str, doc_url: str, text_span: str) -> bool:
    """Product-variant guard: facts are bound only to the exact model variant."""
    # Extract distinct model alphanumeric tokens like 15IAX9, FA507NV, G15-5530, etc.
    code_tokens = re.findall(
        r"\b\d{2}[A-Za-z]{3}\d\b|\b[A-Za-z]{2}\d{3}[A-Za-z]{2}\b|\b[A-Za-z0-9]+-[A-Za-z0-9]+\b",
        candidate_name,
    )
    if not code_tokens:
        # Check brand + main model token
        name_lower = candidate_name.lower()
        return (
            name_lower in doc_title.lower()
            or name_lower in doc_url.lower()
            or name_lower in text_span.lower()
        )

    for tok in code_tokens:
        tok_lower = tok.lower()
        # If candidate code appears in page or span, it matches
        span_codes = re.findall(
            r"\b\d{2}[A-Za-z]{3}\d\b|\b[A-Za-z]{2}\d{3,}[A-Za-z]{2}\b|\b[A-Za-z]\d{2}-\d{4}\b",
            text_span,
        )
        variant_parts = set(tok_lower.split("-")) | {tok_lower}
        if span_codes and any(oc.lower() not in variant_parts for oc in span_codes):
            return False
        if tok_lower in doc_title.lower() or tok_lower in doc_url.lower() or tok_lower in text_span.lower():
            return True
        # If a different competing code is present on the page, reject
        other_codes = re.findall(
            r"\b\d{2}[A-Za-z]{3}\d\b|\b[A-Za-z]{2}\d{3}[A-Za-z]{2}\b",
            doc_title + " " + doc_url,
        )
        if other_codes and any(oc.lower() != tok_lower for oc in other_codes):
            return False

    return False


def _is_candidate_complete(candidate: Candidate, facts: List[Fact], brief: ResearchBrief) -> bool:
    """Check if a candidate has citable official specs for required aspects and price <= budget."""
    cand_name_clean = re.sub(r"[^\w\s]", "", candidate.name).lower().strip()
    cand_facts = []
    for f in facts:
        if not f.candidate:
            continue
        fc_clean = re.sub(r"[^\w\s]", "", f.candidate).lower().strip()
        if fc_clean == cand_name_clean and citable_for(f.aspect, f.source_class, brief.source_policy):
            cand_facts.append(f)

    if not cand_facts:
        return False

    aspects_covered = {f.aspect for f in cand_facts}

    # Must have price
    price_facts = [f for f in cand_facts if f.aspect == "price"]
    if not price_facts and brief.budget:
        return False
    if brief.budget and price_facts:
        val = parse_inr_price(price_facts[0].value)
        if val is None or val > brief.budget:
            return False

    # Check key hardware aspects if required
    for req in ("gpu", "vram", "ram"):
        if req in brief.aspects and req not in aspects_covered:
            return False

    return True


class PhaseBudget:
    def __init__(
        self,
        plan_s: float = 15.0,
        discover_s: float = 40.0,
        verify_s: float = 70.0,
        synth_s: float = 45.0,
    ) -> None:
        self.plan_s = plan_s
        self.discover_s = discover_s
        self.verify_s = verify_s
        self.synth_s = synth_s
        self.start = time.perf_counter()
        self.leftover = 0.0

    def deadline_for(self, phase_budget: float) -> float:
        allowed = phase_budget + self.leftover
        self.leftover = 0.0
        return allowed

    def record_used(self, allowed: float, used: float) -> None:
        if used < allowed:
            self.leftover += (allowed - used)


class ResearchEngine:
    def __init__(
        self,
        config: Any,
        *,
        progress: Optional[ProgressCallback] = None,
        browser_fetch: Optional[BrowserFetchCallback] = None,
        query_planner: Optional[Callable[[str], Awaitable[List[str]]]] = None,
        brief_planner: Optional[BriefPlannerCallback] = None,
        candidate_extractor: Optional[CandidateExtractorCallback] = None,
    ) -> None:
        self.config = config
        self.progress = progress
        self.browser_fetch = browser_fetch
        self.query_planner = query_planner
        self.brief_planner = brief_planner
        self.candidate_extractor = candidate_extractor
        self.search_cache: TTLCache[List[SearchResult]] = TTLCache(256)
        self.document_cache: TTLCache[SourceDocument] = TTLCache(128)
        self._browser_escalations = 0

    async def _notify(self, progress: ResearchProgress) -> None:
        if self.progress is None:
            return
        result = self.progress(progress)
        if inspect.isawaitable(result):
            await result

    def _providers(self):
        from charlie.research.providers import BingProvider, DuckDuckGoProvider, SearXNGProvider, YaCyProvider

        providers = []
        if getattr(self.config, "searxng_url", ""):
            providers.append(
                SearXNGProvider(
                    self.config.searxng_url,
                    engines=getattr(self.config, "searxng_engines", ""),
                    fallback_engines=getattr(self.config, "searxng_fallback_engines", ""),
                )
            )
        if getattr(self.config, "research_ddg_enabled", True):
            providers.append(
                DuckDuckGoProvider(
                    timeout_s=float(getattr(self.config, "research_fetch_timeout_s", 12)),
                    endpoint=getattr(self.config, "research_ddg_endpoint", ""),
                )
            )
        if getattr(self.config, "research_bing_enabled", False):
            providers.append(
                BingProvider(
                    endpoint=getattr(self.config, "research_bing_endpoint", ""),
                    timeout_s=float(getattr(self.config, "research_fetch_timeout_s", 12)),
                )
            )
        if getattr(self.config, "research_yacy_enabled", False) and getattr(self.config, "research_yacy_url", ""):
            providers.append(
                YaCyProvider(
                    base_url=self.config.research_yacy_url,
                    timeout_s=float(getattr(self.config, "research_fetch_timeout_s", 12)),
                    resource=getattr(self.config, "research_yacy_resource", "local"),
                    verify=getattr(self.config, "research_yacy_verify", "cacheonly"),
                )
            )
        configured_order = [
            item.strip().lower()
            for item in str(getattr(self.config, "research_provider_order", "")).split(",")
            if item.strip()
        ]
        if configured_order:
            priority = {name: index for index, name in enumerate(configured_order)}
            providers.sort(key=lambda provider: priority.get(provider.name.lower(), len(priority)))
        return providers

    def decide(self, query: str, requested_mode: str = "auto") -> ResearchDecision:
        if not getattr(self.config, "research_enabled", True):
            return ResearchDecision(False, None, "research disabled")
        return route(query, requested_mode)

    def plan(
        self,
        query: str,
        mode: ResearchMode,
        domain_filters: Optional[List[str]] = None,
    ) -> ResearchPlan:
        return build_plan(
            query,
            mode,
            max_queries=int(getattr(self.config, "research_max_search_queries", 6)),
            market=str(getattr(self.config, "research_market", "IN")),
            locale=str(getattr(self.config, "research_locale", "en-IN")),
            domain_filters=domain_filters,
        )

    def _extract_evidence(
        self,
        sources: List[SourceDocument],
        query: str,
        policy: str = "official_required",
    ) -> List[EvidenceItem]:
        per_source = max(1, int(getattr(self.config, "research_evidence_per_source", 8)))
        ceiling = per_source * max(1, len(sources))
        return build_evidence(
            sources,
            query,
            per_source_max=per_source,
            max_items=ceiling,
            policy=policy,
        )

    async def _search(self, plan: ResearchPlan) -> List[SearchResult]:
        providers = self._providers()
        if not providers:
            return []
        limit = max(1, int(getattr(self.config, "research_max_sources", 12)))
        cache_key = repr(
            (
                tuple(plan.domain_filters),
                tuple((item.text, tuple(item.domain_filters)) for item in plan.queries),
            )
        )
        cached = self.search_cache.get(cache_key)
        if cached is not None:
            return cached
        results = await search_plan(
            plan,
            providers,
            limit=limit,
            max_concurrency=min(2, int(getattr(self.config, "research_max_concurrency", 6))),
            progress=lambda current, total: logger.debug("Research search progress %d/%d", current, total),
        )
        ranked = rank_search_results(results, plan, limit)
        ttl = 60.0 if plan.required_freshness == "current" else 900.0
        self.search_cache.set(cache_key, ranked, ttl)
        return ranked

    async def _fetch_one(
        self,
        result: SearchResult,
        mode: ResearchMode,
        max_chars: Optional[int] = None,
    ) -> Optional[SourceDocument]:
        cache_key = result.canonical_url or result.url
        cached = self.document_cache.get(cache_key)
        if cached is not None and len(cached.content) >= (max_chars or 14000) * 0.8:
            return cached
        status_out: list = []
        try:
            document = await fetch_document(
                result,
                timeout_s=float(getattr(self.config, "research_fetch_timeout_s", 12)),
                status_out=status_out,
                max_chars=max_chars or 14000,
            )
        except ValueError:
            logger.info("Research URL rejected: %s", result.url)
            document = None
        refused = status_out[0] if status_out else None
        crawl_worth_trying = refused is None or refused in {403, 429} or refused >= 500
        if (
            document is None
            and mode is not ResearchMode.QUICK
            and getattr(self.config, "research_crawl_enabled", True)
            and crawl_worth_trying
        ):
            document = await crawl_document(
                result,
                timeout_s=float(getattr(self.config, "research_fetch_timeout_s", 12)),
                max_depth=int(getattr(self.config, "research_crawl_max_depth", 2)),
                max_pages=int(getattr(self.config, "research_crawl_max_pages", 20)),
            )
        browser_worth_trying = refused is None or refused >= 500
        if (
            document is None
            and mode is not ResearchMode.QUICK
            and self.browser_fetch is not None
            and browser_worth_trying
            and self._browser_escalations < _MAX_BROWSER_ESCALATIONS
        ):
            self._browser_escalations += 1
            document = await self.browser_fetch(result)

        if document is not None:
            before = len(document.content)
            document.content = sanitize_source_text(document.content)
            removed = before - len(document.content)
            if not document.content.strip():
                return None
            self.document_cache.set(cache_key, document, 300.0 if mode is ResearchMode.DEEP else 900.0)
        return document

    async def _rank_sources(
        self,
        documents: Sequence[SourceDocument],
        plan: ResearchPlan,
        mode: ResearchMode,
    ) -> List[SourceDocument]:
        limit = int(getattr(self.config, "research_max_sources", 12))
        if not documents or mode is ResearchMode.QUICK:
            return await rank_documents(documents, plan, limit)

        pairs = [
            (document.source_id, f"{document.title}. {document.content[:600]}")
            for document in documents
            if document.source_id
        ]
        semantic = await gather_semantic_scores(
            plan.goal,
            pairs,
            base_url=getattr(self.config, "memory_embedding_url", ""),
            model=getattr(self.config, "memory_embedding_model", ""),
        )
        return await rank_documents(documents, plan, limit, semantic=semantic)

    async def _fetch_sources(
        self,
        results: List[SearchResult],
        mode: ResearchMode,
        max_chars: Optional[int] = None,
    ) -> List[SourceDocument]:
        max_sources = int(getattr(self.config, "research_max_sources", 12))
        max_per_domain = max(1, int(getattr(self.config, "research_max_pages_per_domain", 4)))
        semaphore = asyncio.Semaphore(max(1, int(getattr(self.config, "research_max_concurrency", 6))))
        completed = 0

        selected: List[SearchResult] = []
        domain_counts: dict[str, int] = {}
        for result in results:
            domain = result.domain or "unknown"
            if domain_counts.get(domain, 0) >= max_per_domain:
                continue
            selected.append(result)
            domain_counts[domain] = domain_counts.get(domain, 0) + 1
            if len(selected) >= max_sources:
                break

        async def fetch_one(result: SearchResult) -> Optional[SourceDocument]:
            nonlocal completed
            async with semaphore:
                document = await self._fetch_one(
                    result, mode, **({"max_chars": max_chars} if max_chars is not None else {}),
                )
            completed += 1
            await self._notify(
                ResearchProgress(
                    "reading",
                    f"Reading source {completed}/{len(selected)}",
                    completed,
                    len(selected),
                    mode,
                )
            )
            return document

        documents = await asyncio.gather(*(fetch_one(item) for item in selected))
        return [item for item in documents if item is not None]

    async def _run_sustained(
        self,
        query: str,
        cancel_event: Optional[asyncio.Event] = None,
        domain_filters: Optional[List[str]] = None,
    ) -> ResearchReport:
        """Bounded discover -> verify loop (Decision 1 & Architecture)."""
        started = time.perf_counter()
        budget = PhaseBudget(
            plan_s=15.0,
            discover_s=40.0,
            verify_s=70.0,
            synth_s=45.0,
        )
        phase_timings: Dict[str, float] = {}
        report = ResearchReport(query=query, mode=ResearchMode.DEEP)

        # ---------------------------------------------------------
        # Phase 0: Brief (deterministic parse + optional model fill)
        # ---------------------------------------------------------
        p0_start = time.perf_counter()
        await self._notify(ResearchProgress("planning", "Structuring research requirements", mode=ResearchMode.DEEP))
        brief = parse_brief(query)
        if domain_filters:
            brief.explicit_domains = list(dict.fromkeys(brief.explicit_domains + domain_filters))
        report.brief = brief

        plan_deadline = budget.deadline_for(budget.plan_s)
        if self.brief_planner is not None:
            try:
                extra = await asyncio.wait_for(self.brief_planner(query, brief), timeout=plan_deadline)
                if isinstance(extra, dict):
                    if "aspects" in extra and isinstance(extra["aspects"], list):
                        brief.aspects = list(dict.fromkeys(brief.aspects + extra["aspects"]))
                    if "priority" in extra and isinstance(extra["priority"], list):
                        brief.priority = extra["priority"]
            except Exception as e:
                logger.info("Brief planner finished or skipped: %s", e)

        p0_elapsed = time.perf_counter() - p0_start
        budget.record_used(plan_deadline, p0_elapsed)
        phase_timings["phase_0_brief_ms"] = p0_elapsed * 1000

        if cancel_event and cancel_event.is_set():
            report.stop_reason = "cancelled"
            return report

        # ---------------------------------------------------------
        # Phase 1: Discover (queries -> search -> fetch -> candidates)
        # ---------------------------------------------------------
        p1_start = time.perf_counter()
        await self._notify(ResearchProgress("searching", "Finding candidate options", mode=ResearchMode.DEEP))
        disc_queries = discovery_queries(brief)
        disc_plan = ResearchPlan(
            goal=query,
            mode=ResearchMode.DEEP,
            queries=[ResearchQuery(q, "discovery", list(brief.explicit_domains)) for q in disc_queries],
            domain_filters=list(brief.explicit_domains),
        )
        report.plan = disc_plan

        disc_deadline = budget.deadline_for(budget.discover_s)
        try:
            disc_search_results = await asyncio.wait_for(self._search(disc_plan), timeout=disc_deadline * 0.45)
        except Exception:
            disc_search_results = []

        report.search_results.extend(disc_search_results)

        # Fetch discovery sources (14,000 chars)
        time_left_p1 = max(2.0, disc_deadline - (time.perf_counter() - p1_start))
        try:
            disc_docs = await asyncio.wait_for(
                self._fetch_sources(disc_search_results[:10], ResearchMode.DEEP, max_chars=14000),
                timeout=time_left_p1,
            )
        except Exception:
            disc_docs = []

        # Classify discovery docs and assign IDs
        for idx, doc in enumerate(disc_docs, start=1):
            doc.source_id = f"D{idx}"
            doc.source_class = classify(doc.url, brief=brief).value

        # Extract candidates
        model_proposals = None
        if self.candidate_extractor is not None:
            try:
                model_proposals = await asyncio.wait_for(self.candidate_extractor(brief, disc_docs), timeout=5.0)
            except Exception:
                model_proposals = None

        candidates = extract_candidates(disc_docs, brief, model_proposals)
        report.candidates = candidates

        p1_elapsed = time.perf_counter() - p1_start
        budget.record_used(disc_deadline, p1_elapsed)
        phase_timings["phase_1_discover_ms"] = p1_elapsed * 1000

        if cancel_event and cancel_event.is_set():
            report.stop_reason = "cancelled"
            return report

        # ---------------------------------------------------------
        # Phase 2: Verify per candidate (registry queries, fetch, facts)
        # ---------------------------------------------------------
        p2_start = time.perf_counter()
        verify_deadline = budget.deadline_for(budget.verify_s)
        verified_sources: List[SourceDocument] = []
        verified_facts: List[Fact] = []
        max_verify_chars = int(getattr(self.config, "research_verify_fetch_chars", 60000))
        max_rounds = int(getattr(self.config, "research_max_verify_rounds", 2))

        # If release entity kind: verify official canonical release pages
        if brief.entity_kind == "release":
            await self._notify(ResearchProgress("reading", "Verifying official release documentation", mode=ResearchMode.DEEP))
            rel_plan = ResearchPlan(
                goal=f"{brief.topic} official downloads",
                mode=ResearchMode.DEEP,
                queries=[ResearchQuery(q, "release", list(brief.explicit_domains)) for q in discovery_queries(brief)],
                domain_filters=list(brief.explicit_domains),
            )
            rel_results = await self._search(rel_plan)
            # Ensure canonical official release endpoints are always included
            canonical_urls = canonical_release_urls(brief)
            seen_r_urls = {r.canonical_url or r.url for r in rel_results}
            for cu in dict.fromkeys(canonical_urls):
                if cu not in seen_r_urls:
                    rel_results.insert(
                        0,
                        SearchResult(
                            title=f"{brief.topic} Official Downloads",
                            url=cu,
                            provider="canonical",
                            rank=0,
                        ),
                    )
            rel_docs = await self._fetch_sources(rel_results[:6], ResearchMode.DEEP, max_chars=max_verify_chars)
            for idx, doc in enumerate(rel_docs, start=1):
                doc.source_id = f"S{idx}"
                doc.source_class = classify(doc.url, brief=brief).value
                verified_sources.append(doc)
                facts = extract_facts_from_document(doc, policy=brief.source_policy)
                verified_facts.extend(facts)

            from charlie.research.releases import is_stable_version, pick_stable
            stable_res = pick_stable(rel_docs, brief=brief)
            if stable_res:
                ver, dt, sid = stable_res
                subject_name = "Python" if "python" in brief.topic.lower() else brief.topic.title()
                cand_name = f"{subject_name} {ver}"
                report.candidates = [Candidate(name=cand_name, brand=subject_name, quote=f"{subject_name} {ver} - {dt}", source_id=sid)]
                primary_url = next((d.url for d in rel_docs if d.source_id == sid), "https://www.python.org/downloads/")
                stable_facts = [
                    Fact(candidate=cand_name, aspect="version", value=ver, quote=ver, source_id=sid, source_class=SourceClass.OFFICIAL.value, extractor="releases:pick_stable", url=primary_url),
                ]
                if dt:
                    stable_facts.append(
                        Fact(candidate=cand_name, aspect="release_date", value=dt, quote=dt, source_id=sid, source_class=SourceClass.OFFICIAL.value, extractor="releases:pick_stable", url=primary_url)
                    )
                verified_facts = stable_facts + [
                    f for f in verified_facts
                    if (f.aspect not in ("version", "release_date") or is_stable_version(f.value))
                    and f.value != ver
                ]

        else:
            # Candidate verification loop
            for candidate in candidates:
                for doc in disc_docs:
                    if doc.source_id == candidate.source_id or _matches_candidate_variant(candidate.name, doc.title, doc.url, doc.content[:1000]):
                        disc_facts = extract_facts_from_document(
                            doc,
                            candidate_name=candidate.name,
                            candidate_brand=candidate.brand,
                            policy=brief.source_policy,
                        )
                        for f in disc_facts:
                            if _matches_candidate_variant(candidate.name, doc.title, doc.url, f.quote):
                                verified_facts.append(f)

            def _candidate_coverage(candidate: Candidate) -> tuple[int, int, int]:
                candidate_key = re.sub(r"[^\w\s]", "", candidate.name).lower().strip()
                covered = {
                    fact.aspect
                    for fact in verified_facts
                    if fact.candidate
                    and re.sub(r"[^\w\s]", "", fact.candidate).lower().strip() == candidate_key
                    and fact.aspect in brief.aspects
                    and citable_for(fact.aspect, fact.source_class, brief.source_policy)
                }
                return (len(covered), len([fact for fact in verified_facts if fact.candidate and re.sub(r"[^\w\s]", "", fact.candidate).lower().strip() == candidate_key]), candidate.mentions)

            candidates = sorted(candidates, key=_candidate_coverage, reverse=True)
            report.candidates = candidates
            slots_to_verify = candidates[: (brief.option_count or 3) + 2]
            round_num = 0

            while round_num < max_rounds and slots_to_verify:
                round_num += 1
                for cand_idx, candidate in enumerate(slots_to_verify, start=1):
                    if (time.perf_counter() - p2_start) >= verify_deadline or (cancel_event and cancel_event.is_set()):
                        break

                    cand_brand = candidate.brand or ""
                    brand_host = OFFICIAL_REGISTRY.get(cand_brand.lower(), {}).get("domains", {"official"})
                    primary_host = next(iter(brand_host), "official")
                    await self._notify(
                        ResearchProgress(
                            "reading",
                            f"Verifying {candidate.name} on {primary_host} ({cand_idx}/{len(slots_to_verify)})",
                            cand_idx,
                            len(slots_to_verify),
                            ResearchMode.DEEP,
                        )
                    )

                    v_queries = verification_queries(candidate, brief)
                    v_plan = ResearchPlan(
                        goal=candidate.name,
                        mode=ResearchMode.DEEP,
                        queries=[ResearchQuery(q, "verification", list(brief.explicit_domains)) for q in v_queries],
                    )
                    v_results = await self._search(v_plan)

                    # Fetch official and store verification documents (60,000 chars)
                    v_docs = await self._fetch_sources(v_results[:4], ResearchMode.DEEP, max_chars=max_verify_chars)
                    for doc in v_docs:
                        doc.source_class = classify(doc.url, brief=brief, candidate_brand=candidate.brand).value
                        # Filter out forums/social if official required
                        if brief.source_policy == "official_required" and doc.source_class == SourceClass.FORUM_SOCIAL.value:
                            continue

                        # Product-variant guard: only associate if page matches candidate variant
                        if not _matches_candidate_variant(candidate.name, doc.title, doc.url, doc.content[:1000]):
                            continue

                        verified_sources.append(doc)
                        extracted = extract_facts_from_document(
                            doc,
                            candidate_name=candidate.name,
                            candidate_brand=candidate.brand,
                            policy=brief.source_policy,
                        )
                        # Filter extracted facts with variant guard on quote
                        for f in extracted:
                            if _matches_candidate_variant(candidate.name, doc.title, doc.url, f.quote):
                                verified_facts.append(f)

                # Coverage check
                complete_cands = [c for c in candidates if _is_candidate_complete(c, verified_facts, brief)]
                if len(complete_cands) >= (brief.option_count or 3):
                    break  # Coverage reached!
                # If gap exists and time remains, promote any unverified candidate slots
                verified_names = {c.name for c in slots_to_verify}
                remaining_candidates = [c for c in candidates if c.name not in verified_names]
                if not remaining_candidates or (time.perf_counter() - p2_start) >= verify_deadline - 10.0:
                    break
                slots_to_verify = remaining_candidates[:2]

        p2_elapsed = time.perf_counter() - p2_start
        phase_timings["phase_2_verify_ms"] = p2_elapsed * 1000

        # Assign stable source IDs (S1, S2, ...)
        unique_sources: List[SourceDocument] = []
        seen_urls: Set[str] = set()
        for doc in verified_sources + disc_docs:
            norm_url = doc.canonical_url or doc.url
            if norm_url not in seen_urls:
                seen_urls.add(norm_url)
                unique_sources.append(doc)

        old_doc_ids = {id(doc): getattr(doc, "source_id", None) for doc in unique_sources}

        report.sources = unique_sources
        report.citations = assign_citations(report.sources)
        report.facts = verified_facts

        # Re-map source_id on facts to match citations
        url_to_source_id = {doc.url: doc.source_id for doc in report.sources}
        for doc in report.sources:
            if getattr(doc, "canonical_url", None):
                url_to_source_id[doc.canonical_url] = doc.source_id
        old_id_to_new_id = {}
        for doc in report.sources:
            old_sid = old_doc_ids.get(id(doc))
            if old_sid:
                old_id_to_new_id[old_sid] = doc.source_id
            if hasattr(doc, "source_id") and doc.source_id:
                old_id_to_new_id[doc.source_id] = doc.source_id
        updated_facts: List[Fact] = []
        for f in report.facts:
            mapped_id = url_to_source_id.get(f.url) or old_id_to_new_id.get(f.source_id) or f.source_id
            updated_facts.append(
                Fact(
                    candidate=f.candidate,
                    aspect=f.aspect,
                    value=f.value,
                    quote=f.quote,
                    source_id=mapped_id,
                    source_class=f.source_class,
                    extractor=f.extractor,
                    url=f.url,
                    fetched_at=f.fetched_at,
                )
            )
        report.facts = list({(f.candidate, f.aspect, f.value, f.source_id): f for f in updated_facts}.values())

        # Remap candidate source_ids to valid citation IDs
        if getattr(report, "candidates", None):
            updated_candidates: List[Candidate] = []
            for c in report.candidates:
                c_mapped_id = old_id_to_new_id.get(c.source_id) or c.source_id
                updated_candidates.append(
                    Candidate(
                        name=c.name,
                        brand=c.brand,
                        quote=c.quote,
                        source_id=c_mapped_id,
                        mentions=getattr(c, "mentions", 1),
                    )
                )
            report.candidates = updated_candidates

        # Build evidence from verified sources
        report.evidence = self._extract_evidence(report.sources, query, policy=brief.source_policy)

        # Coverage evaluation
        complete_count = None
        target_count = None
        if brief.entity_kind == "product":
            complete_count = len([c for c in candidates if _is_candidate_complete(c, report.facts, brief)])
            target_count = brief.option_count or 3
            if complete_count < target_count:
                report.gaps.append(
                    f"Found {complete_count} of {target_count} requested options meeting all official specifications and budget."
                )
                report.partial = True

        total_elapsed = (time.perf_counter() - started) * 1000
        report.duration_ms = total_elapsed
        report.phase_ms = phase_timings

        if cancel_event and cancel_event.is_set():
            report.stop_reason = "cancelled"
        elif (
            brief.entity_kind == "product"
            and (not report.facts or (complete_count is not None and complete_count < (target_count or 1)))
        ) or (not report.facts and not report.evidence):
            report.stop_reason = "insufficient-evidence"
        else:
            report.stop_reason = "evidence-sufficient"

        # Structured log line for acceptance evidence
        official_sources_count = len(
            [s for s in report.sources if s.source_class in (SourceClass.OFFICIAL.value, SourceClass.OFFICIAL_STORE.value)]
        )
        logger.info(
            "Research sustained complete | topic=%r | candidates=%d | complete=%d | "
            "official_sources=%d | facts=%d | stop=%s | partial=%s | duration_ms=%.0f",
            brief.topic,
            len(report.candidates),
            len([c for c in report.candidates if _is_candidate_complete(c, report.facts, brief)]),
            official_sources_count,
            len(report.facts),
            report.stop_reason,
            report.partial,
            report.duration_ms,
        )
        return report

    async def _run_inner(
        self,
        query: str,
        mode: ResearchMode,
        cancel_event: Optional[asyncio.Event],
        domain_filters: Optional[List[str]] = None,
    ) -> ResearchReport:
        started = time.perf_counter()
        self._browser_escalations = 0
        plan = self.plan(query, mode, domain_filters=domain_filters)
        report = ResearchReport(query=query, mode=mode, plan=plan)
        brief = parse_brief(query)
        report.brief = brief

        await self._notify(ResearchProgress("planning", f"Planning {mode.value} research", mode=mode))
        if brief.entity_kind == "release":
            plan.queries = [ResearchQuery(text, "official release", list(plan.domain_filters))
                            for text in discovery_queries(brief)]
        elif mode is not ResearchMode.QUICK and self.query_planner is not None:
            queries = await self.query_planner(query)
            if queries:
                plan.queries = [
                    ResearchQuery(text, "targeted evidence", list(plan.domain_filters))
                    for text in queries[:max(1, int(getattr(self.config, "research_max_search_queries", 6)))]
                ]
        if cancel_event and cancel_event.is_set():
            report.stop_reason = "cancelled"
            return report

        await self._notify(ResearchProgress("searching", "Searching current sources", mode=mode))
        report.search_results = await self._search(plan)
        if brief.entity_kind == "release" and mode is not ResearchMode.QUICK:
            seeds = [SearchResult(f"{brief.topic} official releases", url, provider="canonical", rank=0)
                     for url in canonical_release_urls(brief)]
            report.search_results = seeds + report.search_results
        if not report.search_results:
            report.errors.append("No configured research provider returned results")
            report.stop_reason = "no-results"
            report.duration_ms = (time.perf_counter() - started) * 1000
            return report

        await self._notify(
            ResearchProgress(
                "found",
                f"Found {len(report.search_results)} search results",
                len(report.search_results),
                len(report.search_results),
                mode,
            )
        )
        if cancel_event and cancel_event.is_set():
            report.stop_reason = "cancelled"
            return report

        if mode is not ResearchMode.QUICK:
            await self._notify(ResearchProgress("reading", "Reading selected sources", mode=mode))
            documents = await self._fetch_sources(report.search_results, mode)
            for doc in documents:
                doc.source_class = classify(doc.url, brief=brief).value
            report.sources = await self._rank_sources(documents, plan, mode)

        if mode is ResearchMode.DEEP and not report.sources:
            followup = ResearchPlan(
                goal=query, mode=mode, domain_filters=list(plan.domain_filters),
                queries=[ResearchQuery(
                    f"{clean_query(query)} primary documentation", "evidence gap", list(plan.domain_filters),
                )],
            )
            extra = await search_plan(
                followup, self._providers(), limit=int(getattr(self.config, "research_max_sources", 12)), max_concurrency=2,
            )
            report.search_results.extend(extra)
            documents = await self._fetch_sources(extra, mode)
            for doc in documents:
                doc.source_class = classify(doc.url, brief=brief).value
            report.sources = await self._rank_sources(documents, plan, mode)

        report.citations = (
            assign_citations(report.sources)
            if report.sources
            else assign_search_citations(report.search_results)
        )
        report.evidence = self._extract_evidence(report.sources, query, policy=brief.source_policy)

        # Extract deterministic facts on inline path
        if report.sources:
            for doc in report.sources:
                facts = extract_facts_from_document(doc, policy=brief.source_policy)
                report.facts.extend(facts)

        if report.sources:
            grounded_ids = {item.source_id for item in report.evidence}
            report.sources = [item for item in report.sources if item.source_id in grounded_ids]
            report.citations = assign_citations(report.sources)
            report.evidence = self._extract_evidence(report.sources, query, policy=brief.source_policy)
        elif mode is not ResearchMode.QUICK:
            report.citations = []

        if brief.entity_kind == "release" and report.sources:
            stable = pick_stable(report.sources, brief=brief)
            if stable:
                version, released, source_id = stable
                source = next(doc for doc in report.sources if doc.source_id == source_id)
                report.facts = [Fact(candidate=brief.topic, aspect="version", value=version,
                    quote=version, source_id=source_id, source_class=source.source_class,
                    extractor="releases:pick_stable", url=source.url)]
                if released:
                    report.facts.append(Fact(candidate=brief.topic, aspect="release_date", value=released,
                        quote=released, source_id=source_id, source_class=source.source_class,
                        extractor="releases:pick_stable", url=source.url))

        report.products = []
        if is_shopping_query(query, plan):
            try:
                report.products = extract_products(
                    report.sources,
                    query,
                    str(getattr(self.config, "research_currency", "INR")),
                )
            except Exception as exc:
                logger.warning("Product enrichment failed: query=%r error=%s", query, exc, exc_info=True)
                report.products = []
        report.media = media_results(report.search_results)
        report.confidence = compute_confidence(report.evidence, report.sources)
        if report.evidence:
            report.stop_reason = "evidence-sufficient"
        elif mode is ResearchMode.QUICK and report.search_results:
            report.stop_reason = "search-snippets-only"
        else:
            report.stop_reason = "insufficient-evidence"
        report.duration_ms = (time.perf_counter() - started) * 1000
        await self._notify(ResearchProgress("done", "Research evidence ready", mode=mode))
        domains = grounded_domain_count(report.evidence, report.sources)
        logger.info(
            "Research complete: mode=%s queries=%d results=%d sources=%d citations=%d "
            "grounded_domains=%d source_diversity_confidence=%s stop=%s duration_ms=%.0f",
            mode.value,
            len(plan.queries),
            len(report.search_results),
            len(report.sources),
            len(report.citations),
            domains,
            "not-measurable" if report.confidence is None else f"{report.confidence:.2f}",
            report.stop_reason,
            report.duration_ms,
        )
        return report

    async def run(
        self,
        query: str,
        mode: str = "auto",
        *,
        cancel_event: Optional[asyncio.Event] = None,
        domain_filters: Optional[List[str]] = None,
        sustained: bool = False,
    ) -> ResearchReport:
        decision = self.decide(query, mode)
        if not decision.should_research or decision.mode is None:
            return ResearchReport(query=query, mode=ResearchMode.QUICK, stop_reason=decision.reason)

        is_sustained = (
            sustained
            or is_sustained_research_query(query, decision)
            or cancel_event is not None
        )

        if is_sustained:
            return await self._run_sustained(query, cancel_event=cancel_event, domain_filters=domain_filters)

        timeout = float(
            getattr(
                self.config,
                f"research_total_timeout_{decision.mode.value}_s",
                15 if decision.mode is ResearchMode.QUICK else 45,
            )
        )
        try:
            return await asyncio.wait_for(
                self._run_inner(query, decision.mode, cancel_event, domain_filters=domain_filters),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("Research timed out: mode=%s query=%r", decision.mode.value, query)
            return ResearchReport(
                query=query,
                mode=decision.mode,
                stop_reason="timeout",
                errors=["Research time budget exhausted"],
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Research failed: mode=%s query=%r", decision.mode.value, query, exc_info=True)
            return ResearchReport(query=query, mode=decision.mode, stop_reason="error", errors=[type(exc).__name__])

    def run_sync(
        self,
        query: str,
        mode: str = "auto",
        *,
        domain_filters: Optional[List[str]] = None,
        sustained: bool = False,
    ) -> ResearchReport:
        return asyncio.run(self.run(query, mode, domain_filters=domain_filters, sustained=sustained))
