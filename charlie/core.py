import json
import logging
import httpx
import os
import re
from typing import List, Dict
from charlie.research import web_search, read_url

logging.basicConfig(level=logging.INFO)
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
        self.system_prompt = (
            "You are Charlie, a brilliant, witty, and intellectually curious AI assistant. Speak ONLY in English.\n"
            "You have a warm, slightly deadpan but friendly personality—like a well-read friend who is always up for a discovery.\n\n"
            "Rules of Engagement:\n"
            "- Short & Spoken: 1-2 punchy sentences max. No formatting, lists, or symbols.\n"
            "- The 'No Guess' Rule: Never assume intent. If a request is ambiguous, ask a single, sharp clarifying question.\n"
            "- Curiosity Loop: After providing an answer, always ask a curious follow-up question to delve deeper into the topic.\n"
            "- Research Rigor: For ANY real-time data, news, or facts, you MUST use the research tools. Use multiple queries if needed.\n"
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
        
        # Iteration loop for multi-step research
        for _ in range(3):
            messages = [{"role": "system", "content": self.system_prompt}] + self.history[-self.config.max_history:]
            
            try:
                response = await self.client.post(
                    "chat/completions",
                    json={
                        "model": self.config.llm_model,
                        "messages": messages,
                        "temperature": 0.0
                    }
                )
                response.raise_for_status()
                data = response.json()
                reply = data["choices"][0]["message"]["content"]
                
                # Check for Tool usage
                if "TOOL: web_search(" in reply:
                    match = re.search(r'web_search\("([^"]+)"\)', reply)
                    if match:
                        query = match.group(1)
                        logger.info(f"Using tool: web_search for {query}")
                        result = await web_search(query)
                        self.history.append({"role": "assistant", "content": reply})
                        self.history.append({"role": "user", "content": f"Search result: {result}"})
                        continue
                        
                elif "TOOL: read_url(" in reply:
                    match = re.search(r'read_url\("([^"]+)"\)', reply)
                    if match:
                        url = match.group(1)
                        logger.info(f"Using tool: read_url for {url}")
                        result = await read_url(url)
                        self.history.append({"role": "assistant", "content": reply})
                        self.history.append({"role": "user", "content": f"Website content: {result}"})
                        continue
                            
                self.history.append({"role": "assistant", "content": reply})
                self.save_history()
                return reply
                
            except httpx.ConnectError as e:
                logger.error(f"llm_connection_error | Base URL: {self.client.base_url} | Error: {e}")
                return f"I can't connect to my brain. Please check if {self.config.llm_url} is reachable."
            except httpx.HTTPStatusError as e:
                logger.error(f"llm_status_error | {e.response.status_code} | {e.response.text}")
                return f"My brain returned an error: {e.response.status_code}. Please check your API key."
            except Exception as e:
                logger.error(f"llm_unexpected_error | {type(e).__name__}: {e}")
                return "My brain is having some unexpected trouble."
        
        final_reply = self.history[-1]["content"] if self.history else "I'm a bit confused."
        self.save_history()
        return final_reply

    def clear_history(self):
        self.history = []
        self.save_history()

    async def close(self):
        if self.client:
            await self.client.aclose()
            logger.info("Brain connection closed.")
