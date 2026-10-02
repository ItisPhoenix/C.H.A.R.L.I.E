"""Regression tests for redirect-hop SSRF and the loopback web gateway.

Evidence class: TEST/MOCK. Every network call is faked. These tests prove
policy and refusal behavior, not real remote reachability or real browser
identity.
"""

from __future__ import annotations

import asyncio
import contextlib
import http.client
import io
import socket
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from charlie.research import fetch as fetch_module
from charlie.research.fetch import fetch_document
from charlie.research.models import SearchResult
from charlie.web_gateway import _MAX_COMMAND_BYTES, RuntimeWebGateway

# --------------------------------------------------------------------------
# fetch.py helpers
# --------------------------------------------------------------------------


class _StubSocket:
    """Stand in for the socket module so DNS is deterministic and offline."""

    SOCK_STREAM = socket.SOCK_STREAM
    gaierror = socket.gaierror

    def __init__(self, mapping: dict[str, list[str]], *, delay_s: float = 0.0) -> None:
        self.mapping = mapping
        self.delay_s = delay_s

    def getaddrinfo(self, host, port, type=0):  # noqa: A002 - mirrors socket API
        if self.delay_s:
            time.sleep(self.delay_s)
        try:
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port or 0))
                for address in self.mapping[host]
            ]
        except KeyError as exc:
            raise socket.gaierror(f"stub has no mapping for {host!r}") from exc


class _RedirectingClient:
    """httpx.AsyncClient stand-in that reports an already-followed redirect."""

    def __init__(self, final_url: str, text: str, status_code: int = 200) -> None:
        self.final_url = final_url
        self.text = text
        self.status_code = status_code
        self.requested: list[str] = []

    async def get(self, url, **_kwargs):
        self.requested.append(url)
        return SimpleNamespace(status_code=self.status_code, text=self.text, url=self.final_url)


def _stub_dns(monkeypatch, mapping, *, delay_s: float = 0.0) -> _StubSocket:
    stub = _StubSocket(mapping, delay_s=delay_s)
    monkeypatch.setattr(fetch_module, "socket", stub)
    return stub


def _page(title: str = "Public source") -> str:
    filler = "real content " * 40
    return f"<html><head><title>{title}</title></head><body>{filler}</body></html>"


# --------------------------------------------------------------------------
# web_gateway.py helpers
# --------------------------------------------------------------------------


def _gateway(tmp_path, handle=None):
    loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
    loop_thread.start()
    calls: list[dict] = []

    async def default_handle(command):
        calls.append(command)
        return {"accepted": True}

    gateway = RuntimeWebGateway(
        loop=loop,
        command_handler=handle or default_handle,
        snapshot_getter=lambda: {
            "version": 1,
            "revision": 0,
            "title": "Charlie is ready",
            "summary": "Ready",
            "details": [],
        },
        static_dir=tmp_path,
        port=0,
    )
    return gateway, loop, loop_thread, calls


def _teardown(gateway, loop, loop_thread) -> None:
    gateway.close()
    loop.call_soon_threadsafe(loop.stop)
    loop_thread.join(timeout=2)
    loop.close()


def _origin(gateway) -> str:
    return f"http://127.0.0.1:{gateway.port}"


@contextlib.contextmanager
def _authenticated_client(gateway):
    """Bootstrap a session the way the documented operator flow does."""
    with httpx.Client(base_url=_origin(gateway)) as client:
        bootstrap = client.get(f"/?token={gateway.token}", follow_redirects=False)
        assert bootstrap.status_code == 303, bootstrap.text
        assert client.cookies.get("charlie_web_token") == gateway.token
        yield client


# --------------------------------------------------------------------------
# FINDING 1 - SSRF by redirect
# --------------------------------------------------------------------------


