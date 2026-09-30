"""Bounded research orchestration: search, fetch, extract, rank, cite, iterate."""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
import time
from typing import Any, Awaitable, Callable, Iterable, List, Optional, Sequence
from urllib.parse import urlparse

from charlie.research.cache import TTLCache
from charlie.research.citations import assign_citations, assign_search_citations
from charlie.research.crawler import crawl_document
from charlie.research.evidence import build_evidence
from charlie.research.fetch import fetch_document
from charlie.research.media import media_results
from charlie.research.models import (
    EvidenceItem,
    ResearchMode,
    ResearchPlan,
    ResearchProgress,
    ResearchReport,
    SearchResult,
    SourceDocument,
)
from charlie.research.ranking import rank_documents, rank_search_results
from charlie.research.router import ResearchDecision, route
from charlie.research.search import build_plan, search_plan
from charlie.research.shopping import extract_products, is_shopping_query

logger = logging.getLogger("charlie.research.engine")

ProgressCallback = Callable[[ResearchProgress], Any]
BrowserFetchCallback = Callable[[SearchResult], Awaitable[Optional[SourceDocument]]]


# --- Confidence ------------------------------------------------------------
#
# `ResearchReport.confidence` used to be a count proxy:
#   min(1.0, len(evidence)/8 * 0.6 + len(citations)/5 * 0.4)
# Eight evidence items and five citations saturated it.  A measured run fetched
# 12 pages, kept 1 source, drew 40 sentences from that single page, and logged
# confidence 1.00.  That is a fabricated number wearing a measurement's label,
# so it is gone.
#
# What IS measurable in this pipeline is source independence: how many distinct
# organisations actually grounded the evidence.  What is NOT measurable is
# cross-source agreement.  `EvidenceItem.contradiction` (models.py) is declared
# with a `False` default and is set nowhere in the codebase, so nothing knows
# whether two sources corroborate or contradict each other.  An agreement term
# is therefore deliberately absent rather than approximated with a stand-in
# that would look like corroboration while meaning nothing.
#
# Consequences of this design, stated plainly:
#   * one source can never read as high confidence - the curve is pinned low at
#     a single distinct domain, because a lone page is an uncorroborated claim
#     no matter how many sentences were scraped out of it;
#   * the score is a source-diversity indicator, not a calibrated probability
#     that the answer is true, and it never reaches 1.0 for any finite number
#     of web sources;
#   * when the signal cannot be measured at all the result is None, so callers
#     can tell "low confidence" apart from "not measurable".

_TWO_LABEL_PUBLIC_SUFFIXES = frozenset(
    {
        "ac.in", "ac.uk", "co.in", "co.jp", "co.kr", "co.nz", "co.uk", "co.za",
        "com.au", "com.br", "com.cn", "com.mx", "com.sg", "com.tr", "com.tw",
        "gov.in", "gov.uk", "net.au", "net.in", "ne.jp", "or.jp", "org.au",
        "org.in", "org.uk",
    }
)

# Denominator offset for n / (n + k).  k = 2 puts a single domain at 0.33 and
# three domains at 0.60, so corroboration is required before the number can
# even reach the middle of the range.
_CONFIDENCE_HALF_WEIGHT = 2.0


