"""
charlie/dashboard/main.py — FastAPI reverse-proxy for the Charlie Dashboard.

Sits between the Next.js dashboard (browser on :3000) and the internal
ControlServer (aiohttp on :8090).

- Proxies every ``/api/*`` REST request to the ControlServer, injecting the
  control token so the browser never sees it.
- WebSocket connections go directly from the browser to :8090 (localhost bypass
  in the control server's auth middleware handles this).

Req 6.3 / 6.4 — all browser REST traffic routes through this proxy.
"""
from __future__ import annotations

import logging
import asyncio
import os

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

logger = logging.getLogger("DashboardProxy")

# ── Configuration ────────────────────────────────────────────────────────────
CONTROL_HOST = os.getenv("CHARLIE_CONTROL_HOST", "127.0.0.1")
CONTROL_PORT = int(os.getenv("CHARLIE_CONTROL_PORT", "8090"))
CONTROL_BASE = f"http://{CONTROL_HOST}:{CONTROL_PORT}"

# ── FastAPI app ──────────────────────────────────────────────────────────────
app = FastAPI(title="Charlie Dashboard Proxy", docs_url=None, redoc_url=None)

# Reusable async HTTP client (connection-pooled).
_client: httpx.AsyncClient | None = None
_control_token: str = ""


async def _fetch_token() -> str:
    """Fetch the control server token from the unauthenticated /api/token endpoint."""
    global _control_token
    if _control_token:
        return _control_token
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            resp = await c.get(f"{CONTROL_BASE}/api/token")
            if resp.status_code == 200:
                _control_token = resp.json().get("token", "")
                logger.info("control_token_fetched")
    except Exception as exc:
        logger.warning("control_token_fetch_failed | error=%s", exc)
    return _control_token


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=CONTROL_BASE,
            timeout=httpx.Timeout(30.0, connect=5.0),
            follow_redirects=True,
        )
    return _client


# ── Startup: fetch control token ─────────────────────────────────────────────
@app.on_event("startup")
async def _startup():
    await _fetch_token()


# ── Health ───────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "proxy": True, "backend": CONTROL_BASE}


# ── CORS headers ─────────────────────────────────────────────────────────────
_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Allow-Credentials": "true",
}


# ── Catch-all proxy for /api/* ───────────────────────────────────────────────
@app.api_route(
    "/api/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def proxy_api(path: str, request: Request) -> Response:
    """Forward every /api/* request to the ControlServer."""
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=_CORS_HEADERS)

    client = _get_client()
    url = f"/api/{path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"

    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in ("host", "connection", "keep-alive")
    }
    token = _control_token or await _fetch_token()
    if token:
        headers["X-Control-Token"] = token
    body = await request.body()

    try:
        upstream = await client.request(
            method=request.method,
            url=url,
            headers=headers,
            content=body,
        )
        resp_headers = {
            k: v
            for k, v in upstream.headers.items()
            if k.lower()
            not in ("transfer-encoding", "connection", "keep-alive", "content-encoding")
        }
        resp_headers.update(_CORS_HEADERS)
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=resp_headers,
        )
    except httpx.ConnectError:
        return JSONResponse(
            {"error": "control_server_unavailable", "backend": CONTROL_BASE},
            status_code=502,
            headers=_CORS_HEADERS,
        )
    except Exception as exc:
        logger.error("proxy_error | path=%s | error=%s", path, exc)
        return JSONResponse({"error": str(exc)}, status_code=502, headers=_CORS_HEADERS)


# ── WebSocket proxy for /ws/events ──────────────────────────────────────────
@app.websocket("/ws/events")
async def proxy_ws(websocket):
    """Proxy the dashboard WebSocket to the ControlServer.

    Uses ``websockets`` library for the upstream connection.
    """
    try:
        from starlette.websockets import WebSocketState

        await websocket.accept()

        import websockets

        token = _control_token or await _fetch_token()
        upstream_url = f"ws://{CONTROL_HOST}:{CONTROL_PORT}/ws/events"
        if token:
            upstream_url = f"{upstream_url}?token={token}"
        async with websockets.connect(upstream_url) as upstream:

            async def forward_to_upstream():
                """Browser → ControlServer."""
                try:
                    while True:
                        data = await websocket.receive_text()
                        await upstream.send(data)
                except Exception:
                    pass

            async def forward_to_browser():
                """ControlServer → Browser."""
                try:
                    async for msg in upstream:
                        if websocket.client_state == WebSocketState.CONNECTED:
                            await websocket.send_text(msg)
                        else:
                            break
                except Exception:
                    pass

            await asyncio.gather(
                forward_to_upstream(),
                forward_to_browser(),
                return_exceptions=True,
            )
    except ImportError:
        logger.error("ws_proxy_requires_websockets_package")
        await websocket.close(code=1013, reason="proxy dependency missing")
    except Exception as exc:
        logger.error("ws_proxy_error | error=%s", exc)
        try:
            await websocket.close(code=1011, reason=str(exc))
        except Exception:
            pass


# ── Shutdown hook ────────────────────────────────────────────────────────────
@app.on_event("shutdown")
async def _shutdown():
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None
