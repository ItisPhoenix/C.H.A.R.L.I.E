"""Charlie brain -- LLM orchestration, tool loop, streaming.

Single explicit backend (async httpx). No provider names in code.
"""

import asyncio
import logging
import re
import json
from typing import TYPE_CHECKING, AsyncGenerator, Optional, Dict, Any, List

import httpx
from charlie.tools import registry as tool_registry

logger = logging.getLogger("charlie.core")
if TYPE_CHECKING:
    from charlie.config import Config

# --- LLM tuning ---
_LLM_TEMPERATURE = 0.3
_MAX_TOOL_ROUNDS = 4
_TOOL_TIMEOUT_SEC = 15.0
_TOOL_RESULT_MAX_CHARS = 2000

# --- Reasoning tag pattern (shared) ---
_REASONING_RE = re.compile(
    r'(?:'
    r'(?:I\s+(?:should|need|will|can|must|have\s+to)\s+[^.!?]{1,60}?[.!?]\s*)'
    r"|(?:Here(?:'s| is)\s+(?:what|the)[^.!?]{1,40}?[.!?]\s*)"
    r'|(?:To answer that,\s*)'
    r'|(?:The user is\s+(?:asking|looking)[^.!?]{1,40}?[.!?]\s*)'
    r')+', re.IGNORECASE
)

# --- Tool param name map (text-based extraction) ---
_TOOL_PARAM_NAMES: Dict[str, str] = {
    "web_search": "query",
    "shell_execute": "command",
    "file_read": "path",
    "file_write": "path",
}
# --- Fast-path: time/date queries answered from system clock (zero LLM) ---
_TIME_DATE_RE = re.compile(
    r"(?:what(?:'s|\s+is|\s+s)?\s+(?:the\s+)?(?:current\s+)?(?:time|date|day|today))"
    r"|(?:tell\s+(?:me\s+)?(?:the\s+)?(?:time|date|day))"
    r"|(?:what\s+(?:time|date|day)\s+is\s+it)"
    r"|(?:what\s+(?:day\s+of\s+the\s+week|month|year)\s+is\s+it)"
    r"|(?:what(?:'s|\s+is|\s+s)?\s+today(?:'s\s+date)?)"
    r"|(?:(?:current|right\s+now)\s+(?:time|date))",
    re.IGNORECASE,
)


def _answer_time_date(query: str) -> Optional[str]:
    """Answer time/date queries directly from system clock. Returns None if not a time/date query."""
    if not _TIME_DATE_RE.search(query):
        return None
    now = __import__("datetime").datetime.now()
    q = query.lower().strip()
    if "time" in q:
        return f"It's {now.strftime('%I:%M %p')}."
    if "date" in q or "today" in q:
        return f"Today is {now.strftime('%A, %B %d, %Y')}."
    if "month" in q:
        return f"It's {now.strftime('%B')}."
    if "year" in q:
        return f"It's {now.strftime('%Y')}."
    if "week" in q:
        return f"Today is {now.strftime('%A')}."
    if "day" in q:
        return f"Today is {now.strftime('%A, %B %d, %Y')}."
    return None


