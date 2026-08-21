from __future__ import annotations
import asyncio, json, os, socket, ssl, sys, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from charlie.core import Brain
from charlie.config import config
from charlie.utils import build_auth_headers
import httpx

async def controls():
    out={}
    async with httpx.AsyncClient(timeout=20,trust_env=config.llm_trust_env) as client:
        for name,url in {'kilo':config.llm_url.rstrip('/')+'/models','example':'https://example.com/'}.items():
            try: out[name]={'status':(await client.get(url,headers=build_auth_headers(config.llm_key) if name=='kilo' else {})).status_code}
            except Exception as exc: out[name]={'type':type(exc).__name__,'repr':repr(exc),'cause':repr(exc.__cause__)}
    try:
        context=ssl.create_default_context(); reader,writer=await asyncio.open_connection('api.kilo.ai',443,ssl=context,server_hostname='api.kilo.ai'); writer.close(); await writer.wait_closed(); out['raw_kilo']='PASS'
    except Exception as exc: out['raw_kilo']={'type':type(exc).__name__,'repr':repr(exc),'cause':repr(exc.__cause__)}
    return out

original=Brain._stream_completion
async def traced(self,payload,generation):
    try: return await original(self,payload,generation)
    except Exception as exc:
        cause=exc.__cause__
        print(json.dumps({'pid':os.getpid(),'thread':threading.current_thread().name,'loop':id(asyncio.get_running_loop()),'exception':repr(exc),'cause':repr(cause),'cause_cause':repr(cause.__cause__) if cause else '','controls':await controls()},indent=2),flush=True)
        raise
Brain._stream_completion=traced
from main import main
asyncio.run(main())
