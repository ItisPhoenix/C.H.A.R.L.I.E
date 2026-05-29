"""
charlie/dashboard/main.py

FastAPI dashboard server — serves vanilla JS/CSS SPA, proxies API to ControlServer.
"""

import os
from typing import Optional

from fastapi import FastAPI, Response, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
import httpx
import uvicorn

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

async def get_client() -> httpx.AsyncClient:
    global _proxy_client
    if _proxy_client is None or _proxy_client.is_closed:
        _proxy_client = httpx.AsyncClient(timeout=10.0)
    return _proxy_client


async def _proxy(request: Request, path: str) -> Response:
    """Forward request to ControlServer preserving method and body."""
    client = await get_client()
    url = f"{CONTROL_URL}/api/{path}"
    try:
        body = await request.body()
        resp = await client.request(
            method=request.method,
            url=url,
            content=body,
            headers={"Content-Type": request.headers.get("content-type", "application/json")},
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


# ── Landing page redirect for root ────────────────────────────────────────────
@app.get("/index.html")
async def index_html():
    return FileResponse(os.path.join(_dashboard_dir, "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3000)
