from __future__ import annotations

import asyncio
import json
import os
import ssl
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from charlie.runtime import configure

configure()
from charlie.config import config
from charlie.utils import build_auth_headers


async def https(url: str, headers: dict | None = None) -> dict:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=20, headers=headers or {}, trust_env=config.llm_trust_env) as client:
            response = await client.get(url)
            return {"status": response.status_code}
    except Exception as exc:
        cause = exc.__cause__
        return {"error": type(exc).__name__, "repr": repr(exc), "cause": repr(cause)}


async def raw_socket(host: str) -> dict:
    try:
        context = ssl.create_default_context()
        reader, writer = await asyncio.open_connection(host, 443, ssl=context, server_hostname=host)
        writer.close()
        await writer.wait_closed()
        return {"dns_tcp_tls": "PASS"}
    except Exception as exc:
        return {"dns_tcp_tls": type(exc).__name__, "repr": repr(exc), "cause": repr(exc.__cause__)}


async def probe() -> dict:
    import httpx
    headers = build_auth_headers(config.llm_key)
    payload = {"model": config.llm_model, "messages": [{"role": "user", "content": "Reply exactly STAGE_OK"}], "max_tokens": 8, "temperature": 0}
    try:
        async with httpx.AsyncClient(base_url=config.llm_url, headers=headers, timeout=60, trust_env=config.llm_trust_env) as client:
            response = await client.post("chat/completions", json=payload)
            return {"httpx": response.status_code}
    except Exception as exc:
        cause = exc.__cause__
        return {"httpx": type(exc).__name__, "repr": repr(exc), "cause": repr(cause), "cause_cause": repr(cause.__cause__) if cause else ""}


async def zmq_roundtrip() -> dict:
    import zmq
    import zmq.asyncio
    ctx = zmq.asyncio.Context()
    a = ctx.socket(zmq.PAIR)
    b = ctx.socket(zmq.PAIR)
    endpoint = f"inproc://prompt935-{os.getpid()}"
    try:
        a.bind(endpoint)
        b.connect(endpoint)
        await asyncio.sleep(0.05)
        await a.send_string("ZMQ_OK")
        value = await b.recv_string()
        return {"zmq": value, "loop": type(asyncio.get_running_loop()).__name__}
    except Exception as exc:
        return {"zmq": type(exc).__name__, "repr": repr(exc), "cause": repr(exc.__cause__)}
    finally:
        a.close(linger=0); b.close(linger=0); ctx.term()


async def main() -> None:
    stages = []
    stages.append({"stage": "config_httpx", "result": await probe()})
    import main as main_module  # noqa: F401
    stages.append({"stage": "import_main", "result": await probe()})
    from charlie.session_store import SessionStore
    from charlie.audit_store import AuditStore
    temp = Path(tempfile.gettempdir()) / f"charlie-prompt935-{os.getpid()}"
    temp.mkdir(exist_ok=True)
    session = SessionStore(str(temp / "sessions.db")); audit = AuditStore(str(temp / "audit.db"))
    stages.append({"stage": "session_audit", "result": await probe()})
    from charlie.memory_store import MemoryStore
    memory = MemoryStore(config)
    stages.append({"stage": "memory_store", "available": memory.is_available, "result": await probe()})
    from charlie.ipc import EventBus
    stages.append({"stage": "eventbus_import", "result": await probe(), "zmq": await zmq_roundtrip()})
    from charlie.core import Brain
    brain = Brain(config, register_panic_hotkey=False)
    stages.append({"stage": "brain", "result": await probe(), "brain_client_id": id(brain.client), "loop_id": id(asyncio.get_running_loop()), "thread": threading.current_thread().name})
    await brain.close(); session.close(); audit.close()
    generic = {"kilo": await https(config.llm_url.rstrip('/') + '/models', build_auth_headers(config.llm_key)), "example": await https('https://example.com/'), "raw_kilo": await raw_socket('api.kilo.ai')}
    print(json.dumps({"policy": type(asyncio.get_event_loop_policy()).__name__, "loop": type(asyncio.get_running_loop()).__name__, "pid": os.getpid(), "thread": threading.current_thread().name, "stages": stages, "generic": generic}, indent=2))


asyncio.run(main())
