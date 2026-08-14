"""Bounded research orchestration: search, fetch, extract, rank, cite, iterate."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from typing import Any, Awaitable, Callable, List, Optional

from charlie.research.cache import TTLCache
from charlie.research.citations import assign_citations, assign_search_citations
from charlie.research.crawler import crawl_document
from charlie.research.evidence import build_evidence
from charlie.research.fetch import fetch_document
from charlie.research.media import media_results
from charlie.research.models import (
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
from charlie.research.shopping import extract_products

logger = logging.getLogger("charlie.research.engine")

ProgressCallback = Callable[[ResearchProgress], Any]
BrowserFetchCallback = Callable[[SearchResult], Awaitable[Optional[SourceDocument]]]


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

    def plan(self, query: str, mode: ResearchMode) -> ResearchPlan:
        return build_plan(
            query,
            mode,
            max_queries=int(getattr(self.config, "research_max_search_queries", 6)),
            market=str(getattr(self.config, "research_market", "IN")),
            locale=str(getattr(self.config, "research_locale", "en-IN")),
        )

    async def _search(self, plan: ResearchPlan) -> List[SearchResult]:
        providers = self._providers()
        if not providers:
            return []
        limit = max(1, int(getattr(self.config, "research_max_sources", 12)))
        cache_key = "|".join(item.text for item in plan.queries)
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

    async def _run_inner(self, query: str, mode: ResearchMode, cancel_event: Optional[asyncio.Event]) -> ResearchReport:
        started = time.perf_counter()
        plan = self.plan(query, mode)
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
        report.evidence = build_evidence(report.sources, query)

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
                queries=[type(plan.queries[0])(f"{plan.goal} primary source", "follow-up")],
                constraints=plan.constraints,
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
            report.evidence = build_evidence(report.sources, query)

        report.products = extract_products(report.sources, query, str(getattr(self.config, "research_currency", "INR")))
        report.media = media_results(report.search_results)
        report.confidence = min(
            1.0,
            (len(report.evidence) / 8.0) * 0.6
            + (len(report.citations) / 5.0) * 0.4,
        )
        report.stop_reason = "evidence-sufficient" if report.evidence else "search-snippets-only"
        report.duration_ms = (time.perf_counter() - started) * 1000
        await self._notify(ResearchProgress("done", "Research evidence ready", mode=mode))
        logger.info(
            "Research complete: mode=%s queries=%d results=%d sources=%d citations=%d stop=%s duration_ms=%.0f",
            mode.value,
            len(plan.queries),
            len(report.search_results),
            len(report.sources),
            len(report.citations),
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
            return await asyncio.wait_for(self._run_inner(query, decision.mode, cancel_event), timeout=timeout)
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

    def run_sync(self, query: str, mode: str = "auto") -> ResearchReport:
        return asyncio.run(self.run(query, mode))
