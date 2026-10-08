"""Public-web fetching, SSRF protection, and Trafilatura extraction."""

from __future__ import annotations

import asyncio
import hashlib
import html
import ipaddress
import logging
import re
import socket
import time
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse

from charlie.research.credibility import score_credibility
from charlie.research.models import SearchResult, SourceDocument

logger = logging.getLogger("charlie.research.fetch")

# curl_cffi does the TLS handshake with a real browser's JA3/HTTP2 fingerprint.
# Anti-bot vendors match Python's signature and refuse before headers are read,
# so a browser User-Agent on httpx does not help.
try:
    from curl_cffi import CurlOpt as _CurlOpt
    from curl_cffi.requests import AsyncSession as _CurlAsyncSession

    CURL_CFFI_AVAILABLE = True
except ImportError:
    _CurlOpt = None
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
_SPACE_RE = re.compile(r"[ \t\r\f\v]+")
_BLOCK_TAG_RE = re.compile(r"</?(?:p|div|section|article|header|footer|tr|li|h[1-6]|table|ul|ol|dl|dt|dd|br)\b[^>]*>", re.IGNORECASE)
_TABLE_ROW_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_CELL_RE = re.compile(r"<t[hd]\b[^>]*>(.*?)</t[hd]>", re.IGNORECASE | re.DOTALL)

# ``follow_redirects=True`` without a hop bound lets a public URL bounce a
# worker through an unbounded chain before any content is seen. Five hops covers
# real http->https and CDN/language redirects and stops redirect loops early.
_MAX_REDIRECTS = 5
# ``socket.getaddrinfo`` has no timeout parameter, so an async caller must
# bound it externally. Without this a slow or hostile resolver stalls the whole
# event loop for every concurrent research source.
_URL_VALIDATION_TIMEOUT_S = 5.0


def canonicalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower() if parsed.hostname else ""
    try:
        address = ipaddress.ip_address(host)
        if isinstance(address, ipaddress.IPv6Address):
            host = f"[{address.compressed}]"
    except ValueError:
        pass
    port = parsed.port
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        port = None
    netloc = host if port is None else f"{host}:{port}"
    return urlunparse((scheme, netloc, parsed.path or "/", "", parsed.query, ""))


def _is_blocked_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not ip.is_global


def _resolve_public_url(url: str) -> tuple[str, tuple[str, ...]]:
    """Validate a public web URL and return every public address to pin."""
    try:
        parsed = urlparse(url.strip())
        scheme = parsed.scheme.lower()
        host = parsed.hostname.lower() if parsed.hostname else ""
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError("Research URL is malformed") from exc

    if scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        raise ValueError("Research URL must use public http or https without credentials")
    if "%" in host or host in {"localhost", "metadata.google.internal", "metadata"} or host.endswith(".local"):
        raise ValueError("Research URL targets a private or local host")
    if port not in {80, 443}:
        raise ValueError("Research URL uses a disallowed port")

    try:
        addresses = (str(ipaddress.ip_address(host)),)
    except ValueError:
        try:
            records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ValueError("Research URL host could not be resolved") from exc
        addresses = tuple(dict.fromkeys(str(ipaddress.ip_address(record[4][0])) for record in records))

    if not addresses or any(_is_blocked_ip(address) for address in addresses):
        raise ValueError("Research URL targets a private or local network")
    return canonicalize_url(url), addresses


def validate_public_url(url: str) -> str:
    """Allow only public HTTP(S) URLs on ports 80/443."""
    return _resolve_public_url(url)[0]


async def _resolve_public_url_async(
    url: str,
    *,
    timeout_s: float = _URL_VALIDATION_TIMEOUT_S,
) -> tuple[str, tuple[str, ...]]:
    try:
        return await asyncio.wait_for(asyncio.to_thread(_resolve_public_url, url), timeout_s)
    except asyncio.TimeoutError as exc:
        raise ValueError("Research URL host could not be resolved in time") from exc


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
    return (await _resolve_public_url_async(url, timeout_s=timeout_s))[0]


