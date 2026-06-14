import json
import logging
import httpx
import os
import re
import datetime
import asyncio
from typing import List, Dict, Optional, Callable, AsyncGenerator
from charlie.research import web_search, read_url, deep_research
from charlie.research_memory import memory as research_memory
from charlie.personality import CharliePersona

logger = logging.getLogger("charlie.core")

class Brain:
    def __init__(self, config, on_thought_callback: Optional[Callable[[str], None]] = None):
        self.config = config
        self.on_thought_callback = on_thought_callback
        self.history: List[Dict[str, str]] = []
        self.persona = CharliePersona()
        
        base_url = self.config.llm_url
        if not base_url.endswith("/"):
            base_url += "/"
            
        self.client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {self.config.llm_key}"},
            timeout=httpx.Timeout(60.0, connect=15.0),
            verify=True
        )
        self.load_history()

    def load_history(self):
        if os.path.exists(self.config.history_file):
            try:
                with open(self.config.history_file, "r") as f:
                    data = json.load(f)

                # Handle new wrapper format or legacy list
                if isinstance(data, dict):
                    self.history = data.get("messages", [])
                    self.persona.emotional_state = data.get("emotional_state", "neutral")
                    # Load persisted preferences
                    saved_prefs = data.get("preferences", {})
                    if saved_prefs:
                        self.persona.preferences.update(saved_prefs)
                else:
                    self.history = data

                # Enforce max history limit
                self.history = self.history[-self.config.max_history:]
                logger.info(f"Loaded {len(self.history)} messages. Emotion: {self.persona.emotional_state}")
            except Exception as e:
                logger.error(f"history_load_error | {e}")

    async def _consolidate_preferences(self):
        """Use LLM to summarize preferences if they grow too large."""
        if len(self.persona.preferences) <= 10:
            return

        logger.info("Consolidating preferences...")
        pref_text = "\n".join([f"- {k}: {v}" for k, v in self.persona.preferences.items()])
        prompt = (
            "Summarize and consolidate these user preferences into a maximum of 8 broader categories. "
            "Keep the output as a flat JSON dictionary of key: value strings.\n\n"
            f"PREFERENCES:\n{pref_text}"
        )

        try:
            payload = {
                "model": self.config.llm_model,
                "messages": [{"role": "system", "content": "You are a personality management utility. Output ONLY valid JSON."},
                             {"role": "user", "content": prompt}],
                "temperature": 0.1,
                "response_format": {"type": "json_object"}
            }
            response = await self.client.post("chat/completions", json=payload)
            response.raise_for_status()
            new_prefs_raw = response.json()["choices"][0]["message"]["content"]
            self.persona.preferences = json.loads(new_prefs_raw)
            logger.info("Preferences consolidated.")
        except Exception as e:
            logger.error(f"preferences_consolidation_error | {e}")

    def save_history(self):
        try:
            self.history = self.history[-self.config.max_history:]
            data = {
                "messages": self.history,
                "emotional_state": self.persona.emotional_state,
                "preferences": self.persona.preferences
            }
            with open(self.config.history_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"history_save_error | {e}")

    async def chat(self, user_input: str) -> AsyncGenerator[str, None]:
        # Prefix handling for direct deep research
        ui_lower = user_input.lower().strip()
        if ui_lower.startswith("research ") or ui_lower.startswith("deep dive "):
            topic = user_input.split(" ", 1)[1]
            if self.on_thought_callback:
                self.on_thought_callback(f"Starting deep research on {topic}. This may take a moment.")
            report = await deep_research(topic, self)
            self.history.append({"role": "user", "content": user_input})
            self.history.append({"role": "assistant", "content": f"Research complete. Here is the report:\n\n{report}"})
            self.save_history()
            yield f"Research complete. I've compiled a deep dive on {topic} for you."
            return

        # Active recall injection
        related = research_memory.find_related_sessions(user_input)
        if related:
            memory_context = f"\n\nPAST RELATED RESEARCH: We have previously researched: {', '.join(related)}. Use this context if relevant."
        else:
            memory_context = ""

        self.history.append({"role": "user", "content": user_input})
        
        # Multi-step research loop
        for i in range(3):
            self.persona.detect_emotion(user_input)
            
            now = datetime.datetime.now()
            current_date = now.strftime("%A, %B %d, %Y")
            current_time = now.strftime("%I:%M %p")
            
            system_msg = self.persona.build_system_prompt(
                current_date, current_time, memory_context, user_input
            )
            
            full_reply = ""
            is_tool_call = False
            
            messages = [{"role": "system", "content": system_msg}] + self.history[-self.config.max_history:]
            
            try:
                logger.debug(f"Calling LLM (Iteration {i+1})...")
                payload = {
                    "model": self.config.llm_model,
                    "messages": messages,
                    "temperature": 0.3, 
                    "stream": True
                }

                async with self.client.stream("POST", "chat/completions", json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "): continue
                        if line == "data: [DONE]": break
                        
                        try:
                            chunk = json.loads(line[6:])
                            content = chunk["choices"][0]["delta"].get("content", "")
                            if not content: continue
                            
                            full_reply += content
                            
                            if not is_tool_call:
                                # If we already found TOOL: in full_reply, stay in tool mode
                                if "TOOL:" in full_reply.upper():
                                    is_tool_call = True
                                    continue
                                
                                # Otherwise, yield if we are past the potential tool prefix window
                                if len(full_reply) > 20:
                                    yield content
                        except Exception as e:
                            logger.warning(f"stream_parse_error | {e}")
                
                # If we didn't find TOOL: during streaming, but it's in the final text (fallback)
                if not is_tool_call and "TOOL:" in full_reply.upper():
                    is_tool_call = True
                
                # Final yield for non-tool calls if we were buffering
                if not is_tool_call and len(full_reply) <= 20:
                    yield full_reply
                reply = full_reply.strip()
                reply_upper = reply.upper()
                if is_tool_call:
                    if "WEB_SEARCH" in reply_upper:
                        match = re.search(r'web_search\s*\(\s*["\'](.*?)["\']\s*\)', reply, re.IGNORECASE)
                        if match:
                            query = match.group(1)
                            if self.on_thought_callback:
                                self.on_thought_callback("I'm looking into that...")
                            logger.info(f"RESEARCHING: {query}")
                            result = await web_search(query)
                            self.history.append({"role": "assistant", "content": reply})
                            self.history.append({"role": "user", "content": f"Search result: {result}"})
                            continue
                            
                    elif "DEEP_RESEARCH" in reply_upper:
                        match = re.search(r'deep_research\s*\(\s*["\'](.*?)["\']\s*\)', reply, re.IGNORECASE)
                        if match:
                            topic = match.group(1)
                            if self.on_thought_callback:
                                self.on_thought_callback("I'll need to do some deep research on that. One moment.")
                            logger.info(f"DEEP RESEARCHING: {topic}")
                            result = await deep_research(topic, self)
                            self.history.append({"role": "assistant", "content": reply})
                            self.history.append({"role": "user", "content": f"Research Report: {result}"})
                            continue
                            
                    elif "READ_URL" in reply_upper:
                        match = re.search(r'read_url\s*\(\s*["\'](.*?)["\']\s*\)', reply, re.IGNORECASE)
                        if match:
                            url = match.group(1)
                            if self.on_thought_callback:
                                self.on_thought_callback("Let me read through that page...")
                            logger.info(f"READING URL: {url}")
                            result = await read_url(url)
                            self.history.append({"role": "assistant", "content": reply})
                            self.history.append({"role": "user", "content": f"Website content: {result}"})
                            continue
                
                # Final reply
                if reply:
                    self.history.append({"role": "assistant", "content": reply})
                    # Check and consolidate preferences periodically
                    await self._consolidate_preferences()
                    self.save_history()
                return
                
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    logger.warning("Rate limit hit (429).")
                    # Attempt a very short, low-token character-consistent refusal 
                    # by calling the LLM with a minimal prompt, or use fallback if that fails.
                    try:
                        payload = {
                            "model": self.config.llm_model,
                            "messages": [
                                {"role": "system", "content": f"You are Charlie. {self.persona.WORLDVIEW[0]} {self.persona.WORLDVIEW[4]} You are currently hitting a rate limit. Give a 1-sentence blunt, in-character refusal."},
                            ],
                            "max_tokens": 20,
                            "temperature": 0.7
                        }
                        # Use a shorter timeout for this emergency response
                        response = await self.client.post("chat/completions", json=payload, timeout=5.0)
                        if response.status_code == 200:
                            yield response.json()["choices"][0]["message"]["content"].strip()
                            return
                    except:
                        pass
                    yield self.persona.get_rate_limit_message()
                    return
                yield f"My brain returned an error: {e.response.status_code}."
                return
            except httpx.ConnectError as e:
                yield "I can't connect to my brain. Please check your internet."
                return
            except Exception as e:
                logger.error(f"llm_unexpected_error | {type(e).__name__}: {e}")
                yield "My brain is having some unexpected trouble."
                return
        
        return

    async def close(self):
        if self.client:
            await self.client.aclose()
            logger.info("Brain connection closed.")
