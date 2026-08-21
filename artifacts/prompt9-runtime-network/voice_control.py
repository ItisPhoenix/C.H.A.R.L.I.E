from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from charlie.runtime import configure
configure()
from charlie.config import config
from charlie.voice import VoiceEngine
from charlie.utils import build_auth_headers
import httpx

async def main():
    payload={"model":config.llm_model,"messages":[{"role":"user","content":"Reply exactly VOICE_OK"}],"max_tokens":8,"temperature":0}
    voice=None; rows=[]
    try:
        voice=VoiceEngine(config, lambda _: None, lambda: None, lambda: None)
        voice.start()
        async with httpx.AsyncClient(base_url=config.llm_url,headers=build_auth_headers(config.llm_key),timeout=60,trust_env=config.llm_trust_env) as client:
            for _ in range(5):
                try: rows.append((await client.post('chat/completions',json=payload)).status_code)
                except Exception as exc: rows.append({'type':type(exc).__name__,'repr':repr(exc),'cause':repr(exc.__cause__)})
    except Exception as exc:
        rows.append({'voice_setup':type(exc).__name__,'repr':repr(exc),'cause':repr(exc.__cause__)})
    finally:
        if voice:
            try: voice.stop()
            except Exception: pass
    print(json.dumps({'requests':rows},indent=2))

asyncio.run(main())
