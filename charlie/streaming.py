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

_INTERNAL_BLOCK_TAGS = ("think", "thinking", "thought", "longcat_tool_call")
_INTERNAL_OPEN_TAG_RE = re.compile(r"<(" + "|".join(_INTERNAL_BLOCK_TAGS) + r")>", re.IGNORECASE)


def strip_internal_reasoning(text: str) -> str:
    """Remove hidden reasoning blocks from a completed response."""
    for tag in _INTERNAL_BLOCK_TAGS:
        text = re.sub(rf"<{tag}>.*?(?:</{tag}>|$)", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(rf"</?{tag}>", "", text, flags=re.IGNORECASE)
    return text.strip()


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
            logger.warning(
                "Malformed tool-call arguments JSON for %r, defaulting to {}: %r",
                tc.get("name"), tc["arguments"],
            )
            args = {}
        if not tc["name"]:
            logger.warning("Tool-call delta at index %d has an empty name -- will reach execute_tool as ''", idx)
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
            delta = chunk.get("choices", [{}])[0].get("delta", {})
            content = delta.get("content", "")
            if content:
                accumulated += content
                if on_content is not None:
                    on_content(content)
            for tc in delta.get("tool_calls", []):
                _merge_tool_call_delta(tc, tc_by_index)
        except Exception:
            continue

    return accumulated, tc_by_index, cancelled


class FollowupStreamState:
    """Mutable accumulator for stream_followup_content.

    Async generators can't surface extra return values through `async for`,
    so the caller passes this box in and reads `.accumulated` / `.tc_by_index`
    / `.cancelled` once the generator has been fully consumed.
    """

    def __init__(self, *, interactive_vision: Optional[bool] = None) -> None:
        self.accumulated: str = ""
        self.tc_by_index: Dict[int, Dict[str, str]] = {}
        self.cancelled: bool = False
        self.finish_reason: Optional[str] = None
        self.interactive_vision = interactive_vision
        self.completion_status: str = "completed"
        self.timeout_reason: Optional[str] = None


async def stream_followup_content(
    response: Any,
    generation: int,
    current_generation_getter: Callable[[], int],
    state: FollowupStreamState,
    stop_on_finish_reason: bool = False,
) -> AsyncGenerator[str, None]:
    """Async-generator SSE parser for tool-followup completions.

    Unlike parse_sse_stream (which buffers and returns once), this yields
    each raw content chunk as it arrives so the caller can push it through a
    TextStreamFilter and yield immediately -- required to keep the streamed
    reply's Time-To-First-Audio low. Tool-call deltas and the accumulated
    text land on `state`; `state.cancelled` is set if the chat generation
    changes mid-stream (barge-in) instead of raising. Vision callers can
    stop on an observable server finish reason without waiting for `[DONE]`.
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
            choice = chunk.get("choices", [{}])[0]
            finish_reason = choice.get("finish_reason")
            if finish_reason is not None:
                state.finish_reason = str(finish_reason)
            delta = choice.get("delta", {})
            content = delta.get("content", "")
            if content:
                state.accumulated += content
                yield content
            for tc in delta.get("tool_calls", []):
                _merge_tool_call_delta(tc, state.tc_by_index)
            if stop_on_finish_reason and finish_reason is not None:
                return
        except Exception:
            continue


class TextStreamFilter:
    """Filter reasoning and structured tool-call syntax from user-facing text."""
    def __init__(self):
        self.buffer = ""
        self.in_internal_block: Optional[str] = None
        self.in_tool_line = False
        self.in_tool_call = False
        self.tool_call_xml = False
        self.await_tool_close = False

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
            if self.await_tool_close:
                closing = re.search(r"</tool_call\s*>", self.buffer, re.IGNORECASE)
                if closing:
                    self.buffer = self.buffer[closing.end():]
                    self.await_tool_close = False
                    continue
                self.buffer = self.buffer[-64:]
                break
            if self.in_internal_block:
                closing_tag = f"</{self.in_internal_block}>"
                end_match = re.search(re.escape(closing_tag), self.buffer, re.IGNORECASE)
                if end_match:
                    self.buffer = self.buffer[end_match.end():]
                    self.in_internal_block = None
                    continue
                else:
                    keep_len = len(closing_tag) - 1
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

            if self.in_tool_call:
                json_start = self.buffer.find("{")
                if json_start == -1:
                    self.buffer = ""
                    break
                try:
                    _payload, json_length = json.JSONDecoder().raw_decode(self.buffer[json_start:])
                except json.JSONDecodeError:
                    break
                self.buffer = self.buffer[json_start + json_length:]
                closing_tag = re.match(r"\s*</tool_call\s*>", self.buffer, re.IGNORECASE)
                if closing_tag:
                    self.buffer = self.buffer[closing_tag.end():]
                self.in_tool_call = False
                self.await_tool_close = self.tool_call_xml and closing_tag is None
                continue

            # Find tags in buffer
            internal_match = _INTERNAL_OPEN_TAG_RE.search(self.buffer)
            tool_close_match = re.search(r"</tool_call\s*>", self.buffer, re.IGNORECASE)
            tool_idx = self.buffer.find("TOOL:")
            tool_call_matches = [
                (index, marker)
                for marker in ("_tool_call", "<tool_call>")
                if (index := self.buffer.casefold().find(marker.casefold())) != -1
            ]

            indices = []
            if internal_match:
                indices.append((internal_match.start(), "internal"))
            if tool_close_match:
                indices.append((tool_close_match.start(), "tool_close"))
            if tool_idx != -1:
                indices.append((tool_idx, "tool"))
            if tool_call_matches:
                indices.append((min(tool_call_matches)[0], "tool_call"))

            if not indices:
                # No tags, check for partial match prefixes at end
                max_partial = 0
                patterns = tuple(f"<{tag}>" for tag in _INTERNAL_BLOCK_TAGS) + (
                    "TOOL:", "_tool_call", "<tool_call>", "</tool_call>"
                )
                for pattern in patterns:
                    for i in range(1, len(pattern)):
                        prefix = pattern[:i]
                        if self.buffer.casefold().endswith(prefix.casefold()):
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
                indices.sort()
                first_idx, tag_type = indices[0]
                if first_idx > 0:
                    output += self.buffer[:first_idx]
                    self.buffer = self.buffer[first_idx:]

                if tag_type == "internal":
                    assert internal_match is not None
                    self.in_internal_block = internal_match.group(1).casefold()
                    self.buffer = self.buffer[internal_match.end() - first_idx:]
                elif tag_type == "tool":
                    self.in_tool_line = True
                    self.buffer = self.buffer[5:]
                elif tag_type == "tool_close":
                    assert tool_close_match is not None
                    self.buffer = self.buffer[tool_close_match.end() - first_idx:]
                elif tag_type == "tool_call":
                    marker_length = next(
                        len(marker)
                        for index, marker in tool_call_matches
                        if index == first_idx
                    )
                    self.in_tool_call = True
                    self.tool_call_xml = self.buffer.casefold().startswith("<tool_call>")
                    self.buffer = self.buffer[marker_length:]
                continue

        return output

    def flush(self) -> str:
        if not self.in_internal_block and not self.in_tool_line and not self.in_tool_call and not self.await_tool_close:
            ret = self.buffer
            self.buffer = ""
            patterns = tuple(f"<{tag}>" for tag in _INTERNAL_BLOCK_TAGS) + (
                "TOOL:", "_tool_call", "<tool_call>", "</tool_call>"
            )
            for pattern in patterns:
                for length in range(1, len(pattern)):
                    if ret.casefold().endswith(pattern[:length].casefold()):
                        ret = ret[:-length]
                        break
            return ret
        return ""