def strip_internal_reasoning(text: str) -> str:
    """Remove model reasoning/thinking tags before user-facing output."""
    text = re.sub(r'<(thought|thinking|longcat_tool_call)>.*?</\1>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = _REASONING_RE.sub('', text)
    return text.strip()


class Brain:
    """Minimal voice-first brain: single explicit backend."""

    def __init__(self, config: "Config", on_thought_callback: Optional[callable] = None, session_store=None):
        self.config = config
        self.on_thought_callback = on_thought_callback
        self.session_store = session_store
        self.client = httpx.AsyncClient(
            base_url=config.llm_url,
            headers={"Authorization": f"Bearer {config.llm_key}"},
            timeout=60.0,
        )
        self._chat_generation = 0

    def cancel_chat(self) -> None:
        """Cancel the current chat generation (barge-in support).

        Increments a monotonic counter. Streaming loops check this counter
        and break early when it changes -- no event race conditions.
        """
        self._chat_generation += 1

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()

    async def chat_stream(self, user_input: str) -> AsyncGenerator[str, None]:
        from pathlib import Path
        from datetime import datetime

        generation = self._chat_generation
        # --- Fast-path: time/date queries bypass LLM entirely ---
        fast = _answer_time_date(user_input)
        if fast is not None:
            logger.info("Fast-path time/date: %s -> %s", user_input, fast)
            yield fast
            return

        # Read MEMORY.md and USER.md
        memory_content = ""
        user_content = ""
        max_chars = self.config.prompt_memory_max // 2

        try:
            m_path = Path(self.config.memory_file)
            if not m_path.exists():
                m_path.write_text("", encoding="utf-8")
            memory_content = m_path.read_text(encoding="utf-8")[:max_chars]
        except Exception as e:
            logger.warning(f"Error reading memory file: {e}")

        try:
            u_path = Path(self.config.user_file)
            if not u_path.exists():
                u_path.write_text("", encoding="utf-8")
            user_content = u_path.read_text(encoding="utf-8")[:max_chars]
        except Exception as e:
            logger.warning(f"Error reading user file: {e}")

        soul_text = self.config.soul or "You are Charlie. Be concise and warm."
        tools_text = tool_registry.build_tool_prompt()
        now = datetime.now()
        system_msg = (
            f"{soul_text}\n\n"
            f"Current date: {now.strftime('%A, %B %d, %Y')}. "
            f"Current time: {now.strftime('%I:%M %p')}.\n"
            "Output rules: short spoken sentences. No markdown. No lists. No emojis.\n\n"
            f"You have access to these tools:\n{tools_text}\n\n"
            "To use a tool, output a line exactly like:\n"
            'TOOL: tool_name("argument")\n'
            'Example: TOOL: web_search("latest news")\n\n'
            "CRITICAL RULES for tool use:\n"
            "- Use a tool ONLY when the user explicitly asks for live/external data you cannot know.\n"
            "- NEVER use tools for: time, date, calculations, math, or general knowledge.\n"
            "- The current time and date are provided above -- use them directly.\n"
            "- Use a tool at MOST ONCE per question. Never repeat the same tool call.\n"
            "- After receiving tool results, answer immediately using those results.\n"
            "- Do NOT call tools if you already have the answer from prior results.\n\n"
            f"[MEMORY]\n{memory_content}\n\n"
            f"[USER]\n{user_content}"
        )
        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_input},
        ]
        tools = tool_registry.get_tool_definitions()
        payload: Dict[str, Any] = {
            "model": self.config.llm_model,
            "messages": messages,
            "temperature": _LLM_TEMPERATURE,
            "stream": True,
            "tools": tools,
            "tool_choice": "auto",
        }
        if getattr(self.config, "llm_disable_reasoning", False):
            payload["reasoning"] = {"effort": "none"}

        accumulated = ""
        _tool_calls_by_index: Dict[int, Dict[str, str]] = {}
        try:
            async with self.client.stream(
                "POST", "chat/completions", json=payload
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if self._chat_generation != generation:
                        logger.info("Chat generation cancelled (barge-in)")
                        return
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
                        for tc in delta.get("tool_calls", []):
                            idx = tc.get("index", 0)
                            if idx not in _tool_calls_by_index:
                                _tool_calls_by_index[idx] = {"id": "", "name": "", "arguments": ""}
                            entry = _tool_calls_by_index[idx]
                            if tc.get("id"):
                                entry["id"] = tc["id"]
                            func = tc.get("function", {})
                            if func.get("name"):
                                entry["name"] = func["name"]
                            if func.get("arguments"):
                                entry["arguments"] += func["arguments"]
                    except Exception:
                        continue
        except Exception as exc:
            logger.warning(f"LLM stream error: {exc}")
            raise

        # Build tool_calls list from streamed deltas
        streamed_tool_calls: List[Dict[str, Any]] = []
        for idx in sorted(_tool_calls_by_index.keys()):
            tc = _tool_calls_by_index[idx]
            try:
                args = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except json.JSONDecodeError:
                args = {}
            streamed_tool_calls.append({
                "id": tc["id"],
                "name": tc["name"],
                "arguments": args,
            })

        tool_calls = streamed_tool_calls
        if not tool_calls and accumulated:
            tool_calls = self._extract_tool_calls(accumulated)

        # If no tool calls, yield accumulated content and return
        if not tool_calls:
            if accumulated:
                yield accumulated
            return

        # Duplicate tool call cache
        _seen_tool_calls: Dict[str, str] = {}

        # Concurrent tool execution helper
        async def _exec_one(call: Dict[str, Any]) -> str:
            ck = f"{call['name']}({json.dumps(call['arguments'], sort_keys=True)})"
            if ck in _seen_tool_calls:
                logger.info(f"Tool {call['name']} already executed, reusing result")
                return _seen_tool_calls[ck]
            try:
                r = await asyncio.wait_for(
                    asyncio.get_running_loop().run_in_executor(
                        None, tool_registry.execute_tool, call['name'], call['arguments']
                    ),
                    timeout=_TOOL_TIMEOUT_SEC,
                )
            except asyncio.TimeoutError:
                r = f"Error: Tool '{call['name']}' timed out after {_TOOL_TIMEOUT_SEC}s"
                logger.warning(f"Tool {call['name']} timed out")
            _seen_tool_calls[ck] = r
            return r

        i = 0
        while i < _MAX_TOOL_ROUNDS:
            if not tool_calls:
                break

            exec_results = await asyncio.gather(
                *[_exec_one(c) for c in tool_calls]
            )
            tool_results = [
                {"tool_call_id": c.get("id"), "role": "tool", "name": c["name"], "content": r}
                for c, r in zip(tool_calls, exec_results)
            ]

            # Text-based tool calls (Ollama) vs native function calling
            is_text_based = any(c.get("id") is None for c in tool_calls)
            if is_text_based:
                tool_summary = ""
                for c, r in zip(tool_calls, tool_results):
                    result_content = r['content'][:_TOOL_RESULT_MAX_CHARS]
                    # Add explicit confirmation for shell_execute
                    if c['name'] == 'shell_execute':
                        cmd = c.get('arguments', '')
                        if 'Command executed successfully' in result_content:
                            tool_summary += (
                                f"shell_execute{cmd} executed successfully. "
                                f"The command is now running.\n"
                            )
                        else:
                            tool_summary += (
                                f"shell_execute{cmd} returned: {result_content}\n"
                            )
                    else:
                        tool_summary += (
                            f"{c['name']}({c['arguments']}) returned: {result_content}\n"
                        )
                tool_summary += (
                    "\nIMPORTANT: The tools above have been executed. "
                    "Confirm to the user what was done. Do NOT call any more tools."
                )
                messages.append({"role": "tool", "content": tool_summary})
            else:
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": r["tool_call_id"],
                            "type": "function",
                            "function": {
                                "name": c["name"],
                                "arguments": json.dumps(c["arguments"]),
                            },
                        }
                        for c, r in zip(tool_calls, tool_results)
                    ],
                })
                messages.extend(tool_results)

            followup_payload: Dict[str, Any] = {
                "model": self.config.llm_model,
                "messages": messages,
                "temperature": _LLM_TEMPERATURE,
                "stream": True,
                "tools": tools,
                "tool_choice": "auto",
            }
            if getattr(self.config, "llm_disable_reasoning", False):
                followup_payload["reasoning"] = {"effort": "none"}

            followup_tc_by_index: Dict[int, Dict[str, str]] = {}
            try:
                accumulated = ""
                async with self.client.stream(
                    "POST", "chat/completions", json=followup_payload
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if self._chat_generation != generation:
                            logger.info("Tool follow-up cancelled (barge-in)")
                            return
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
                                if not content.strip().startswith("TOOL:"):
                                    yield content
                            for tc in delta.get("tool_calls", []):
                                idx = tc.get("index", 0)
                                if idx not in followup_tc_by_index:
                                    followup_tc_by_index[idx] = {"id": "", "name": "", "arguments": ""}
                                entry = followup_tc_by_index[idx]
                                if tc.get("id"):
                                    entry["id"] = tc["id"]
                                func = tc.get("function", {})
                                if func.get("name"):
                                    entry["name"] = func["name"]
                                if func.get("arguments"):
                                    entry["arguments"] += func["arguments"]
                        except Exception:
                            continue
            except Exception as tool_exc:
                logger.warning(f"Tool follow-up LLM error: {tool_exc}")
                break

            tool_calls = []
            for idx in sorted(followup_tc_by_index.keys()):
                tc = followup_tc_by_index[idx]
                try:
                    args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append({"id": tc["id"], "name": tc["name"], "arguments": args})
            i += 1

    def _extract_tool_calls(self, text: str) -> List[Dict[str, Any]]:
        """Extract tool calls from both JSON and text-based TOOL: format."""
        calls = []
        if not text:
            return calls

        # 1. Try JSON tool_calls format (native function calling)
        if "tool_calls" in text:
            try:
                data = json.loads(text)
                if isinstance(data, dict) and data.get("tool_calls"):
                    for tc in data["tool_calls"]:
                        function = tc.get("function", {})
                        arguments = {}
                        if isinstance(function.get("arguments"), str):
                            try:
                                arguments = json.loads(function["arguments"]) if function["arguments"] else {}
                            except json.JSONDecodeError:
                                arguments = {}
                        calls.append({
                            "id": tc.get("id"),
                            "name": function.get("name"),
                            "arguments": arguments,
                        })
                    return calls
            except json.JSONDecodeError:
                pass

        # 2. Try text-based TOOL: format (Ollama models)
        tool_pattern = re.compile(r'TOOL:\s*(\w+)\(\s*["\'](.+?)["\']\s*\)')
        for match in tool_pattern.finditer(text):
            tool_name = match.group(1)
            tool_arg = match.group(2)
            param_name = _TOOL_PARAM_NAMES.get(tool_name, "query")
            calls.append({
                "id": None,
                "name": tool_name,
                "arguments": {param_name: tool_arg},
            })
        return calls
