from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from charlie.runtime import configure
configure()
from charlie.config import config
from charlie.ipc import EventBus
from charlie.utils import build_auth_headers
import httpx

async def main():
    payload={"model":config.llm_model,"messages":[{"role":"user","content":"Reply exactly ACTIVE_ZMQ_OK"}],"max_tokens":8,"temperature":0}
    rows=[]
    async with EventBus(pub_port=5655,pull_port=5656,is_producer=True) as bus:
        async with httpx.AsyncClient(base_url=config.llm_url,headers=build_auth_headers(config.llm_key),timeout=60,trust_env=config.llm_trust_env) as client:
            for _ in range(5):
                try:
                    r=await client.post('chat/completions',json=payload); rows.append(r.status_code)
                except Exception as exc: rows.append({"type":type(exc).__name__,"repr":repr(exc),"cause":repr(exc.__cause__)})
    print(json.dumps({'policy':type(asyncio.get_event_loop_policy()).__name__,'loop':type(asyncio.get_running_loop()).__name__,'requests':rows},indent=2))

asyncio.run(main())