def test_fetch_document_refuses_redirect_to_loopback_target(monkeypatch):
    """A public URL that redirects to a loopback hop must be refused."""
    _stub_dns(
        monkeypatch,
        {
            "public.example.com": ["93.184.216.34"],
            "127.0.0.1": ["127.0.0.1"],
        },
    )
    client = _RedirectingClient("http://127.0.0.1:8080/admin", _page("Internal admin"))

    result = SearchResult(title="Public", url="https://public.example.com/start", provider="search")
    document = asyncio.run(fetch_document(result, client=client))

    assert document is None, "redirect to a loopback target produced a source document"
    assert client.requested == ["https://public.example.com/start"]


def test_fetch_document_refuses_redirect_to_private_network_target(monkeypatch):
    _stub_dns(
        monkeypatch,
        {"public.example.com": ["93.184.216.34"], "internal.example.com": ["10.1.2.3"]},
    )
    client = _RedirectingClient("http://internal.example.com/secrets", _page("Secrets"))

    result = SearchResult(title="Public", url="https://public.example.com/start", provider="search")
    document = asyncio.run(fetch_document(result, client=client))

    assert document is None


def test_fetch_document_refuses_redirect_to_non_http_scheme(monkeypatch):
    _stub_dns(monkeypatch, {"public.example.com": ["93.184.216.34"]})
    client = _RedirectingClient("file:///etc/passwd", _page("Local file"))

    result = SearchResult(title="Public", url="https://public.example.com/start", provider="search")
    document = asyncio.run(fetch_document(result, client=client))

    assert document is None


def test_fetch_document_allows_redirect_that_stays_public(monkeypatch):
    """Guard against over-refusal: a public -> public redirect still yields a document."""
    _stub_dns(
        monkeypatch,
        {"public.example.com": ["93.184.216.34"], "mirror.example.org": ["93.184.216.35"]},
    )
    client = _RedirectingClient("https://mirror.example.org/article", _page("Mirrored"))

    result = SearchResult(title="Public", url="https://public.example.com/start", provider="search")
    document = asyncio.run(fetch_document(result, client=client))

    assert document is not None
    assert document.url == "https://mirror.example.org/article"


def test_fetch_bounds_redirect_hops_on_the_http_client(monkeypatch):
    """follow_redirects must be paired with an explicit max_redirects bound."""
    monkeypatch.setattr(fetch_module, "CURL_CFFI_AVAILABLE", False)
    seen: dict[str, object] = {}
    real_client = httpx.AsyncClient

    class RecordingClient(real_client):
        def __init__(self, **kwargs):
            seen.update(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr(fetch_module.httpx, "AsyncClient", RecordingClient)

    async def _drive():
        await fetch_module._get_browser_shaped("https://example.invalid/", timeout_s=5.0)

    # .invalid never resolves, so this fails at connect time without network I/O.
    with pytest.raises(Exception):
        asyncio.run(_drive())

    assert seen.get("follow_redirects") is True
    assert seen.get("max_redirects") == fetch_module._MAX_REDIRECTS
    assert 1 <= fetch_module._MAX_REDIRECTS <= 10


def test_async_url_validation_does_not_block_the_event_loop(monkeypatch):
    """A slow resolver must not stall the loop; a heartbeat keeps ticking."""
    _stub_dns(monkeypatch, {"slow.example.com": ["93.184.216.34"]}, delay_s=0.45)

    async def _drive():
        ticks = 0

        async def heartbeat():
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.01)

        beat = asyncio.create_task(heartbeat())
        try:
            resolved = await fetch_module.validate_public_url_async("https://slow.example.com/x")
        finally:
            beat.cancel()
        return resolved, ticks

    resolved, ticks = asyncio.run(_drive())

    assert resolved == "https://slow.example.com/x"
    assert ticks > 15, f"event loop was starved during DNS resolution (only {ticks} ticks)"


