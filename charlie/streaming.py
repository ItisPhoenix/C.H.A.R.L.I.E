"""Shared SSE streaming parser for OpenAI-compatible chat completions.

Centralizes the repetitive SSE line-parsing logic used across
Brain._stream_completion (parse_sse_stream, buffer-then-return) and
Brain.chat_stream's tool-followup rounds (stream_followup_content,
yields chunks live for low Time-To-First-Audio).
"""

import json
import logging
import re
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("charlie.streaming")


def _merge_tool_call_delta(tc: Dict[str, Any], tc_by_index: Dict[int, Dict[str, str]]) -> None:
    """Merge one streamed tool_call delta into the accumulator dict.

    Shared by parse_sse_stream and stream_followup_content so the two SSE
    parsers agree on exactly how partial tool-call JSON is stitched together.
    """
    idx = tc.get("index", 0)
    if idx not in tc_by_index:
        tc_by_index[idx] = {"id": "", "name": "", "arguments": ""}
    entry = tc_by_index[idx]
    if tc.get("id"):
        entry["id"] = tc["id"]
    func = tc.get("function", {})
    if func.get("name"):
        entry["name"] = func["name"]
    if func.get("arguments"):
        entry["arguments"] += func["arguments"]


def collect_tool_calls(tc_by_index: Dict[int, Dict[str, str]]) -> List[Dict[str, Any]]:
    """Collect parsed tool calls from an accumulated tc_by_index mapping."""
    calls: List[Dict[str, Any]] = []
    for idx in sorted(tc_by_index.keys()):
        tc = tc_by_index[idx]
        try:
            args = json.loads(tc["arguments"]) if tc["arguments"] else {}
        except json.JSONDecodeError:
            args = {}
        calls.append({"id": tc["id"], "name": tc["name"], "arguments": args})
    return calls


async def parse_sse_stream(
    response: Any,
    generation: int,
    current_generation_getter: Callable[[], int],
    on_content: Optional[Callable[[str], None]] = None,
) -> Tuple[str, Dict[int, Dict[str, str]], bool]:
    """Parse an SSE stream from an OpenAI-compatible chat completions endpoint.

    Buffers the whole response and returns once it's done -- used by
    Brain._stream_completion, whose caller doesn't need partial chunks (the
    first LLM response is yielded whole after tool-call detection, or not at
    all if tool calls were found). For the streamed follow-up replies the
    user actually hears/reads live, see stream_followup_content below.

    Args:
        response: An httpx async response with aiter_lines().
        generation: The expected generation number (for cancel detection).
        current_generation_getter: A callable returning the current generation value.
        on_content: Optional callback invoked with each content token.

    Returns:
        (accumulated_text, tc_by_index, was_cancelled)

    The caller uses tc_by_index to build tool_calls via collect_tool_calls().
    """
    accumulated = ""
    tc_by_index: Dict[int, Dict[str, str]] = {}
    cancelled = False

    async for line in response.aiter_lines():
        if current_generation_getter() != generation:
            cancelled = True
            break
        if not line.startswith("data: "):
            continue
        if line.strip() == "data: [DONE]":
            break
        try:
            chunk = json.loads(line[6:])
            if "error" in chunk:
                logger.warning("parse_sse_stream: upstream error: %s", chunk["error"])
                continue
            delta = chunk.get("choices", [{}])[0].get("delta", {})
            content = delta.get("content", "")
            if content:
                accumulated += content
                if on_content is not None:
                    on_content(content)
            for tc in delta.get("tool_calls", []):
                _merge_tool_call_delta(tc, tc_by_index)
        except Exception as exc:
            logger.warning("parse_sse_stream: failed to parse SSE line %r: %s", line[:200], exc)
            continue

    return accumulated, tc_by_index, cancelled


class FollowupStreamState:
    """Mutable accumulator for stream_followup_content.

    Async generators can't surface extra return values through `async for`,
    so the caller passes this box in and reads `.accumulated` / `.tc_by_index`
    / `.cancelled` once the generator has been fully consumed.
    """

    def __init__(self) -> None:
        self.accumulated: str = ""
        self.tc_by_index: Dict[int, Dict[str, str]] = {}
        self.cancelled: bool = False


