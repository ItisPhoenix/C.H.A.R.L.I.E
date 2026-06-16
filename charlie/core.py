import json
import logging
import httpx
import os
import re
import datetime
import asyncio
import time
from typing import List, Dict, Optional, Callable, AsyncGenerator
from charlie.research import web_search, read_url, deep_research
from charlie.research_memory import memory as research_memory
from charlie.memory_manager import MemoryManager
from charlie.personality import CharliePersona
from charlie.profile_manager import ProfileManager
from charlie.llm_router import LLMRouter
from charlie.mcp_client import CharlieMCPClient
from charlie.discovery import SystemDiscovery
from charlie.bridge import CharlieBridge
from charlie.ui_launcher import UILauncher

logger = logging.getLogger("charlie.core")

class Brain:
    def __init__(self, config, on_thought_callback: Optional[Callable[[str], None]] = None):
        self.config = config
        self.on_thought_callback = on_thought_callback
        self.history: List[Dict[str, str]] = []
        self.persona = CharliePersona(config=self.config)

        # Protect shared conversation state against concurrent background tasks
        self._history_lock = asyncio.Lock()

        # Barge-in: signals the chat generator to stop yielding
        self.cancel_chat_event = asyncio.Event()
        
        # Cloud LLM Client (NVIDIA)
        cloud_url = self.config.llm_url
        if not cloud_url.endswith("/"): cloud_url += "/"
        self.cloud_client = httpx.AsyncClient(
            base_url=cloud_url,
            headers={"Authorization": f"Bearer {self.config.llm_key}"},
            timeout=httpx.Timeout(60.0, connect=15.0)
        )

        # Fast Client for background tasks
        fast_url = self.config.fast_llm_url
        if not fast_url.endswith("/"): fast_url += "/"
        fast_headers = {}
        if self.config.fast_llm_key and self.config.fast_llm_key != "no-key":
            fast_headers["Authorization"] = f"Bearer {self.config.fast_llm_key}"
        self.fast_client = httpx.AsyncClient(
            base_url=fast_url,
            headers=fast_headers,
            timeout=httpx.Timeout(30.0, connect=10.0)
        )

        # Local LLM Client (Ollama / local OpenAI-compatible endpoint)
        local_url = self.config.local_llm_url
        if not local_url.endswith("/"): local_url += "/"
        local_headers = {}
        if self.config.local_llm_key and self.config.local_llm_key != "no-key":
            local_headers["Authorization"] = f"Bearer {self.config.local_llm_key}"
        self.local_client = httpx.AsyncClient(
            base_url=local_url,
            headers=local_headers,
            timeout=httpx.Timeout(15.0, connect=5.0)  # stricter timeout for local
        )

        self.llm_router = LLMRouter(config)

        # System Self-Awareness (Dynamic Discovery)
        self.discovery = SystemDiscovery(self.config)
        # MCP Client (optional — tools are loaded in background)
        self.mcp_client = CharlieMCPClient(self.config.mcp_config_path)
        self._mcp_tools_prompt = ""  # populated after connection
        self.load_history()
        self.memory_manager = MemoryManager(self.config.memory_db_path)
        self.profile_manager = ProfileManager(
            soul_path=self.config.soul_path,
            user_path=self.config.user_path,
        )
        self.persona.soul_content = self.profile_manager.load_soul()
        self.persona.user_profile = self.profile_manager.load_user_profile()

        # WebSocket Bridge for Buddy UI
        self.bridge = CharlieBridge(brain=self, port=self.config.buddy_port)
        self.bridge_task = None

        # UI Launcher for Electron app
        self.ui_launcher = None
        if self.config.enable_buddy_ui:
            self.ui_launcher = UILauncher()
            self.ui_launcher.start()


    def load_history(self):
        if os.path.exists(self.config.history_file):
            try:
                with open(self.config.history_file, "r") as f:
                    data = json.load(f)

                # Handle new wrapper format or legacy list
                if isinstance(data, dict):
                    self.history = data.get("messages", [])
                    self.persona.emotional_state = data.get("emotional_state", "neutral")
                else:
                    self.history = data

                # Enforce max history limit
                self.history = self.history[-self.config.max_history:]
                logger.info(f"Loaded {len(self.history)} messages. Emotion: {self.persona.emotional_state}")
            except Exception as e:
                logger.error(f"history_load_error | {e}")


    async def start_mcp(self):
        """Connect MCP client and populate tool prompt."""
        await self.mcp_client.start()
        if self.mcp_client.is_available:
            self._mcp_tools_prompt = self.mcp_client.get_tools_for_prompt()
            logger.info("MCP tools loaded.")
        else:
            logger.info("MCP not available — operating in chat-only mode.")

    def save_history(self):
        try:
            self.history = self.history[-self.config.max_history:]
            data = {
                "messages": self.history,
                "emotional_state": self.persona.emotional_state,
            }
            self.persona.save_stances()
            with open(self.config.history_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"history_save_error | {e}")

    def _infer_category(self, text: str) -> str:
        keywords = {
            "preferences": ["like", "love", "prefer", "hate", "dislike", "enjoy", "favorite"],
            "work": ["work", "job", "career", "company", "boss", "colleague", "team", "project"],
            "health": ["allergic", "allergy", "medication", "doctor", "health", "diet", "exercise"],
            "family": ["wife", "husband", "partner", "child", "son", "daughter", "parent", "mother", "father", "sibling"],
            "location": ["live", "city", "country", "move", "born", "from"],
        }
        text_lower = text.lower()
        for category, words in keywords.items():
            if any(w in text_lower for w in words):
                return category
        return "general"

    async def _run_background_research(self, query: str, is_deep: bool):
        """Perform research in the background and notify when complete."""
        try:
            if is_deep:
                result = await deep_research(query, self)
            else:
                result = await web_search(query)
                
            # Generate a 1-sentence semantic summary for long-term memory
            summary = "Research complete."
            try:
                summary_prompt = f"Summarize the key takeaway of this research on '{query}' in exactly ONE sentence for my long-term memory:\n\n{result[:4000]}"
                resp = await self.fast_client.post(
                    "chat/completions",
                    json={"model": self.config.fast_llm_model, "messages": [{"role": "user", "content": summary_prompt}], "max_tokens": 100}
                )
                if resp.status_code == 200:
                    summary = resp.json()["choices"][0]["message"]["content"].strip()
            except Exception as e:
                logger.warning(f"background_summary_failed | {e}")

            # Persist to semantic memory
            research_memory.add_semantic_knowledge(query, summary)
            # Integrate into history as a system message for context injection
            self.history.append({
                "role": "system", 
                "content": f"Background research complete for '{query}'.\n\nSUMMARY: {summary}\n\nFULL REPORT:\n{result[:3000]}"
            })
            self.save_history()
            
            # Notification chime
            if self.on_thought_callback:
                self.on_thought_callback(f"Ding! Research complete on {query}. I've integrated the findings into my memory.")
                
        except Exception as e:
            logger.error(f"background_research_error | {e}")
            if self.on_thought_callback:
                self.on_thought_callback(f"My background research on {query} hit a snag.")

    async def _run_background_read(self, url: str):
        """Read a URL in the background and notify when complete."""
        if not self.config.fast_llm_key or self.config.fast_llm_key == "no-key":
            # Can't summarize without fast LLM, but still inject the snippet directly
            try:
                result = await read_url(url)
                self.history.append({
                    "role": "system",
                    "content": f"Finished reading URL: {url}.\n\nCONTENT SNIPPET:\n{result[:2000]}"
                })
                self.save_history()
                if self.on_thought_callback:
                    self.on_thought_callback("Ding! I've finished reading that page for you.")
            except Exception as e:
                logger.error(f"background_read_error | {e}")
                if self.on_thought_callback:
                    self.on_thought_callback("I couldn't finish reading the URL.")
            return
        try:
            result = await read_url(url)
            
            # Summarize content for context injection
            summary = "URL reading complete."
            try:
                summary_prompt = f"Summarize the key information from this website '{url}' in exactly ONE sentence:\n\n{result[:4000]}"
                resp = await self.fast_client.post(
                    "chat/completions",
                    json={"model": self.config.fast_llm_model, "messages": [{"role": "user", "content": summary_prompt}], "max_tokens": 100}
                )
                if resp.status_code == 200:
                    summary = resp.json()["choices"][0]["message"]["content"].strip()
            except Exception as e:
                logger.warning(f"background_read_summary_failed | {e}")

            # Integrate into history
            self.history.append({
                "role": "system", 
                "content": f"Finished reading URL: {url}.\n\nSUMMARY: {summary}\n\nCONTENT SNIPPET:\n{result[:2000]}"
            })
            self.save_history()
            
            if self.on_thought_callback:
                self.on_thought_callback("Ding! I've finished reading that page for you.")
                
        except Exception as e:
            logger.error(f"background_read_error | {e}")
            if self.on_thought_callback:
                self.on_thought_callback("I couldn't finish reading the URL.")

    async def _run_background_memory_extraction(self, user_input: str, assistant_response: str):
        """Auto-extract facts from conversation using the fast LLM."""
        if not self.config.fast_llm_key or self.config.fast_llm_key == "no-key":
            logger.debug("memory_extraction_skipped | no fast_llm_key configured")
            return
        try:
            prompt = (
                "Categorise each fact as one of: preferences, work, health, family, location, general. "
                "Return ONLY a JSON list of objects with keys 'content' and 'category'. "
                "Example: [{\"content\": \"User works as a software engineer\", \"category\": \"work\"}]. "
                "If no facts are present, return []."
                f"\n\nUser: {user_input}\nCharlie: {assistant_response}"
            )
            payload = {
                "model": self.config.fast_llm_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
                "max_tokens": 1024,
            }
            resp = await self.fast_client.post(
                "chat/completions",
                json=payload,
                timeout=30,
            )
            content = resp.json()["choices"][0]["message"]["content"]
            facts = json.loads(content)
            for fact in facts:
                if isinstance(fact, dict) and fact.get("content"):
                    self.memory_manager.store(fact["content"].strip(), "fact", fact.get("category", "general"))
                    logger.info(f"auto_fact_extracted | {fact['content'].strip()}")
        except Exception as e:
            logger.warning(f"memory_extraction_failed | {e}")

    async def _consolidate_conversation(self):
        """Summarise older conversation turns into episodic memory, truncating history."""
        if not self.config.fast_llm_key or self.config.fast_llm_key == "no-key":
            logger.debug("consolidation_skipped | no fast_llm_key configured")
            return
        try:
            slice_end = len(self.history) - 5  # Keep last 5 messages intact
            if slice_end <= 0:
                return
            to_summarise = self.history[:slice_end]
            joined = "\n".join(f"{m['role']}: {m['content']}" for m in to_summarise)
            prompt = (
                "Summarise the following conversation into a short paragraph. "
                "Include key decisions, facts, and topics discussed."
                f"\n\n{joined}"
            )
            payload = {
                "model": self.config.fast_llm_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
                "max_tokens": 512,
            }
            resp = await self.fast_client.post(
                "chat/completions",
                json=payload,
                timeout=30,
            )
            summary = resp.json()["choices"][0]["message"]["content"].strip()
            self.memory_manager.store(summary, "conversation_summary", "consolidated")
            # Remove the old messages we just summarised, keeping last 5
            self.history = self.history[-5:]
            self.save_history()
            logger.info(f"conversation_consolidated | {summary[:100]}...")
        except Exception as e:
            logger.warning(f"conversation_consolidation_failed | {e}")

    async def _call_llm_stream(self, client, model, messages, timeout=httpx.Timeout(60.0, connect=15.0)):
        """Stream completion from a single LLM backend.

        Yields content chunks. Raises on connection failure or non-2xx.
        """
        payload = {"messages": messages, "temperature": 0.3, "stream": True, "model": model}
        async with client.stream("POST", "chat/completions", json=payload, timeout=timeout) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                if line == "data: [DONE]":
                    break
                try:
                    chunk = json.loads(line[6:])
                    choices = chunk.get("choices", [])
                    if not choices:
                        continue
                    content = choices[0].get("delta", {}).get("content", "")
                    if content:
                        yield content
                except Exception:
                    continue

    async def chat(self, user_input: str) -> AsyncGenerator[str, None]:
        # Prefix handling for direct deep research (Asynchronous)
        ui_lower = user_input.lower().strip()
        # Explicit memory commands (store / forget)
        # Fast-path store: explicit "remember that X" variants
        # Broader phrasings fall through to LLM, guided by personality prompt
        store_match = re.match(
            r"^(?:(?:charlie,?\s*)?"
            r"(?:can you\s+)?"
            r"(?:i want you to\s+)?"
            r"remember\s+that\s+"
            r"|(?:charlie,?\s*)?(?:store|save)\s+this:?\s+)"
            r"(.+)$",
            user_input, re.I
        )
        if store_match:
            fact = store_match.group(1).strip()
            if fact and len(fact) > 2:
                category = self._infer_category(fact)
                self.memory_manager.store(fact, "fact", category)
                self.profile_manager.add_user_fact(fact, category)
                yield "Got it. I'll remember that."
            else:
                yield "What should I remember?"
            return
        match = re.match(r"^(?:charlie,?\s*)?forget\s+(.+)$", user_input, re.I)
        if match:
            fact = match.group(1).strip()
            if self.memory_manager.delete_by_content(fact):
                self.profile_manager.remove_user_fact(fact)
                yield "Done. I've removed that memory."
            else:
                yield "I don't have a memory matching that exactly."
            return
        if ui_lower.startswith("research ") or ui_lower.startswith("deep dive "):
            topic = user_input.split(" ", 1)[1]
            asyncio.create_task(self._run_background_research(topic, is_deep=True))
            self.history.append({"role": "user", "content": user_input})
            self.save_history()
            yield f"I'm spinning up a background thread for {topic}. I'll alert you when it's done."
            return

        # Active semantic recall + session mapping
        related = research_memory.find_related_sessions(user_input)
        semantic = research_memory.get_semantic_knowledge(user_input)
        
        # Build memory context from research_memory + memory_manager
        memory_context = ""
        if related:
            memory_context += f"\n\nPAST RELATED RESEARCH: We have previously researched: {', '.join(related)}."
        if semantic:
            memory_context += semantic
        core_facts = self.memory_manager.get_core_facts(self.config.memory_max_core_facts)
        relevant = self.memory_manager.search(user_input, limit=self.config.memory_max_recall)
        memory_context_parts = []
        if core_facts:
            memory_context_parts.append("CORE FACTS ABOUT USER:\n" + "\n".join(f"- {f}" for f in core_facts))
        if relevant:
            memory_context_parts.append("\nRELATED MEMORIES:\n" + "\n".join(f"- {m['content']}" for m in relevant))
        if memory_context_parts:
            memory_context += "\n" + "\n".join(memory_context_parts)
        if memory_context:
            memory_context += "\nUse this context if relevant."
        if self._mcp_tools_prompt:
            memory_context += self._mcp_tools_prompt

        self.history.append({"role": "user", "content": user_input})
        
        # Multi-step research loop
        for i in range(3):
            # Reload profiles so TOOL: update_soul and remembered facts are visible
            self.persona.soul_content = self.profile_manager.load_soul()
            self.persona.user_profile = self.profile_manager.load_user_profile()

            self.persona.detect_emotion(user_input)
            
            now = datetime.datetime.now()
            current_date = now.strftime("%A, %B %d, %Y")
            current_time = now.strftime("%I:%M %p")

            # Dynamic System Discovery (Hardware, Brain, Upgrades)
            manifest = self.discovery.discover_manifest(self.mcp_client)
            system_manifest_prompt = self.discovery.format_manifest_for_prompt(manifest)
            
            # Pass both metadata and text block to personality builder
            system_msg = self.persona.build_system_prompt(
                current_date, current_time, memory_context, user_input, 
                capabilities=manifest, system_manifest=system_manifest_prompt
            )
            full_reply = ""
            is_tool_call = False
            has_yielded_start = False
            
            messages = [{"role": "system", "content": system_msg}] + self.history[-self.config.max_history:]
            
            try:
                logger.debug(f"Calling LLM (Iteration {i+1})...")
                llm_start_time = time.time()
                llm_ttft_logged = False
                
                # Use LLM Router to select backend based on input complexity
                async def _local_fn(_):
                    """Call the local LLM with timeout/fallback."""
                    fallback = False
                    if not self.config.enable_local_llm:
                        logger.info("Local LLM disabled, falling back to cloud.")
                        fallback = True
                    else:
                        try:
                            chunks_yielded = 0
                            async for chunk in self._call_llm_stream(
                                self.local_client,
                                self.config.local_llm_model,
                                messages,
                                timeout=httpx.Timeout(15.0, connect=10.0),
                            ):
                                chunks_yielded += 1
                                yield chunk
                            if chunks_yielded == 0:
                                fallback = True
                        except Exception as e:
                            logger.warning(f"Local LLM failed (timeout/error): {e}. Falling back to cloud.")
                            fallback = True
                    
                    if fallback:
                        async for chunk in _cloud_fn(_):
                            yield chunk
                async def _cloud_fn(_):
                    """Call cloud LLM (fast first, fallback to NVIDIA)."""
                    backends = []
                    payload_size = len(json.dumps(messages))
                    if self.config.fast_llm_key and self.config.fast_llm_key != "no-key":
                        if payload_size < 24000:
                            backends.append((self.fast_client, self.config.fast_llm_model))
                        else:
                            logger.info(f"Payload too large ({payload_size} chars). Skipping fast LLM.")
                    backends.append((self.cloud_client, self.config.llm_model))
                    last_error = None
                    for client, model in backends:
                        try:
                            async for chunk in self._call_llm_stream(client, model, messages):
                                yield chunk
                            return  # success — stop
                        except Exception as e:
                            logger.warning(f"Cloud LLM {model} failed: {e}")
                            last_error = e
                            continue
                    raise RuntimeError(f"All cloud backends failed: {last_error}")

                # Consume the router-streamed response
                async for content in self.llm_router.route(user_input, _local_fn, _cloud_fn):
                    # Barge-in: stop yielding if user interrupted
                    if self.cancel_chat_event.is_set():
                        self.cancel_chat_event.clear()
                        return
                    full_reply += content
                    if not is_tool_call:
                        # Detect Tool Call early
                        if "TOOL:" in full_reply.upper()[:20]:
                            is_tool_call = True
                            continue

                        # Handle Thinking Tags (Common in models like Nemotron/DeepSeek)
                        if "<think>" in full_reply:
                            if "</think>" not in full_reply:
                                continue
                            # fall through: thinking block is closed, content follows

                        # Buffer only the very beginning to be 100% sure it's not "TOOL:"
                        if not has_yielded_start:
                            if not full_reply.upper().startswith("T") or len(full_reply) >= 5:
                                to_yield = full_reply
                                if "</think>" in to_yield:
                                    to_yield = to_yield.split("</think>", 1)[1]

                                if to_yield:
                                    if not llm_ttft_logged:
                                        llm_ttft_ms = (time.time() - llm_start_time) * 1000
                                        logger.info(f"pipeline_stage | stage=llm_ttft | latency_ms={llm_ttft_ms:.1f}")
                                        llm_ttft_logged = True
                                    yield to_yield
                                    has_yielded_start = True
                        else:
                            if not llm_ttft_logged:
                                llm_ttft_ms = (time.time() - llm_start_time) * 1000
                                logger.info(f"pipeline_stage | stage=llm_ttft | latency_ms={llm_ttft_ms:.1f}")
                                llm_ttft_logged = True
                            yield content
                # Clean up thinking tags for history
                full_reply = re.sub(r'<think>.*?</think>', '', full_reply, flags=re.DOTALL).strip()
                reply = full_reply.strip()
                reply_upper = reply.upper()
                if is_tool_call:
                    if "WEB_SEARCH" in reply_upper:
                        match = re.search(r'web_search\s*\(\s*["\'](.*?)["\']\s*\)', reply, re.IGNORECASE)
                        if match:
                            query = match.group(1)
                            self.history.append({"role": "assistant", "content": reply})
                            yield f"Searching for {query}..."
                            # Synchronous search — inject results into history, loop again
                            search_result = await web_search(query)
                            self.history.append({
                                "role": "system",
                                "content": f"Web search results for '{query}' (searched {current_date}):\n\n{search_result}\n\nNow answer the user's original question using these results. Be direct, concise, and in character."
                            })
                            self.save_history()
                            # Continue the for-loop to let LLM answer with results
                            continue
                            
                    elif "DEEP_RESEARCH" in reply_upper:
                        match = re.search(r'deep_research\s*\(\s*["\'](.*?)["\']\s*\)', reply, re.IGNORECASE)
                        if match:
                            topic = match.group(1)
                            self.history.append({"role": "assistant", "content": reply})
                            asyncio.create_task(self._run_background_research(topic, is_deep=True))
                            yield "I'm starting a deep research task in the background."
                            return
                            
                    elif "READ_URL" in reply_upper:
                        match = re.search(r'read_url\s*\(\s*["\'](.*?)["\']\s*\)', reply, re.IGNORECASE)
                        if match:
                            url = match.group(1)
                            self.history.append({"role": "assistant", "content": reply})
                            asyncio.create_task(self._run_background_read(url))
                            yield "I'm reading through that link in the background. I'll let you know when I'm done."
                            return
                    
                    elif "UPDATE_SOUL" in reply_upper:
                        match = re.search(r'update_soul\s*\(\s*["\'](.*?)["\']\s*,\s*["\'](.*?)["\']\s*\)', reply, re.IGNORECASE | re.DOTALL)
                        if match:
                            section = match.group(1).strip()
                            content = match.group(2).strip()
                            self.history.append({"role": "assistant", "content": reply})
                            if self.profile_manager.update_soul_section(section, content):
                                # Reload soul content for subsequent turns
                                self.persona.soul_content = self.profile_manager.load_soul()
                                yield f"I've updated my {section}."
                            else:
                                yield "I couldn't update that section."
                            return
                    
                    # MCP Tool Call: TOOL: server_name/tool_name({"param": "value"})
                    elif self.mcp_client.is_available and "TOOL:" in reply_upper:
                        mcp_match = re.match(
                            r'TOOL:\s*(\S+?)(?:/(\S+?))?\s*\(\s*(\{.*?\})\s*\)',
                            reply, re.IGNORECASE | re.DOTALL
                        )
                        if mcp_match:
                            server_or_tool = mcp_match.group(1)
                            tool_short = mcp_match.group(2)
                            args_str = mcp_match.group(3)
                            # Build full tool key
                            if tool_short:
                                tool_key = f"{server_or_tool}/{tool_short}"
                            else:
                                tool_key = server_or_tool
                            try:
                                arguments = json.loads(args_str)
                            except json.JSONDecodeError:
                                yield "I couldn't parse the tool arguments."
                                return

                            self.history.append({"role": "assistant", "content": reply})
                            yield "Let me check that... "

                            # Execute the tool call
                            result = await self.mcp_client.call_tool(tool_key, arguments)

                            # Feed result as observation and let LLM respond
                            self.history.append({
                                "role": "system",
                                "content": f"OBSERVATION from tool '{tool_key}':\n{result}\n\nNow answer the user's original question using this observation."
                            })
                            self.save_history()
                            # Continue the research loop to let LLM answer with observation
                            continue
                
                # Final reply
                if reply:
                    self.history.append({"role": "assistant", "content": reply})
                    
                    # Proactive memory mention (log for next turn)
                    if relevant and self.config.memory_auto_extract:
                        top_memory = relevant[0]["content"]
                        mem_kws = self.memory_manager._extract_keywords(top_memory)
                        if any(kw in ui_lower for kw in mem_kws):
                            logger.info(f"proactive_memory | {top_memory}")
                    
                    # Auto-extract facts after turn (background)
                    total_words = len(user_input.split()) + len(reply.split())
                    if self.config.memory_auto_extract and total_words >= self.config.memory_extract_threshold_words:
                        asyncio.create_task(
                            self._run_background_memory_extraction(user_input, reply)
                        )
                    
                    # Conversation consolidation trigger
                    if len(self.history) >= self.config.memory_consolidate_after * 2:
                        asyncio.create_task(self._consolidate_conversation())
                    
                    self.save_history()
                return
                
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    logger.warning("Rate limit hit (429).")
                    try:
                        payload = {
                            "model": self.config.llm_model,
                            "messages": [
                                {"role": "system", "content": "You are Charlie. You are currently hitting a rate limit. Give a 1-sentence blunt, in-character refusal."},
                            ],
                            "max_tokens": 20,
                            "temperature": 0.7
                        }
                        response = await self.cloud_client.post("chat/completions", json=payload, timeout=5.0)
                        if response.status_code == 200:
                            yield response.json()["choices"][0]["message"]["content"].strip()
                            return
                    except Exception:
                        pass
                    yield self.persona.get_rate_limit_message()
                    return
                yield f"My brain returned an error: {e.response.status_code}."
                return
            except httpx.ConnectError:
                yield "I can't connect to my brain. Please check your internet."
                return
            except Exception as e:
                logger.error(f"llm_unexpected_error | {type(e).__name__}: {e}")
                yield "My brain is having some unexpected trouble."
                return
        
        return
    def cancel_chat(self):
        """Signal the chat generator to stop yielding (barge-in)."""
        self.cancel_chat_event.set()

    async def close(self):
        if hasattr(self, 'ui_launcher') and self.ui_launcher:
            self.ui_launcher.stop()
        if hasattr(self, 'bridge_task') and self.bridge_task:
            self.bridge_task.cancel()
        if self.fast_client:
            await self.fast_client.aclose()
        if self.cloud_client:
            await self.cloud_client.aclose()
        if self.local_client:
            await self.local_client.aclose()
        logger.info("Brain connection closed.")