def test_async_url_validation_times_out_on_a_stuck_resolver(monkeypatch):
    _stub_dns(monkeypatch, {"stuck.example.com": ["93.184.216.34"]}, delay_s=5.0)

    async def _drive():
        started = time.monotonic()
        with pytest.raises(ValueError):
            await fetch_module.validate_public_url_async("https://stuck.example.com/x", timeout_s=0.2)
        return time.monotonic() - started

    elapsed = asyncio.run(_drive())

    assert elapsed < 2.0, f"bounded validation took {elapsed:.2f}s"


def test_async_url_validation_reports_blocked_host_as_value_error(monkeypatch):
    _stub_dns(monkeypatch, {"127.0.0.1": ["127.0.0.1"]})

    async def _drive():
        with pytest.raises(ValueError):
            await fetch_module.validate_public_url_async("http://127.0.0.1/x")

    asyncio.run(_drive())


# --------------------------------------------------------------------------
# FINDING 2 - web gateway N-04 (no unauthenticated minting)
# --------------------------------------------------------------------------


def test_scene_refuses_unauthenticated_request_and_mints_no_cookie(tmp_path):
    gateway, loop, loop_thread, _calls = _gateway(tmp_path)
    try:
        gateway.start()
        with httpx.Client(base_url=_origin(gateway)) as client:
            response = client.get("/api/scene")
            assert response.status_code == 401, (
                "/api/scene minted a session for an unauthenticated caller"
            )
            assert "charlie_web_token" not in response.headers.get("set-cookie", "")
            assert not client.cookies, "unauthenticated request installed a cookie"
    finally:
        _teardown(gateway, loop, loop_thread)


def test_scene_refuses_forged_token(tmp_path):
    gateway, loop, loop_thread, _calls = _gateway(tmp_path)
    try:
        gateway.start()
        with httpx.Client(base_url=_origin(gateway)) as client:
            response = client.get(
                "/api/scene", headers={"Cookie": "charlie_web_token=not-the-real-token"}
            )
            assert response.status_code == 401
    finally:
        _teardown(gateway, loop, loop_thread)


def test_static_document_does_not_mint_a_session(tmp_path):
    """Serving the app shell must not hand a session to any local page load."""
    (tmp_path / "index.html").write_text("<html>charlie</html>", encoding="utf-8")
    gateway, loop, loop_thread, _calls = _gateway(tmp_path)
    try:
        gateway.start()
        with httpx.Client(base_url=_origin(gateway)) as client:
            document = client.get("/")
            assert document.status_code == 200
            assert "charlie_web_token" not in document.headers.get("set-cookie", "")
            assert not client.cookies
    finally:
        _teardown(gateway, loop, loop_thread)


def test_token_bootstrap_sets_session_cookie(tmp_path):
    gateway, loop, loop_thread, _calls = _gateway(tmp_path)
    try:
        gateway.start()
        with httpx.Client(base_url=_origin(gateway)) as client:
            bootstrap = client.get(f"/?token={gateway.token}", follow_redirects=False)
            assert bootstrap.status_code == 303
            assert "charlie_web_token" in bootstrap.headers.get("set-cookie", "")
            location = bootstrap.headers["location"]
            assert gateway.token not in location, "the token must not survive in the address bar"
            assert client.cookies.get("charlie_web_token") == gateway.token
    finally:
        _teardown(gateway, loop, loop_thread)


def test_wrong_token_query_is_refused(tmp_path):
    gateway, loop, loop_thread, _calls = _gateway(tmp_path)
    try:
        gateway.start()
        with httpx.Client(base_url=_origin(gateway)) as client:
            response = client.get("/?token=wrong", follow_redirects=False)
            assert response.status_code in (401, 303)
            assert "charlie_web_token" not in response.headers.get("set-cookie", "")
            assert not client.cookies
    finally:
        _teardown(gateway, loop, loop_thread)


def test_authenticated_scene_still_serves_the_snapshot(tmp_path):
    gateway, loop, loop_thread, _calls = _gateway(tmp_path)
    try:
        gateway.start()
        with _authenticated_client(gateway) as client:
            scene = client.get("/api/scene")
            assert scene.status_code == 200
            assert scene.json()["title"] == "Charlie is ready"
    finally:
        _teardown(gateway, loop, loop_thread)


