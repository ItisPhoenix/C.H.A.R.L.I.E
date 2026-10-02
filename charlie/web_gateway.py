"""Small loopback web gateway for Charlie's live browser projection.

Security posture (all of it refuses rather than allows):

* The session cookie is the only thing that mints authority. ``/api/scene`` and
  every other API route require it; serving the static app shell does not,
  because a shell is not a session. A page load or top-level navigation by any
  local page therefore never produces a usable session.
* The bootstrap token is single-use. ``/api/scene`` and the command route are
  reached by opening ``http://127.0.0.1:<port>/?token=<token>`` exactly once;
  the gateway answers with a 303 to the clean path so the token does not linger
  in history or leak via Referer. The gateway answers with 401 for every later
  attempt with that value.
* The token is NOT only logged. Charlie's :class:`SensitiveDataFilter` redacts
  ``?token=`` out of every log record, so a log-only bootstrap flow cannot
  retrieve it. Redaction is correct and stays. :meth:`RuntimeWebGateway.start`
  instead writes the one-time URL to an operator-only file
  (``bootstrap_token_file`` / ``CHARLIE_WEB_TOKEN_FILE``) and, when no file is
  configured, prints it to an interactive console.
* A successful bootstrap mints a *different* session secret, so the value an
  operator pastes is spent on first use and never becomes a bearer credential.
  The session lifetime is enforced by the server against ``_SESSION_MAX_AGE_S``;
  the cookie's ``Max-Age`` is only a hint the client is free to ignore.
* ``Secure`` is deliberately absent: the origin is plain ``http://`` loopback.
* State-changing POSTs require an explicit, allowlisted ``Origin``. A missing
  Origin is a refusal, not a pass: a non-browser client that omits it is
  exactly the client CSRF cannot protect, so it must be rejected.
* Request framing must be unambiguous. More than one ``Content-Length`` field
  (even with equal values) and any ``Transfer-Encoding`` field are refusals
  rather than a guess, per RFC 9110 §8.6 and RFC 9112 §6.1.
* Every socket read is bounded in time as well as in size, so a client that
  declares a ``Content-Length`` and then stops talking cannot pin a handler
  thread, and the SSE subscriber registry is capped.
* Error bodies carry a generic message plus a correlation id. That correlation
  id is written to the log as well, so an id a caller can quote always
  resolves. Exception text goes to the log only, and the log records the
  command *shape* rather than the user's text.
* Static responses declare a ``Content-Type``, carry ``X-Content-Type-Options:
  nosniff``, and the HTML shell carries a Content-Security-Policy. This origin
  is allowlisted for credentialed requests, so a MIME-sniffed file would
  otherwise become same-origin script running with the session cookie.
* Static paths are contained by path component against the resolved root, not
  by string prefix: ``<static>_SIBLING`` is not inside ``<static>``.
* A refusal drains the unread remainder of the request body first, then closes.
  Closing while the client's own write is still in flight makes the OS answer
  with RST, and the client then sees a transport error instead of the status
  code.
* An SSE client that cannot keep up is dropped and unregistered rather than
  silently starved while it stays subscribed.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import mimetypes
import os
import queue
import secrets
import sys
import threading
import time
from concurrent.futures import TimeoutError as _FutureTimeout
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import parse_qs, unquote, urlparse

logger = logging.getLogger("charlie.web_gateway")

_COOKIE_NAME = "charlie_web_token"
_TOKEN_PARAM = "token"
_MAX_COMMAND_BYTES = 64 * 1024
# Upper bound on bytes discarded purely so a refusal can be delivered as a clean
# status instead of a connection reset. Separate from _MAX_COMMAND_BYTES: this is
# a courtesy drain for clients that over-post, not an accepted payload size.
_MAX_DRAIN_BYTES = 8 * 1024 * 1024
# Wall-clock bound on the courtesy drain, so a client that declares a
# Content-Length and never sends it cannot pin a handler thread.
_DRAIN_TIMEOUT_S = 1.0
# Wall-clock bound on reading an accepted command body. A declared-but-unwritten
# body is a refusal, not a block: without this, ``Content-Length: 65535`` plus
# ten bytes pins the handler thread for as long as the OS keeps the socket.
_BODY_TIMEOUT_S = 3.0
# Idle bound for the whole request/response exchange. ``socketserver`` applies
# this to the accepted socket, so an open connection that never sends a request
# line costs one thread for at most this long instead of forever.
_HANDLER_IDLE_TIMEOUT_S = 15.0
# SSE frames are tiny and the stream is long-lived by design, so its writes get
# their own generous bound rather than the short idle one.
_SSE_WRITE_TIMEOUT_S = 60.0
# Hard cap on concurrent SSE subscribers. Each subscriber pins a handler thread
# for the lifetime of the stream, so an unbounded registry is a thread-exhaustion
# primitive even though the events themselves are authenticated.
_MAX_SSE_CLIENTS = 32
_COMMAND_TIMEOUT_S = 30.0
_SSE_QUEUE_MAXSIZE = 128
_SSE_KEEPALIVE_S = 15
_SESSION_MAX_AGE_S = 8 * 60 * 60
_TOKEN_FILE_ENV = "CHARLIE_WEB_TOKEN_FILE"
_GENERIC_INTERNAL_ERROR = "Charlie hit an internal error handling that request."
_GENERIC_TIMEOUT_ERROR = "Charlie did not answer that command in time."
_DEFAULT_CONTENT_TYPE = "application/octet-stream"
# Applied to HTML documents only. 'unsafe-inline' is limited to styles because the
# shell is a React build that sets element styles through the CSSOM; scripts are
# same-origin files only, which is the property that actually matters here.
_SHELL_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; font-src 'self' data:; media-src 'self' blob:; "
    "connect-src 'self'; object-src 'none'; frame-ancestors 'none'; "
    "base-uri 'none'; form-action 'none'"
)
_HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
# Command payloads carry arbitrary user text (``submit_text``), so the log gets
# the shape and never the values.
_MAX_SHAPE_KEYS = 24
_MAX_SHAPE_TYPE_CHARS = 64
# Distinguishes "there was no prior timeout" from "the prior timeout was None
# (blocking)". ``settimeout(None)`` is the call that restores a blocking socket,
# so skipping it would leave a short timeout behind on a reused connection.
_UNSET = object()


class _SseClient:
    """One subscribed SSE reader, with an overflow latch."""

    def __init__(self, maxsize: int = _SSE_QUEUE_MAXSIZE) -> None:
        self.queue: queue.Queue[bytes] = queue.Queue(maxsize=maxsize)
        self.overflowed = False


class RuntimeWebGateway:
    def __init__(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        command_handler: Callable[[dict], Awaitable[Any]],
        snapshot_getter: Callable[[], dict],
        static_dir: Optional[Path] = None,
        host: str = "127.0.0.1",
        port: int = 8001,
        bootstrap_token_file: Optional[Path] = None,
    ) -> None:
        self._loop = loop
        self._command_handler = command_handler
        self._snapshot_getter = snapshot_getter
        self._static_dir = Path(static_dir) if static_dir is not None else None
        self.host = host
        self.port = port
        # One lock guards the bootstrap token, the live session secret, and its
        # issue time together: minting and consuming are one atomic step, so a
        # replayed bootstrap can never win a race against the first use.
        self._session_lock = threading.Lock()
        self._bootstrap_token: Optional[str] = secrets.token_urlsafe(24)
        self._session_issued_at: Optional[float] = None
        # ``token`` is the *live* session secret. Before the first bootstrap it
        # holds the bootstrap token, so the documented ``gateway.token`` still
        # works for building the operator URL; afterwards it holds only the
        # cookie value and the bootstrap token is gone for good.
        self.token = self._bootstrap_token
        self._bootstrap_token_file = self._resolve_token_file(bootstrap_token_file)
        self._clients: dict[int, _SseClient] = {}
        self._clients_lock = threading.Lock()
        self._next_client_id = 0
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: threading.Thread | None = None

    @staticmethod
    def _resolve_token_file(explicit: Optional[Path]) -> Optional[Path]:
        """Prefer an explicit path, then the environment, then nothing."""
        if explicit is not None:
            return Path(explicit)
        configured = os.environ.get(_TOKEN_FILE_ENV, "").strip()
        return Path(configured) if configured else None

    # ------------------------------------------------------------------
    # identity / helpers
    # ------------------------------------------------------------------
    @property
    def bootstrap_url(self) -> str:
        """The one URL an operator opens to create a session.

        Empty once the token has been spent. There is no second URL, and the
        live session secret must never be advertised here.
        """
        with self._session_lock:
            token = self._bootstrap_token
        if token is None:
            return ""
        return f"http://{self.host}:{self.port}/?{_TOKEN_PARAM}={token}"

    def _allowed_origins(self) -> set[str]:
        """Origins derived from the port actually bound, never a hardcoded one."""
        return {f"http://{self.host}:{self.port}", f"http://localhost:{self.port}"}

    @staticmethod
    def _error_id() -> str:
        return secrets.token_hex(4)

    # ------------------------------------------------------------------
    # session lifetime
    # ------------------------------------------------------------------
    def _consume_bootstrap_token(self, candidate: str) -> bool:
        """Accept the bootstrap token exactly once and mint a fresh session secret.

        Single use plus rotation: the value an operator pastes is spent on first
        use, and the cookie it produces is a different secret, so observing
        either one later yields nothing.

        ``secrets.compare_digest`` raises ``TypeError`` on a non-ASCII ``str``,
        which would escape as a connection reset rather than a status. The shape
        is rejected before the comparison instead.
        """
        if not candidate.isascii():
            return False
        with self._session_lock:
            expected = self._bootstrap_token
            if expected is None or not secrets.compare_digest(candidate, expected):
                return False
            self._bootstrap_token = None
            self.token = secrets.token_urlsafe(24)
            self._session_issued_at = time.monotonic()
            return True

    def _session_matches(self, candidate: str) -> bool:
        """Constant-time compare plus the server-side lifetime check.

        The cookie's ``Max-Age`` is a request from the client, not a guarantee:
        only this check decides whether a session is still live.
        """
        with self._session_lock:
            current = self.token
            issued = self._session_issued_at
        matched = bool(current) and secrets.compare_digest(candidate, current or "")
        if not matched or issued is None:
            return False
        return (time.monotonic() - issued) < _SESSION_MAX_AGE_S

    # ------------------------------------------------------------------
    # SSE client registry
    # ------------------------------------------------------------------
    def _attach_client(self, maxsize: int = _SSE_QUEUE_MAXSIZE) -> Optional[_SseClient]:
        """Register an SSE reader, or return None when the registry is full."""
        with self._clients_lock:
            if len(self._clients) >= _MAX_SSE_CLIENTS:
                logger.warning(
                    "web gateway refused an SSE subscriber: %d already attached",
                    len(self._clients),
                )
                return None
            self._next_client_id += 1
            client = _SseClient(maxsize=maxsize)
            self._clients[self._next_client_id] = client
            return client

    def _detach_client(self, client: _SseClient) -> None:
        with self._clients_lock:
            for key, value in list(self._clients.items()):
                if value is client:
                    del self._clients[key]

    def _client_count(self) -> int:
        with self._clients_lock:
            return len(self._clients)

    def publish(self, event: Any) -> None:
        """Fan an event out to every SSE subscriber.

        A subscriber that cannot keep up is latched as overflowed and
        unregistered. Silently dropping the event while it stays subscribed
        would leave a stale-looking client connected forever.
        """
        try:
            payload = json.dumps(self._project_event(event), default=str)
        except (TypeError, ValueError):
            logger.warning("web gateway dropped an unserialisable event", exc_info=True)
            return
        frame = f"data: {payload}\n\n".encode("utf-8")
        with self._clients_lock:
            targets = list(self._clients.items())
        for key, client in targets:
            if client.overflowed:
                continue
            try:
                client.queue.put_nowait(frame)
            except queue.Full:
                client.overflowed = True
                with self._clients_lock:
                    self._clients.pop(key, None)
                logger.warning("web gateway dropped a stalled SSE client")

    def scene_snapshot(self) -> dict:
        try:
            snapshot = copy.deepcopy(self._snapshot_getter())
        except Exception:
            logger.exception("web gateway snapshot getter failed")
            return {"version": 1, "revision": 0, "title": "Charlie", "summary": "", "details": []}
        if not isinstance(snapshot, dict):
            return {"version": 1, "revision": 0, "title": "Charlie", "summary": "", "details": []}
        return snapshot

    def _project_event(self, event: Any) -> Any:
        if isinstance(event, dict):
            return event
        if hasattr(event, "to_dict"):
            try:
                return event.to_dict()
            except Exception:
                logger.warning("web gateway could not project event", exc_info=True)
        return {"type": type(event).__name__, "payload": repr(event)}

    @staticmethod
    def _command_shape(command: Any) -> str:
        """Describe a command for the log without echoing the user's text."""
        if not isinstance(command, dict):
            return f"non-object command ({type(command).__name__})"
        kind = str(command.get("type", ""))[:_MAX_SHAPE_TYPE_CHARS]
        keys = sorted(str(key)[:_MAX_SHAPE_TYPE_CHARS] for key in command)
        truncated = keys[:_MAX_SHAPE_KEYS]
        if len(keys) > _MAX_SHAPE_KEYS:
            truncated.append(f"+{len(keys) - _MAX_SHAPE_KEYS} more")
        return f"type={kind!r} keys={truncated}"

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------
    def start(self) -> None:
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "CharlieWeb/1.0"
            # Appends the interpreter version to every Server banner, pre-auth and
            # on every response. Charlie needs no part of it.
            sys_version = ""
            # Applied to the accepted socket by socketserver. Without it the
            # socket is blocking and a client that opens a connection and says
            # nothing holds a thread indefinitely.
            timeout = _HANDLER_IDLE_TIMEOUT_S

            def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
                logger.debug("web gateway %s", fmt % args)

            # -- auth ---------------------------------------------------
            def _authorized(self) -> bool:
                raw = self.headers.get("Cookie")
                if not raw:
                    return False
                try:
                    jar = SimpleCookie()
                    jar.load(raw)
                except Exception:
                    return False
                morsel = jar.get(_COOKIE_NAME)
                if morsel is None:
                    return False
                candidate = morsel.value
                # A quoted cookie value may carry non-ASCII, and
                # compare_digest raises TypeError on it. Refuse the shape here so
                # the answer is a 401 rather than a connection reset.
                if not candidate.isascii():
                    return False
                return gateway._session_matches(candidate)

            def _json(self, status: int, payload: dict) -> None:
                """Serialise first, then commit to a status.

                Serialising before ``send_response`` means an unserialisable
                payload used to send zero bytes and reset the connection instead
                of answering with a status. The fallback keeps the module's
                generic-error contract intact.
                """
                try:
                    body = json.dumps(payload).encode("utf-8")
                except (TypeError, ValueError, RecursionError):
                    logger.exception("web gateway could not serialise a response body")
                    status = HTTPStatus.INTERNAL_SERVER_ERROR
                    body = json.dumps({"error": _GENERIC_INTERNAL_ERROR}).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def _refuse(
                self,
                status: int,
                message: str,
                *,
                correlation: bool = False,
                error_id: Optional[str] = None,
            ) -> None:
                """Answer an error without echoing anything internal.

                The unread remainder of the request body is discarded first, so
                the client's own write finishes and the status code reaches it
                instead of a connection reset. The connection is then closed:
                this handler declares HTTP/1.0, so a refusal must not leave the
                socket readable for a follow-up request.
                """
                self._drain_request_body()
                self.close_connection = True
                payload: dict[str, Any] = {"error": message}
                if correlation:
                    identifier = error_id or gateway._error_id()
                    payload["error_id"] = identifier
                    self._pending_error_id = identifier
                self._json(status, payload)

            def _note_correlation(self) -> None:
                """Make the correlation id a caller just received resolvable.

                Without this the id exists only in the response body, so nobody
                could answer a report that quoted one.
                """
                identifier = getattr(self, "_pending_error_id", None)
                if identifier:
                    logger.warning("web gateway refused a request: error_id=%s", identifier)

            def _drain_request_body(self) -> bool:
                """Discard the *unread* remainder of the request body.

                Returns True when nothing readable is left. Two things keep this
                safe to call from any refusal: ``_request_body_consumed`` records
                what the handler already read, and the drain is bounded by time
                as well as bytes because a client may declare a
                ``Content-Length`` and then never send it.
                """
                try:
                    declared = int(self.headers.get("Content-Length", "0") or 0)
                except (TypeError, ValueError):
                    return False
                remaining = declared - int(getattr(self, "_request_body_consumed", 0) or 0)
                if remaining <= 0:
                    return True
                if remaining > _MAX_DRAIN_BYTES:
                    # Beyond this bound a clean status is impossible: the only ways
                    # to avoid a reset are to read the body or to lie. Refusing to
                    # read keeps this from being a read-amplification vector.
                    return False
                # ``None`` is a real prior state (blocking), not "unknown":
                # settimeout(None) is what puts the socket back.
                previous: Any = _UNSET
                try:
                    previous = self.connection.gettimeout()
                    self.connection.settimeout(_DRAIN_TIMEOUT_S)
                    while remaining > 0:
                        chunk = self.rfile.read(min(remaining, 64 * 1024))
                        if not chunk:
                            return False
                        remaining -= len(chunk)
                except (OSError, TimeoutError):
                    return False
                finally:
                    if previous is not _UNSET:
                        try:
                            self.connection.settimeout(previous)
                        except OSError:
                            pass
                return True

            def _read_command_body(self, length: int) -> bytes:
                """Read exactly ``length`` bytes under a wall-clock bound.

                A short read is a refusal, never a block. Without the bound, a
                client that declares 64 KiB and sends ten bytes pins this thread
                for as long as the OS keeps the connection open.
                """
                previous: Any = _UNSET
                try:
                    previous = self.connection.gettimeout()
                    self.connection.settimeout(_BODY_TIMEOUT_S)
                    payload = self.rfile.read(length)
                except (OSError, TimeoutError):
                    raise _CommandRejected(
                        HTTPStatus.BAD_REQUEST, "command body could not be read"
                    ) from None
                finally:
                    if previous is not _UNSET:
                        try:
                            self.connection.settimeout(previous)
                        except OSError:
                            pass
                self._request_body_consumed = len(payload)
                if len(payload) != length:
                    raise _CommandRejected(
                        HTTPStatus.BAD_REQUEST, "command body could not be read"
                    )
                return payload

            def _reject_ambiguous_framing(self) -> None:
                """Refuse a request whose body framing is not unambiguous.

                ``self.headers.get("Content-Length")`` returns only the first
                value, so a second field was silently ignored (RFC 9110 §8.6
                requires rejecting more than one, even when the values agree).
                RFC 9112 §6.1 makes Content-Length alongside Transfer-Encoding a
                request-smuggling primitive, and chunked framing is unsupported
                here anyway. Guessing is the one option that is never right.
                """
                if self.headers.get("Transfer-Encoding") is not None:
                    self._refuse(
                        HTTPStatus.BAD_REQUEST, "Transfer-Encoding is not supported"
                    )
                    return
                if len(self.headers.get_all("Content-Length") or ()) > 1:
                    self._refuse(
                        HTTPStatus.BAD_REQUEST, "multiple Content-Length values"
                    )

            def _redirect_clean(self, path: str) -> None:
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("Location", path or "/")
                self.send_header("Content-Length", "0")
                self.send_header(
                    "Set-Cookie",
                    f"{_COOKIE_NAME}={gateway.token}; HttpOnly; SameSite=Strict; "
                    f"Path=/; Max-Age={_SESSION_MAX_AGE_S}",
                )
                self.send_header("Cache-Control", "no-store")
                self.end_headers()

            def _bootstrap(self, parsed) -> bool:
                """Exchange a correct ``?token=`` for a session cookie."""
                supplied = parse_qs(parsed.query).get(_TOKEN_PARAM, [])
                if not supplied:
                    return False
                if not gateway._consume_bootstrap_token(supplied[0]):
                    logger.warning(
                        "Rejected web gateway bootstrap with an invalid or spent token from %s",
                        self.client_address[0] if self.client_address else "unknown",
                    )
                    self._refuse(HTTPStatus.UNAUTHORIZED, "invalid bootstrap token")
                    return True
                self._redirect_clean("/")
                return True

            # -- GET -----------------------------------------------------
            def do_GET(self) -> None:  # noqa: N802
                self._reject_ambiguous_framing()
                parsed = urlparse(self.path)
                if parsed.path.startswith("/api/"):
                    if parsed.path == "/api/scene":
                        if not self._authorized():
                            self._refuse(
                                HTTPStatus.UNAUTHORIZED, "gateway session required"
                            )
                            return
                        self._json(HTTPStatus.OK, gateway.scene_snapshot())
                        return
                    if parsed.path == "/api/events":
                        if not self._authorized():
                            self._refuse(
                                HTTPStatus.UNAUTHORIZED, "gateway session required"
                            )
                            return
                        client = gateway._attach_client()
                        if client is None:
                            self._refuse(
                                HTTPStatus.SERVICE_UNAVAILABLE,
                                "too many event subscribers",
                            )
                            return
                        gateway._stream(self, client)
                        return
                    self._refuse(HTTPStatus.NOT_FOUND, "not found")
                    return
                if self._bootstrap(parsed):
                    return
                gateway._serve_static(self, parsed.path)

            # -- POST ----------------------------------------------------
            def do_POST(self) -> None:  # noqa: N802
                self._request_body_consumed = 0
                self._reject_ambiguous_framing()
                if urlparse(self.path).path != "/api/commands":
                    self._refuse(HTTPStatus.NOT_FOUND, "not found")
                    return
                # Validate the declared body length BEFORE the auth and origin
                # checks. Those refuse without reading the body, so running them
                # first leaves the client's own write racing our close. A
                # header-only check here rejects an oversized body while no unread
                # bytes remain, and bounds what the drain must discard below.
                try:
                    length = self._command_length()
                except _LengthRejected as rejection:
                    self._refuse(rejection.status, rejection.message)
                    return
                if not self._authorized():
                    self._refuse(HTTPStatus.UNAUTHORIZED, "gateway session required")
                    return
                origin = self.headers.get("Origin")
                if not origin or origin not in gateway._allowed_origins():
                    # A missing Origin used to pass this check, which handed the
                    # gateway to every non-browser local process.
                    logger.warning(
                        "Rejected web gateway POST with origin %r", origin or "<missing>"
                    )
                    self._refuse(HTTPStatus.FORBIDDEN, "origin rejected")
                    return
                try:
                    payload = self._read_command_body(length)
                    command = json.loads(payload.decode("utf-8"))
                    if not isinstance(command, dict):
                        raise _CommandRejected(
                            HTTPStatus.BAD_REQUEST, "command body must be a JSON object"
                        )
                except _CommandRejected as rejection:
                    self._refuse(rejection.status, rejection.message, correlation=True)
                    self._note_correlation()
                    return
                except (UnicodeDecodeError, ValueError, RecursionError):
                    # RecursionError is a RuntimeError, not a ValueError: a deeply
                    # nested body used to escape as a connection reset.
                    self._refuse(
                        HTTPStatus.BAD_REQUEST,
                        "command body must be a JSON object",
                        correlation=True,
                    )
                    self._note_correlation()
                    return
                except OSError:
                    self._refuse(HTTPStatus.BAD_REQUEST, "command body could not be read")
                    return

                future = asyncio.run_coroutine_threadsafe(
                    gateway._command_handler(command), gateway._loop
                )
                try:
                    result = future.result(timeout=_COMMAND_TIMEOUT_S)
                except _FutureTimeout:
                    future.cancel()
                    self._refuse(
                        HTTPStatus.GATEWAY_TIMEOUT, _GENERIC_TIMEOUT_ERROR
                    )
                    return
                except Exception as exc:
                    identifier = gateway._error_id()
                    logger.exception(
                        "web gateway command handler failed (%s): command %s",
                        identifier,
                        gateway._command_shape(command),
                        exc_info=exc,
                    )
                    self._refuse(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        _GENERIC_INTERNAL_ERROR,
                        correlation=True,
                        error_id=identifier,
                    )
                    return
                self._json(HTTPStatus.OK, result if isinstance(result, dict) else {"result": result})

            def _command_length(self) -> int:
                # ``_reject_ambiguous_framing`` already refused a second
                # Content-Length, so exactly one value can reach here.
                raw = self.headers.get("Content-Length")
                if raw is None:
                    raise _LengthRejected(
                        HTTPStatus.LENGTH_REQUIRED, "Content-Length is required"
                    )
                try:
                    length = int(raw)
                except (TypeError, ValueError):
                    raise _LengthRejected(
                        HTTPStatus.BAD_REQUEST, "Content-Length must be an integer"
                    ) from None
                if length <= 0:
                    raise _LengthRejected(
                        HTTPStatus.BAD_REQUEST, "Content-Length must be positive"
                    )
                if length > _MAX_COMMAND_BYTES:
                    raise _LengthRejected(
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                        f"command body exceeds {_MAX_COMMAND_BYTES} bytes",
                    )
                return length

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="charlie-web-gateway", daemon=True
        )
        self._thread.start()
        logger.info("Web gateway listening on %s:%s", self.host, self.port)
        self._announce_bootstrap()

    def _announce_bootstrap(self) -> None:
        """Put the one-time bootstrap URL where the operator will see it.

        The previous contract was "logged once at startup", which is untrue:
        :class:`~charlie.log_redaction.SensitiveDataFilter` redacts ``?token=``
        from every record, so the operator could not retrieve the token at all.
        Redaction is the right behaviour for a non-expiring credential and is
        left alone. The URL is handed over on a side channel the operator
        controls instead, preferring a file and falling back to the console.
        """
        url = self.bootstrap_url
        if not url:
            return
        target = self._bootstrap_token_file
        if target is not None:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(f"{url}\n", encoding="utf-8")
                try:
                    os.chmod(target, 0o600)
                except OSError:
                    # Windows chmod does not express an ACL, so this is a no-op
                    # there; the file inherits the user's directory permissions.
                    logger.warning(
                        "Could not restrict permissions on the gateway token file %s",
                        target,
                        exc_info=True,
                    )
            except OSError:
                logger.warning(
                    "Could not write the gateway operator token file %s", target, exc_info=True
                )
            else:
                logger.info("Web gateway bootstrap URL written to %s", target)
                return
        stream = sys.stdout
        try:
            interactive = bool(stream) and stream.isatty()
        except (AttributeError, ValueError):
            interactive = False
        if interactive:
            try:
                stream.write(
                    "\nCharlie web gateway: open this URL once to start a session.\n"
                    f"{url}\n"
                )
                stream.flush()
                return
            except (OSError, ValueError):
                logger.warning(
                    "Could not print the gateway bootstrap URL", exc_info=True
                )
        logger.info(
            "Web gateway bootstrap URL was not exposed to an operator. Set %s or pass "
            "bootstrap_token_file to a RuntimeWebGateway to retrieve it.",
            _TOKEN_FILE_ENV,
        )

    def _stream(self, handler: BaseHTTPRequestHandler, client: _SseClient) -> None:
        owned = True
        connection = getattr(handler, "connection", None)
        previous: Any = _UNSET
        if connection is not None:
            try:
                previous = connection.gettimeout()
                connection.settimeout(_SSE_WRITE_TIMEOUT_S)
            except OSError:
                previous = _UNSET
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Connection", "close")
        handler.end_headers()
        try:
            while True:
                if client.overflowed:
                    break
                try:
                    frame = client.queue.get(timeout=_SSE_KEEPALIVE_S)
                except queue.Empty:
                    frame = b": keepalive\n\n"
                handler.wfile.write(frame)
                handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            owned = False
        finally:
            self._detach_client(client)
            if owned:
                handler.close_connection = True
            if previous is not _UNSET and connection is not None:
                try:
                    connection.settimeout(previous)
                except OSError:
                    pass

    def _serve_static(self, handler: BaseHTTPRequestHandler, path: str) -> None:
        """Serve a static file, refusing anything outside the static root.

        ``_refuse`` lives on the request handler because that is where the
        response machinery is; this method only decides the path.
        """
        relative = unquote(path.lstrip("/")) or "index.html"
        if self._static_dir is None:
            handler._refuse(HTTPStatus.NOT_FOUND, "not found")
            return
        try:
            root = self._static_dir.resolve()
            target = (root / relative).resolve()
        except OSError:
            handler._refuse(HTTPStatus.NOT_FOUND, "not found")
            return
        # Both sides are resolved, so containment is a path-component question.
        # A string prefix accepts any sibling whose name begins with the static
        # root's basename (<static>_SIBLING), which served those files to any
        # unauthenticated caller.
        try:
            target.relative_to(root)
        except ValueError:
            handler._refuse(HTTPStatus.FORBIDDEN, "not found")
            return
        if not target.is_file():
            handler._refuse(HTTPStatus.NOT_FOUND, "not found")
            return
        try:
            body = target.read_bytes()
        except OSError:
            handler._refuse(HTTPStatus.NOT_FOUND, "not found")
            return
        content_type = mimetypes.guess_type(target.name)[0] or _DEFAULT_CONTENT_TYPE
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Content-Type-Options", "nosniff")
        if content_type.split(";", 1)[0].strip().lower() in _HTML_TYPES:
            handler.send_header("Content-Security-Policy", _SHELL_CSP)
        handler.end_headers()
        handler.wfile.write(body)

    def close(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                logger.warning("Web gateway server close failed", exc_info=True)
        if thread is not None:
            thread.join(timeout=2)


class _LengthRejected(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class _CommandRejected(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
