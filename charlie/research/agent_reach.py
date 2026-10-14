"""Public Agent Reach-compatible discovery adapters and local channel status."""

from __future__ import annotations

import html
import json
import math
import re
import shutil
import sys
import sysconfig
from pathlib import Path
from typing import Any

import httpx

from charlie.research.models import SearchResult

_MAX_RESULTS = 8
_MAX_RESPONSE_BYTES = 64 * 1024
_MAX_QUERY_CHARS = 256
_MAX_TIMEOUT_SECONDS = 8.0
_BVID = re.compile(r"BV[0-9A-Za-z]{10}\Z")
_HTML_TAG = re.compile(r"<[^>]{0,256}>")
_PLATFORM_DOMAINS = {
    "twitter": ("x.com", "twitter.com"),
    "x": ("x.com", "twitter.com"),
    "reddit": ("reddit.com",),
    "facebook": ("facebook.com",),
    "instagram": ("instagram.com",),
    "github": ("github.com",),
    "youtube": ("youtube.com", "youtu.be"),
    "bilibili": ("bilibili.com", "b23.tv"),
    "xiaohongshu": ("xiaohongshu.com", "xhslink.com"),
    "linkedin": ("linkedin.com",),
    "v2ex": ("v2ex.com",),
    "xueqiu": ("xueqiu.com",),
}


def platform_domains_in_query(query: str) -> list[str]:
    """Return explicit Agent Reach platform domains mentioned in the request."""
    text = str(query or "").casefold()
    return list(dict.fromkeys(
        domain
        for name, domains in _PLATFORM_DOMAINS.items()
        if (
            re.search(r"\b(?:twitter|x\.com|twitter\.com)\b", text)
            if name in {"x", "twitter"}
            else re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", text)
        )
        for domain in domains
    ))


class BilibiliProvider:
    """Public Bilibili search API fallback, matching Agent Reach's no-login channel."""

    name = "agent_reach_bilibili"

    async def search(self, query: str, *, limit: int, domain_filters=None) -> list[SearchResult]:
        if domain_filters and not any(
            domain.casefold().removeprefix("www.") in {"bilibili.com", "b23.tv"}
            for domain in domain_filters
        ):
            return []
        return await search_bilibili(query, limit=limit)


async def search_bilibili(query: str, *, limit: int = 5, timeout_s: float = 6.0) -> list[SearchResult]:
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
                "https://api.bilibili.com/x/web-interface/search/all/v2",
                params={"keyword": query.strip(), "page": 1},
                headers={"User-Agent": "Charlie-Research/1.0", "Accept": "application/json"},
            ) as response:
                response.raise_for_status()
                output = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(output) + len(chunk) > _MAX_RESPONSE_BYTES:
                        return []
                    output.extend(chunk)
        payload = json.loads(output)
    except (httpx.HTTPError, UnicodeDecodeError, json.JSONDecodeError, OSError, ValueError):
        return []

    data = payload.get("data") if isinstance(payload, dict) else None
    groups = data.get("result") if isinstance(data, dict) else None
    if not isinstance(groups, list):
        return []
    video_groups = [group for group in groups if isinstance(group, dict) and group.get("result_type") == "video"]
    results: list[SearchResult] = []
    seen: set[str] = set()
    for group in video_groups:
        for row in group.get("data", []):
            if not isinstance(row, dict):
                continue
            bvid = row.get("bvid")
            if not isinstance(bvid, str) or not _BVID.fullmatch(bvid):
                continue
            url = f"https://www.bilibili.com/video/{bvid}"
            if url in seen:
                continue
            seen.add(url)
            title = html.unescape(_HTML_TAG.sub("", str(row.get("title") or ""))).strip()
            if not title:
                continue
            # Search metadata helps select a URL; Charlie's fetcher must acquire evidence from that URL.
            results.append(SearchResult(
                title=title[:300],
                url=url,
                canonical_url=url,
                provider="agent_reach_bilibili",
                rank=len(results) + 1,
                domain="www.bilibili.com",
            ))
            if len(results) >= result_limit:
                return results
    return results


