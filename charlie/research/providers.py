"""Search-provider adapters. SearXNG is the default and paid providers are optional."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Protocol
from urllib.parse import urlparse

import httpx

from charlie.research.models import SearchResult

logger = logging.getLogger("charlie.research.providers")


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
            self._href = unescape(attributes.get("href") or "")
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
        results: List[SearchResult] = []
        for rank, item in enumerate(payload.get("results", [])[:limit], start=1):
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


@dataclass
class DuckDuckGoProvider:
    timeout_s: float = 8.0
    name: str = "duckduckgo"

    async def search(
        self, query: str, *, limit: int, domain_filters: Optional[List[str]] = None
    ) -> List[SearchResult]:
        if domain_filters:
            query = f"{query} " + " ".join(f"site:{domain}" for domain in domain_filters)
        async with httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=True) as client:
            response = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": "CharlieResearch/1.0"},
            )
            response.raise_for_status()
        parser = _DuckDuckGoParser()
        parser.feed(response.text)
        return [
            SearchResult(
                title=title or "Untitled source",
                url=url,
                snippet=snippet,
                provider=self.name,
                rank=index,
                domain=_domain(url),
            )
            for index, (title, url, snippet) in enumerate(parser.results[:limit], start=1)
            if url.startswith(("http://", "https://"))
        ]


async def search_with_fallback(
    providers: List[SearchProvider],
    query: str,
    *,
    limit: int,
    domain_filters: Optional[List[str]] = None,
) -> List[SearchResult]:
    """Use optional providers only when the primary provider cannot answer."""
    for provider in providers:
        try:
            results = await provider.search(query, limit=limit, domain_filters=domain_filters)
            if results:
                return results
        except Exception:
            logger.warning("Research provider %s failed for %r", provider.name, query, exc_info=True)
    return []
