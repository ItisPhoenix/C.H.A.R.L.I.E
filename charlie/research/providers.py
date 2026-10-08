"""Search-provider adapters. SearXNG is the default and paid providers are optional."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Protocol, Tuple
from urllib.parse import parse_qs, parse_qsl, urlparse

import httpx

from charlie.research.models import SearchResult

try:
    from curl_cffi.requests import AsyncSession as _CurlAsyncSession
except ImportError:  # pragma: no cover - exercised only in minimal installs
    _CurlAsyncSession = None

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


_QUERY_LEADING_NOISE_RE = re.compile(
    r"^\s*(?:research|search|find|look\s+into|tell\s+me\s+about)\s+",
    re.IGNORECASE,
)
_QUERY_GENERIC_NOISE_RE = re.compile(
    r"\b(?:best|latest|current|compare|options?|alternatives?|using)\b",
    re.IGNORECASE,
)
_QUERY_RELEASE_NOISE_RE = re.compile(
    r"\b(?:stable|official|sources?)\b",
    re.IGNORECASE,
)


def _normalise_provider_query(query: str) -> str:
    """Remove search-instruction filler while preserving the user's subject."""
    value = _QUERY_LEADING_NOISE_RE.sub("", query or "")
    value = _QUERY_GENERIC_NOISE_RE.sub(" ", value)
    if re.search(r"\b(?:release|version|date)\b", value, re.IGNORECASE):
        value = _QUERY_RELEASE_NOISE_RE.sub(" ", value)
    return re.sub(r"\s+", " ", value).strip() or query.strip()


@dataclass
class SearXNGProvider:
    base_url: str
    timeout_s: float = 8.0
    name: str = "searxng"
    engines: str = ""
    fallback_engines: str = ""

    async def search(
        self, query: str, *, limit: int, domain_filters: Optional[List[str]] = None
    ) -> List[SearchResult]:
        if not self.base_url:
            return []
        from charlie.research.ranking import search_result_matches_query

        def has_relevant_results(payload: dict) -> bool:
            return any(search_result_matches_query(query, SearchResult(
                str(item.get("title") or ""), str(item.get("url") or ""),
                str(item.get("content") or item.get("snippet") or ""),
            )) for item in payload.get("results") or [])
        provider_query = _normalise_provider_query(query)
        params: Dict[str, Any] = {"q": provider_query, "format": "json", "language": "en"}
        if self.engines.strip():
            params["engines"] = self.engines.strip()
        lowered = provider_query.lower()
        if any(word in lowered for word in ("news", "headline", "breaking")):
            params["categories"] = "news"
            if any(word in lowered for word in ("today", "latest", "current", "now", "live", "trending")):
                params["time_range"] = "day"
        if domain_filters:
            params["q"] = f"{provider_query} ({' OR '.join('site:' + domain for domain in domain_filters)})"
        configured_engines = [engine.strip() for engine in self.engines.split(",") if engine.strip()]
        async with httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=True) as client:
            response = await client.get(f"{self.base_url.rstrip('/')}/search", params=params)
            response.raise_for_status()
            payload = response.json()
            if not has_relevant_results(payload) and (payload.get("results") or payload.get("unresponsive_engines")):
                # Do not drop a curated pool back to the instance defaults:
                # those defaults are exactly where suspended/irrelevant Google
                # and Bing results re-entered the evidence chain. A one-engine
                # legacy configuration still gets one bounded DDG retry.
                fallback_engines = [engine.strip() for engine in self.fallback_engines.split(",") if engine.strip()]
                if len(configured_engines) <= 1 and fallback_engines:
                    logger.warning(
                        "SearXNG returned no relevant results; retrying configured fallback engine(s): %s",
                        ",".join(fallback_engines),
                    )
                    params["engines"] = ",".join(fallback_engines)
                    response = await client.get(f"{self.base_url.rstrip('/')}/search", params=params)
                    response.raise_for_status()
                    payload = response.json()
                else:
                    logger.warning(
                        "SearXNG curated engine pool returned no relevant results; skipping instance defaults"
                    )
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
        for rank, item in enumerate(raw_items, start=1):
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            if domain_filters:
                host = (urlparse(url).hostname or "").lower()
                if not any(host == domain.lower() or host.endswith("." + domain.lower()) for domain in domain_filters):
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
            if not search_result_matches_query(query, results[-1]):
                results.pop()
                continue
            if len(results) >= limit:
                break
        return results