def _fallback_text(markup: str) -> str:
    """Extract text preserving spec rows, table cells, and paragraph linebreaks."""
    # First convert table rows into key: value lines if possible
    def replace_tr(match: re.Match[str]) -> str:
        row_content = match.group(1)
        cells = [_TAG_RE.sub("", c).strip() for c in _CELL_RE.findall(row_content)]
        non_empty = [c for c in cells if c]
        if len(non_empty) == 2:
            return f"\n{non_empty[0]}: {non_empty[1]}\n"
        elif non_empty:
            return f"\n{' | '.join(non_empty)}\n"
        return "\n"

    transformed = _TABLE_ROW_RE.sub(replace_tr, markup)
    # Replace block boundaries with newlines
    transformed = _BLOCK_TAG_RE.sub("\n", transformed)
    # Strip remaining tags
    cleaned = _TAG_RE.sub(" ", transformed)
    unescaped = html.unescape(cleaned)
    lines = [
        _SPACE_RE.sub(" ", line).strip()
        for line in unescaped.splitlines()
        if line.strip()
    ]
    return "\n".join(lines)


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
            # Retain line breaks so table rows, lists, and specifications survive extraction
            lines = [
                _SPACE_RE.sub(" ", line).strip()
                for line in extracted.splitlines()
                if line.strip()
            ]
            return "\n".join(lines), "trafilatura"
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
    max_chars: int = _MAX_DOCUMENT_CHARS,
) -> Optional[SourceDocument]:
    text = content.strip()[:max_chars]
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


async def _get_browser_shaped(
    url: str,
    *,
    timeout_s: float,
) -> tuple[int, str, str]:
    """Fetch with a validated, pinned peer and validate every redirect first."""
    if not CURL_CFFI_AVAILABLE or _CurlAsyncSession is None or _CurlOpt is None:
        raise RuntimeError("Safe public-web transport is unavailable")

    deadline = time.monotonic() + timeout_s
    current_url = url
    for hop in range(_MAX_REDIRECTS + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Research source acquisition budget expired")
        current_url, addresses = await _resolve_public_url_async(
            current_url,
            timeout_s=min(_URL_VALIDATION_TIMEOUT_S, remaining),
        )
        parsed = urlparse(current_url)
        host = parsed.hostname or ""
        try:
            ipaddress.ip_address(host)
            resolve_options = {}
        except ValueError:
            host = host.encode("idna").decode("ascii")
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            pinned_addresses = ",".join(
                f"[{address}]" if ":" in address else address for address in addresses
            )
            resolve_options = {
                _CurlOpt.RESOLVE: [f"{host}:{port}:{pinned_addresses}"]
            }

        async with _CurlAsyncSession(
            impersonate="chrome",
            trust_env=False,
            allow_redirects=False,
            curl_options=resolve_options,
        ) as session:
            response = await asyncio.wait_for(
                session.get(
                    current_url,
                    headers=_BROWSER_HEADERS,
                    timeout=max(0.1, remaining),
                    allow_redirects=False,
                ),
                timeout=remaining,
            )
            try:
                peer = getattr(response, "primary_ip", "")
                if isinstance(peer, bytes):
                    peer = peer.decode("ascii", "strict")
                peer = str(ipaddress.ip_address(str(peer)))
            except (AttributeError, TypeError, ValueError, OSError) as exc:
                raise ValueError("Research connection peer could not be verified") from exc
            if peer not in addresses or _is_blocked_ip(peer):
                raise ValueError("Research connection did not use a validated public address")

        status = int(response.status_code)
        location = response.headers.get("location") or response.headers.get("Location")
        if status in {301, 302, 303, 307, 308} and location:
            if hop >= _MAX_REDIRECTS:
                raise ValueError("Research source exceeded the redirect limit")
            current_url = urljoin(current_url, str(location))
            continue
        return status, response.text, current_url

    raise ValueError("Research source exceeded the redirect limit")


async def fetch_document(
    result: SearchResult,
    *,
    timeout_s: float = 12.0,
    status_out: Optional[list] = None,
    max_chars: int = _MAX_DOCUMENT_CHARS,
) -> Optional[SourceDocument]:
    """Fetch and extract one source.

    ``status_out`` receives the HTTP status on an error response, so callers
    can tell a 403 bot-wall from a transport failure.

    Every request is pinned to a resolved public address and redirects are
    validated before the next connection is attempted.
    """
    try:
        status, body, final_url = await _get_browser_shaped(result.url, timeout_s=timeout_s)
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
            max_chars=max_chars,
        )
    except ValueError as exc:
        logger.info("Refusing research URL %s: %s", result.url, exc)
        return None
    except Exception:
        logger.debug("Research fetch failed for %s", result.url, exc_info=True)
        return None
