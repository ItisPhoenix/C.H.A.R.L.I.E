"""Hardening regression tests for ``charlie/web_gateway.py``.

Evidence class: TEST/MOCK over REAL sockets. Every request here is a
hand-built byte stream written to a real loopback listener, so header and
framing behaviour is asserted on the wire rather than through an HTTP client
that would normalise the very things under test.

Every read is bounded by a wall-clock deadline and no test loops without one,
so a regression produces a failure instead of a wedged suite.

Each test names the finding it pins:

* F1  static path containment (prefix sibling directory)
* F2  bounded reads, handler timeout, capped SSE registry
* F3  static responses carry ``Content-Type``/``nosniff``/CSP
* F4  three reachable uncaught exceptions
* F5  server-side session expiry and a distinct session secret
* F6  ambiguous request framing (duplicate ``Content-Length``, ``Transfer-Encoding``)
* F7  courtesy drain restores a blocking socket
* F8  ``Server`` banner must not disclose the Python version
* F9  bootstrap token is retrievable by the operator
* F10 every correlation id reaches the log
* F11 command payloads are never logged
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import socket
import stat
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from charlie import web_gateway as web_gateway_module
from charlie.web_gateway import _SESSION_MAX_AGE_S, RuntimeWebGateway

# Bounded generously above the server's own body deadline so a fixed gateway
# answers well inside the window and a broken one fails on the deadline.
_CLIENT_READ_TIMEOUT_S = 12.0


# --------------------------------------------------------------------------
# gateway helpers
# --------------------------------------------------------------------------


def _gateway(tmp_path, handle=None, **kwargs):
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
        **kwargs,
    )
    return gateway, loop, loop_thread, calls


def _teardown(gateway, loop, loop_thread) -> None:
    gateway.close()
    loop.call_soon_threadsafe(loop.stop)
    loop_thread.join(timeout=2)
    loop.close()


def _origin(gateway) -> str:
    return f"http://127.0.0.1:{gateway.port}"


# --------------------------------------------------------------------------
# raw wire helpers
# --------------------------------------------------------------------------


def _request_bytes(
    method: str,
    path: str,
    headers: list[tuple[str, str]],
    body: bytes = b"",
    *,
    content_length: int | None = None,
    omit_content_length: bool = False,
) -> bytes:
    """Build one request by hand so nothing normalises the framing."""
    lines = [f"{method} {path} HTTP/1.1"]
    lines.extend(f"{name}: {value}" for name, value in headers)
    if not omit_content_length:
        declared = len(body) if content_length is None else content_length
        lines.append(f"Content-Length: {declared}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + body


def _raw_exchange(
    port: int,
    request: bytes,
    *,
    read_timeout: float = _CLIENT_READ_TIMEOUT_S,
) -> bytes:
    """Write one request and read the reply under a hard deadline.

    Returns whatever arrived. An empty result means the server never answered,
    which is exactly the failure several of these tests are looking for.
    """
    deadline = time.monotonic() + read_timeout
    chunks: list[bytes] = []
    with socket.create_connection(("127.0.0.1", port), timeout=read_timeout) as sock:
        sock.settimeout(read_timeout)
        sock.sendall(request)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sock.settimeout(max(0.05, remaining))
            try:
                chunk = sock.recv(65536)
            except (TimeoutError, OSError):
                break
            if not chunk:
                break
            chunks.append(chunk)
    return b"".join(chunks)


def _recv_until(sock: socket.socket, predicate, deadline: float) -> bytes:
    """Read from *sock* until *predicate* is satisfied or the deadline passes."""
    received = b""
    while time.monotonic() < deadline:
        sock.settimeout(max(0.05, deadline - time.monotonic()))
        try:
            chunk = sock.recv(65536)
        except (TimeoutError, OSError):
            break
        if not chunk:
            break
        received += chunk
        if predicate(received):
            break
    return received


def _response_complete(raw: bytes) -> bool:
    head, _, body = raw.partition(b"\r\n\r\n")
    if not _:
        return False
    length = 0
    for line in head.decode("latin-1").split("\r\n")[1:]:
        name, _, value = line.partition(":")
        if name.strip().lower() == "content-length":
            try:
                length = int(value.strip())
            except ValueError:
                length = 0
    return len(body) >= length


def _parse_response(raw: bytes) -> tuple[int | None, dict[str, str], bytes]:
    """Return ``(status, lowercased-headers, body)``; status is None if silent."""
    if not raw:
        return None, {}, b""
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status = None
    if lines and lines[0].startswith("HTTP/"):
        parts = lines[0].split(" ")
        if len(parts) > 1 and parts[1].isdigit():
            status = int(parts[1])
    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        if name:
            headers[name.strip().lower()] = value.strip()
    return status, headers, body


def _get(port: int, path: str, headers: list[tuple[str, str]] | None = None):
    request = _request_bytes(
        "GET", path, [("Host", f"127.0.0.1:{port}"), *(headers or [])]
    )
    return _parse_response(_raw_exchange(port, request))


def _session_cookie(gateway) -> str:
    """Bootstrap the documented way and return the session cookie value.

    Read off the wire rather than off ``gateway.token`` so these tests stay
    honest about what the server actually handed out.
    """
    status, headers, _ = _get(gateway.port, f"/?token={gateway.token}")
    assert status == 303, f"bootstrap returned {status}: {headers}"
    raw_cookie = headers["set-cookie"]
    value = raw_cookie.split(";", 1)[0].split("=", 1)[1]
    assert value, "bootstrap set an empty session cookie"
    return value


def _command_headers(gateway, cookie: str) -> list[tuple[str, str]]:
    return [
        ("Host", f"127.0.0.1:{gateway.port}"),
        ("Cookie", f"charlie_web_token={cookie}"),
        ("Origin", _origin(gateway)),
        ("Content-Type", "application/json"),
    ]


def _post(gateway, cookie: str, body: bytes, **kwargs):
    request = _request_bytes(
        "POST", "/api/commands", _command_headers(gateway, cookie), body, **kwargs
    )
    return _parse_response(_raw_exchange(gateway.port, request))


def _static_tree(tmp_path: Path) -> tuple[Path, Path]:
    """A static root plus a sibling whose name starts with the root's name."""
    root = tmp_path / "web"
    root.mkdir(exist_ok=True)
    (root / "index.html").write_text("<html>shell</html>", encoding="utf-8")
    (root / "notes.txt").write_text("plain notes", encoding="utf-8")
    (root / "payload.unknownext").write_bytes(b"\x00\x01binary")
    sibling = tmp_path / "web_SIBLING"
    sibling.mkdir(exist_ok=True)
    (sibling / "secret.txt").write_text("SIBLING-SECRET-CANARY", encoding="utf-8")
    return root, sibling