@dataclass
class YaCyProvider:
    """Search a self-hosted YaCy index without contacting upstream engines."""

    base_url: str
    timeout_s: float = 8.0
    resource: str = "local"
    verify: str = "cacheonly"
    name: str = "yacy"

    async def search(
        self, query: str, *, limit: int, domain_filters: Optional[List[str]] = None
    ) -> List[SearchResult]:
        if not self.base_url:
            return []
        from charlie.research.ranking import search_result_matches_query

        provider_query = _normalise_provider_query(query)
        if domain_filters:
            provider_query = f"{provider_query} " + " ".join(f"site:{domain}" for domain in domain_filters)
        params = {
            "query": provider_query,
            "resource": self.resource.strip() or "local",
            "verify": self.verify.strip() or "cacheonly",
            "maximumRecords": max(1, int(limit)),
            "startRecord": 0,
            "nav": "none",
            "contentdom": "text",
        }
        async with httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=True) as client:
            response = await client.get(f"{self.base_url.rstrip('/')}/yacysearch.json", params=params)
            response.raise_for_status()
            payload = response.json()

        channels = payload.get("channels") if isinstance(payload, dict) else None
        items: list[dict] = []
        if isinstance(channels, list):
            for channel in channels:
                if isinstance(channel, dict):
                    items.extend(item for item in channel.get("items", []) if isinstance(item, dict))
        if not items and isinstance(payload, dict):
            items = [item for item in payload.get("items", []) if isinstance(item, dict)]

        results: List[SearchResult] = []
        for item in items:
            url = str(item.get("link") or item.get("url") or "").strip()
            if not url:
                continue
            result = SearchResult(
                title=str(item.get("title") or "Untitled source").strip(),
                url=url,
                snippet=str(item.get("description") or item.get("snippet") or "").strip(),
                provider=self.name,
                rank=len(results) + 1,
                published_at=item.get("pubDate") or item.get("published_at"),
                domain=_domain(url),
            )
            if not search_result_matches_query(query, result):
                continue
            if domain_filters:
                host = _domain(url)
                if not any(host == domain.lower() or host.endswith(f".{domain.lower()}") for domain in domain_filters):
                    continue
            results.append(result)
            if len(results) >= limit:
                break
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
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}


