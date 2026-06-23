import logging
import re
import json
from typing import TYPE_CHECKING, AsyncGenerator, Optional, Dict, Any, List

import httpx
from charlie.tools import registry as tool_registry

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
    text = re.sub(r'  思考.*?  思考结束', '', text, flags=re.DOTALL)
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

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()

    async def chat_stream(self, user_input: str) -> AsyncGenerator[str, None]:
        from pathlib import Path

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
        system_msg = (
            f"{soul_text}\n\n"
            "Output rules: short spoken sentences. No markdown. No lists. No emojis.\n\n"
            f"You have access to these tools:\n{tools_text}\n\n"
            "To use a tool, output a line exactly like:\n"
            'TOOL: tool_name("argument")\n'
            'Example: TOOL: web_search("latest news")\n'
            "The system will run the tool and give you the result before you reply.\n"
            "Only use a tool when you genuinely need external data. Do not guess when you can search.\n\n"
            f"[MEMORY]\n{memory_content}\n\n"
            f"[USER]\n{user_content}"
        )
        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_input},
        ]
        tools = tool_registry.get_tool_definitions()
        payload = {
            "model": self.config.llm_model,
            "messages": messages,
            "temperature": 0.3,
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
                        # Accumulate streamed tool-call deltas
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
        i = 0
        max_tool_rounds = 4
        while i < max_tool_rounds:
            if not tool_calls:
                break

            tool_results = []
            for call in tool_calls:
                result = tool_registry.execute_tool(call["name"], call["arguments"])
                tool_results.append({
                    "tool_call_id": call.get("id"),
                    "role": "tool",
                    "name": call["name"],
                    "content": result,
                })

            # Check if these are text-based tool calls (no native IDs)
            is_text_based = any(c.get("id") is None for c in tool_calls)
            if is_text_based:
                # For Ollama models: inject tool results as plain text
                tool_summary = "Tool results:\n"
                for c, r in zip(tool_calls, tool_results):
                    tool_summary += f"{c['name']}({c['arguments']}) returned: {r['content'][:2000]}\n"
                messages.append({"role": "assistant", "content": tool_summary})
            else:
                # For native function-calling models
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

            followup_payload = {
                "model": self.config.llm_model,
                "messages": messages,
                "temperature": 0.3,
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
        """Extract tool calls from both JSON and text-based TOOL: format.

        Handles:
        - Native function calling JSON deltas
        - Text format: TOOL: web_search("query")
        """
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

        # 2. Try text-based TOOL: format (Ollama models that can't use native tools)
        # Matches: TOOL: web_search("query") or TOOL:web_search("query")
        tool_pattern = re.compile(r'TOOL:\s*(\w+)\(\s*["\'](.+?)["\']\s*\)')
        for match in tool_pattern.finditer(text):
            tool_name = match.group(1)
            tool_arg = match.group(2)
            # Map tool to its primary argument name
            param_name = {
                "web_search": "query",
                "shell_execute": "command",
                "file_read": "path",
                "file_write": "path",
            }.get(tool_name, "query")
            calls.append({
                "id": None,
                "name": tool_name,
                "arguments": {param_name: tool_arg},
            })
        return calls