# --------------------------------------------------------------------------
# F1 - static path containment
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/%2e%2e/web_SIBLING/secret.txt",
        "/..%2fweb_SIBLING%2fsecret.txt",
        "/%2E%2E/web_SIBLING/secret.txt",
    ],
)
def test_sibling_directory_sharing_the_static_prefix_is_not_served(tmp_path, path):
    """F1: containment must be by path component, not by string prefix.

    ``str.startswith`` accepts any sibling whose name begins with the static
    root's basename, which handed unauthenticated callers every file in it.
    """
    root, _sibling = _static_tree(tmp_path)
    gateway, loop, loop_thread, _calls = _gateway(root)
    try:
        gateway.start()
        status, _headers, body = _get(gateway.port, path)
        assert status != 200, f"{path} was served with 200: {body[:120]!r}"
        assert b"SIBLING-SECRET-CANARY" not in body, (
            f"{path} leaked a file from outside the static root"
        )
    finally:
        _teardown(gateway, loop, loop_thread)


def test_absolute_style_escape_from_the_static_root_is_not_served(tmp_path):
    """F1: the same leak reached through a bare ``..`` segment."""
    root, _sibling = _static_tree(tmp_path)
    gateway, loop, loop_thread, _calls = _gateway(root)
    try:
        gateway.start()
        status, _headers, body = _get(gateway.port, "/../web_SIBLING/secret.txt")
        assert status != 200, body[:120]
        assert b"SIBLING-SECRET-CANARY" not in body
    finally:
        _teardown(gateway, loop, loop_thread)