@dataclass
class DuckDuckGoProvider:
    timeout_s: float = 8.0
    name: str = "duckduckgo"
    endpoint: str = ""

    async def search(
        self, query: str, *, limit: int, domain_filters: Optional[List[str]] = None
    ) -> List[SearchResult]:
        provider_query = _normalise_provider_query(query)
        request_params = {"q": provider_query}
        if domain_filters:
            if len(domain_filters) == 1:
                request_params["q"] = f"site:{domain_filters[0]} {provider_query}"
            # DuckDuckGo honours at most one site: operator and silently ignores
            # the rest, so multiple filters are applied here instead of being
            # concatenated into a query that quietly returns the wrong pages.
        endpoint = self.endpoint.strip() or os.getenv("RESEARCH_DDG_ENDPOINT", "").strip()
        if not endpoint:
            logger.warning("DuckDuckGo fallback has no endpoint configured")
            return []
        if _CurlAsyncSession is not None:
            # DDG's no-JS endpoint is a browser form POST. curl_cffi keeps the
            # Chrome TLS/HTTP2 fingerprint that the endpoint expects; it does
            # not bypass a challenge, so challenge pages remain a clean miss.
            async with _CurlAsyncSession(impersonate="chrome") as client:
                response = await client.post(
                    endpoint,
                    data=request_params,
                    headers=_DDG_BROWSER_HEADERS,
                    timeout=self.timeout_s,
                    allow_redirects=True,
                )
                response.raise_for_status()
        else:
            async with httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=True) as client:
                response = await client.post(
                    endpoint,
                    data=request_params,
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


_BING_CHALLENGE_MARKERS = (
    "are you a robot",
    "unusual traffic",
    "verify you are human",
    "captcha",
)


def _normalize_bing_href(href: str) -> str:
    value = unescape(href).strip()
    try:
        parsed = urlparse(value)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    if parsed.hostname and parsed.hostname.lower() in {"bing.com", "www.bing.com"} and parsed.path == "/ck/a":
        encoded = parse_qs(parsed.query).get("u", [""])[0]
        if encoded.startswith("a1"):
            try:
                decoded = base64.urlsafe_b64decode(encoded[2:] + "=" * (-len(encoded[2:]) % 4)).decode()
                decoded_url = urlparse(decoded)
                if decoded_url.scheme.lower() in {"http", "https"} and decoded_url.netloc:
                    return decoded_url.geturl()
            except (UnicodeDecodeError, ValueError):
                return ""
    return parsed.geturl()


class _BingParser(HTMLParser):
    """Extract first-page Bing web results without a browser or API key."""

    def __init__(self) -> None:
        super().__init__()
        self.results: List[tuple[str, str, str]] = []
        self._in_result = False
        self._kind = ""
        self._href = ""
        self._title = ""
        self._snippet = ""
        self._buffer: List[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "li" and "b_algo" in classes:
            self._in_result = True
            self._kind = ""
            self._href = ""
            self._title = ""
            self._snippet = ""
        elif self._in_result and tag == "a" and self._kind == "":
            href = _normalize_bing_href(attributes.get("href") or "")
            if href:
                self._kind = "title"
                self._href = href
                self._buffer = []
        elif self._in_result and tag == "p":
            self._kind = "snippet"
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._in_result and self._kind:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._in_result:
            return
        if self._kind == "title" and tag == "a":
            self._title = " ".join("".join(self._buffer).split())
            self._kind = ""
        elif self._kind == "snippet" and tag == "p":
            self._snippet = " ".join("".join(self._buffer).split())
            self._kind = ""
        elif tag == "li":
            if self._href and self._title:
                self.results.append((self._title, self._href, self._snippet))
            self._in_result = False
            self._kind = ""


@dataclass
class BingProvider:
    endpoint: str = ""
    timeout_s: float = 8.0
    name: str = "bing"

    async def search(
        self, query: str, *, limit: int, domain_filters: Optional[List[str]] = None
    ) -> List[SearchResult]:
        endpoint = self.endpoint.strip() or os.getenv("RESEARCH_BING_ENDPOINT", "").strip()
        if not endpoint:
            return []
        provider_query = _normalise_provider_query(query)
        params = {"q": provider_query}
        if domain_filters and len(domain_filters) == 1:
            params["q"] = f"site:{domain_filters[0]} {provider_query}"
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.bing.com/",
        }
        if _CurlAsyncSession is not None:
            async with _CurlAsyncSession(impersonate="chrome") as client:
                response = await client.get(endpoint, params=params, headers=headers, timeout=self.timeout_s, allow_redirects=True)
                response.raise_for_status()
        else:
            async with httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=True) as client:
                response = await client.get(endpoint, params=params, headers=headers)
                response.raise_for_status()
        body = response.text
        lowered = body.lower()
        if any(marker in lowered for marker in _BING_CHALLENGE_MARKERS):
            logger.warning("Bing HTML fallback returned a challenge for %r", query)
            return []
        parser = _BingParser()
        parser.feed(body)
        from charlie.research.ranking import search_result_matches_query

        results: List[SearchResult] = []
        for title, url, snippet in parser.results:
            if domain_filters:
                host = _domain(url)
                if not any(host == item or host.endswith(f".{item}") for item in domain_filters):
                    continue
            result = SearchResult(title, url, snippet, provider=self.name, rank=len(results) + 1, domain=_domain(url))
            if not search_result_matches_query(query, result):
                continue
            results.append(result)
            if len(results) >= limit:
                break
        return results


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