def organisational_domain(host: str) -> str:
    """Reduce a host to the identity that could count as an independent source.

    Subdomains are collapsed (``blog.example.com`` == ``www.example.com``)
    because they are the same publisher, and counting them as corroborating
    sources is the exact failure mode this replaces.  Two-label public suffixes
    are handled with a small curated set rather than a full public-suffix list;
    ranking.py already takes shortcuts of this kind, and the error direction
    here is conservative - undercounting publishers lowers the reported
    confidence, it never inflates it.
    """
    cleaned = (host or "").strip().lower()
    if not cleaned:
        return ""
    cleaned = cleaned.split("@")[-1]
    if cleaned.startswith("["):  # IPv6 literal, not a publisher identity.
        return ""
    cleaned = cleaned.split(":")[0].rstrip(".")
    labels = [label for label in cleaned.split(".") if label]
    if len(labels) < 2:
        return ".".join(labels)
    if len(labels) >= 3 and ".".join(labels[-2:]) in _TWO_LABEL_PUBLIC_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def compute_confidence(
    evidence: Sequence[EvidenceItem],
    sources: Iterable[SourceDocument],
) -> Optional[float]:
    """Return a source-diversity confidence for a research report.

    Counts only the domains of sources that actually contributed at least one
    surviving evidence item, so a fetched-but-unused page cannot raise the
    number.

    Returns None when the signal is not measurable at all - no evidence, or
    evidence whose source cannot be traced to a resolvable domain.  A caller
    that receives None must treat the result as "unknown", never as "zero".
    """
    domain_by_source: dict[str, str] = {}
    for document in sources:
        host = organisational_domain(document.domain) or organisational_domain(
            urlparse(document.url).netloc
        )
        if host and document.source_id:
            domain_by_source[document.source_id] = host

    grounded = {
        domain_by_source[item.source_id]
        for item in evidence
        if domain_by_source.get(item.source_id)
    }
    if not grounded:
        return None
    count = float(len(grounded))
    return count / (count + _CONFIDENCE_HALF_WEIGHT)


def grounded_domain_count(
    evidence: Sequence[EvidenceItem],
    sources: Iterable[SourceDocument],
) -> int:
    """Distinct independent publishers behind the surviving evidence."""
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


# --- Scraped-text hygiene --------------------------------------------------
#
# A real delivered answer contained: "the growth rate figure shows as `NaN`
# (likely a data issue on the source side) [S2]".  A literal NaN had survived
# extraction, become an EvidenceItem, and been quoted to the user as content.
#
# The canonical filter for this is `charlie.research.evidence._safe_sentence`,
# which another agent owns and which is deliberately NOT edited from here.
# This engine-level scrub is applied at the single choke point every fetched
# document passes through (`ResearchEngine._fetch_one`), so junk is removed
# before `SourceDocument.content` is built, ranked, cached, or quoted - which
# also keeps the document cache clean for later turns.
#
# Junk-bearing sentences are DROPPED, not rewritten.  Deleting the token out of
# "revenue grew from NaN" would produce fluent text that reads as authoritative
# while having silently altered a source.  Losing recall is the safer error
# when the contract is never to fabricate.

_ENTITY_RE = re.compile(r"&(?:nbsp|ensp|emsp|thinsp|#160|#8194|#8195|#8201);", re.I)
_ZERO_WIDTH_RE = re.compile("[\u00ad\u200b-\u200f\u2060\ufeff]")
_TEMPLATE_RE = re.compile(r"\{[{%][^}%]{0,400}[}%]\\}")
_RAW_TAG_RE = re.compile(r"</?[A-Za-z][^<>]{0,400}>")
_OBJECT_RE = re.compile(r"\[object\s+Object\]", re.I)
_SEGMENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_ALNUM_RE = re.compile(r"[A-Za-z0-9\u00c0-\u024f]")

# Case-sensitive on purpose: `NaN` is the JS literal, while "nan" is a real
# English word (grandmother, nanometre) that must survive.
_NAN_RE = re.compile(r"(?<![A-Za-z0-9_])(?:NaN|[+-]?Infinity)(?![A-Za-z0-9_])")
_UNDEFINED_RE = re.compile(r"(?<![A-Za-z0-9_])undefined(?![A-Za-z0-9_])", re.I)
# "undefined behaviour/behavior" is legitimate academic English.
_UNDEFINED_LEGIT_RE = re.compile(r"undefined\s+(?:behaviour|behaviors|behavior)s?", re.I)
# Bare "null" is legitimate research prose ("null hypothesis", "null result"),
# so only code-shaped uses count as junk.
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
    """Return True when a text segment is extraction noise, not page content."""
    text = segment.strip()
    if not text:
        return True
    if not _ALNUM_RE.search(text):
        # Punctuation-only leftovers: "&nbsp;", "{", "}", "|".
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
    """Strip extraction artifacts out of scraped page text.

    Normalises entity/zero-width/template noise, then drops any segment that is
    a JS literal or a placeholder rather than prose.  Returns cleaned text, or
    an empty string when the document was entirely junk.
    """
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