def test_files_inside_the_static_root_are_still_served(tmp_path):
    """F1: the refusal must be narrow. Containment is not a blanket 403."""
    root, _sibling = _static_tree(tmp_path)
    gateway, loop, loop_thread, _calls = _gateway(root)
    try:
        gateway.start()
        status, _headers, body = _get(gateway.port, "/index.html")
        assert status == 200
        assert b"shell" in body
        status, _headers, body = _get(gateway.port, "/notes.txt")
        assert status == 200
        assert b"plain notes" in body
    finally:
        _teardown(gateway, loop, loop_thread)


# --------------------------------------------------------------------------
# F3 - static response headers
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,expected_type",
    [
        ("/index.html", "text/html"),
        ("/notes.txt", "text/plain"),
        ("/payload.unknownext", "application/octet-stream"),
    ],
)
def test_static_response_declares_a_content_type_and_nosniff(tmp_path, path, expected_type):
    """F3: an unlabelled body is MIME-sniffed by the browser.

    ``_allowed_origins`` includes this loopback origin, so a sniffed file
    becomes same-origin script with credentialed access.
    """
    root, _sibling = _static_tree(tmp_path)
    gateway, loop, loop_thread, _calls = _gateway(root)
    try:
        gateway.start()
        status, headers, _body = _get(gateway.port, path)
        assert status == 200
        assert expected_type in headers.get("content-type", ""), headers
        assert headers.get("x-content-type-options") == "nosniff", headers
    finally:
        _teardown(gateway, loop, loop_thread)


def test_shell_document_carries_a_content_security_policy(tmp_path):
    """F3: nosniff stops sniffing; CSP stops execution if sniffing wins."""
    root, _sibling = _static_tree(tmp_path)
    gateway, loop, loop_thread, _calls = _gateway(root)
    try:
        gateway.start()
        status, headers, _body = _get(gateway.port, "/index.html")
        assert status == 200
        policy = headers.get("content-security-policy", "")
        assert policy, "the shell document was served without a CSP"
        assert "default-src 'self'" in policy, policy
        assert "object-src 'none'" in policy, policy
        assert "frame-ancestors 'none'" in policy, policy
        # An inline-script hole would make the header decorative.
        assert "script-src 'self'" in policy, policy
        assert "unsafe-inline" not in policy.split("script-src")[1].split(";")[0], policy
    finally:
        _teardown(gateway, loop, loop_thread)


# --------------------------------------------------------------------------
# F8 - Server banner
# --------------------------------------------------------------------------


def test_server_banner_does_not_disclose_the_python_version(tmp_path):
    """F8: the banner is emitted pre-auth on every single response."""
    root, _sibling = _static_tree(tmp_path)
    gateway, loop, loop_thread, _calls = _gateway(root)
    try:
        gateway.start()
        status, headers, _body = _get(gateway.port, "/index.html")
        assert status == 200
        banner = headers.get("server", "")
        assert banner.startswith("CharlieWeb/"), banner
        assert "Python/" not in banner, banner
        assert sys.version.split()[0] not in banner, banner
    finally:
        _teardown(gateway, loop, loop_thread)


# --------------------------------------------------------------------------
# F6 - ambiguous request framing
# --------------------------------------------------------------------------


def test_duplicate_content_length_is_refused(tmp_path):
    """F6: ``headers.get`` returns only the first value (RFC 9110 §8.6)."""
    gateway, loop, loop_thread, calls = _gateway(tmp_path)
    try:
        gateway.start()
        cookie = _session_cookie(gateway)
        body = b'{"type": "noop"}'
        head = "\r\n".join(
            [
                "POST /api/commands HTTP/1.1",
                f"Host: 127.0.0.1:{gateway.port}",
                f"Cookie: charlie_web_token={cookie}",
                f"Origin: {_origin(gateway)}",
                "Content-Type: application/json",
                f"Content-Length: {len(body)}",
                f"Content-Length: {len(body) + 50}",
            ]
        )
        raw = _raw_exchange(gateway.port, (head + "\r\n\r\n").encode("latin-1") + body)
        status, _headers, _payload = _parse_response(raw)
        assert status == 400, (
            f"two different Content-Length values returned {status}; "
            "the request was framed ambiguously"
        )
        assert calls == [], "an ambiguously framed body reached the command handler"
    finally:
        _teardown(gateway, loop, loop_thread)