# --------------------------------------------------------------------------
# FINDING 2 - web gateway N-05 (CSRF must not treat a missing Origin as a pass)
# --------------------------------------------------------------------------


def test_command_post_without_origin_header_is_refused(tmp_path):
    seen: list[dict] = []

    async def handle(command):
        seen.append(command)
        return {"accepted": True}

    gateway, loop, loop_thread, _calls = _gateway(tmp_path, handle=handle)
    try:
        gateway.start()
        with _authenticated_client(gateway) as client:
            response = client.post("/api/commands", json={"type": "submit_text", "text": "hi"})
            assert response.status_code == 403, "a POST with no Origin header bypassed CSRF"
            assert seen == []
    finally:
        _teardown(gateway, loop, loop_thread)


def test_command_post_with_matching_origin_succeeds(tmp_path):
    seen: list[dict] = []

    async def handle(command):
        seen.append(command)
        return {"accepted": True}

    gateway, loop, loop_thread, _calls = _gateway(tmp_path, handle=handle)
    try:
        gateway.start()
        with _authenticated_client(gateway) as client:
            response = client.post(
                "/api/commands",
                json={"type": "submit_text", "text": "hi"},
                headers={"Origin": _origin(gateway)},
            )
            assert response.status_code == 200
            assert response.json() == {"accepted": True}
            assert seen == [{"type": "submit_text", "text": "hi"}]
    finally:
        _teardown(gateway, loop, loop_thread)


def test_command_post_with_foreign_origin_is_refused(tmp_path):
    seen: list[dict] = []

    async def handle(command):
        seen.append(command)
        return {"accepted": True}

    gateway, loop, loop_thread, _calls = _gateway(tmp_path, handle=handle)
    try:
        gateway.start()
        with _authenticated_client(gateway) as client:
            response = client.post(
                "/api/commands",
                json={"type": "submit_text", "text": "hi"},
                headers={"Origin": "http://127.0.0.1:8000"},
            )
            assert response.status_code == 403
            assert seen == []
    finally:
        _teardown(gateway, loop, loop_thread)


def test_origin_allowlist_is_derived_from_the_bound_port(tmp_path):
    """The gateway bound an ephemeral port; localhost:8001 must not be trusted."""
    gateway, loop, loop_thread, _calls = _gateway(tmp_path)
    try:
        gateway.start()
        assert gateway.port != 8001
        with _authenticated_client(gateway) as client:
            bound = client.post(
                "/api/commands",
                json={"type": "submit_text"},
                headers={"Origin": f"http://localhost:{gateway.port}"},
            )
            assert bound.status_code == 200
            stale = client.post(
                "/api/commands",
                json={"type": "submit_text"},
                headers={"Origin": "http://localhost:8001"},
            )
            assert stale.status_code == 403
    finally:
        _teardown(gateway, loop, loop_thread)


def test_unauthenticated_post_is_refused(tmp_path):
    seen: list[dict] = []

    async def handle(command):
        seen.append(command)
        return {"accepted": True}

    gateway, loop, loop_thread, _calls = _gateway(tmp_path, handle=handle)
    try:
        gateway.start()
        with httpx.Client(base_url=_origin(gateway)) as client:
            response = client.post(
                "/api/commands",
                json={"type": "submit_text"},
                headers={"Origin": _origin(gateway)},
            )
            assert response.status_code == 401
            assert seen == []
    finally:
        _teardown(gateway, loop, loop_thread)


# --------------------------------------------------------------------------
# FINDING 2 - web gateway N-06 (no raw exception text)
# --------------------------------------------------------------------------