class ResearchEngine:
    def __init__(
        self,
        config: Any,
        *,
        progress: Optional[ProgressCallback] = None,
        browser_fetch: Optional[BrowserFetchCallback] = None,
    ) -> None:
        self.config = config
        self.progress = progress
        self.browser_fetch = browser_fetch
        self.search_cache: TTLCache[List[SearchResult]] = TTLCache(256)
        self.document_cache: TTLCache[SourceDocument] = TTLCache(128)

    async def _notify(self, progress: ResearchProgress) -> None:
        if self.progress is None:
            return
        result = self.progress(progress)
        if inspect.isawaitable(result):
            await result

    def _providers(self):
        from charlie.research.providers import (
            DuckDuckGoProvider,
            ExaProvider,
            SearXNGProvider,
            TavilyProvider,
        )

        providers = []
        if getattr(self.config, "searxng_url", ""):
            providers.append(SearXNGProvider(self.config.searxng_url))
        if getattr(self.config, "exa_api_key", ""):
            providers.append(ExaProvider(self.config.exa_api_key))
        if getattr(self.config, "tavily_api_key", ""):
            providers.append(TavilyProvider(self.config.tavily_api_key))
        if getattr(self.config, "research_ddg_enabled", True):
            providers.append(
                DuckDuckGoProvider(
                    timeout_s=float(getattr(self.config, "research_fetch_timeout_s", 12))
                )
            )
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

    def _extract_evidence(self, sources: List[SourceDocument], query: str) -> List[EvidenceItem]:
        """Build evidence with a per-source budget sized to the source list.

        The ceiling scales with the number of fetched documents so no source can
        be starved of evidence by a more verbose neighbour.
        """
        per_source = max(1, int(getattr(self.config, "research_evidence_per_source", 6)))
        ceiling = per_source * max(1, len(sources))
        return build_evidence(sources, query, per_source_max=per_source, max_items=ceiling)

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
            logger.info("Research search cache hit: mode=%s queries=%d", plan.mode.value, len(plan.queries))
            return cached
        results = await search_plan(
            plan,
            providers,
            limit=limit,
            max_concurrency=int(getattr(self.config, "research_max_concurrency", 6)),
            progress=lambda current, total: logger.debug("Research search progress %d/%d", current, total),
        )
        ranked = rank_search_results(results, plan, limit)
        ttl = 60.0 if plan.required_freshness == "current" else 900.0
        self.search_cache.set(cache_key, ranked, ttl)
        return ranked

    async def _fetch_one(self, result: SearchResult, mode: ResearchMode) -> Optional[SourceDocument]:
        cache_key = result.canonical_url or result.url
        cached = self.document_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            document = await fetch_document(
                result,
                timeout_s=float(getattr(self.config, "research_fetch_timeout_s", 12)),
            )
        except ValueError:
            logger.info("Research URL rejected: %s", result.url)
            document = None
        if document is None and mode is not ResearchMode.QUICK and getattr(self.config, "research_crawl_enabled", True):
            document = await crawl_document(
                result,
                timeout_s=float(getattr(self.config, "research_fetch_timeout_s", 12)),
                max_depth=int(getattr(self.config, "research_crawl_max_depth", 2)),
                max_pages=int(getattr(self.config, "research_crawl_max_pages", 20)),
            )
        if document is None and mode is not ResearchMode.QUICK and self.browser_fetch is not None:
            logger.info("Escalating source to Browser Executor: %s", result.url)
            document = await self.browser_fetch(result)
        if document is not None:
            before = len(document.content)
            document.content = sanitize_source_text(document.content)
            removed = before - len(document.content)
            if not document.content.strip():
                logger.info(
                    "Research source had no usable text after sanitising: url=%s removed_chars=%d",
                    result.url,
                    removed,
                )
                return None
            if removed:
                logger.info(
                    "Research source sanitised: url=%s removed_chars=%d kept_chars=%d",
                    result.url,
                    removed,
                    len(document.content),
                )
            self.document_cache.set(cache_key, document, 300.0 if mode is ResearchMode.DEEP else 900.0)
        return document

    async def _fetch_sources(self, results: List[SearchResult], mode: ResearchMode) -> List[SourceDocument]:
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
                document = await self._fetch_one(result, mode)
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

    async def _run_inner(
        self,
        query: str,
        mode: ResearchMode,
        cancel_event: Optional[asyncio.Event],
        domain_filters: Optional[List[str]] = None,
    ) -> ResearchReport:
        started = time.perf_counter()
        plan = self.plan(query, mode, domain_filters=domain_filters)
        report = ResearchReport(query=query, mode=mode, plan=plan)
        await self._notify(ResearchProgress("planning", f"Planning {mode.value} research", mode=mode))
        if cancel_event and cancel_event.is_set():
            report.stop_reason = "cancelled"
            return report

        await self._notify(ResearchProgress("searching", "Searching current sources", mode=mode))
        report.search_results = await self._search(plan)
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
            report.sources = rank_documents(
                await self._fetch_sources(report.search_results, mode),
                plan,
                int(getattr(self.config, "research_max_sources", 12)),
            )
        report.citations = (
            assign_citations(report.sources)
            if report.sources
            else assign_search_citations(report.search_results)
        )
        report.evidence = self._extract_evidence(report.sources, query)

        if mode is ResearchMode.DEEP and len(report.sources) < 2 and not (cancel_event and cancel_event.is_set()):
            await self._notify(
                ResearchProgress(
                    "iterating",
                    "Evidence thin; running one bounded follow-up search",
                    mode=mode,
                )
            )
            followup = ResearchPlan(
                goal=plan.goal,
                mode=mode,
                queries=[
                    type(plan.queries[0])(
                        f"{plan.goal} primary source",
                        "follow-up",
                        list(plan.domain_filters),
                    )
                ],
                constraints=plan.constraints,
                domain_filters=list(plan.domain_filters),
            )
            extra = await search_plan(
                followup,
                self._providers(),
                limit=4,
                max_concurrency=int(getattr(self.config, "research_max_concurrency", 6)),
            )
            extra_docs = await self._fetch_sources(extra, mode)
            existing_urls = {old.url for old in report.search_results}
            report.search_results.extend(item for item in extra if item.url not in existing_urls)
            report.sources = rank_documents(
                report.sources + extra_docs,
                plan,
                int(getattr(self.config, "research_max_sources", 12)),
            )
            report.citations = assign_citations(report.sources)
            report.evidence = self._extract_evidence(report.sources, query)

        if report.sources:
            # A source is dropped only when it genuinely produced no usable
            # evidence for the query.  Renumbering after the drop means the
            # evidence has to be rebuilt so its source IDs match the new
            # citation IDs.
            grounded_ids = {item.source_id for item in report.evidence}
            report.sources = [item for item in report.sources if item.source_id in grounded_ids]
            report.citations = assign_citations(report.sources)
            report.evidence = self._extract_evidence(report.sources, query)
        elif mode is not ResearchMode.QUICK:
            report.citations = []

        report.products = []
        if is_shopping_query(query, plan):
            try:
                report.products = extract_products(
                    report.sources,
                    query,
                    str(getattr(self.config, "research_currency", "INR")),
                )
            except Exception as exc:
                logger.warning(
                    "Product enrichment failed: query=%r error=%s",
                    query,
                    exc,
                    exc_info=True,
                )
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
        if report.confidence is None:
            logger.info(
                "Research confidence NOT MEASURABLE: mode=%s sources=%d evidence=%d reason=%s",
                mode.value,
                len(report.sources),
                len(report.evidence),
                "no-grounded-source-domains",
            )
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
    ) -> ResearchReport:
        decision = self.decide(query, mode)
        if not decision.should_research or decision.mode is None:
            return ResearchReport(query=query, mode=ResearchMode.QUICK, stop_reason=decision.reason)
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
    ) -> ResearchReport:
        return asyncio.run(self.run(query, mode, domain_filters=domain_filters))