def test_matching_duplicate_content_length_is_still_refused(tmp_path):
    """F6: RFC 9110 forbids more than one field even when the values agree."""
    gateway, loop, loop_thread, calls = _gateway(tmp_path)
    try:
        gateway.start()
        cookie = _session_cookie(gateway)
        body = b'{"type": "noop"}'
        head = "\r\n".join(
            [
                "POST /api/commands HTTP/1.1",
                f"Host: 127.0.0.1:{gateway.port}",
                f"Cookie: charlie_web_token={cookie}",
                f"Origin: {_origin(gateway)}",
                "Content-Type: application/json",
                f"Content-Length: {len(body)}",
                f"Content-Length: {len(body)}",
            ]
        )
        raw = _raw_exchange(gateway.port, (head + "\r\n\r\n").encode("latin-1") + body)
        status, _headers, _payload = _parse_response(raw)
        assert status == 400, f"repeated Content-Length returned {status}"
        assert calls == [], "an ambiguously framed body reached the command handler"
    finally:
        _teardown(gateway, loop, loop_thread)


def test_transfer_encoding_is_refused(tmp_path):
    """F6: TE plus CL is a request-smuggling primitive; TE alone is unsupported."""
    gateway, loop, loop_thread, calls = _gateway(tmp_path)
    try:
        gateway.start()
        cookie = _session_cookie(gateway)
        body = b'{"type": "noop"}'
        head = "\r\n".join(
            [
                "POST /api/commands HTTP/1.1",
                f"Host: 127.0.0.1:{gateway.port}",
                f"Cookie: charlie_web_token={cookie}",
                f"Origin: {_origin(gateway)}",
                "Content-Type: application/json",
                "Transfer-Encoding: chunked",
                f"Content-Length: {len(body)}",
            ]
        )
        raw = _raw_exchange(gateway.port, (head + "\r\n\r\n").encode("latin-1") + body)
        status, _headers, _payload = _parse_response(raw)
        assert status == 400, f"Transfer-Encoding framing returned {status}"
        assert calls == [], "a chunk-framed body reached the command handler"
    finally:
        _teardown(gateway, loop, loop_thread)


def test_a_refused_request_closes_the_connection(tmp_path):
    """F6: HTTP/1.0 is declared, so a refusal must not leave the socket readable.

    ``_refuse`` computed ``close_connection = not drained``, so after answering
    a refusal the server sat waiting for a second request on that connection.
    A clean EOF is the postcondition; a keep-alive server instead holds the
    connection open until its idle timeout.
    """
    gateway, loop, loop_thread, calls = _gateway(tmp_path)
    try:
        gateway.start()
        refusal = _request_bytes(
            "POST",
            "/api/commands",
            [
                ("Host", f"127.0.0.1:{gateway.port}"),
                ("Origin", _origin(gateway)),
            ],
            body=b'{"type": "noop"}',
        )
        deadline = time.monotonic() + _CLIENT_READ_TIMEOUT_S
        with socket.create_connection(
            ("127.0.0.1", gateway.port), timeout=_CLIENT_READ_TIMEOUT_S
        ) as sock:
            sock.settimeout(_CLIENT_READ_TIMEOUT_S)
            sock.sendall(refusal)
            raw = _recv_until(sock, _response_complete, deadline).decode("latin-1")
            assert raw.count("HTTP/1.") == 1, raw[:400]
            assert "HTTP/1.0 401" in raw, raw[:400]
            assert calls == []

            # The refusal must be the last thing on this connection, promptly.
            # A keep-alive server instead holds it open until its idle timeout.
            sock.settimeout(2.0)
            try:
                trailing = sock.recv(65536)
            except (TimeoutError, OSError) as exc:
                raise AssertionError(
                    "the server kept the connection open after a refusal "
                    f"({type(exc).__name__}); it must close it"
                ) from exc
            assert trailing == b"", f"the server sent more after the refusal: {trailing[:200]!r}"
    finally:
        _teardown(gateway, loop, loop_thread)


