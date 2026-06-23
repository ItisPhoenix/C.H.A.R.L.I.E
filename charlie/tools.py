import os
import subprocess
import logging

from typing import Callable, Dict, Any, List

logger = logging.getLogger("charlie.tools")

class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, Dict[str, Any]] = {}

    def register_tool(self, name: str, description: str, schema: Dict[str, Any]):
        def decorator(func: Callable[..., Any]):
            self._tools[name] = {
                "func": func,
                "description": description,
                "schema": schema
            }
            return func
        return decorator

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        definitions = []
        for name, info in self._tools.items():
            definitions.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": info["description"],
                    "parameters": info["schema"]
                }
            })
        return definitions

    def build_tool_prompt(self) -> str:
        """Build a plain-text tool description for the system prompt."""
        lines = []
        for name, info in self._tools.items():
            params = info["schema"].get("properties", {})
            param_parts = []
            for pname, pinfo in params.items():
                required = pname in info["schema"].get("required", [])
                param_parts.append(f"{pname}: {pinfo.get('description', '')}{' (required)' if required else ''}")
            param_str = ", ".join(param_parts) if param_parts else "no arguments"
            lines.append(f"- {name}({param_str}): {info['description']}")
        return "\n".join(lines)

    def execute_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        if name not in self._tools:
            logger.error("Tool '%s' not found.", name)
            return f"Error: Tool '{name}' is not registered."

        func = self._tools[name]["func"]
        try:
            logger.info("Executing tool '%s' with arguments: %s", name, arguments)
            result = func(**arguments)
            return str(result)
        except Exception as e:  # pragma: no cover - defensive
            logger.exception("Error executing tool '%s': %s", name, e)
            return f"Error executing tool '{name}': {str(e)}"

# Global tool registry
registry = ToolRegistry()

@registry.register_tool(
    name="web_search",
    description="Search the web for up-to-date information.",
    schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query to run."
            }
        },
        "required": ["query"]
    }
)
def web_search(query: str) -> str:
    tavily_key = os.getenv("TAVILY_API_KEY")
    exa_key = os.getenv("EXA_API_KEY")

    import httpx

    if exa_key:
        try:
            logger.info("Performing Exa search for: %s", query)
            response = httpx.post(
                "https://api.exa.ai/search",
                headers={
                    "x-api-key": exa_key,
                    "content-type": "application/json"
                },
                json={
                    "query": query,
                    "numResults": 5,
                    "text": True
                },
                timeout=10.0,
            )
            if response.status_code == 200:
                data = response.json()
                results = []
                for item in data.get("results", []):
                    title = item.get("title", "No Title")
                    url = item.get("url", "No URL")
                    text = item.get("text", "") or ""
                    text = text[:800] + "..." if len(text) > 800 else text
                    results.append(f"Title: {title}\nURL: {url}\nContent: {text}")
                return "\n\n".join(results) or "No results found."
            logger.error("Exa search failed with %s: %s", response.status_code, response.text)
        except Exception:
            logger.exception("Exa search error for query: %s", query)

    if tavily_key:
        try:
            logger.info("Performing Tavily search for: %s", query)
            response = httpx.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": tavily_key,
                    "query": query,
                    "max_results": 5,
                    "include_raw_content": False,
                },
                timeout=10.0,
            )
            if response.status_code == 200:
                data = response.json()
                results = []
                for item in data.get("results", []):
                    title = item.get("title", "No Title")
                    url = item.get("url", "No URL")
                    content = item.get("content", "") or ""
                    results.append(f"Title: {title}\nURL: {url}\nContent: {content}")
                return "\n\n".join(results) or "No results found."
            logger.error("Tavily search failed with %s: %s", response.status_code, response.text)
        except Exception:
            logger.exception("Tavily search error for query: %s", query)

    try:
        logger.info("Performing DuckDuckGo fallback search for: %s", query)
        response = httpx.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8.0,
        )
        if response.status_code == 200:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(response.text, "html.parser")
            results = [item.get_text(strip=True) for item in soup.find_all("a", class_="result__snippet")[:5]]
            return "\n".join(results) or "No fallback search results found."
    except Exception:
        logger.exception("Fallback search error for query: %s", query)

    return "Error: Web search failed and no search API keys were configured."

@registry.register_tool(
    name="shell_execute",
    description="Run a shell command and get output. Risky commands are blocked.",
    schema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute."
            }
        },
        "required": ["command"]
    }
)
def shell_execute(command: str) -> str:
    blocked_keywords = [
        "rm -rf", "mkfs", "dd if=", "format ", "shutdown",
        "reboot", "poweroff", ":(){:|:&};:"
    ]
    lowered = command.lower()
    for keyword in blocked_keywords:
        if keyword in lowered:
            return f"Error: Command execution blocked due to risky keyword: '{keyword}'"

    try:
        process = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=15.0,
        )
        return f"STDOUT:\n{process.stdout}\nSTDERR:\n{process.stderr}"
    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 15 seconds."
    except Exception as e:
        return f"Error executing shell command: {str(e)}"

@registry.register_tool(
    name="file_read",
    description="Read the text content of a file.",
    schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "The path to the file to read."
            }
        },
        "required": ["path"]
    }
)
def file_read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except Exception as e:
        return f"Error reading file: {str(e)}"

@registry.register_tool(
    name="file_write",
    description="Write content to a file (creates or overwrites it).",
    schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "The path to the file to write."
            },
            "content": {
                "type": "string",
                "description": "The text content to write to the file."
            }
        },
        "required": ["path", "content"]
    }
)
def file_write(path: str, content: str) -> str:
    try:
        destination = os.path.dirname(os.path.abspath(path))
        os.makedirs(destination, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return f"Successfully wrote to {path}"
    except Exception as e:
        return f"Error writing file: {str(e)}"
