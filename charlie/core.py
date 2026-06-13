import json
import logging
import httpx
import os
import re
import datetime
import asyncio
from typing import List, Dict
from charlie.research import web_search, read_url

logger = logging.getLogger("charlie.core")

class Brain:
    def __init__(self, config):
        self.config = config
        self.history: List[Dict[str, str]] = []
        
        base_url = self.config.llm_url
        if not base_url.endswith("/"):
            base_url += "/"
            
        self.client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {self.config.llm_key}"},
            timeout=httpx.Timeout(60.0, connect=15.0),
            verify=True
        )
        self.base_prompt = (
            "You are Charlie, a brilliant, witty, and intellectually curious AI assistant. Speak ONLY in English.\n"
            "You have a warm, slightly deadpan but friendly personality—like a well-read friend who is always up for a discovery.\n\n"
            "Rules of Engagement:\n"
            "- Short & Spoken: 1-2 punchy sentences max. No formatting, lists, or symbols.\n"
            "- The 'No Guess' Rule: Never assume intent. If a request is ambiguous, ask a single, sharp clarifying question.\n"
            "- Curiosity Loop: After providing an answer, always ask a curious follow-up question to delve deeper into the topic.\n"
            "- The 'Always Verify' Rule: You MUST use research tools for ANY facts that could have changed since late 2023. This includes news, weather, stock prices, and current events.\n"
            "- Pivot on Failure: If a search returns no results, pivot to a broader query and try again.\n\n"
            "Tools Available:\n"
            "- TOOL: web_search(\"query\") - For news, facts, and live data.\n"
            "- TOOL: read_url(\"https://...\") - To extract content from a specific site.\n\n"
            "Trigger a tool by starting your response with EXACTLY the tool text."
        )
        self.load_history()

    def load_history(self):
        if os.path.exists(self.config.history_file):
            try:
                with open(self.config.history_file, "r") as f:
                    self.history = json.load(f)
                # Enforce max history limit
                self.history = self.history[-self.config.max_history:]
                logger.info(f"Loaded {len(self.history)} messages from history.")
            except Exception as e:
                logger.error(f"history_load_error | {e}")

    def save_history(self):
        try:
            self.history = self.history[-self.config.max_history:]
            with open(self.config.history_file, "w") as f:
                json.dump(self.history, f, indent=2)
        except Exception as e:
            logger.error(f"history_save_error | {e}")

    async def chat(self, user_input: str) -> str:
        self.history.append({"role": "user", "content": user_input})
        
        # Multi-step research loop
        for i in range(3):
            now = datetime.datetime.now()
            current_date = now.strftime("%A, %B %d, %Y")
            current_time = now.strftime("%I:%M %p")
            
            system_msg = (
                f"CORE DIRECTIVE: Today is {current_date}. The current time is {current_time}. "
                "Any event or data requested must be searched if it is relative to 'today' or 'now'.\n\n"
                f"{self.base_prompt}"
            )
            
            messages = [{"role": "system", "content": system_msg}] + self.history[-self.config.max_history:]
            
            try:
                logger.debug(f"Calling LLM (Iteration {i+1})...")
                
                # Exponential backoff for 429 Rate Limits
                response = None
                for retry in range(3):
                    response = await self.client.post(
                        "chat/completions",
                        json={"model": self.config.llm_model, "messages": messages, "temperature": 0.0}
                    )
                    if response.status_code == 429:
                        wait = (retry + 1) * 3
                        logger.warning(f"Rate limited (429). Retry {retry+1}/3 in {wait}s...")
                        await asyncio.sleep(wait)
                        continue
                    break
                
                response.raise_for_status()
                data = response.json()
                reply = data["choices"][0]["message"]["content"]
                
                logger.debug(f"Brain reply: {reply}")
                
                # Check for Tool usage (Robust to whitespace and quotes)
                reply_upper = reply.strip().upper()
                
                if "TOOL:" in reply_upper and "WEB_SEARCH" in reply_upper:
                    match = re.search(r'web_search\s*\(\s*["\'](.*?)["\']\s*\)', reply, re.IGNORECASE)
                    if match:
                        query = match.group(1)
                        logger.info(f"RESEARCHING: {query}")
                        result = await web_search(query)
                        self.history.append({"role": "assistant", "content": reply})
                        self.history.append({"role": "user", "content": f"Search result: {result}"})
                        continue
                        
                elif "TOOL:" in reply_upper and "READ_URL" in reply_upper:
                    match = re.search(r'read_url\s*\(\s*["\'](.*?)["\']\s*\)', reply, re.IGNORECASE)
                    if match:
                        url = match.group(1)
                        logger.info(f"READING URL: {url}")
                        result = await read_url(url)
                        self.history.append({"role": "assistant", "content": reply})
                        self.history.append({"role": "user", "content": f"Website content: {result}"})
                        continue
                            
                # No tool call detected, this is the final answer
                self.history.append({"role": "assistant", "content": reply})
                self.save_history()
                return reply
                
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    return "My brain is a bit overwhelmed with requests right now. Let's try again in a moment."
                logger.error(f"llm_status_error | {e.response.status_code}")
                return f"My brain returned an error: {e.response.status_code}."
            except httpx.ConnectError as e:
                logger.error(f"llm_connection_error | {e}")
                return "I can't connect to my brain. Please check your internet."
            except Exception as e:
                logger.error(f"llm_unexpected_error | {type(e).__name__}: {e}")
                return "My brain is having some unexpected trouble."
        
        # If loop exhausted
        final_reply = self.history[-1]["content"] if self.history else "I'm a bit confused."
        
        # Ensure we never return raw TOOL strings to user
        if "TOOL:" in final_reply.upper():
             logger.info("Loop exhausted with tool call. Requesting final synthesis...")
             try:
                 messages.append({"role": "assistant", "content": final_reply})
                 messages.append({"role": "user", "content": "The search is complete. Please synthesize the findings into a spoken answer."})
                 response = await self.client.post(
                    "chat/completions",
                    json={ "model": self.config.llm_model, "messages": messages, "temperature": 0.5 }
                 )
                 response.raise_for_status()
                 final_reply = response.json()["choices"][0]["message"]["content"]
             except Exception:
                 final_reply = "I've gathered some info, but I'm still processing it. What else would you like to know?"
             
        self.save_history()
        return final_reply

    def clear_history(self):
        self.history = []
        self.save_history()

    async def close(self):
        if self.client:
            await self.client.aclose()
            logger.info("Brain connection closed.")