async def stream_followup_content(
    response: Any,
    generation: int,
    current_generation_getter: Callable[[], int],
    state: FollowupStreamState,
) -> AsyncGenerator[str, None]:
    """Async-generator SSE parser for tool-followup completions.

    Unlike parse_sse_stream (which buffers and returns once), this yields
    each raw content chunk as it arrives so the caller can push it through a
    TextStreamFilter and yield immediately -- required to keep the streamed
    reply's Time-To-First-Audio low. Tool-call deltas and the accumulated
    text land on `state`; `state.cancelled` is set if the chat generation
    changes mid-stream (barge-in) instead of raising.
    """
    async for line in response.aiter_lines():
        if current_generation_getter() != generation:
            state.cancelled = True
            return
        if not line.startswith("data: "):
            continue
        if line.strip() == "data: [DONE]":
            return
        try:
            chunk = json.loads(line[6:])
            if "error" in chunk:
                logger.warning("stream_followup_content: upstream error: %s", chunk["error"])
                continue
            delta = chunk.get("choices", [{}])[0].get("delta", {})
            content = delta.get("content", "")
            if content:
                state.accumulated += content
                yield content
            for tc in delta.get("tool_calls", []):
                _merge_tool_call_delta(tc, state.tc_by_index)
        except Exception as exc:
            logger.warning("stream_followup_content: failed to parse SSE line %r: %s", line[:200], exc)
            continue


class TextStreamFilter:
    """Filter stream to block <think>...</think> blocks and lines starting with TOOL:.

    Some models tag each reasoning block with a per-turn id --
    <think:6124c78e>...</think:6124c78e> -- instead of the plain <think>...</think>
    pair. An exact-string match on "<think>" doesn't catch that variant, so the
    whole reasoning block used to leak straight into the visible reply. The
    opening-tag regex below matches both forms and remembers which closing tag
    to look for.
    """
    _THINK_OPEN_RE = re.compile(r"<think(:[\w-]+)?>")
    # Bound on how much unmatched "<think" tail to hold back while waiting for
    # more chunks (covers the id suffix + closing '>' arriving split across chunks).
    _MAX_OPEN_TAG_LOOKAHEAD = 40

    def __init__(self):
        self.buffer = ""
        self.in_thinking = False
        self.think_close_tag = "</think>"
        self.in_tool_line = False

    def push(self, chunk: str) -> str:
        self.buffer += chunk
        # Strip search result tags dynamically
        self.buffer = re.sub(
            r"\[SEARCH RESULTS.*?\]",
            "",
            self.buffer,
            flags=re.IGNORECASE,
        )
        output = ""

        while True:
            if self.in_thinking:
                end_idx = self.buffer.find(self.think_close_tag)
                if end_idx != -1:
                    self.buffer = self.buffer[end_idx + len(self.think_close_tag):]
                    self.in_thinking = False
                    continue
                else:
                    keep_len = len(self.think_close_tag) - 1
                    if len(self.buffer) > keep_len:
                        self.buffer = self.buffer[-keep_len:]
                    break

            if self.in_tool_line:
                newline_idx = self.buffer.find("\n")
                if newline_idx != -1:
                    self.buffer = self.buffer[newline_idx + 1:]
                    self.in_tool_line = False
                    continue
                else:
                    self.buffer = ""
                    break

            # Find tags in buffer
            think_match = self._THINK_OPEN_RE.search(self.buffer)
            tool_idx = self.buffer.find("TOOL:")

            candidates = []
            if think_match:
                candidates.append((think_match.start(), "think", think_match))
            if tool_idx != -1:
                candidates.append((tool_idx, "tool", None))

            if not candidates:
                # No confirmed tag yet. An in-progress "<think" (with its id
                # suffix or closing '>' still to arrive) could still be a
                # partial match -- hold back a bounded tail rather than
                # requiring an exact fixed-length prefix like plain "<think>".
                open_idx = self.buffer.rfind("<think")
                if open_idx != -1 and open_idx >= len(self.buffer) - self._MAX_OPEN_TAG_LOOKAHEAD:
                    if open_idx > 0:
                        output += self.buffer[:open_idx]
                    self.buffer = self.buffer[open_idx:]
                    break

                max_partial = 0
                for pattern in ("<think", "TOOL:"):
                    for i in range(1, len(pattern)):
                        prefix = pattern[:i]
                        if self.buffer.endswith(prefix):
                            max_partial = max(max_partial, len(prefix))

                if max_partial > 0:
                    yield_len = len(self.buffer) - max_partial
                    if yield_len > 0:
                        output += self.buffer[:yield_len]
                        self.buffer = self.buffer[yield_len:]
                else:
                    output += self.buffer
                    self.buffer = ""
                break
            else:
                candidates.sort(key=lambda c: c[0])
                first_idx, tag_type, match = candidates[0]
                if first_idx > 0:
                    output += self.buffer[:first_idx]
                    self.buffer = self.buffer[first_idx:]

                if tag_type == "think":
                    self.in_thinking = True
                    tag_id = match.group(1) or ""
                    self.think_close_tag = f"</think{tag_id}>"
                    self.buffer = self.buffer[len(match.group(0)):]
                elif tag_type == "tool":
                    self.in_tool_line = True
                    self.buffer = self.buffer[5:]
                continue

        return output

    def flush(self) -> str:
        if not self.in_thinking and not self.in_tool_line:
            ret = self.buffer
            self.buffer = ""
            return ret
        return ""
