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

_MIN_CONTENT_CHARS = 160
_MAX_DOCUMENT_CHARS = 14000
_TAG_RE = re.compile(r"<[^>]+>")
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
    if len(text) < _MIN_CONTENT_CHARS:
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


async def fetch_document(
    result: SearchResult,
    *,
    timeout_s: float = 12.0,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[SourceDocument]:
    current_url = validate_public_url(result.url)
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=timeout_s, follow_redirects=True)
    try:
        response = await http_client.get(
            current_url,
            headers={"User-Agent": "CharlieResearch/1.0 (+public-web-research)"},
        )
        response.raise_for_status()
        final_url = validate_public_url(str(response.url))
        text, method = extract_text(response.text)
        redirected = SearchResult(
            title=result.title,
            url=final_url,
            snippet=result.snippet,
            provider=result.provider,
            rank=result.rank,
            published_at=result.published_at,
            domain=urlparse(final_url).netloc.lower(),
        )
        return document_from_content(redirected, text, extraction_method=method)
    except Exception:
        logger.debug("Research fetch failed for %s", result.url, exc_info=True)
        return None
    finally:
        if owns_client:
            await http_client.aclose()
