"""Shared SSE streaming parser for OpenAI-compatible chat completions.

Centralizes the repetitive SSE line-parsing logic used across Brain._stream_completion
and Brain.chat_stream follow-up loops.
"""

import json
import logging
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger("charlie.streaming")


async def parse_sse_stream(
    response: Any,
    generation: int,
    current_generation: int,
    on_content: Optional[Callable[[str], None]] = None,
) -> Tuple[str, Dict[int, Dict[str, str]], bool]:
    """Parse an SSE stream from an OpenAI-compatible chat completions endpoint.

    Args:
        response: An httpx async response with aiter_lines().
        generation: The expected generation number (for cancel detection).
        current_generation: Brain's current _chat_generation value.
        on_content: Optional callback invoked with each content token.
            If None, content is only accumulated (for _stream_completion).
            If provided, content is accumulated AND passed to the callback
            (for chat_stream follow-ups that yield tokens).

    Returns:
        (accumulated_text, tc_by_index, was_cancelled)

    The caller uses tc_by_index to build tool_calls via _collect_tool_calls().
    """
    accumulated = ""
    tc_by_index: Dict[int, Dict[str, str]] = {}
    cancelled = False

    async for line in response.aiter_lines():
        if current_generation != generation:
            cancelled = True
            break
        if not line.startswith("data: "):
            continue
        if line.strip() == "data: [DONE]":
            break
        try:
            chunk = json.loads(line[6:])
            delta = chunk.get("choices", [{}])[0].get("delta", {})
            content = delta.get("content", "")
            if content:
                accumulated += content
                if on_content is not None:
                    on_content(content)
            for tc in delta.get("tool_calls", []):
                idx = tc.get("index", 0)
                if idx not in tc_by_index:
                    tc_by_index[idx] = {
                        "id": "",
                        "name": "",
                        "arguments": "",
                    }
                entry = tc_by_index[idx]
                if tc.get("id"):
                    entry["id"] = tc["id"]
                func = tc.get("function", {})
                if func.get("name"):
                    entry["name"] = func["name"]
                if func.get("arguments"):
                    entry["arguments"] += func["arguments"]
        except Exception:
            continue

    return accumulated, tc_by_index, cancelled