def test_internal_exception_text_is_not_leaked_to_the_caller(tmp_path):
    secret = "sqlite3.OperationalError: no such table: sessions_db at C:\\charlie\\store.db"

    async def handle(_command):
        raise RuntimeError(secret)

    gateway, loop, loop_thread, _calls = _gateway(tmp_path, handle=handle)
    try:
        gateway.start()
        with _authenticated_client(gateway) as client:
            response = client.post(
                "/api/commands",
                json={"type": "submit_text"},
                headers={"Origin": _origin(gateway)},
            )
            assert response.status_code >= 500, response.text
            body = response.text
            assert secret not in body
            assert "sqlite3" not in body
            assert "sessions_db" not in body
            assert "store.db" not in body
            payload = response.json()
            assert payload["error_id"], "error responses must carry a correlation id"
            assert "internal error" in payload["error"].lower()
    finally:
        _teardown(gateway, loop, loop_thread)


def test_malformed_command_body_reports_client_error(tmp_path):
    gateway, loop, loop_thread, _calls = _gateway(tmp_path)
    try:
        gateway.start()
        with _authenticated_client(gateway) as client:
            response = client.post(
                "/api/commands",
                content=b"[1,2,3]",
                headers={"Origin": _origin(gateway)},
            )
            assert response.status_code == 400
            assert "error_id" in response.json()
    finally:
        _teardown(gateway, loop, loop_thread)


# --------------------------------------------------------------------------
# FINDING 2 - bounded Content-Length
# --------------------------------------------------------------------------


def _raw_post(gateway: RuntimeWebGateway, content_length: str) -> int:
    conn = http.client.HTTPConnection("127.0.0.1", gateway.port, timeout=5)
    try:
        conn.putrequest("POST", "/api/commands")
        conn.putheader("Host", f"127.0.0.1:{gateway.port}")
        conn.putheader("Cookie", f"charlie_web_token={gateway.token}")
        conn.putheader("Origin", _origin(gateway))
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", content_length)
        conn.endheaders()
        response = conn.getresponse()
        response.read()
        return response.status
    finally:
        conn.close()


@pytest.mark.parametrize(
    "content_length,expected",
    [("-5", 400), ("99999999", 413), ("not-a-number", 400), ("99999999999999999999", 413)],
)
def test_content_length_is_bounds_checked(tmp_path, content_length, expected):
    seen: list[dict] = []

    async def handle(command):
        seen.append(command)
        return {"accepted": True}

    gateway, loop, loop_thread, _calls = _gateway(tmp_path, handle=handle)
    try:
        gateway.start()
        status = _raw_post(gateway, content_length)
        assert status == expected, f"Content-Length {content_length!r} returned {status}"
        assert seen == [], "an out-of-bounds body reached the command handler"
    finally:
        _teardown(gateway, loop, loop_thread)


def test_bounded_but_oversized_body_is_rejected(tmp_path):
    seen: list[dict] = []

    async def handle(command):
        seen.append(command)
        return {"accepted": True}

    gateway, loop, loop_thread, _calls = _gateway(tmp_path, handle=handle)
    try:
        gateway.start()
        status = _raw_post(gateway, str(70 * 1024))
        assert status == 413
        assert seen == []
    finally:
        _teardown(gateway, loop, loop_thread)


# --------------------------------------------------------------------------
# FINDING 2 - SSE overflow drops the client
# --------------------------------------------------------------------------


def test_sse_client_whose_queue_is_full_is_dropped_not_starved(tmp_path):
    gateway, loop, loop_thread, _calls = _gateway(tmp_path)
    try:
        gateway.start()
        stalled = gateway._attach_client(maxsize=1)
        healthy = gateway._attach_client(maxsize=8)
        stalled.queue.put_nowait(b"data: backlog\n\n")
        assert gateway._client_count() == 2

        gateway.publish({"type": "thinking", "payload": {"message": "working"}})

        assert gateway._client_count() == 1, "a stalled SSE client stayed registered"
        assert stalled.overflowed is True
        assert healthy.overflowed is False
        assert healthy.queue.qsize() == 1
    finally:
        _teardown(gateway, loop, loop_thread)


