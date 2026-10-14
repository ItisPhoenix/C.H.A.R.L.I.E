"""Anonymous public GitHub repository discovery for the research pipeline."""

from __future__ import annotations

import json
import math
import re
from typing import Any
from urllib.parse import urlsplit

import httpx

from charlie.research.models import SearchResult

_MAX_RESULTS = 8
_MAX_OUTPUT_BYTES = 64 * 1024
_MAX_QUERY_CHARS = 256
_MAX_TIMEOUT_SECONDS = 8.0
_REPOSITORY_PART_RE = re.compile(r"[A-Za-z0-9_.-]{1,100}\Z")


class GitHubProvider:
    name = "github"

    async def search(self, query: str, *, limit: int, domain_filters=None) -> list[SearchResult]:
        return await search_github(query, limit=limit)


def _canonical_repository_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.query
            or parsed.fragment
        ):
            return None
    except ValueError:
        return None

    parts = parsed.path.rstrip("/").split("/")
    if len(parts) != 3 or parts[0] or not all(_REPOSITORY_PART_RE.fullmatch(part) for part in parts[1:]):
        return None
    owner, repository = parts[1:]
    if owner in {".", ".."} or repository in {".", ".."} or repository.endswith(".git"):
        return None
    return f"https://github.com/{owner}/{repository}"


def _results(payload: Any, result_limit: int) -> list[SearchResult]:
    rows = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    results: list[SearchResult] = []
    seen_urls: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = _canonical_repository_url(row.get("html_url"))
        name = row.get("full_name")
        if not url or not isinstance(name, str) or len(name) > 205:
            continue
        owner, repository = url.removeprefix("https://github.com/").split("/", 1)
        if name.casefold() != f"{owner}/{repository}".casefold() or url in seen_urls:
            continue
        seen_urls.add(url)
        results.append(SearchResult(
            title=name,
            url=url,
            canonical_url=url,
            provider="github",
            rank=len(results) + 1,
            domain="github.com",
        ))
        if len(results) >= result_limit:
            break
    return results


async def search_github(
    query: str,
    *,
    limit: int = 5,
    timeout_s: float = 6.0,
) -> list[SearchResult]:
    """Search public repositories without invoking gh or reading saved credentials."""
    if not isinstance(query, str) or not query.strip() or len(query.strip()) > _MAX_QUERY_CHARS:
        return []
    try:
        result_limit = max(1, min(_MAX_RESULTS, int(limit)))
        timeout = float(timeout_s)
    except (TypeError, ValueError, OverflowError):
        return []
    if not math.isfinite(timeout) or timeout <= 0:
        return []
    timeout = min(timeout, _MAX_TIMEOUT_SECONDS)

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            async with client.stream(
                "GET",
                "https://api.github.com/search/repositories",
                params={"q": query.strip(), "per_page": result_limit},
                headers={"Accept": "application/vnd.github+json", "User-Agent": "Charlie-Research/1.0"},
            ) as response:
                response.raise_for_status()
                output = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(output) + len(chunk) > _MAX_OUTPUT_BYTES:
                        return []
                    output.extend(chunk)
        return _results(json.loads(output), result_limit)
    except (httpx.HTTPError, UnicodeDecodeError, json.JSONDecodeError, OSError, ValueError):
        return []
