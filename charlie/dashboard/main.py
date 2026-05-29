"""
charlie/dashboard/main.py

FastAPI dashboard server — serves vanilla JS/CSS SPA, proxies API + WebSocket to ControlServer.
All browser traffic routes through here so the ControlServer token stays server-side.
"""

import asyncio
import logging
import os
from typing import Optional

from fastapi import FastAPI, Response, HTTPException, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
import httpx
import uvicorn
import websockets

logger = logging.getLogger("charlie.dashboard")

app = FastAPI(title="CHARLIE Dashboard", version="1.0.0")

# ── CORS ─────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
        # Local network access (phone/tablet on same WiFi)
        "http://192.168.0.1:3000",
        "http://192.168.1.1:3000",
    ],
    allow_origin_regex=r"https?://192\.168\.\d+\.\d+:\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static files ──────────────────────────────────────────────────────────────
_dashboard_dir = os.path.join(os.path.dirname(__file__))
app.mount("/static", StaticFiles(directory=_dashboard_dir), name="static")

# ── Root → index.html ─────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return RedirectResponse(url="/static/index.html")

# ── API proxy to ControlServer on 8090 ────────────────────────────────────────
CONTROL_URL = "http://localhost:8090"

_proxy_client: Optional[httpx.AsyncClient] = None
_server_token: Optional[str] = None


async def get_client() -> httpx.AsyncClient:
    global _proxy_client
    if _proxy_client is None or _proxy_client.is_closed:
        _proxy_client = httpx.AsyncClient(timeout=10.0)
    return _proxy_client


@app.on_event("startup")
async def _fetch_server_token():
    """Fetch auth token from ControlServer on startup. Retries if ControlServer isn't ready yet."""
    global _server_token
    for attempt in range(5):
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{CONTROL_URL}/api/token")
                if resp.status_code == 200:
                    _server_token = resp.json().get("token")
                    if _server_token:
                        logger.info("dashboard_token_acquired")
                        return
        except Exception:
            pass
        logger.warning(f"dashboard_token_retry | attempt={attempt + 1}")
        await asyncio.sleep(2)
    logger.error("dashboard_token_failed | proxy will return 503 for API requests")


async def _proxy(request: Request, path: str) -> Response:
    """Forward request to ControlServer injecting server-side token."""
    if not _server_token:
        raise HTTPException(status_code=503, detail="ControlServer token not available")
    client = await get_client()
    url = f"{CONTROL_URL}/api/{path}"
    try:
        body = await request.body()
        fwd_headers = {
            "Content-Type": request.headers.get("content-type", "application/json"),
            "X-Control-Token": _server_token,
        }
        resp = await client.request(
            method=request.method,
            url=url,
            content=body,
            headers=fwd_headers,
        )
        return Response(content=resp.content, media_type="application/json", status_code=resp.status_code)
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="ControlServer unavailable")
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── Generic proxy (catch-all for /api/*) ──────────────────────────────────────
@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_api(request: Request, path: str):
    """Proxy all /api/* requests to ControlServer."""
    return await _proxy(request, path)


# ── Chat history + message ─────────────────────────────────────────────────────
@app.get("/api/chat/history")
async def chat_history(request: Request):
    return await _proxy(request, "chat/history")


@app.post("/api/chat/message")
async def chat_message(request: Request):
    return await _proxy(request, "chat/message")


@app.post("/api/chat/send")
async def chat_send(request: Request):
    return await _proxy(request, "chat/send")


@app.get("/api/status")
async def status(request: Request):
    return await _proxy(request, "status")


# ── WebSocket proxy to ControlServer ──────────────────────────────────────────
@app.websocket("/ws/events")
async def websocket_proxy(ws: WebSocket):
    """Proxy WebSocket connections to ControlServer. Token stays server-side."""
    await ws.accept()
    if not _server_token:
        await ws.close(code=1013, reason="ControlServer token not available")
        return

    try:
        async with websockets.connect(
            f"{CONTROL_URL}/ws/events?token={_server_token}",
            ping_interval=20,
            ping_timeout=10,
        ) as backend_ws:

            async def forward_to_backend():
                try:
                    while True:
                        data = await ws.receive_text()
                        await backend_ws.send(data)
                except Exception:
                    pass

            async def forward_to_client():
                try:
                    async for message in backend_ws:
                        await ws.send_text(message)
                except Exception:
                    pass

            await asyncio.gather(
                forward_to_backend(),
                forward_to_client(),
                return_exceptions=True,
            )
    except Exception as e:
        logger.error(f"ws_proxy_error | {e}")
    finally:
        try:
            await ws.close()
        except Exception:
            pass


# ── Landing page redirect for root ────────────────────────────────────────────
@app.get("/index.html")
async def index_html():
    return FileResponse(os.path.join(_dashboard_dir, "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3005)
