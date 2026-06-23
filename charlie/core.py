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
from charlie.budget import IterationBudget

logger = logging.getLogger("charlie.core")
if TYPE_CHECKING:
    from charlie.config import Config

# --- LLM tuning ---
_LLM_TEMPERATURE = 0.3
_TOOL_TIMEOUT_SEC = 15.0
_TOOL_TIMEOUTS = {
    "web_search": 15.0,
    "file_read": 10.0,
    "file_write": 10.0,
    "shell_execute": 30.0,
}
_TOOL_RESULT_MAX_CHARS = 2000
_COMPRESSION_THRESHOLD = 0.8

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
    "memory": "action",
    "session_search": "query",
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


def _token_count(messages: List[Dict[str, Any]]) -> int:
    """Approximate token count of messages."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        total = 0
        for msg in messages:
            total += len(enc.encode(msg.get("content", "") or ""))
            total += 4
        return total
    except Exception:
        count = 0
        for msg in messages:
            text = msg.get("content", "") or ""
            count += len(text) // 4 + 1
        return count


def _sanitize_roles(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ensure messages alternate correctly. Collapses consecutive assistants and inserts stub assistants before tools."""
    cleaned = []
    for msg in messages:
        role = msg.get("role")
        if not cleaned:
            if role == "tool":
                logger.error("Role violation: first message is tool")
                raise ValueError("First message cannot be tool")
            cleaned.append(msg)
            continue

        last_role = cleaned[-1].get("role")

        if role == "assistant":
            if last_role == "assistant":
                # Collapse consecutive assistant messages
                prev = cleaned[-1]
                if msg.get("content") and prev.get("content"):
                    prev["content"] += msg["content"]
                elif msg.get("content") and not prev.get("content"):
                    prev["content"] = msg["content"]
                if msg.get("tool_calls") and prev.get("tool_calls"):
                    prev["tool_calls"].extend(msg["tool_calls"])
                elif msg.get("tool_calls") and not prev.get("tool_calls"):
                    prev["tool_calls"] = msg["tool_calls"]
                continue
            cleaned.append(msg)
            continue

        if role == "tool":
            if last_role != "assistant":
                cleaned.append({"role": "assistant", "content": None})
            cleaned.append(msg)
            continue

        if role == "user":
            if last_role == "user":
                cleaned[-1]["content"] = (cleaned[-1].get("content") or "") + (msg.get("content") or "")
                continue
            cleaned.append(msg)
            continue

        cleaned.append(msg)
    return cleaned


def _prune_old_tool_results(messages: List[Dict[str, Any]], keep_last: int = 2) -> List[Dict[str, Any]]:
    """Keep only the last N tool-result turns. older ones are pruned."""
    prefix_end = 0
    for i, msg in enumerate(messages):
        if msg.get("role") == "user":
            prefix_end = i
            break
    prefix = messages[:prefix_end + 1]
    rest = messages[prefix_end + 1:]

    turns = []
    current_turn = []
    for msg in rest:
        role = msg.get("role")
        if role in ("assistant", "user"):
            if current_turn:
                turns.append(current_turn)
            current_turn = [msg]
        elif role == "tool":
            current_turn.append(msg)
        else:
            if current_turn:
                turns.append(current_turn)
            current_turn = [msg]
    if current_turn:
        turns.append(current_turn)

    tool_turn_indices = [idx for idx, t in enumerate(turns) if any(m.get("role") == "tool" for m in t)]
    kept_indices = set(tool_turn_indices[-keep_last:]) if len(tool_turn_indices) > keep_last else set(tool_turn_indices)

    result = prefix[:]
    for idx, t in enumerate(turns):
        if any(m.get("role") == "tool" for m in t):
            if idx in kept_indices:
                result.extend(t)
        else:
            result.extend(t)
    return result


