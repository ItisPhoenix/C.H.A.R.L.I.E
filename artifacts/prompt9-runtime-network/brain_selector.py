from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from charlie.runtime import configure

configure()
from charlie.config import config
from charlie.core import Brain


async def main() -> None:
    brain = Brain(config, register_panic_hotkey=False)
    info = {"policy": type(asyncio.get_event_loop_policy()).__name__, "loop": type(asyncio.get_running_loop()).__name__, "loop_id": id(asyncio.get_running_loop()), "client_id": id(brain.client), "pid": os.getpid(), "thread": threading.current_thread().name, "client_requests": [], "chat_stream": []}
    try:
        for _ in range(5):
            response = await brain.client.post("chat/completions", json={"model": config.llm_model, "messages": [{"role": "user", "content": "Reply exactly SELECTOR_CLIENT_OK"}], "max_tokens": 8, "temperature": 0})
            info["client_requests"].append(response.status_code)
        for _ in range(5):
            text = ""
            async for chunk in brain.chat_stream("Reply exactly SELECTOR_CHAT_OK", platform="web"):
                text += chunk
            info["chat_stream"].append({"status": "PASS", "length": len(text)})
    except Exception as exc:
        info["error"] = {"type": type(exc).__name__, "repr": repr(exc), "cause": repr(exc.__cause__), "context": repr(exc.__context__)}
    finally:
        await brain.close()
    print(json.dumps(info, indent=2))


asyncio.run(main())
