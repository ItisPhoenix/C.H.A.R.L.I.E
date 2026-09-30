"""Public-web fetching, SSRF protection, and Trafilatura extraction."""

from __future__ import annotations

import hashlib
import html
import ipaddress
import logging
import re
import socket
from typing import Optional
from urllib.parse import urlparse, urlunparse

import httpx

from charlie.research.models import SearchResult, SourceDocument

logger = logging.getLogger("charlie.research.fetch")

# curl_cffi does the TLS handshake with a real browser's JA3/HTTP2 fingerprint.
# Anti-bot vendors match Python's signature and refuse before headers are read,
# so a browser User-Agent on httpx does not help.
try:
    from curl_cffi.requests import AsyncSession as _CurlAsyncSession

    CURL_CFFI_AVAILABLE = True
except ImportError:
    _CurlAsyncSession = None
    CURL_CFFI_AVAILABLE = False

_BROWSER_HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
}

_MIN_CONTENT_CHARS = 160
_MAX_DOCUMENT_CHARS = 14000
_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title\s*>", re.IGNORECASE | re.DOTALL)
_SPACE_RE = re.compile(r"\s+")


def canonicalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower() if parsed.hostname else ""
    port = parsed.port
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        port = None
    netloc = host if port is None else f"{host}:{port}"
    return urlunparse((scheme, netloc, parsed.path or "/", "", parsed.query, ""))


def _is_blocked_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_public_url(url: str) -> str:
    """Allow only public HTTP(S), including redirect targets checked by caller."""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Research URL must use public http or https")
    host = parsed.hostname.lower()
    if host in {"localhost", "metadata.google.internal", "metadata"} or host.endswith(".local"):
        raise ValueError("Research URL targets a private or local host")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise ValueError("Research URL host could not be resolved") from exc
    if any(_is_blocked_ip(address) for address in addresses):
        raise ValueError("Research URL targets a private or local network")
    return canonicalize_url(url)


def _fallback_text(markup: str) -> str:
    text = html.unescape(_TAG_RE.sub(" ", markup))
    return _SPACE_RE.sub(" ", text).strip()


def extract_html_title(markup: str) -> Optional[str]:
    match = _TITLE_RE.search(markup)
    if match is None:
        return None
    title = html.unescape(_TAG_RE.sub(" ", match.group(1)))
    return _SPACE_RE.sub(" ", title).strip() or None


def extract_text(markup: str) -> tuple[str, str]:
    try:
        import trafilatura

        extracted = trafilatura.extract(markup, include_comments=False, include_tables=True) or ""
        if extracted.strip():
            return _SPACE_RE.sub(" ", extracted).strip(), "trafilatura"
    except ImportError:
        logger.debug("Trafilatura unavailable; using conservative HTML text fallback")
    except Exception:
        logger.debug("Trafilatura extraction failed", exc_info=True)
    return _fallback_text(markup), "html-text"


def document_from_content(
    result: SearchResult,
    content: str,
    *,
    extraction_method: str,
    title: Optional[str] = None,
) -> Optional[SourceDocument]:
    text = content.strip()[:_MAX_DOCUMENT_CHARS]
    minimum = 1 if result.provider == "browser_read" else _MIN_CONTENT_CHARS
    if len(text) < minimum:
        return None
    canonical = canonicalize_url(result.url)
    return SourceDocument(
        url=result.url,
        canonical_url=canonical,
        title=title or result.title,
        domain=result.domain,
        content=text,
        extraction_method=extraction_method,
        word_count=len(text.split()),
        content_hash=hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest(),
        relevance_score=0.0,
        quality_score=min(1.0, len(text) / 4000),
        published_at=result.published_at,
    )


async def _get_browser_shaped(
    url: str,
    *,
    timeout_s: float,
) -> tuple[int, str, str]:
    """GET with a browser TLS fingerprint. Returns (status, text, final_url).

    Falls back to httpx so the feature degrades rather than disappearing.
    """
    if CURL_CFFI_AVAILABLE and _CurlAsyncSession is not None:
        async with _CurlAsyncSession(impersonate="chrome") as session:
            response = await session.get(
                url,
                headers=_BROWSER_HEADERS,
                timeout=timeout_s,
                allow_redirects=True,
            )
            return int(response.status_code), response.text, str(response.url)

    async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
        response = await client.get(url, headers=_BROWSER_HEADERS)
        return int(response.status_code), response.text, str(response.url)


async def fetch_document(
    result: SearchResult,
    *,
    timeout_s: float = 12.0,
    client: Optional[httpx.AsyncClient] = None,
    status_out: Optional[list] = None,
) -> Optional[SourceDocument]:
    """Fetch and extract one source.

    ``status_out`` receives the HTTP status on an error response, so callers
    can tell a 403 bot-wall from a transport failure.
    """
    current_url = validate_public_url(result.url)
    try:
        if client is not None:
            response = await client.get(current_url, headers=_BROWSER_HEADERS)
            status, body, final_url = int(response.status_code), response.text, str(response.url)
        else:
            status, body, final_url = await _get_browser_shaped(
                current_url, timeout_s=timeout_s
            )
        if status >= 400:
            if status_out is not None:
                status_out.append(status)
            logger.debug(
                "Research fetch refused with HTTP %s for %s", status, result.url
            )
            return None
        page_title = extract_html_title(body)
        text, method = extract_text(body)
        redirected = SearchResult(
            title=result.title,
            url=final_url,
            snippet=result.snippet,
            provider=result.provider,
            rank=result.rank,
            published_at=result.published_at,
            domain=urlparse(final_url).netloc.lower(),
        )
        return document_from_content(
            redirected,
            text,
            extraction_method=method,
            title=page_title or result.title,
        )
    except Exception:
        logger.debug("Research fetch failed for %s", result.url, exc_info=True)
        return None