def platform_availability() -> dict[str, dict[str, Any]]:
    """Describe access class and local prerequisites; this is not a live health probe."""
    yt_dlp = shutil.which("yt-dlp")
    node_or_deno = shutil.which("node") or shutil.which("deno")
    agent_reach = shutil.which("agent-reach")
    if agent_reach is None:
        scripts_dir = Path(sysconfig.get_path("scripts"))
        candidate = scripts_dir / ("agent-reach.exe" if sys.platform == "win32" else "agent-reach")
        agent_reach = str(candidate) if candidate.is_file() else None
    statuses: dict[str, dict[str, Any]] = {
        "agent_reach_cli": {
            "status": "installed" if agent_reach else "missing",
            "blocker": "" if agent_reach else "Agent Reach CLI is not on PATH; no software was installed.",
        },
        "github": {
            "status": "public_no_auth",
            "backend": "Charlie anonymous GitHub REST search; public repositories only",
            "blocker": "Private repositories and write operations require user-configured authorization.",
        },
        "bilibili": {
            "status": "public_no_auth",
            "backend": "Agent Reach-compatible public search API; result pages still require fetch access",
            "blocker": "Upstream API may be unavailable or region-blocked; no live probe was run.",
        },
        "youtube": {
            "status": "ready" if yt_dlp and node_or_deno else "missing_prerequisite",
            "backend": "yt-dlp",
            "blocker": "" if yt_dlp and node_or_deno else "Requires yt-dlp and a JavaScript runtime (Node.js or Deno).",
        },
        "web": {"status": "public_read", "backend": "Charlie's bounded public fetch path", "blocker": ""},
        "rss": {
            "status": "public_known_url",
            "backend": "Charlie's bounded public fetch path",
            "blocker": "Feed URL must already be known; RSS is not keyword discovery.",
        },
        "linkedin": {
            "status": "public_read_only",
            "backend": "Charlie's bounded public fetch path",
            "blocker": "Private profiles, search, and authenticated data require user setup.",
        },
        "v2ex": {
            "status": "public_api_read",
            "backend": "Public V2EX topic API",
            "blocker": "The public API has no keyword-search endpoint; use normal research providers for discovery.",
        },
        "xueqiu": {
            "status": "manual_setup_required",
            "backend": "Agent Reach Xueqiu channel",
            "blocker": (
                "Live quotes/search depend on upstream access and optional session configuration; "
                "no credentials were inspected."
            ),
        },
        "reddit": {
            "status": "requires_user_auth",
            "backend": "OpenCLI or rdt-cli",
            "blocker": "Requires an explicitly configured logged-in session or user-provided credentials.",
        },
        "twitter": {
            "status": "requires_user_auth",
            "backend": "twitter-cli / OpenCLI",
            "blocker": "Search requires user-configured authentication; no cookies or credentials are read.",
        },
        "facebook": {
            "status": "requires_user_auth",
            "backend": "OpenCLI",
            "blocker": "Requires an explicitly configured logged-in session.",
        },
        "instagram": {
            "status": "requires_user_auth",
            "backend": "OpenCLI",
            "blocker": "Requires an explicitly configured logged-in session.",
        },
        "xiaohongshu": {
            "status": "requires_user_auth",
            "backend": "OpenCLI or user-configured cookie export",
            "blocker": "Requires manual setup; Charlie does not inspect browser cookies.",
        },
        "boss": {
            "status": "requires_user_auth",
            "backend": "Dedicated Chrome session via Agent Reach",
            "blocker": "Requires a user-created session and manual login.",
        },
        "xiaoyuzhou": {
            "status": "manual_setup_required",
            "backend": "Agent Reach transcription channel",
            "blocker": "Requires explicit transcription provider setup.",
        },
    }
    discovery = {
        "status": "provider_dependent",
        "backend": "Configured Charlie public web search providers with platform-domain filters",
        "blocker": (
            "Only publicly indexed pages are discoverable; direct platform search, private pages, "
            "and authenticated content are unavailable."
        ),
    }
    for platform in _PLATFORM_DOMAINS:
        if platform == "x":
            statuses[platform] = dict(statuses["twitter"])
        statuses[platform]["public_discovery"] = dict(discovery)
    return statuses
