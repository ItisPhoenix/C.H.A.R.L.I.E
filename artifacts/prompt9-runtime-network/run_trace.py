from __future__ import annotations
import asyncio, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from charlie.core import Brain
original = Brain._stream_completion
async def traced(self, payload, generation):
    try:
        return await original(self, payload, generation)
    except Exception as exc:
        print({'trace_pid':os.getpid(),'loop':id(asyncio.get_running_loop()),'thread':__import__('threading').current_thread().name,'exception':repr(exc),'cause':repr(exc.__cause__),'context':repr(exc.__context__)}, flush=True)
        cause=exc.__cause__
        if cause:
            print({'cause_cause':repr(cause.__cause__),'cause_context':repr(cause.__context__)}, flush=True)
        raise
Brain._stream_completion = traced
from main import main
asyncio.run(main())
