"""Browser rendering behind a public-address-pinning egress proxy."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import sys
import time
from typing import Optional
from urllib.parse import urlparse, urlsplit, urlunsplit

from charlie.research.fetch import (
    _is_blocked_ip,
    _resolve_public_url_async,
    document_from_content,
    extract_html_title,
    extract_text,
    validate_public_url_async,
)
from charlie.research.models import SearchResult, SourceDocument

logger = logging.getLogger("charlie.research.crawler")

_MAX_PROXY_HEADER_BYTES = 32 * 1024
_MAX_PROXY_RESPONSE_BYTES = 24 * 1024 * 1024
_FORWARDED_HEADERS = {"accept", "accept-language", "origin", "range", "user-agent"}
_BLOCKED_BROWSER_APIS = """
for (const name of ['RTCPeerConnection', 'webkitRTCPeerConnection']) {
  try { Object.defineProperty(globalThis, name, { value: undefined, configurable: false }); }
  catch (_) {}
}
"""


def _result_at_url(result: SearchResult, url: str) -> SearchResult:
    return SearchResult(
        title=result.title,
        url=url,
        snippet=result.snippet,
        provider=result.provider,
        rank=result.rank,
        published_at=result.published_at,
        domain=urlparse(url).netloc.lower(),
    )


async def _open_pinned_target(
    url: str,
    *,
    timeout_s: float = 5.0,
) -> tuple[str, asyncio.StreamReader, asyncio.StreamWriter]:
    """Resolve and validate a public destination, then connect to that exact IP."""
    canonical, addresses = await _resolve_public_url_async(url, timeout_s=timeout_s)
    parsed = urlparse(canonical)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    failures: list[OSError] = []
    for address in addresses:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(address, port), timeout=timeout_s
            )
        except (OSError, asyncio.TimeoutError) as exc:
            failures.append(exc)
            continue
        peer = writer.get_extra_info("peername")
        try:
            peer_ip = str(ipaddress.ip_address(str(peer[0])))
        except (IndexError, TypeError, ValueError) as exc:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            raise ValueError("Research connection peer could not be verified") from exc
        if peer_ip not in addresses or _is_blocked_ip(peer_ip):
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            raise ValueError("Research connection did not use a validated public address")
        return canonical, reader, writer
    raise ConnectionError("No validated public address accepted the research connection") from (
        failures[-1] if failures else None
    )


class _BrowserEgressProxy:
    """Allow a disposable browser to reach only validated public HTTP(S) peers."""

    def __init__(self, *, timeout_s: float) -> None:
        self.timeout_s = max(1.0, timeout_s)
        self.server: Optional[asyncio.AbstractServer] = None

    @property
    def url(self) -> str:
        if self.server is None or not self.server.sockets:
            raise RuntimeError("Research egress proxy is not running")
        port = self.server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    async def _handle(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        upstream_writer: Optional[asyncio.StreamWriter] = None
        try:
            head = await asyncio.wait_for(
                client_reader.readuntil(b"\r\n\r\n"), timeout=min(5.0, self.timeout_s)
            )
            if len(head) > _MAX_PROXY_HEADER_BYTES:
                await self._error(client_writer, 431, "Request Header Fields Too Large")
                return
            lines = head.decode("iso-8859-1").split("\r\n")
            parts = lines[0].split(" ", 2)
            if len(parts) != 3:
                await self._error(client_writer, 400, "Bad Request")
                return
            method, target, _version = parts
            headers = {}
            for line in lines[1:]:
                if ":" in line:
                    name, value = line.split(":", 1)
                    headers[name.strip().casefold()] = value.strip()

            if method.upper() == "CONNECT":
                authority = urlsplit(f"//{target}")
                host, port = authority.hostname, authority.port
                if not host or port != 443 or authority.username or authority.password:
                    await self._error(client_writer, 403, "Egress Denied")
                    return
                rendered_host = f"[{host}]" if ":" in host else host
                _canonical, upstream_reader, upstream_writer = await _open_pinned_target(
                    f"https://{rendered_host}/", timeout_s=min(5.0, self.timeout_s)
                )
                client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await client_writer.drain()
                await self._tunnel(
                    client_reader,
                    client_writer,
                    upstream_reader,
                    upstream_writer,
                )
                return

            parsed = urlsplit(target)
            if (
                method.upper() not in {"GET", "HEAD"}
                or parsed.scheme.casefold() != "http"
                or parsed.port not in (None, 80)
                or headers.get("transfer-encoding")
                or headers.get("content-length", "0") not in {"", "0"}
                or "upgrade" in headers
                or "upgrade" in headers.get("connection", "").casefold()
            ):
                await self._error(client_writer, 403, "Egress Denied")
                return

            canonical, upstream_reader, upstream_writer = await _open_pinned_target(
                target, timeout_s=min(5.0, self.timeout_s)
            )
            upstream_url = urlsplit(canonical)
            if upstream_url.scheme != "http" or upstream_url.port not in (None, 80):
                await self._error(client_writer, 403, "Egress Denied")
                return
            path = urlunsplit(("", "", upstream_url.path or "/", upstream_url.query, "", ""))
            forwarded = [f"{method.upper()} {path} HTTP/1.1", f"Host: {upstream_url.netloc}"]
            forwarded.extend(
                f"{name}: {value}"
                for name, value in headers.items()
                if name in _FORWARDED_HEADERS
            )
            forwarded.append("Connection: close")
            upstream_writer.write(("\r\n".join(forwarded) + "\r\n\r\n").encode("iso-8859-1"))
            await upstream_writer.drain()
            await self._send_response(upstream_reader, client_writer)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ValueError, OSError, ConnectionError):
            with contextlib.suppress(Exception):
                await self._error(client_writer, 502, "Research Egress Unavailable")
        except Exception:
            logger.debug("Research egress proxy request failed", exc_info=True)
            with contextlib.suppress(Exception):
                await self._error(client_writer, 502, "Research Egress Unavailable")
        finally:
            if upstream_writer is not None:
                upstream_writer.close()
                with contextlib.suppress(Exception):
                    await upstream_writer.wait_closed()
            client_writer.close()
            with contextlib.suppress(Exception):
                await client_writer.wait_closed()

    @staticmethod
    async def _error(writer: asyncio.StreamWriter, status: int, reason: str) -> None:
        writer.write(
            f"HTTP/1.1 {status} {reason}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n".encode(
                "ascii"
            )
        )
        await writer.drain()

    async def _send_response(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        total = 0
        deadline = time.monotonic() + self.timeout_s
        while total < _MAX_PROXY_RESPONSE_BYTES:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            chunk = await asyncio.wait_for(reader.read(65536), timeout=remaining)
            if not chunk:
                return
            total += len(chunk)
            writer.write(chunk)
            await writer.drain()

    async def _tunnel(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        upstream_reader: asyncio.StreamReader,
        upstream_writer: asyncio.StreamWriter,
    ) -> None:
        transferred = [0]

        async def pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            while transferred[0] < _MAX_PROXY_RESPONSE_BYTES:
                chunk = await reader.read(65536)
                if not chunk:
                    return
                transferred[0] += len(chunk)
                writer.write(chunk)
                await writer.drain()

        tasks = {
            asyncio.create_task(pump(client_reader, upstream_writer)),
            asyncio.create_task(pump(upstream_reader, client_writer)),
        }
        _done, pending = await asyncio.wait(
            tasks,
            timeout=self.timeout_s,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _render_page_sync(url: str, proxy_url: str, timeout_s: float) -> tuple[str, str, Optional[str]]:
    import asyncio

    timeout_ms = max(1000, int(timeout_s * 1000))
    launch_args = [
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-quic",
        "--disable-sync",
        "--disable-webrtc",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
    ]

    def render_scrapling() -> tuple[str, str, Optional[str]]:
        from scrapling.fetchers import DynamicFetcher

        def prepare_page(page) -> None:
            page.add_init_script(_BLOCKED_BROWSER_APIS)
            if hasattr(page, "route_web_socket"):
                page.route_web_socket("**/*", lambda websocket: websocket.close())

        logger.info("Research dynamic fetch via Scrapling for host=%s", urlparse(url).hostname)
        response = DynamicFetcher.fetch(
            url,
            headless=True,
            disable_resources=True,
            timeout=timeout_ms,
            wait=250,
            network_idle=False,
            proxy=proxy_url,
            extra_flags=launch_args,
            page_setup=prepare_page,
            google_search=False,
        )
        markup = response.body.decode("utf-8", "replace")
        return (
            str(response.url),
            markup if int(response.status) < 400 else "",
            extract_html_title(markup),
        )

    def render_playwright() -> tuple[str, str, Optional[str]]:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                timeout=timeout_ms,
                proxy={"server": proxy_url, "bypass": "<-loopback>"},
                args=launch_args,
            )
            context = None
            try:
                context = browser.new_context(service_workers="block", accept_downloads=False)
                context.add_init_script(_BLOCKED_BROWSER_APIS)
                page = context.new_page()
                if hasattr(page, "route_web_socket"):
                    page.route_web_socket("**/*", lambda websocket: websocket.close())
                response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                if response is not None and int(response.status) >= 400:
                    return str(page.url), "", page.title()
                with contextlib.suppress(Exception):
                    page.wait_for_load_state("networkidle", timeout=min(1500, timeout_ms))
                return str(page.url), page.content(), page.title()
            finally:
                if context is not None:
                    context.close()
                browser.close()

    def render() -> tuple[str, str, Optional[str]]:
        try:
            return render_scrapling()
        except Exception:
            logger.debug("Scrapling dynamic fetch failed; using the existing isolated browser path", exc_info=True)
            return render_playwright()

    if sys.platform != "win32":
        return render()

    from charlie.browser.controller import _policy_swap

    with _policy_swap():
        prior_policy = asyncio.get_event_loop_policy()
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        try:
            return render()
        finally:
            asyncio.set_event_loop_policy(prior_policy)


async def crawl_document(
    result: SearchResult,
    *,
    timeout_s: float = 30.0,
    max_depth: int = 1,
    max_pages: int = 4,
) -> Optional[SourceDocument]:
    """Render one public page in a fresh browser whose egress is pinned by proxy."""
    del max_depth, max_pages
    try:
        safe_url = await validate_public_url_async(result.url)
    except ValueError as exc:
        logger.info("Skipping blocked research browser URL host=%s: %s", urlparse(result.url).hostname, exc)
        return None

    proxy = _BrowserEgressProxy(timeout_s=timeout_s)
    try:
        await proxy.start()
        final_url, markup, title = await asyncio.wait_for(
            asyncio.to_thread(_render_page_sync, safe_url, proxy.url, timeout_s),
            timeout=max(5.0, timeout_s + 3.0),
        )
        if not markup:
            return None
        final_url = await validate_public_url_async(final_url)
        content, extraction_method = extract_text(markup)
        return document_from_content(
            _result_at_url(result, final_url),
            content,
            extraction_method=f"browser:egress-isolated:{extraction_method}",
            title=title or extract_html_title(markup) or result.title,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.info(
            "Isolated research browser failed for host=%s",
            urlparse(result.url).hostname,
            exc_info=True,
        )
        return None
    finally:
        await proxy.close()
