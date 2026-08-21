from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import threading
from importlib.metadata import version
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def package_version(name: str) -> str:
    try:
        return version(name)
    except Exception:
        return "not-installed"


def set_mode(mode: str) -> None:
    if mode == "selector":
        from charlie.runtime import configure
        configure()
    elif mode == "proactor":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


async def request(client, payload: dict) -> dict:
    try:
        response = await client.post("chat/completions", json=payload)
        return {"result": "PASS" if response.is_success else "HTTP_ERROR", "status": response.status_code}
    except Exception as exc:
        cause = exc.__cause__
        return {
            "result": type(exc).__name__,
            "exception": repr(exc),
            "cause": repr(cause),
            "cause_cause": repr(cause.__cause__) if cause else "",
            "cause_context": repr(cause.__context__) if cause else "",
        }


async def five_requests(config, headers) -> list[dict]:
    import httpx
    payload = {"model": config.llm_model, "messages": [{"role": "user", "content": "Reply exactly LOOP_OK"}], "max_tokens": 8, "temperature": 0}
    async with httpx.AsyncClient(base_url=config.llm_url, headers=headers, timeout=60.0, trust_env=config.llm_trust_env) as client:
        return [await request(client, payload) for _ in range(5)]


def loop_info(mode: str) -> dict:
    policy = asyncio.get_event_loop_policy()
    loop = asyncio.new_event_loop()
    try:
        return {"mode": mode, "policy": type(policy).__name__, "loop": type(loop).__name__, "thread": threading.current_thread().name, "pid": os.getpid()}
    finally:
        loop.close()


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "default"
    set_mode(mode)
    from charlie.config import config
    from charlie.utils import build_auth_headers
    import httpx
    result = loop_info(mode)
    result.update({"python": sys.executable, "httpx": httpx.__version__, "httpcore": package_version("httpcore"), "pyzmq": package_version("pyzmq"), "tornado": package_version("tornado")})
    result["requests"] = asyncio.run(five_requests(config, build_auth_headers(config.llm_key)))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
