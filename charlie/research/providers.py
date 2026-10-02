"""Search-provider adapters. SearXNG is the default and paid providers are optional."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Protocol, Tuple
from urllib.parse import parse_qs, parse_qsl, urlparse

import httpx

from charlie.research.models import SearchResult

logger = logging.getLogger("charlie.research.providers")


def _normalize_duckduckgo_href(href: str) -> str:
    """Unwrap DDG result redirects and retain only valid HTTP(S) targets."""
    value = unescape(href).strip()
    if value.startswith("//"):
        value = f"https:{value}"
    try:
        parsed = urlparse(value)
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc or not hostname:
        return ""

    if hostname.lower() in {"duckduckgo.com", "www.duckduckgo.com"} and parsed.path == "/l/":
        targets = parse_qs(parsed.query).get("uddg", [])
        if len(targets) != 1:
            return ""
        value = targets[0].strip()
        try:
            parsed = urlparse(value)
            hostname = parsed.hostname
            parsed.port
        except ValueError:
            return ""
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc or not hostname:
            return ""

    return parsed._replace(scheme=parsed.scheme.lower()).geturl()


class _DuckDuckGoParser(HTMLParser):
    """Small dependency-free parser for DDG's public HTML fallback."""

    def __init__(self) -> None:
        super().__init__()
        self.results: List[tuple[str, str, str]] = []
        self._kind = ""
        self._href = ""
        self._buffer: List[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        classes = set()
        attributes = dict(attrs)
        classes.update((attributes.get("class") or "").split())
        if tag == "a" and "result__a" in classes:
            self._kind = "title"
            self._href = _normalize_duckduckgo_href(attributes.get("href") or "")
            self._buffer = []
        elif "result__snippet" in classes or (tag == "td" and "result-snippet" in classes):
            self._kind = "snippet"
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._kind:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._kind:
            return
        value = " ".join("".join(self._buffer).split())
        if self._kind == "title" and self._href:
            self.results.append((value, self._href, ""))
        elif self._kind == "snippet" and self.results:
            title, url, _ = self.results[-1]
            self.results[-1] = (title, url, value)
        self._kind = ""
        self._buffer = []


class SearchProvider(Protocol):
    name: str

    async def search(
        self, query: str, *, limit: int, domain_filters: Optional[List[str]] = None
    ) -> List[SearchResult]: ...


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower()


@dataclass
class SearXNGProvider:
    base_url: str
    timeout_s: float = 8.0
    name: str = "searxng"

    async def search(
        self, query: str, *, limit: int, domain_filters: Optional[List[str]] = None
    ) -> List[SearchResult]:
        if not self.base_url:
            return []
        params: Dict[str, Any] = {"q": query, "format": "json", "language": "en"}
        lowered = query.lower()
        if any(word in lowered for word in ("today", "latest", "current", "now", "live", "trending")):
            params["time_range"] = "day"
        if any(word in lowered for word in ("news", "headline", "breaking")):
            params["categories"] = "news"
        if domain_filters:
            params["site"] = ",".join(domain_filters)
        async with httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=True) as client:
            response = await client.get(f"{self.base_url.rstrip('/')}/search", params=params)
            response.raise_for_status()
            payload = response.json()
        # SearXNG answers HTTP 200 with an empty set when every upstream engine
        # is suspended (CAPTCHA, rate limit, access denied). That is
        # indistinguishable from a genuine no-hits query unless surfaced.
        # A non-empty result set means the instance IS searching, so a
        # suspended engine is degraded coverage rather than failure.
        raw_items = payload.get("results") or []
        unresponsive = payload.get("unresponsive_engines") or []
        if unresponsive and not raw_items:
            detail = ", ".join(f"{n}: {r}" for n, r in unresponsive if n)
            logger.warning(
                "SearXNG returned no results; upstream engines suspended (%s). "
                "The instance is reachable but cannot search.",
                detail,
            )
        elif unresponsive:
            logger.info(
                "SearXNG degraded: %d result(s) from healthy engines; suspended (%s).",
                len(raw_items),
                ", ".join(f"{n}: {r}" for n, r in unresponsive if n),
            )
        results: List[SearchResult] = []
        for rank, item in enumerate(raw_items[:limit], start=1):
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            results.append(
                SearchResult(
                    title=str(item.get("title") or "Untitled source").strip(),
                    url=url,
                    snippet=str(item.get("content") or item.get("snippet") or "").strip(),
                    provider=self.name,
                    rank=rank,
                    published_at=item.get("publishedDate") or item.get("published_at"),
                    domain=_domain(url),
                )
            )
        return results