# --------------------------------------------------------------------------
# F2 - bounded reads
# --------------------------------------------------------------------------


def test_handler_class_declares_a_socket_timeout(tmp_path):
    """F2: without a class timeout the socket is blocking forever."""
    gateway, loop, loop_thread, _calls = _gateway(tmp_path)
    try:
        gateway.start()
        handler_cls = gateway._server.RequestHandlerClass
        timeout = getattr(handler_cls, "timeout", None)
        assert timeout is not None, "the request handler declares no socket timeout"
        assert 0 < timeout <= 60, timeout
    finally:
        _teardown(gateway, loop, loop_thread)


def test_declared_but_unsent_body_is_refused_instead_of_pinning_a_thread(tmp_path):
    """F2: declaring 64 KiB and sending 10 bytes used to block forever."""
    gateway, loop, loop_thread, calls = _gateway(tmp_path)
    try:
        gateway.start()
        cookie = _session_cookie(gateway)
        request = _request_bytes(
            "POST",
            "/api/commands",
            _command_headers(gateway, cookie),
            b'{"type":',
            content_length=65535,
        )
        started = time.monotonic()
        status, _headers, _body = _parse_response(
            _raw_exchange(gateway.port, request, read_timeout=_CLIENT_READ_TIMEOUT_S)
        )
        elapsed = time.monotonic() - started
        assert status == 400, (
            f"a short body returned {status!r} after {elapsed:.2f}s "
            "(the handler thread was pinned)"
        )
        assert elapsed < _CLIENT_READ_TIMEOUT_S, elapsed
        assert calls == []

        # And the listener is still healthy for the next caller.
        status, _headers, body = _get(
            gateway.port, "/api/scene", [("Cookie", f"charlie_web_token={cookie}")]
        )
        assert status == 200, status
        assert json.loads(body)["title"] == "Charlie is ready"
    finally:
        _teardown(gateway, loop, loop_thread)


def test_sse_registry_is_capped_and_refuses_past_the_cap(tmp_path):
    """F2: ``_clients`` grew without bound and every entry pinned a thread."""
    gateway, loop, loop_thread, _calls = _gateway(tmp_path)
    try:
        gateway.start()
        cap = getattr(web_gateway_module, "_MAX_SSE_CLIENTS", None)
        assert cap is not None, "the SSE registry declares no cap"
        for index in range(cap):
            assert gateway._attach_client() is not None, f"cap rejected client {index}"
        assert gateway._client_count() == cap
        assert gateway._attach_client() is None, "the SSE registry accepted a client past its cap"

        cookie = _session_cookie(gateway)
        status, _headers, _body = _get(
            gateway.port, "/api/events", [("Cookie", f"charlie_web_token={cookie}")]
        )
        assert status == 503, f"/api/events past the cap returned {status}"
        assert gateway._client_count() == cap
    finally:
        _teardown(gateway, loop, loop_thread)


# --------------------------------------------------------------------------
# F7 - courtesy drain restores a blocking socket
# --------------------------------------------------------------------------


class _RecordingConnection:
    def __init__(self) -> None:
        self.timeout = None
        self.calls: list[float | None] = []

    def gettimeout(self):
        return self.timeout

    def settimeout(self, value):
        self.timeout = value
        self.calls.append(value)


class _BytesReader:
    def __init__(self, payload: bytes) -> None:
        self._buffer = io.BytesIO(payload)

    def read(self, size: int) -> bytes:
        return self._buffer.read(size)


def test_courtesy_drain_restores_a_blocking_socket(tmp_path):
    """F7: a skipped restore left a 1 s timeout on the next request."""
    gateway, loop, loop_thread, _calls = _gateway(tmp_path)
    try:
        gateway.start()
        handler_cls = gateway._server.RequestHandlerClass
        connection = _RecordingConnection()
        headers = SimpleNamespace()
        headers.get = lambda name, default=None: "10" if name == "Content-Length" else default
        handler = SimpleNamespace(
            headers=headers,
            connection=connection,
            rfile=_BytesReader(b"0123456789"),
            _request_body_consumed=0,
        )

        drained = handler_cls._drain_request_body(handler)
        assert drained is True
        assert 1.0 in connection.calls, "the drain never applied its own bound"
        assert connection.timeout is None, (
            f"the drain left the socket at timeout={connection.timeout!r}; "
            "the prior blocking state was not restored"
        )
    finally:
        _teardown(gateway, loop, loop_thread)


