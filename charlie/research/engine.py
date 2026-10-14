"""Bounded research orchestration: search, fetch, extract, rank, cite, iterate."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import re
import time
from dataclasses import replace
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Sequence, Set
from urllib.parse import urlparse

from charlie.research.cache import TTLCache
from charlie.research.citations import assign_citations, assign_search_citations
from charlie.research.crawler import crawl_document
from charlie.research.credibility import (
    organisational_domain as _organisational_domain,
)
from charlie.research.evidence import build_evidence, fact_evidence
from charlie.research.facts import (
    conflicting_facts,
    model_facts,
    matches_product_variant,
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
    Subquestion,
)
from charlie.research.ranking import rank_documents, rank_search_results
from charlie.research.router import ResearchDecision, is_sustained_research_query, route
from charlie.research.search import (
    clean_query,
    build_plan,
    explicit_domain_filters,
    parse_brief,
    search_plan,
)
from charlie.research.semantics import gather_semantic_scores
from charlie.research.sources import (
    classify,
    citable_for,
)

logger = logging.getLogger("charlie.research.engine")




ProgressCallback = Callable[[ResearchProgress], Any]
BrowserFetchCallback = Callable[[SearchResult], Awaitable[Optional[SourceDocument]]]
BriefPlannerCallback = Callable[[str, ResearchBrief], Awaitable[Dict[str, Any]]]
CandidateExtractorCallback = Callable[[ResearchBrief, List[SourceDocument]], Awaitable[List[Dict[str, Any]]]]

_SHARED_SEARCH_CACHE: TTLCache[List[SearchResult]] = TTLCache(256)
_SHARED_DOCUMENT_CACHE: TTLCache[SourceDocument] = TTLCache(128)


_CONFIDENCE_HALF_WEIGHT = 2.0
CREDIBILITY_FLOOR = 0.4

# Only a provider that actually ran a search can support a claim about the world.
# Any other outcome is our plumbing and must not read as "the web has no answer".
_SEARCHED_PROVIDER_STATUSES = frozenset({"ok", "empty"})


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


def _source_allowed(
    source: Optional[SourceDocument], policy: str, domain_filters: Optional[Sequence[str]] = None
) -> bool:
    if source is None:
        return False
    if policy == "official_required" and source.source_class not in {
        SourceClass.OFFICIAL.value,
        SourceClass.OFFICIAL_STORE.value,
    }:
        return False
    if domain_filters:
        hosts = [
            (urlparse(url).hostname or "").casefold().rstrip(".")
            for url in (source.url, source.canonical_url)
            if url
        ]
        return bool(hosts) and all(
            any(
                host == domain.casefold().rstrip(".")
                or host.endswith("." + domain.casefold().rstrip("."))
                for domain in domain_filters
            )
            for host in hosts
        )
    return True


def _research_brief(query: str, mode: ResearchMode) -> ResearchBrief:
    """Retain the request; the model supplies its questions and evidence fields."""
    brief = parse_brief(query)
    brief.original_request = query
    brief.objective = query
    brief.depth = mode.value
    brief.required_subquestions = [Subquestion(id="q1", question=query, required_fields=["q1"])]
    return brief





def _update_report_coverage(report: ResearchReport) -> bool:
    """Resolve model-planned questions only from independently bound fact quotes."""
    if not report.coverage:
        return False
    sources = {source.source_id: source for source in report.sources}
    policy = report.brief.source_policy if report.brief else "any_reputable"
    domains = report.brief.explicit_domains if report.brief else []
    gaps = []
    for question in report.coverage:
        if not question.evidence_requirements:
            question.status = "unresolved"
            question.supporting_claim_ids = []
            question.missing_evidence = ["An evidence obligation plan was not available"]
            gaps.append(f"Unresolved: {question.question}")
            continue
        facts = [
            fact for fact in report.facts
            if fact.aspect == question.id
            and _source_allowed(sources.get(fact.source_id), policy, domains)
        ]
        bound = fact_evidence(report.sources, facts)
        question.supporting_claim_ids = [item.passage_id for item in bound]
        question.refuting_claim_ids = []
        question.conflict = conflicting_facts(facts)
        question.status = "supported" if bound and not question.conflict else "unresolved"
        question.missing_evidence = (
            [] if question.status == "supported" else ["No consistent fetched evidence resolved this question"]
        )
        if question.status == "unresolved":
            gaps.append(f"Unresolved: {question.question}")
    report.gaps = list(dict.fromkeys(
        [gap for gap in report.gaps if not str(gap).startswith("Unresolved: ")] + gaps
    ))
    return all(question.status == "supported" for question in report.coverage)


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
    return matches_product_variant(candidate_name, doc_title, doc_url, text_span)


def _is_candidate_complete(candidate: Candidate, facts: List[Fact], brief: ResearchBrief) -> bool:
    """Compatibility check against only the fields supplied by this request."""
    supported = [
        fact for fact in facts
        if fact.candidate and fact.candidate.casefold() == candidate.name.casefold()
        and citable_for(fact.aspect, fact.source_class, brief.source_policy)
    ]
    return bool(supported) and not conflicting_facts(supported) and set(brief.aspects).issubset(
        {fact.aspect for fact in supported}
    )


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
        fact_extractor: Optional[CandidateExtractorCallback] = None,
        cache_scope: Optional[str] = None,
    ) -> None:
        self.config = config
        self.progress = progress
        self.browser_fetch = browser_fetch
        self.query_planner = query_planner
        self.brief_planner = brief_planner
        self.candidate_extractor = candidate_extractor
        self.fact_extractor = fact_extractor
        self.cache_scope = cache_scope.strip() if isinstance(cache_scope, str) and cache_scope.strip() else None
        self.search_cache: TTLCache[List[SearchResult]] = (
            _SHARED_SEARCH_CACHE if self.cache_scope else TTLCache(256)
        )
        self.document_cache: TTLCache[SourceDocument] = (
            _SHARED_DOCUMENT_CACHE if self.cache_scope else TTLCache(128)
        )
        self._active_report: Optional[ResearchReport] = None
        self._acquisition_deadline: Optional[float] = None
        self._query_limit: Optional[int] = None
        self._queries_used = 0
        self._source_limit: Optional[int] = None
        self._source_attempts = 0
        self._fetched_urls: Set[str] = set()
        self._concurrency_limit: Optional[int] = None
        self._iterations_limit = 1
        self._llm_call_limit: Optional[int] = None
        self._llm_calls_used = 0
        self._source_timeout_s: Optional[float] = None
        self._is_background_sustained = False

    def _configure_budget(self, mode: ResearchMode, *, sustained: bool) -> float:
        scope = "sustained" if sustained else mode.value
        limits = {
            "quick": (20.0, 4.0, 1, 2, 2, 1, 8.0, 2),
            "standard": (75.0, 12.0, 6, 12, 4, 2, 12.0, 5),
            "deep": (150.0, 20.0, 12, 24, 5, 4, 15.0, 6),
            "sustained": (300.0, 30.0, 24, 40, 6, 6, 20.0, 10),
        }
        total_s, reserve_s, queries, sources, concurrency, iterations, per_source_s, llm_calls = limits[scope]
        timeout_name = f"research_total_timeout_{scope}_s"
        total_s = float(getattr(self.config, timeout_name, total_s))
        reserve_s = float(
            getattr(self.config, f"research_synthesis_reserve_{scope}_s", reserve_s)
        )
        reserve_s = min(reserve_s, max(0.1, total_s * 0.25))
        self._acquisition_deadline = time.perf_counter() + max(0.0, total_s - reserve_s)
        self._query_limit = min(
            max(1, int(getattr(self.config, "research_max_search_queries", queries))), queries
        )
        self._queries_used = 0
        self._fetched_urls.clear()
        self._source_limit = min(
            max(1, int(getattr(self.config, "research_max_sources", sources))), sources
        )
        self._source_attempts = 0
        self._concurrency_limit = min(
            max(1, int(getattr(self.config, "research_max_concurrency", concurrency))), concurrency
        )
        self._iterations_limit = min(
            iterations,
            max(1, int(getattr(self.config, "research_max_iterations", iterations))),
        )
        self._llm_call_limit = min(
            llm_calls,
            max(1, int(getattr(self.config, "research_max_llm_calls", llm_calls))),
        )
        self._llm_calls_used = 0
        self._source_timeout_s = min(
            max(0.1, float(getattr(self.config, "research_fetch_timeout_s", per_source_s))),
            per_source_s,
        )
        self._is_background_sustained = sustained
        return max(0.0, total_s - reserve_s)

    def _consume_llm_call(self) -> bool:
        if self._llm_call_limit is not None and self._llm_calls_used >= self._llm_call_limit:
            return False
        self._llm_calls_used += 1
        return True

    async def _plan_brief(self, query: str, mode: ResearchMode) -> ResearchBrief:
        brief = _research_brief(query, mode)
        if self.brief_planner is None or not self._consume_llm_call():
            return brief
        try:
            planned = await self.brief_planner(query, brief)
            questions = planned.get("questions", []) if isinstance(planned, dict) else []
            scoped = []
            for item in questions:
                if not isinstance(item, dict) or not isinstance(item.get("question"), str):
                    continue
                question = item["question"].strip()
                requirements = item.get("evidence_requirements", [])
                if not question or not isinstance(requirements, list):
                    continue
                for requirement in requirements:
                    if not isinstance(requirement, str) or not requirement.strip():
                        continue
                    identity = f"q{len(scoped) + 1}"
                    scoped.append(Subquestion(
                        id=identity, question=f"{question}: {requirement.strip()}", required_fields=[identity],
                        evidence_requirements=[requirement.strip()],
                    ))
            if scoped:
                brief.required_subquestions = scoped
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.info("Research question planning unavailable; retaining the exact request", exc_info=True)
        return brief

    async def _read_model_facts(self, report: ResearchReport) -> None:
        if not report.sources or self.fact_extractor is None or not self._consume_llm_call():
            return
        try:
            proposals = await self.fact_extractor(report.brief, report.sources)
            facts = model_facts(proposals, report.sources, policy=report.brief.source_policy)
            question_ids = {item.id for item in report.coverage}
            retained = {(fact.aspect, fact.value, fact.quote, fact.source_id): fact for fact in report.facts}
            for fact in facts:
                if fact.aspect in question_ids:
                    retained[(fact.aspect, fact.value, fact.quote, fact.source_id)] = fact
            report.facts = list(retained.values())
            passages = {item.passage_id: item for item in report.evidence}
            passages.update({item.passage_id: item for item in fact_evidence(report.sources, report.facts)})
            report.evidence = list(passages.values())
            report.bind_passages()
        except asyncio.CancelledError:
            raise
        except Exception:
            report.errors.append("Model evidence extraction was unavailable")
            logger.info("Research evidence extraction unavailable", exc_info=True)

    def _remaining_acquisition_s(self) -> Optional[float]:
        if self._acquisition_deadline is None:
            return None
        return max(0.0, self._acquisition_deadline - time.perf_counter())

    async def _notify(self, progress: ResearchProgress) -> None:
        if self.progress is None:
            return
        report = self._active_report
        progress = replace(
            progress,
            source_count=len(self._fetched_urls),
            evidence_count=len(report.evidence) if report and report.evidence else None,
            finding_count=len(report.facts) if report and report.facts else None,
        )
        result = self.progress(progress)
        if inspect.isawaitable(result):
            await result

    def _providers(self):
        from charlie.research.providers import BingProvider, DuckDuckGoProvider, SearXNGProvider, YaCyProvider

        provider_timeout = self._source_timeout_s or float(
            getattr(self.config, "research_fetch_timeout_s", 20)
        )
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
                    timeout_s=provider_timeout,
                    endpoint=getattr(self.config, "research_ddg_endpoint", ""),
                )
            )
        if getattr(self.config, "research_bing_enabled", False):
            providers.append(
                BingProvider(
                    endpoint=getattr(self.config, "research_bing_endpoint", ""),
                    timeout_s=provider_timeout,
                )
            )
        if getattr(self.config, "research_yacy_enabled", False) and getattr(self.config, "research_yacy_url", ""):
            providers.append(
                YaCyProvider(
                    base_url=self.config.research_yacy_url,
                    timeout_s=provider_timeout,
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
            max_queries=min(
                int(getattr(self.config, "research_max_search_queries", 24)),
                self._query_limit
                or {ResearchMode.QUICK: 2, ResearchMode.STANDARD: 6, ResearchMode.DEEP: 12}[mode],
            ),
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
        if any(domain.removeprefix("www.").casefold() == "github.com" for domain in plan.domain_filters):
            from charlie.research.github import GitHubProvider

            providers.insert(0, GitHubProvider())
        if any(
            domain.casefold().removeprefix("www.") in {"bilibili.com", "b23.tv"}
            for domain in plan.domain_filters
        ):
            from charlie.research.agent_reach import BilibiliProvider

            providers.insert(0, BilibiliProvider())
        if not providers:
            if self._active_report is not None:
                self._active_report.provider_outcomes.append(
                    {
                        "provider": "configured",
                        "status": "unavailable",
                        "result_count": 0,
                        "error_class": "no_provider_configured",
                    }
                )
            return []
        if self._query_limit is not None:
            available_queries = max(0, self._query_limit - self._queries_used)
            plan.queries = plan.queries[:available_queries]
            if not plan.queries:
                return []
            self._queries_used += len(plan.queries)
        source_cap = self._source_limit or int(getattr(self.config, "research_max_sources", 40))
        limit = max(1, min(int(getattr(self.config, "research_max_sources", 40)), source_cap))
        provider_config = tuple(
            getattr(self.config, name, None)
            for name in (
                "searxng_url",
                "searxng_engines",
                "searxng_fallback_engines",
                "research_ddg_enabled",
                "research_ddg_endpoint",
                "research_bing_enabled",
                "research_bing_endpoint",
                "research_yacy_enabled",
                "research_yacy_url",
                "research_yacy_resource",
                "research_yacy_verify",
                "research_provider_order",
                "research_fetch_timeout_s",
                "research_market",
                "research_locale",
            )
        )
        provider_scope = hashlib.sha256(repr(provider_config).encode("utf-8")).hexdigest()
        cache_key = repr(
            (
                self.cache_scope,
                provider_scope,
                tuple(plan.domain_filters),
                tuple((item.text, tuple(item.domain_filters)) for item in plan.queries),
                plan.required_freshness,
                limit,
            )
        )
        cached = self.search_cache.get(cache_key)
        if cached is not None:
            return cached
        provider_outcomes: List[dict[str, object]] = []
        results = await search_plan(
            plan,
            providers,
            limit=limit,
            max_concurrency=self._concurrency_limit or int(getattr(self.config, "research_max_concurrency", 6)),
            progress=lambda current, total: logger.debug("Research search progress %d/%d", current, total),
            provider_outcomes=provider_outcomes,
        )
        if self._active_report is not None:
            self._active_report.provider_outcomes.extend(provider_outcomes)
        ranked = rank_search_results(results, plan, limit)
        if ranked:
            ttl = 60.0 if plan.required_freshness == "current" else 900.0
            self.search_cache.set(cache_key, ranked, ttl)
        return ranked

    async def _fetch_one(
        self,
        result: SearchResult,
        mode: ResearchMode,
        max_chars: Optional[int] = None,
        timeout_s: Optional[float] = None,
    ) -> Optional[SourceDocument]:
        browser_owner = getattr(self.browser_fetch, "__self__", None)
        fetcher_scope = id(browser_owner if browser_owner is not None else self.browser_fetch)
        cache_key = repr((self.cache_scope, fetcher_scope, result.canonical_url or result.url))
        cached = self.document_cache.get(cache_key)
        if cached is not None and len(cached.content) >= (max_chars or 14000) * 0.8:
            return cached
        status_out: list = []
        try:
            document = await fetch_document(
                result,
                timeout_s=timeout_s or self._source_timeout_s or float(
                    getattr(self.config, "research_fetch_timeout_s", 20)
                ),
                status_out=status_out,
                max_chars=max_chars or 14000,
            )
        except ValueError:
            logger.info("Research URL rejected: %s", result.url)
            document = None
        fallback_enabled = (
            getattr(self.config, "research_crawl_enabled", False) or self.browser_fetch is not None
        )
        terminal_fetch_status = bool(status_out and status_out[-1] in {404, 410})
        thin_extraction = bool(
            document is not None
            and document.extraction_method == "html-text"
            and len(document.content) < 1200
        )
        if (document is None or thin_extraction) and fallback_enabled and not terminal_fetch_status:
            try:
                if self.browser_fetch is not None:
                    crawled = await self.browser_fetch(result)
                else:
                    crawled = await crawl_document(
                        result,
                        timeout_s=timeout_s or self._source_timeout_s or float(
                            getattr(self.config, "research_fetch_timeout_s", 20)
                        ),
                    )
                if crawled is not None:
                    document = crawled
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.info("Research browser escalation failed for %s", result.url, exc_info=True)
        if status_out:
            logger.debug("Research source returned HTTP %s: %s", status_out[-1], result.url)

        if document is not None:
            document.content = sanitize_source_text(document.content)
            if not document.content.strip():
                return None
            document.refresh_identity()
            self.document_cache.set(cache_key, document, 300.0 if mode is ResearchMode.DEEP else 900.0)
        return document

    async def _fetch_until_covered(
        self,
        results: Sequence[SearchResult],
        mode: ResearchMode,
        report: ResearchReport,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> List[SourceDocument]:
        """Acquire small batches, stopping at supported coverage rather than a quota."""
        documents: List[SourceDocument] = []
        selected: List[SearchResult] = []
        seen: Set[str] = set()
        domains: dict[str, int] = {}
        domain_limit = max(1, int(getattr(self.config, "research_max_pages_per_domain", 4)))
        for result in results:
            identity = result.canonical_url or result.url
            domain = result.domain or "unknown"
            if identity in seen or domains.get(domain, 0) >= domain_limit:
                continue
            seen.add(identity)
            domains[domain] = domains.get(domain, 0) + 1
            selected.append(result)
        batch_size = 1 if mode is ResearchMode.QUICK else max(1, self._concurrency_limit or 1)
        policy = report.brief.source_policy if report.brief else "balanced"
        for offset in range(0, len(selected), batch_size):
            remaining = self._remaining_acquisition_s()
            if (cancel_event and cancel_event.is_set()) or (remaining is not None and remaining <= 0):
                break
            if self._source_limit is not None and self._source_attempts >= self._source_limit:
                break
            batch = await self._fetch_sources(
                selected[offset:offset + batch_size], mode, timeout_s=remaining,
            )
            for document in batch:
                document.source_class = classify(document.url, brief=report.brief).value
            if policy == "official_required" and report.brief:
                batch = [doc for doc in batch if _source_allowed(
                    doc, policy, report.brief.explicit_domains,
                )]
            documents.extend(batch)
            report.sources = (
                await rank_documents(documents, report.plan, len(documents))
                if report.plan and documents else list(documents)
            )
            report.citations = assign_citations(report.sources)
            report.evidence = self._extract_evidence(report.sources, report.query, policy=policy)
            report.bind_passages()
        return documents

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
            (
                document.document_id or document.source_id or document.canonical_url or document.url,
                f"{document.title}. {document.content[:600]}",
            )
            for document in documents
            if document.document_id or document.source_id or document.canonical_url or document.url
        ]
        semantic = await gather_semantic_scores(
            plan.goal,
            pairs,
            base_url=getattr(self.config, "memory_embedding_url", ""),
            model=getattr(self.config, "memory_embedding_model", ""),
            timeout_s=min(2.0, max(0.01, self._remaining_acquisition_s() or 2.0)),
        )
        return await rank_documents(documents, plan, limit, semantic=semantic)

    async def _fetch_sources(
        self,
        results: List[SearchResult],
        mode: ResearchMode,
        max_chars: Optional[int] = None,
        timeout_s: Optional[float] = None,
    ) -> List[SourceDocument]:
        configured_max = int(getattr(self.config, "research_max_sources", 40))
        remaining_attempts = (
            max(0, self._source_limit - self._source_attempts)
            if self._source_limit is not None
            else configured_max
        )
        max_sources = min(configured_max, remaining_attempts)
        max_per_domain = max(1, int(getattr(self.config, "research_max_pages_per_domain", 4)))
        concurrency = min(
            int(getattr(self.config, "research_max_concurrency", 6)),
            self._concurrency_limit or int(getattr(self.config, "research_max_concurrency", 6)),
        )
        semaphore = asyncio.Semaphore(max(1, concurrency))
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
        if max_sources <= 0 or not selected:
            return []
        self._source_attempts += len(selected)
        source_timeout = self._source_timeout_s or float(
            getattr(self.config, "research_fetch_timeout_s", 20)
        )
        remaining = self._remaining_acquisition_s()
        if remaining is not None:
            timeout_s = remaining if timeout_s is None else min(timeout_s, remaining)

        async def fetch_one(result: SearchResult) -> Optional[SourceDocument]:
            nonlocal completed
            try:
                async with semaphore:
                    document = await self._fetch_one(
                        result,
                        mode,
                        **({"max_chars": max_chars} if max_chars is not None else {}),
                        timeout_s=source_timeout,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Research source fetch failed: %s", result.url, exc_info=True)
                document = None
            completed += 1
            if document is not None:
                self._fetched_urls.add(document.url)
            try:
                await self._notify(
                    ResearchProgress(
                        "reading",
                        f"Reading source {completed}/{len(selected)}",
                        completed,
                        len(selected),
                        mode,
                    )
                )
            except Exception:
                logger.debug("Research progress callback failed", exc_info=True)
            return document

        tasks = [asyncio.create_task(fetch_one(item)) for item in selected]
        try:
            done, pending = await asyncio.wait(tasks, timeout=timeout_s)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        documents: List[SourceDocument] = []
        for task in tasks:
            if task not in done or task.cancelled():
                continue
            try:
                document = task.result()
            except Exception:
                logger.warning("Research source worker failed", exc_info=True)
                continue
            if document is not None:
                documents.append(document)
        return documents

    async def _run_sustained(
        self, query: str, cancel_event: Optional[asyncio.Event] = None,
        domain_filters: Optional[List[str]] = None, *, mode: ResearchMode = ResearchMode.DEEP,
    ) -> ResearchReport:
        """Sustained work uses the shared request-driven acquisition loop."""
        return await self._run_inner(query, mode, cancel_event, domain_filters)

    async def _run_inner(
        self,
        query: str,
        mode: ResearchMode,
        cancel_event: Optional[asyncio.Event],
        domain_filters: Optional[List[str]] = None,
    ) -> ResearchReport:
        started = time.perf_counter()
        brief = await self._plan_brief(query, mode)
        if domain_filters:
            brief.explicit_domains = list(dict.fromkeys(brief.explicit_domains + domain_filters))
        plan = self.plan(query, mode, domain_filters=brief.explicit_domains)
        report = ResearchReport(query=query, mode=mode, plan=plan)
        self._active_report = report
        report.brief = brief
        report.coverage = brief.required_subquestions

        max_gap_queries = 2
        followup_iterations = max(0, self._iterations_limit - 1) if mode is not ResearchMode.QUICK else 0
        query_budget = self._query_limit
        if query_budget is not None:
            followup_reserve = min(
                max(0, query_budget - 1),
                followup_iterations * max_gap_queries,
            )
            initial_query_budget = max(1, query_budget - followup_reserve)
        else:
            followup_reserve = 0
            initial_query_budget = max(
                1,
                int(getattr(self.config, "research_max_search_queries", 6)),
            )

        await self._notify(ResearchProgress("planning", f"Planning {mode.value} research", mode=mode))
        if mode is not ResearchMode.QUICK:
            deterministic_queries = list(plan.queries)
            planned_queries = []
            if self.query_planner is not None and self._consume_llm_call():
                try:
                    queries = await self.query_planner(
                        f"Original request: {query}\n"
                        f"Plan at most {initial_query_budget} distinct search queries."
                    )
                    if isinstance(queries, (list, tuple)):
                        planned_query_texts = []
                        seen_planned = set()
                        for text in queries:
                            if not isinstance(text, str) or not text.strip():
                                continue
                            normalized = text.strip()
                            if normalized.casefold() in seen_planned:
                                continue
                            planned_query_texts.append(normalized)
                            seen_planned.add(normalized.casefold())
                            if len(planned_query_texts) >= initial_query_budget:
                                break
                        planned_queries = [
                            ResearchQuery(text, "targeted evidence", list(plan.domain_filters))
                            for text in planned_query_texts
                        ]
                except Exception:
                    logger.info("Optional research query planning failed; using deterministic queries", exc_info=True)
            plan.queries = []
            seen_queries: set[str] = set()
            for candidate in deterministic_queries + planned_queries:
                key = candidate.text.strip().casefold()
                if key and key not in seen_queries:
                    plan.queries.append(candidate)
                    seen_queries.add(key)
            plan.queries = plan.queries[:initial_query_budget]
        if cancel_event and cancel_event.is_set():
            report.stop_reason = "cancelled"
            return report

        await self._notify(ResearchProgress("searching", "Searching current sources", mode=mode))
        report.search_results = await self._search(plan)
        if not report.search_results:
            outcomes = report.provider_outcomes
            statuses = [item.get("status") for item in outcomes]
            if "challenge" in statuses:
                report.stop_reason = "provider-challenge"
            elif any(status in {"failed", "unavailable"} for status in statuses):
                report.stop_reason = "provider-exhausted"
            elif statuses and not any(status in _SEARCHED_PROVIDER_STATUSES for status in statuses):
                report.stop_reason = "provider-unconfigured"
            else:
                report.stop_reason = "no-results"
            report.errors.append(f"No results to verify ({report.stop_reason})")
            report.duration_ms = (time.perf_counter() - started) * 1000
            return report

        await self._notify(
            ResearchProgress(
                "found",
                f"Found {len(report.search_results)} candidate links",
                len(report.search_results),
                len(report.search_results),
                mode,
            )
        )
        if cancel_event and cancel_event.is_set():
            report.stop_reason = "cancelled"
            return report

        # Quick changes the acquisition budget, not the requirement to read evidence.
        await self._notify(ResearchProgress("reading", "Reading selected sources", mode=mode))
        documents = await self._fetch_until_covered(
            report.search_results, mode, report, cancel_event,
        )
        if cancel_event and cancel_event.is_set():
            report.stop_reason = "cancelled"
            return report
        for doc in documents:
            doc.source_class = classify(doc.url, brief=brief).value
        if brief.source_policy == "official_required":
            documents = [
                doc for doc in documents
                if _source_allowed(doc, brief.source_policy, brief.explicit_domains)
            ]
        ranked_sources = await self._rank_sources(documents, plan, mode)
        ranked_urls = {doc.canonical_url or doc.url for doc in ranked_sources}
        source_limit = max(1, int(getattr(self.config, "research_max_sources", 12)))
        # Keep zero-score fetches visible; they remain ungrounded until evidence extraction succeeds.
        unranked_fetched = [
            doc for doc in documents
            if doc.relevance_score <= 0 and (doc.canonical_url or doc.url) not in ranked_urls
        ]
        report.sources = ranked_sources + unranked_fetched[:max(0, source_limit - len(ranked_sources))]

        report.citations = (
            assign_citations(report.sources)
            if report.sources
            else assign_search_citations(report.search_results)
        )
        report.evidence = self._extract_evidence(report.sources, query, policy=brief.source_policy)
        report.bind_passages()

        await self._read_model_facts(report)

        if mode is not ResearchMode.QUICK:
            seen_queries = {item.text.casefold() for item in plan.queries}
            seen_urls = {result.canonical_url or result.url for result in report.search_results}
            for _iteration in range(followup_iterations):
                if _update_report_coverage(report):
                    break
                remaining_queries = (
                    max(0, self._query_limit - self._queries_used)
                    if self._query_limit is not None
                    else max_gap_queries
                )
                available_followup_queries = min(max_gap_queries, remaining_queries)
                if available_followup_queries <= 0:
                    break
                unresolved = [item for item in report.coverage if item.status == "unresolved"]
                followup_queries = []
                gap_lines = []
                fallback_queries = []
                for item in unresolved:
                    query_text = clean_query(item.question)
                    if item.required_fields and item.required_fields[0].startswith("source:"):
                        domain = item.required_fields[0].split(":", 1)[1]
                        gap_lines.append(f"{item.question} (required domain: {domain})")
                        fallback_queries.append(f"{query_text} site:{domain}")
                    else:
                        gap_lines.append(item.question)
                        fallback_queries.append(query_text)
                planned_gaps = []
                if (
                    self.query_planner is not None
                    and (self._llm_call_limit is None or self._llm_calls_used + 1 < self._llm_call_limit)
                    and self._consume_llm_call()
                ):
                    try:
                        planned = await self.query_planner(
                            f"Original request: {query}\n"
                            f"Plan at most {available_followup_queries} distinct follow-up search queries.\n"
                            "Unresolved evidence gaps:\n"
                            + "\n".join(f"- {item}" for item in gap_lines)
                        )
                        if isinstance(planned, (list, tuple)):
                            planned_gaps = [
                                text.strip() for text in planned
                                if isinstance(text, str) and text.strip()
                            ]
                    except Exception:
                        logger.info("Research gap query planning unavailable; using the gap wording", exc_info=True)
                for query_text in planned_gaps or fallback_queries:
                    normalized_query = query_text.strip()
                    if not normalized_query or normalized_query.casefold() in seen_queries:
                        continue
                    followup_queries.append(
                        ResearchQuery(
                            normalized_query,
                            "evidence gap",
                            list(dict.fromkeys(plan.domain_filters + explicit_domain_filters(normalized_query))),
                        )
                    )
                    seen_queries.add(normalized_query.casefold())
                    if len(followup_queries) >= available_followup_queries:
                        break
                if not followup_queries:
                    break
                before = sum(item.status != "unresolved" for item in report.coverage)
                followup = ResearchPlan(
                    goal=query,
                    mode=mode,
                    queries=followup_queries,
                    domain_filters=list(plan.domain_filters),
                )
                extra = await self._search(followup)
                extra = [
                    result for result in extra
                    if (result.canonical_url or result.url) not in seen_urls
                ]
                if not extra:
                    break
                for result in extra:
                    seen_urls.add(result.canonical_url or result.url)
                report.search_results.extend(extra)
                if report.plan is not None:
                    report.plan.queries.extend(followup.queries)
                new_documents = await self._fetch_sources(
                    extra,
                    mode,
                    timeout_s=self._remaining_acquisition_s(),
                )
                if not new_documents:
                    break
                for document in new_documents:
                    document.source_class = classify(document.url, brief=brief).value
                unique_documents = {
                    document.canonical_url or document.url: document
                    for document in report.sources + new_documents
                }
                report.sources = await self._rank_sources(
                    list(unique_documents.values()), plan, mode
                )
                report.citations = assign_citations(report.sources)
                report.evidence = self._extract_evidence(
                    report.sources, query, policy=brief.source_policy
                )
                report.bind_passages()
                await self._read_model_facts(report)
                _update_report_coverage(report)
                after = sum(item.status != "unresolved" for item in report.coverage)
                if after <= before:
                    break

        report.products = []
        report.media = media_results(report.search_results)
        report.confidence = compute_confidence(report.evidence, report.sources)
        coverage_complete = _update_report_coverage(report)
        if report.evidence and coverage_complete:
            report.stop_reason = "evidence-sufficient"
        elif report.evidence:
            report.stop_reason = "partial-evidence"
            report.partial = True
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
            report = ResearchReport(query=query, mode=ResearchMode.QUICK, stop_reason=decision.reason)
            report.finalize_outcome()
            return report

        explicit_research = bool(re.search(r"\\bresearch\\b", query, re.IGNORECASE))
        sustained = sustained and explicit_research
        deep_budget = sustained or is_sustained_research_query(query, decision)
        selected_mode = ResearchMode.DEEP if deep_budget else decision.mode
        timeout = self._configure_budget(selected_mode, sustained=sustained)
        self._active_report = None
        try:
            operation = self._run_inner(
                query, selected_mode, cancel_event, domain_filters=domain_filters,
            )
            report = await asyncio.wait_for(operation, timeout=max(0.01, timeout))
        except asyncio.TimeoutError:
            logger.warning("Research acquisition budget expired: mode=%s query=%r", selected_mode.value, query)
            report = self._active_report or ResearchReport(query=query, mode=selected_mode)
            report.stop_reason = "timeout"
            report.partial = bool(report.evidence or report.facts)
            report.errors.append("Research acquisition budget exhausted")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Research failed: mode=%s query=%r", selected_mode.value, query, exc_info=True)
            report = self._active_report or ResearchReport(query=query, mode=selected_mode)
            report.stop_reason = "error"
            report.errors.append(type(exc).__name__)
        report.finalize_outcome()
        report.llm_calls_used = self._llm_calls_used
        report.llm_call_limit = self._llm_call_limit
        return report

    def run_sync(
        self,
        query: str,
        mode: str = "auto",
        *,
        domain_filters: Optional[List[str]] = None,
        sustained: bool = False,
    ) -> ResearchReport:
        return asyncio.run(self.run(query, mode, domain_filters=domain_filters, sustained=sustained))