@dataclass
class ExaProvider:
    api_key: str
    timeout_s: float = 8.0
    name: str = "exa"

    async def search(self, query: str, *, limit: int, domain_filters: Optional[List[str]] = None) -> List[SearchResult]:
        if not self.api_key:
            return []
        payload: Dict[str, Any] = {"query": query, "numResults": limit, "contents": {"text": {}}}
        if domain_filters:
            payload["includeDomains"] = domain_filters
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            response = await client.post(
                "https://api.exa.ai/search",
                headers={"x-api-key": self.api_key, "content-type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        return [
            SearchResult(
                title=str(item.get("title") or "Untitled source"),
                url=str(item.get("url") or ""),
                snippet=str(item.get("text") or item.get("snippet") or ""),
                provider=self.name,
                rank=index,
                domain=_domain(str(item.get("url") or "")),
            )
            for index, item in enumerate(data.get("results", [])[:limit], start=1)
            if item.get("url")
        ]


@dataclass
class TavilyProvider:
    api_key: str
    timeout_s: float = 8.0
    name: str = "tavily"

    async def search(self, query: str, *, limit: int, domain_filters: Optional[List[str]] = None) -> List[SearchResult]:
        if not self.api_key:
            return []
        payload: Dict[str, Any] = {
            "api_key": self.api_key,
            "query": query,
            "max_results": limit,
            "include_raw_content": False,
        }
        if domain_filters:
            payload["include_domains"] = domain_filters
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            response = await client.post("https://api.tavily.com/search", json=payload)
            response.raise_for_status()
            data = response.json()
        return [
            SearchResult(
                title=str(item.get("title") or "Untitled source"),
                url=str(item.get("url") or ""),
                snippet=str(item.get("content") or ""),
                provider=self.name,
                rank=index,
                domain=_domain(str(item.get("url") or "")),
            )
            for index, item in enumerate(data.get("results", [])[:limit], start=1)
            if item.get("url")
        ]


_DDG_CHALLENGE_MARKERS = (
    "anomaly.js",
    "anomaly-modal",
    "are you a robot",
    "unusual traffic",
    "please verify you are a human",
    "detected unusual activity",
)

_DDG_BROWSER_HEADERS = {
    # httpx carries a Python TLS fingerprint, so a branded User-Agent is
    # cosmetic. A real browser header is still correct: DDG also weighs it when
    # deciding whether to serve the challenge page instead of results.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://duckduckgo.com/",
}


@dataclass
class DuckDuckGoProvider:
    timeout_s: float = 8.0
    name: str = "duckduckgo"

    async def search(
        self, query: str, *, limit: int, domain_filters: Optional[List[str]] = None
    ) -> List[SearchResult]:
        request_params = {"q": query}
        if domain_filters:
            if len(domain_filters) == 1:
                request_params["q"] = f"site:{domain_filters[0]} {query}"
            # DuckDuckGo honours at most one site: operator and silently ignores
            # the rest, so multiple filters are applied here instead of being
            # concatenated into a query that quietly returns the wrong pages.
        async with httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=True) as client:
            response = await client.get(
                "https://html.duckduckgo.com/html/",
                params=request_params,
                headers=_DDG_BROWSER_HEADERS,
            )
            response.raise_for_status()
        body = response.text
        lowered = body.lower()
        if any(marker in lowered for marker in _DDG_CHALLENGE_MARKERS):
            logger.warning(
                "DuckDuckGo served a bot challenge for %r; treating the "
                "fallback as unavailable rather than as zero results.",
                query,
            )
            return []
        parser = _DuckDuckGoParser()
        parser.feed(body)
        hits = []
        for index, (title, url, snippet) in enumerate(parser.results[: limit * 2], start=1):
            if domain_filters:
                host = _domain(url)
                if not any(host == item or host.endswith(f".{item}") for item in domain_filters):
                    continue
            hits.append(
                SearchResult(
                    title=title or "Untitled source",
                    url=url,
                    snippet=snippet,
                    provider=self.name,
                    rank=len(hits) + 1,
                    domain=_domain(url),
                )
            )
            if len(hits) >= limit:
                break
        return hits


_TRACKING_PARAMS = frozenset(
    {
        "utm", "fbclid", "gclid", "dclid", "msclkid", "igshid", "mc_cid", "mc_eid",
        "ref", "referrer", "source", "spm", "yclid", "_hsenc", "_hsmi", "icid",
    }
)


def _is_tracking_param(key: str) -> bool:
    lowered = key.lower()
    return lowered in _TRACKING_PARAMS or lowered.startswith("utm_")


def _dedupe_key(result: SearchResult) -> str:
    """Normalize a URL so the same page from two providers collapses to one hit.

    Providers disagree on scheme, ``www.``, trailing slashes, fragments and
    tracking tags, so a naive string compare keeps the same article several
    times and inflates apparent source coverage.
    """
    url = (result.url or "").strip().lower()
    if not url:
        return ""

    parsed = urlparse(url if "://" in url else f"https://{url}")
    netloc = parsed.netloc.removeprefix("www.")
    path = (parsed.path or "/").rstrip("/") or "/"

    kept = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not _is_tracking_param(key)
    ]
    query = "&".join(f"{key}={value}" for key, value in sorted(kept))

    return f"{netloc}{path}" + (f"?{query}" if query else "")


async def search_with_fallback(
    providers: List[SearchProvider],
    query: str,
    *,
    limit: int,
    domain_filters: Optional[List[str]] = None,
) -> List[SearchResult]:
    """Query every provider concurrently and merge the union of their hits.

    Strict first-provider-wins loses coverage: a provider that honours the
    domain filter strictly answers with a handful of results and suppresses
    providers that would return far more. Merging keeps the broadest
    evidence set while still degrading gracefully when a provider fails.
    """
    if not providers:
        return []

    async def run(provider: SearchProvider) -> Tuple[str, List[SearchResult]]:
        try:
            hits = await provider.search(query, limit=limit, domain_filters=domain_filters)
        except Exception:
            logger.warning("Research provider %s failed for %r", provider.name, query, exc_info=True)
            return provider.name, []
        return provider.name, hits

    outcomes = await asyncio.gather(*(run(p) for p in providers))

    merged: List[SearchResult] = []
    seen: set = set()
    for name, hits in outcomes:
        if not hits:
            continue
        added = 0
        for hit in hits:
            key = _dedupe_key(hit)
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(hit)
            added += 1
        logger.debug(
            "Research provider %s contributed %d unique result(s) for %r",
            name,
            added,
            query,
        )

    # Preserve provider priority: SearXNG-first ordering wins on rank ties.
    priority = {p.name: i for i, p in enumerate(providers)}
    merged.sort(key=lambda r: (priority.get(r.provider, len(priority)),))
    return merged[:limit]