# --------------------------------------------------------------------------
# F4 - reachable uncaught exceptions
# --------------------------------------------------------------------------


def test_deeply_nested_json_body_returns_a_status_not_a_reset(tmp_path):
    """F4a: ``RecursionError`` is a RuntimeError, not a ValueError."""
    gateway, loop, loop_thread, calls = _gateway(tmp_path)
    try:
        gateway.start()
        cookie = _session_cookie(gateway)
        # Deep enough to blow the JSON scanner's recursion guard, comfortably
        # inside the 64 KiB command limit so the body itself is accepted.
        depth = 32000
        body = b"[" * depth + b"]" * depth
        status, headers, payload = _post(gateway, cookie, body)
        assert status == 400, (
            f"deep nesting returned {status!r} (headers={headers}, "
            f"body={payload[:120]!r}); the connection was reset instead"
        )
        assert "error_id" in json.loads(payload), payload[:200]
        assert calls == []
    finally:
        _teardown(gateway, loop, loop_thread)


def test_non_ascii_bootstrap_token_is_refused_with_a_status(tmp_path):
    """F4b: ``secrets.compare_digest`` raises TypeError on non-ASCII str."""
    gateway, loop, loop_thread, _calls = _gateway(tmp_path)
    try:
        gateway.start()
        request = _request_bytes(
            "GET", "/?token=%C3%A9%C3%A9", [("Host", f"127.0.0.1:{gateway.port}")]
        )
        status, _headers, _body = _parse_response(_raw_exchange(gateway.port, request))
        assert status == 401, f"non-ASCII ?token= returned {status!r}"
    finally:
        _teardown(gateway, loop, loop_thread)


def test_non_ascii_session_cookie_is_refused_with_a_status(tmp_path):
    """F4b: the same TypeError is reachable unauthenticated on /api/scene."""
    gateway, loop, loop_thread, _calls = _gateway(tmp_path)
    try:
        gateway.start()
        request = _request_bytes(
            "GET",
            "/api/scene",
            [
                ("Host", f"127.0.0.1:{gateway.port}"),
                ("Cookie", 'charlie_web_token="\xc3\xa9\xc3\xa9"'),
            ],
        )
        status, headers, _body = _parse_response(_raw_exchange(gateway.port, request))
        assert status == 401, f"non-ASCII cookie returned {status!r} (headers={headers})"
        assert "charlie_web_token" not in headers.get("set-cookie", "")
    finally:
        _teardown(gateway, loop, loop_thread)


def test_unserialisable_command_result_still_answers_with_a_status(tmp_path):
    """F4c: ``_json`` serialised before ``send_response`` and sent zero bytes."""

    async def handle(command):
        return {"ok": True, "unserialisable": object()}

    gateway, loop, loop_thread, _calls = _gateway(tmp_path, handle=handle)
    try:
        gateway.start()
        cookie = _session_cookie(gateway)
        status, _headers, payload = _post(
            gateway, cookie, b'{"type": "noop"}'
        )
        assert status == 500, f"unserialisable result returned {status!r} (body={payload[:120]!r})"
        body = json.loads(payload)
        assert "internal error" in body["error"].lower(), body
    finally:
        _teardown(gateway, loop, loop_thread)


# --------------------------------------------------------------------------
# F5 - session lifetime
# --------------------------------------------------------------------------


def test_session_cookie_is_not_the_bootstrap_token(tmp_path):
    """F5: an operator pastes the bootstrap token once; it must not stay live."""
    gateway, loop, loop_thread, _calls = _gateway(tmp_path)
    try:
        gateway.start()
        bootstrap_token = gateway.token
        cookie = _session_cookie(gateway)
        assert cookie != bootstrap_token, (
            "the session cookie is byte-identical to the long-lived bootstrap token"
        )
        status, _headers, _body = _get(
            gateway.port, "/api/scene", [("Cookie", f"charlie_web_token={bootstrap_token}")]
        )
        assert status == 401, f"the bootstrap token still authenticates ({status})"
    finally:
        _teardown(gateway, loop, loop_thread)