def _halve_history(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep first system and last 2 user messages, drop the middle."""
    system_msg = messages[0] if messages and messages[0].get("role") == "system" else None
    user_msgs = [m for m in messages if m.get("role") == "user"]
    last_two_user = user_msgs[-2:]
    result = []
    if system_msg:
        result.append(system_msg)
    result.extend(last_two_user)
    result.insert(1, {"role": "system", "content": "[Earlier conversation omitted due to length.]"})
    return result


def _compress_messages(messages: List[Dict[str, Any]], config: "Config") -> List[Dict[str, Any]]:
    total = _token_count(messages)
    window = getattr(config, "context_window", 8192)
    threshold = int(_COMPRESSION_THRESHOLD * window)
    if total <= threshold:
        return messages

    pruned = _prune_old_tool_results(messages, keep_last=2)
    if _token_count(pruned) <= threshold:
        return pruned

    return _halve_history(pruned)


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
        self._tool_locks: Dict[str, asyncio.Lock] = {}
        # Fallback LLM client for provider failover
        self._fallback_client = None
        if (config.fallback_llm_url
                and config.fallback_llm_key
                and config.fallback_llm_key not in ("no-key", "no_key")):
            self._fallback_client = httpx.AsyncClient(
                base_url=config.fallback_llm_url,
                headers={"Authorization": f"Bearer {config.fallback_llm_key}"},
                timeout=60.0,
            )
            self._fallback_model = config.fallback_llm_model
            logger.info("Fallback LLM configured: %s", config.fallback_llm_url)

    def cancel_chat(self) -> None:
        """Cancel the current chat generation (barge-in support)."""
        self._chat_generation += 1

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()
        if self._fallback_client:
            await self._fallback_client.aclose()

    async def _stream_completion(
        self,
        payload: Dict[str, Any],
        generation: int,
    ) -> tuple:
        """Stream a chat completion with automatic fallback to secondary provider.

        Returns (accumulated_text, tool_calls_list, fallback_used).
        """
        client = self.client
        model = self.config.llm_model

        try:
            async with client.stream(
                "POST", "chat/completions", json=payload
            ) as response:
                response.raise_for_status()
                accumulated = ""
                tc_by_index: Dict[int, Dict[str, str]] = {}
                async for line in response.aiter_lines():
                    if self._chat_generation != generation:
                        logger.info("Chat generation cancelled (barge-in)")
                        return ("", [], False)
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
                    except Exception:
                        continue

                tool_calls = []
                for idx in sorted(tc_by_index.keys()):
                    tc = tc_by_index[idx]
                    try:
                        args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                    except json.JSONDecodeError:
                        args = {}
                    tool_calls.append({
                        "id": tc["id"],
                        "name": tc["name"],
                        "arguments": args,
                    })
                return (accumulated, tool_calls, False)

        except Exception as exc:
            logger.warning("Primary LLM stream error: %s", exc)
            if not self._fallback_client:
                raise
            client = self._fallback_client
            model = self._fallback_model
            logger.info("Falling back to secondary LLM: %s", self.config.fallback_llm_url)

        # Fallback attempt
        payload["model"] = model
        async with client.stream(
            "POST", "chat/completions", json=payload
        ) as response:
            response.raise_for_status()
            accumulated = ""
            tc_by_index: Dict[int, Dict[str, str]] = {}
            async for line in response.aiter_lines():
                if self._chat_generation != generation:
                    logger.info("Chat generation cancelled (barge-in)")
                    return ("", [], True)
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
                except Exception:
                    continue

            tool_calls = []
            for idx in sorted(tc_by_index.keys()):
                tc = tc_by_index[idx]
                try:
                    args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append({
                    "id": tc["id"],
                    "name": tc["name"],
                    "arguments": args,
                })
            return (accumulated, tool_calls, True)

    async def chat_stream(self, user_input: str) -> AsyncGenerator[str, None]:
        from pathlib import Path
        from datetime import datetime

        generation = self._chat_generation
        fast = _answer_time_date(user_input)
        if fast is not None:
            logger.info("Fast-path time/date: %s -> %s", user_input, fast)
            yield fast
            return

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
            "- Use web_search ONLY when the user explicitly asks for live/external data you cannot know.\n"
            "- Use the memory tool when the user asks you to remember something, or asks what you know about them.\n"
            "- Use the session_search tool when the user asks about past conversations.\n"
            "- When the user asks 'what do you know about me', summarize the [USER] section above.\n"
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
        
        messages = _compress_messages(_sanitize_roles(messages), self.config)
        
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

        accumulated, tool_calls, _used_fallback = await self._stream_completion(payload, generation)
        if not tool_calls and accumulated:
            tool_calls = self._extract_tool_calls(accumulated)

        if not tool_calls:
            if accumulated:
                yield accumulated
            return

        _seen_tool_calls: Dict[str, str] = {}

        async def _exec_one(call: Dict[str, Any]) -> str:
            ck = f"{call['name']}({json.dumps(call['arguments'], sort_keys=True)})"
            if ck in _seen_tool_calls:
                logger.info(f"Tool {call['name']} already executed, reusing result")
                return _seen_tool_calls[ck]
            
            tool_name = call['name']
            timeout = _TOOL_TIMEOUTS.get(tool_name, _TOOL_TIMEOUT_SEC)
            lock = self._tool_locks.setdefault(tool_name, asyncio.Lock())
            
            async def _run():
                return await asyncio.get_running_loop().run_in_executor(
                    None, tool_registry.execute_tool, call['name'], call['arguments']
                )

            try:
                if tool_registry.is_interactive(tool_name):
                    async with lock:
                        r = await asyncio.wait_for(_run(), timeout=timeout)
                else:
                    r = await asyncio.wait_for(_run(), timeout=timeout)
            except asyncio.TimeoutError:
                r = f"Error: Tool '{tool_name}' timed out after {timeout}s"
                logger.warning(f"Tool {tool_name} timed out")
            _seen_tool_calls[ck] = r
            return r

        budget = IterationBudget(max_turns=self.config.iteration_budget_max)

        while True:
            if not tool_calls:
                break

            allowed_calls = []
            for call in tool_calls:
                if budget.try_spend(call['name']):
                    allowed_calls.append(call)
                else:
                    yield "I've reached my tool limit for this turn. Let me know if you want me to continue."
                    return
            
            tool_calls = allowed_calls

            read_only = [c for c in tool_calls if not tool_registry.is_interactive(c['name'])]
            interactive = [c for c in tool_calls if tool_registry.is_interactive(c['name'])]

            results_map = {}
            if read_only:
                ro_results = await asyncio.gather(*[_exec_one(c) for c in read_only])
                # Map back to original indices
                ro_indices = [tool_calls.index(c) for c in read_only]
                for idx, r in zip(ro_indices, ro_results):
                    results_map[idx] = r

            if interactive:
                for c in interactive:
                    idx = tool_calls.index(c)
                    results_map[idx] = await _exec_one(c)

            exec_results = [results_map[i] for i in range(len(tool_calls))]

            tool_results = [
                {"tool_call_id": c.get("id"), "role": "tool", "name": c["name"], "content": r}
                for c, r in zip(tool_calls, exec_results)
            ]

            is_text_based = any(c.get("id") is None for c in tool_calls)
            if is_text_based:
                tool_summary = ""
                for c, r in zip(tool_calls, tool_results):
                    result_content = r['content'][:_TOOL_RESULT_MAX_CHARS]
                    if c['name'] == 'shell_execute':
                        cmd = c.get('arguments', '')
                        if 'Command executed successfully' in result_content:
                            tool_summary += f"shell_execute{cmd} executed successfully. The command is now running.\n"
                        else:
                            tool_summary += f"shell_execute{cmd} returned: {result_content}\n"
                    else:
                        tool_summary += f"{c['name']}({c['arguments']}) returned: {result_content}\n"
                tool_summary += "\nIMPORTANT: The tools above have been executed. Confirm to the user what was done. Do NOT call any more tools."
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

            messages = _compress_messages(_sanitize_roles(messages), self.config)

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
            followup_client = self.client
            followup_model = self.config.llm_model
            try:
                accumulated = ""
                async with followup_client.stream(
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
                if self._fallback_client:
                    logger.warning("Follow-up primary LLM error: %s, falling back", tool_exc)
                    followup_client = self._fallback_client
                    followup_model = self._fallback_model
                    followup_payload["model"] = followup_model
                    followup_tc_by_index.clear()
                    try:
                        accumulated = ""
                        async with followup_client.stream(
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
                    except Exception as fb_exc:
                        logger.warning("Follow-up fallback LLM also failed: %s", fb_exc)
                        break
                else:
                    logger.warning("Tool follow-up LLM error: %s", tool_exc)
                    break

            tool_calls = []
            for idx in sorted(followup_tc_by_index.keys()):
                tc = followup_tc_by_index[idx]
                try:
                    args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append({"id": tc["id"], "name": tc["name"], "arguments": args})

    def _extract_tool_calls(self, text: str) -> List[Dict[str, Any]]:
        """Extract tool calls from both JSON and text-based TOOL: format."""
        calls = []
        if not text:
            return calls

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

        tool_pattern = re.compile(r'TOOL:\s*(\w+)\(([^)]*)\)')
        for match in tool_pattern.finditer(text):
            tool_name = match.group(1)
            raw_args = match.group(2).strip()
            expected_params = _TOOL_PARAM_NAMES.get(tool_name)
            if expected_params and raw_args:
                # Single positional arg: TOOL: tool_name("value")
                # Multi-arg: TOOL: tool_name("val1", "val2", ...)
                quoted = re.findall(r'["\']([^"\']*)["\']', raw_args)
                if len(quoted) == 1:
                    arguments = {expected_params: quoted[0]}
                elif len(quoted) > 1:
                    # Map positional to known params
                    params_list = ["action", "target", "content", "old_text"]
                    arguments = {}
                    for i, val in enumerate(quoted):
                        if i < len(params_list):
                            arguments[params_list[i]] = val
                else:
                    arguments = {expected_params: raw_args}
            else:
                param_name = expected_params or "query"
                arguments = {param_name: raw_args.strip("'\"")}
            calls.append({
                "id": None,
                "name": tool_name,
                "arguments": arguments,
            })
        return calls