def test_stream_loop_returns_immediately_for_an_overflowed_client(tmp_path):
    """A dropped client must not keep a gateway worker thread blocked forever."""

    class _FakeWriter:
        def __init__(self) -> None:
            self.buffer = io.BytesIO()

        def write(self, data):
            self.buffer.write(data)

        def flush(self):
            pass

    class _FakeHandler:
        def __init__(self) -> None:
            self.wfile = _FakeWriter()
            self.headers_sent = 0

        def send_response(self, _status):
            self.headers_sent += 1

        def send_header(self, *_args):
            pass

        def end_headers(self):
            pass

    gateway, loop, loop_thread, _calls = _gateway(tmp_path)
    try:
        gateway.start()
        stalled = gateway._attach_client(maxsize=1)
        stalled.queue.put_nowait(b"data: backlog\n\n")
        gateway.publish({"type": "thinking", "payload": {"message": "working"}})
        assert stalled.overflowed is True

        handler = _FakeHandler()
        finished = threading.Event()

        def _serve():
            gateway._stream(handler, stalled)
            finished.set()

        worker = threading.Thread(target=_serve, daemon=True)
        worker.start()
        assert finished.wait(timeout=2), "the SSE loop kept running for a dropped client"
        worker.join(timeout=2)
        assert gateway._client_count() == 0
    finally:
        _teardown(gateway, loop, loop_thread)


# --------------------------------------------------------------------------
# FINDING N-06b - refusing a POST must not reset the client's connection
# --------------------------------------------------------------------------
# Refusing before the request body is read races the client's own write: the
# server closes with unread data in the receive buffer, the OS answers with RST,
# and the client observes a connection error instead of the status code. Larger
# bodies fail more reliably, so these use sizes that reproduce it every time.


@pytest.mark.parametrize("body_bytes", [64 * 1024, 512 * 1024])
def test_unauthenticated_post_with_large_body_gets_a_clean_status(body_bytes, tmp_path):
    """A refused POST must yield the status code, never a transport error."""
    loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
    loop_thread.start()

    async def handle(command):
        raise AssertionError("an unauthenticated command must never be dispatched")

    gateway = RuntimeWebGateway(
        loop=loop,
        command_handler=handle,
        snapshot_getter=lambda: {
            "version": 1, "revision": 0, "title": "x", "summary": "y", "details": [],
        },
        static_dir=tmp_path,
        port=0,
    )
    gateway.start()
    origin = _origin(gateway)
    try:
        with httpx.Client(base_url=origin, timeout=30.0) as client:
            response = client.post(
                "/api/commands",
                json={"type": "submit_text", "text": "a" * body_bytes},
                headers={"Origin": origin},
            )
        assert response.status_code in (401, 413), response.text
    finally:
        _teardown(gateway, loop, loop_thread)


def test_oversized_unauthenticated_body_is_rejected_without_a_reset(tmp_path):
    """An oversized body is refused on its length, so no unread bytes remain."""
    loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
    loop_thread.start()

    async def handle(command):
        raise AssertionError("an unauthenticated command must never be dispatched")

    gateway = RuntimeWebGateway(
        loop=loop,
        command_handler=handle,
        snapshot_getter=lambda: {
            "version": 1, "revision": 0, "title": "x", "summary": "y", "details": [],
        },
        static_dir=tmp_path,
        port=0,
    )
    gateway.start()
    origin = _origin(gateway)
    try:
        with httpx.Client(base_url=origin, timeout=30.0) as client:
            response = client.post(
                "/api/commands",
                content=b"a" * (_MAX_COMMAND_BYTES + 1024),
                headers={
                    "Origin": origin,
                    "Content-Type": "application/json",
                    "Content-Length": str(_MAX_COMMAND_BYTES + 1024),
                },
            )
        assert response.status_code == 413, response.text
    finally:
        _teardown(gateway, loop, loop_thread)
