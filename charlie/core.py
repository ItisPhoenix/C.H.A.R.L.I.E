import logging
import re
from typing import TYPE_CHECKING, AsyncGenerator, Optional

import httpx
import json

logger = logging.getLogger("charlie.core")

if TYPE_CHECKING:
    from charlie.config import Config

# Shared thinking-tag/reasoning patterns for output cleanups
_REASONING_RE = re.compile(
    r'(?:'
    r'(?:I\s+(?:should|need|will|can|must|have\s+to)\s+[^.!?]{1,60}?[.!?]\s*)'
    r'|(?:Let\s+me\s+[^.!?]{1,60}?[.!?]\s*)'
    r'|(?:First(?:ly)?[,:]?\s+[^.!?]{1,40}?[.!?]\s*)'
    r'|(?:Based\s+on\s+(?:my\s+)?(?:research|analysis|data|experience|knowledge)[,:]?\s*)'
    r'|(?:Searching\s+for\s+[^.!?]{1,40}?[.!?]\s*)'
    r"|(?:I'll\s+[^.!?]{1,40}?[.!?]\s*)"
    r"|(?:Here(?:'s| is)\s+(?:what|the)[^.!?]{1,40}?[.!?]\s*)"
    r'|(?:To answer that,\s*)'
    r'|(?:The user is\s+(?:asking|looking)[^.!?]{1,40}?[.!?]\s*)'
    r')+', re.IGNORECASE
)


def strip_internal_reasoning(text: str) -> str:
    """Remove model reasoning/thinking tags before user-facing output."""
    text = re.sub(r'  \u601d\u8003.*?  \u601d\u8003\u7ed3\u675f', '', text, flags=re.DOTALL)
    text = re.sub(r'<(thought|thinking|longcat_tool_call)>.*?</\1>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = _REASONING_RE.sub('', text)
    return text.strip()


class Brain:
    """Minimal voice-first brain: single explicit backend."""

    def __init__(self, config: "Config", on_thought_callback: Optional[callable] = None):
        self.config = config
        self.on_thought_callback = on_thought_callback
        self.client = httpx.AsyncClient(
            base_url=config.llm_url,
            headers={"Authorization": f"Bearer {config.llm_key}"},
            timeout=60.0,
        )

    async def chat_stream(self, user_input: str) -> AsyncGenerator[str, None]:
        system_msg = (
            "You are Charlie, a concise voice assistant. "
            "Reply in short spoken-friendly sentences. "
            "No markdown, no lists, no emojis."
        )
        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_input},
        ]
        payload = {
            "model": self.config.llm_model,
            "messages": messages,
            "temperature": 0.3,
            "stream": True,
        }
        if getattr(self.config, "llm_disable_reasoning", False):
            payload["reasoning"] = {"effort": "none"}

        try:
            async with self.client.stream(
                "POST", "chat/completions", json=payload
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    if line.strip() == "data: [DONE]":
                        break
                    try:
                        chunk = json.loads(line[6:])
                        content = (
                            chunk.get("choices", [{}])[0]
                            .get("delta", {})
                            .get("content", "")
                        )
                        if content:
                            yield content
                    except Exception:
                        continue
        except Exception as exc:
            logger.warning(f"LLM stream error: {exc}")
            raise

    async def close(self):
        await self.client.aclose()