def test_bootstrap_token_is_single_use(tmp_path):
    """F5: anything that ever observed the pasted token must stop working."""
    gateway, loop, loop_thread, _calls = _gateway(tmp_path)
    try:
        gateway.start()
        bootstrap_token = gateway.token
        first_status, _h, _b = _get(gateway.port, f"/?token={bootstrap_token}")
        assert first_status == 303, first_status

        second_status, second_headers, _b = _get(
            gateway.port, f"/?token={bootstrap_token}"
        )
        assert second_status == 401, f"replayed bootstrap returned {second_status}"
        assert "charlie_web_token" not in second_headers.get("set-cookie", ""), second_headers
    finally:
        _teardown(gateway, loop, loop_thread)


def test_session_expires_server_side_not_only_via_cookie_max_age(tmp_path):
    """F5: ``Max-Age`` is a hint; only the server can enforce expiry."""
    gateway, loop, loop_thread, _calls = _gateway(tmp_path)
    try:
        gateway.start()
        cookie = _session_cookie(gateway)
        status, _headers, _body = _get(
            gateway.port, "/api/scene", [("Cookie", f"charlie_web_token={cookie}")]
        )
        assert status == 200, status

        assert gateway._session_issued_at is not None, (
            "the gateway did not record when the session was issued"
        )
        gateway._session_issued_at -= _SESSION_MAX_AGE_S + 1

        status, _headers, _body = _get(
            gateway.port, "/api/scene", [("Cookie", f"charlie_web_token={cookie}")]
        )
        assert status == 401, (
            "an expired session was still accepted because only the cookie's "
            "Max-Age was enforced"
        )
    finally:
        _teardown(gateway, loop, loop_thread)


def test_cookie_flags_are_unchanged(tmp_path):
    """F5 guard: Secure stays omitted (the origin is http loopback)."""
    gateway, loop, loop_thread, _calls = _gateway(tmp_path)
    try:
        gateway.start()
        _status, headers, _body = _get(gateway.port, f"/?token={gateway.token}")
        cookie = headers["set-cookie"]
        assert "HttpOnly" in cookie, cookie
        assert "SameSite=Strict" in cookie, cookie
        assert "Path=/" in cookie, cookie
        assert f"Max-Age={_SESSION_MAX_AGE_S}" in cookie, cookie
        assert "Secure" not in cookie, cookie
    finally:
        _teardown(gateway, loop, loop_thread)


# --------------------------------------------------------------------------
# F9 - the operator can retrieve the token
# --------------------------------------------------------------------------


def test_bootstrap_token_is_written_to_the_operator_only_file(tmp_path):
    """F9: the docstring promises the token at startup; redaction ate it."""
    root, _sibling = _static_tree(tmp_path)
    token_file = tmp_path / "operator" / "web-token.txt"
    gateway, loop, loop_thread, _calls = _gateway(root, bootstrap_token_file=token_file)
    try:
        gateway.start()
        assert token_file.is_file(), "no operator token file was written"
        written = token_file.read_text(encoding="utf-8").strip()
        assert gateway.token in written, written
        assert written.startswith("http://127.0.0.1:"), written
        # The token file must not sit inside anything the gateway serves.
        served_root = root.resolve()
        assert served_root not in token_file.resolve().parents, (
            "the operator token file landed inside the served static root"
        )
        status, _headers, _body = _get(
            gateway.port, "/../operator/web-token.txt"
        )
        assert status != 200, "the token file was served over HTTP"
        if os.name == "posix":
            mode = stat.S_IMODE(token_file.stat().st_mode)
            assert mode & 0o077 == 0, f"token file mode is {oct(mode)}"
    finally:
        _teardown(gateway, loop, loop_thread)


