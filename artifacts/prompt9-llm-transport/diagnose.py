from __future__ import annotations

import asyncio
import json
import os
import socket
import ssl
import sys
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import httpx
from charlie.config import config
from charlie.core import Brain
from charlie.utils import build_auth_headers


PAYLOAD = {
    "model": config.llm_model,
    "messages": [{"role": "user", "content": "Reply with exactly TRANSPORT_OK"}],
    "max_tokens": 8,
    "temperature": 0,
}


def exc_info(exc: BaseException) -> dict[str, str]:
    def describe(value: BaseException | None) -> str:
        return f"{type(value).__name__}: {value}" if value else ""

    return {
        "exception": describe(exc),
        "cause": describe(exc.__cause__),
        "context": describe(exc.__context__),
    }


async def request(client: httpx.AsyncClient) -> dict:
    try:
        response = await client.post("chat/completions", json=PAYLOAD)
        return {"result": "PASS" if response.is_success else "HTTP_ERROR", "status": response.status_code}
    except Exception as exc:  # diagnostic boundary
        return {"result": type(exc).__name__, **exc_info(exc)}


async def matrix() -> None:
    headers = build_auth_headers(config.llm_key)
    modes = {
        "A_brain_settings": config.llm_trust_env,
        "B_trust_env_false": False,
        "C_trust_env_true": True,
    }
    results = {}
    for name, trust_env in modes.items():
        async with httpx.AsyncClient(
            base_url=config.llm_url,
            headers=headers,
            timeout=60.0,
            trust_env=trust_env,
        ) as client:
            results[name] = await request(client)
    try:
        response = httpx.post(
            config.llm_url.rstrip("/") + "/chat/completions",
            headers=headers,
            json=PAYLOAD,
            timeout=60.0,
        )
        results["D_top_level_default"] = {"result": "PASS" if response.is_success else "HTTP_ERROR", "status": response.status_code}
    except Exception as exc:  # diagnostic boundary
        results["D_top_level_default"] = {"result": type(exc).__name__, **exc_info(exc)}
    print(json.dumps({"effective": effective(), "matrix": results}, indent=2))


async def brain_client(repetitions: int = 5) -> None:
    brain = Brain(config, register_panic_hotkey=False)
    results = []
    try:
        for _ in range(repetitions):
            results.append(await request(brain.client))
    finally:
        await brain.close()
    print(json.dumps({"brain_client_repetitions": results}, indent=2))


async def stream_request(client: httpx.AsyncClient) -> dict:
    try:
        stream_payload = dict(PAYLOAD, stream=True)
        async with client.stream("POST", "chat/completions", json=stream_payload) as response:
            response.raise_for_status()
            first = await response.aiter_bytes().__anext__()
        return {"result": "PASS", "status": response.status_code, "first_bytes": len(first)}
    except Exception as exc:  # diagnostic boundary
        return {"result": type(exc).__name__, **exc_info(exc)}


async def stream_matrix() -> None:
    headers = build_auth_headers(config.llm_key)
    results = {}
    for name, trust_env in {
        "A_brain_settings": config.llm_trust_env,
        "B_trust_env_false": False,
        "C_trust_env_true": True,
    }.items():
        async with httpx.AsyncClient(
            base_url=config.llm_url,
            headers=headers,
            timeout=60.0,
            trust_env=trust_env,
        ) as client:
            results[name] = await stream_request(client)
    brain = Brain(config, register_panic_hotkey=False)
    try:
        results["D_real_brain_stream"] = await stream_request(brain.client)
    finally:
        await brain.close()
    print(json.dumps({"stream_matrix": results}, indent=2))


async def tool_payload() -> None:
    brain = Brain(config, register_panic_hotkey=False)
    try:
        payload = brain._build_payload([{"role": "user", "content": "Reply with exactly TOOL_PAYLOAD_OK"}])
        result = await request(brain.client)
        stream_result = await stream_request(brain.client)
        tool_count = len(payload.get("tools", []))
        print(json.dumps({
            "tool_count": tool_count,
            "tool_choice": payload.get("tool_choice"),
            "payload_bytes": len(json.dumps(payload)),
            "non_stream_control": result,
            "stream_control_without_tools": stream_result,
        }, indent=2))
        try:
            async with brain.client.stream("POST", "chat/completions", json=payload) as response:
                response.raise_for_status()
                first = await response.aiter_bytes().__anext__()
            print(json.dumps({"stream_with_tools": {"result": "PASS", "status": response.status_code, "first_bytes": len(first)}}, indent=2))
        except Exception as exc:  # diagnostic boundary
            print(json.dumps({"stream_with_tools": {"result": type(exc).__name__, **exc_info(exc)}}, indent=2))
    finally:
        await brain.close()


async def brain_chat() -> None:
    brain = Brain(config, register_panic_hotkey=False)
    try:
        text = ""
        async for chunk in brain.chat_stream("Reply with exactly INTERNAL_BRAIN_OK", platform="web"):
            text += chunk
        print(json.dumps({"result": "PASS", "text_length": len(text), "text": text[:160]}, indent=2))
    except Exception as exc:  # diagnostic boundary
        print(json.dumps({"result": type(exc).__name__, **exc_info(exc)}, indent=2))
    finally:
        await brain.close()


def effective() -> dict:
    return {
        "python": sys.executable,
        "cwd": str(Path.cwd()),
        "LLM_URL": config.llm_url,
        "LLM_MODEL": config.llm_model,
        "LLM_TRUST_ENV": config.llm_trust_env,
        "NATIVE_TOOL_CALLING": getattr(config, "native_tool_calling", None),
        "httpx": httpx.__version__,
        "HTTP_PROXY_present": bool(os.getenv("HTTP_PROXY")),
        "HTTPS_PROXY_present": bool(os.getenv("HTTPS_PROXY")),
        "ALL_PROXY_present": bool(os.getenv("ALL_PROXY")),
    }


def network() -> None:
    host = urlsplit(config.llm_url).hostname or "api.kilo.ai"
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    rows = []
    for family, _, _, _, sockaddr in addresses:
        row = {"family": "IPv6" if family == socket.AF_INET6 else "IPv4", "address": sockaddr[0]}
        try:
            with socket.create_connection(sockaddr, timeout=10) as raw:
                row["tcp"] = "PASS"
                context = ssl.create_default_context()
                with context.wrap_socket(raw, server_hostname=host):
                    row["tls"] = "PASS"
        except Exception as exc:  # diagnostic boundary
            row["error"] = {"type": type(exc).__name__, "message": str(exc)}
        rows.append(row)
    print(json.dumps({"dns_host": host, "addresses": rows}, indent=2))


async def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else "matrix"
    if action == "matrix":
        await matrix()
    elif action == "stream_matrix":
        await stream_matrix()
    elif action == "tool_payload":
        await tool_payload()
    elif action == "brain_chat":
        await brain_chat()
    elif action == "brain":
        await brain_client()
    elif action == "network":
        network()
    else:
        raise SystemExit(f"unknown action: {action}")


if __name__ == "__main__":
    asyncio.run(main())
