from __future__ import annotations
import asyncio, json, os, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from charlie.config import config
from charlie.utils import build_auth_headers
import httpx

async def main():
    entry=str(Path(__file__).resolve().parents[2] / 'charlie' / 'web_server_entry.py')
    env=os.environ.copy(); env['CHARLIE_LAUNCH_ID']='prompt935-web-child'
    proc=subprocess.Popen([sys.executable, entry], cwd=str(Path(entry).parents[1].parent), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await asyncio.sleep(3)
    rows=[]; payload={"model":config.llm_model,"messages":[{"role":"user","content":"Reply exactly WEB_CHILD_OK"}],"max_tokens":8,"temperature":0}
    try:
        async with httpx.AsyncClient(base_url=config.llm_url,headers=build_auth_headers(config.llm_key),timeout=60,trust_env=config.llm_trust_env) as client:
            for _ in range(5):
                try: rows.append((await client.post('chat/completions',json=payload)).status_code)
                except Exception as exc: rows.append({'type':type(exc).__name__,'repr':repr(exc),'cause':repr(exc.__cause__)})
    finally:
        proc.terminate(); proc.wait(timeout=10)
    print(json.dumps({'rows':rows},indent=2))

asyncio.run(main())