def test_bootstrap_url_is_printed_for_an_interactive_operator(tmp_path, capsys, monkeypatch):
    """F9: with no file configured the token must still reach the operator."""
    root, _sibling = _static_tree(tmp_path)
    assert os.environ.get("CHARLIE_WEB_TOKEN_FILE") is None, (
        "the environment override leaked into the test environment"
    )

    class _TtyBuffer(io.StringIO):
        def isatty(self) -> bool:
            return True

    tty = _TtyBuffer()
    monkeypatch.setattr(sys, "stdout", tty)
    gateway, loop, loop_thread, _calls = _gateway(root)
    try:
        gateway.start()
        printed = tty.getvalue()
        lines = [line.strip() for line in printed.splitlines() if line.strip()]
        assert any(
            line.startswith("http://127.0.0.1:") and gateway.token in line for line in lines
        ), f"the bootstrap token was not written to the operator console: {printed!r}"
    finally:
        _teardown(gateway, loop, loop_thread)
    assert isinstance(capsys.readouterr().out, str)


def test_bootstrap_token_file_env_override(tmp_path, monkeypatch):
    """F9: operators can name the file without touching application code."""
    root, _sibling = _static_tree(tmp_path)
    token_file = tmp_path / "operator" / "env-token.txt"
    monkeypatch.setenv("CHARLIE_WEB_TOKEN_FILE", str(token_file))
    gateway, loop, loop_thread, _calls = _gateway(root)
    try:
        gateway.start()
        assert token_file.is_file()
        assert gateway.token in token_file.read_text(encoding="utf-8")
    finally:
        _teardown(gateway, loop, loop_thread)


def test_bootstrap_url_is_not_reused_after_the_token_is_consumed(tmp_path):
    """F5/F9: the documented URL must not silently change meaning."""
    gateway, loop, loop_thread, _calls = _gateway(tmp_path)
    try:
        gateway.start()
        assert gateway.bootstrap_url, "no bootstrap URL before bootstrap"
        _session_cookie(gateway)
        assert gateway.bootstrap_url == "", (
            "the gateway still advertises a bootstrap URL for a consumed token"
        )
    finally:
        _teardown(gateway, loop, loop_thread)


def test_module_docstring_matches_the_token_handling():
    """F9: the stale claim is the defect; keep the contract honest."""
    text = web_gateway_module.__doc__ or ""
    assert "logged once at" not in text, (
        "the module docstring still claims the bootstrap token is logged once; "
        "the log redaction filter makes that untrue"
    )


# --------------------------------------------------------------------------
# F10 / F11 - correlation ids and payload logging
# --------------------------------------------------------------------------


def test_every_correlation_id_handed_to_a_caller_reaches_the_log(tmp_path, caplog):
    """F10: an error_id a caller can quote must resolve in the log."""
    gateway, loop, loop_thread, _calls = _gateway(tmp_path)
    try:
        gateway.start()
        cookie = _session_cookie(gateway)
        with caplog.at_level(logging.WARNING, logger="charlie.web_gateway"):
            status, _headers, payload = _post(gateway, cookie, b"not-json")
        assert status == 400, status
        error_id = json.loads(payload)["error_id"]
        assert error_id in caplog.text, (
            f"correlation id {error_id!r} was handed to the caller but never logged"
        )
    finally:
        _teardown(gateway, loop, loop_thread)


def test_command_payload_is_not_written_to_the_log_on_handler_failure(tmp_path, caplog):
    """F11: ``submit_text`` carries arbitrary user text."""

    async def boom(command):
        raise RuntimeError("handler exploded")

    gateway, loop, loop_thread, _calls = _gateway(tmp_path, handle=boom)
    try:
        gateway.start()
        cookie = _session_cookie(gateway)
        canary = "USER-TEXT-CANARY-must-not-be-logged"
        body = json.dumps({"type": "submit_text", "text": canary}).encode("utf-8")
        with caplog.at_level(logging.ERROR, logger="charlie.web_gateway"):
            status, _headers, payload = _post(gateway, cookie, body)
        assert status == 500, status
        assert canary not in caplog.text, "the full command payload was logged"
        assert "submit_text" in caplog.text, (
            "the log should still record the command shape"
        )
        assert "handler exploded" in caplog.text, "the real exception was dropped"
        assert canary not in payload.decode("utf-8"), payload
    finally:
        _teardown(gateway, loop, loop_thread)
