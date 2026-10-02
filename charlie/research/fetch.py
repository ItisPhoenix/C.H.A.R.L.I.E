"""Public-web fetching, SSRF protection, and Trafilatura extraction."""

from __future__ import annotations

import asyncio
import hashlib
import html
import inspect
import ipaddress
import logging
import re
import socket
from typing import Optional
from urllib.parse import urlparse, urlunparse

import httpx

from charlie.research.credibility import score_credibility
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

# ``follow_redirects=True`` without a hop bound lets a public URL bounce a
# worker through an unbounded chain before any content is seen. Five hops covers
# real http->https and CDN/language redirects and stops redirect loops early.
_MAX_REDIRECTS = 5
# ``socket.getaddrinfo`` has no timeout parameter, so an async caller must
# bound it externally. Without this a slow or hostile resolver stalls the whole
# event loop for every concurrent research source.
_URL_VALIDATION_TIMEOUT_S = 5.0
_CURL_MAX_REDIRECTS_SUPPORTED: Optional[bool] = None


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
    """Allow only public HTTP(S). Redirect targets are re-checked by the caller."""
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


async def validate_public_url_async(
    url: str,
    *,
    timeout_s: float = _URL_VALIDATION_TIMEOUT_S,
) -> str:
    """Async entry point for :func:`validate_public_url`.

    Name resolution is blocking and unbounded, so it runs in a worker thread
    behind an explicit deadline. The caller gets the loop back even when the
    resolver never answers; the abandoned thread cannot cancel itself but it no
    longer holds up every other coroutine.
    """
    try:
        return await asyncio.wait_for(asyncio.to_thread(validate_public_url, url), timeout_s)
    except asyncio.TimeoutError as exc:
        raise ValueError("Research URL host could not be resolved in time") from exc


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


def _is_soft_not_found(text: str) -> bool:
    """Detect error pages served with HTTP 200.

    Publishers routinely serve a branded "page not found" page at 200, and the
    extractor happily returns its navigation chrome. Those pages carry enough
    text to clear a length gate and were being treated as real sources, so the
    evidence chain could cite a page that says it does not exist.
    """
    lowered = text.lower()
    markers = (
        "page not found",
        "page doesn't exist",
        "page does not exist",
        "this page is missing",
        "404 not found",
        "404 error",
        "we can't find that page",
        "we cannot find that page",
        "content not found",
        "we're sorry, this page",
        "the page you are looking for",
        "the page you requested",
    )
    return any(marker in lowered for marker in markers)


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
    if _is_soft_not_found(text):
        logger.info("Discarding soft-404 page returned with 200: %s", result.url)
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
        quality_score=score_credibility(text, domain=result.domain).score,
        published_at=result.published_at,
    )


def _curl_supports_max_redirects() -> bool:
    """Probe once whether the installed curl_cffi can bound redirect hops."""
    global _CURL_MAX_REDIRECTS_SUPPORTED
    if _CURL_MAX_REDIRECTS_SUPPORTED is None:
        try:
            parameters = inspect.signature(_CurlAsyncSession.request).parameters
            _CURL_MAX_REDIRECTS_SUPPORTED = "max_redirects" in parameters
        except (TypeError, ValueError, AttributeError):
            _CURL_MAX_REDIRECTS_SUPPORTED = False
        if not _CURL_MAX_REDIRECTS_SUPPORTED:
            logger.warning(
                "curl_cffi cannot bound redirect hops; the post-fetch URL re-check "
                "is the only remaining redirect defence on this install"
            )
    return _CURL_MAX_REDIRECTS_SUPPORTED


async def _get_browser_shaped(
    url: str,
    *,
    timeout_s: float,
) -> tuple[int, str, str]:
    """GET with a browser TLS fingerprint. Returns (status, text, final_url).

    Falls back to httpx so the feature degrades rather than disappearing.
    """
    if CURL_CFFI_AVAILABLE and _CurlAsyncSession is not None:
        redirect_kwargs: dict[str, int] = {}
        if _curl_supports_max_redirects():
            redirect_kwargs["max_redirects"] = _MAX_REDIRECTS
        async with _CurlAsyncSession(impersonate="chrome") as session:
            response = await session.get(
                url,
                headers=_BROWSER_HEADERS,
                timeout=timeout_s,
                allow_redirects=True,
                **redirect_kwargs,
            )
            return int(response.status_code), response.text, str(response.url)

    async with httpx.AsyncClient(
        timeout=timeout_s,
        follow_redirects=True,
        max_redirects=_MAX_REDIRECTS,
    ) as client:
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

    Both the requested URL and the post-redirect URL are validated. Redirects
    are followed by the HTTP client, so checking only the requested URL lets a
    public URL bounce the fetch into the private network and the extracted
    private page becomes a citable source.
    """
    try:
        current_url = await validate_public_url_async(result.url)
    except ValueError:
        logger.info("Refusing research URL before fetch: %s", result.url)
        return None
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
        try:
            final_url = await validate_public_url_async(final_url)
        except ValueError:
            logger.warning(
                "Refusing research fetch: %s redirected to a non-public target %s",
                result.url,
                final_url,
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
