"""Charlie tool registry and built-in tools.

All tool definitions, execution logic, and provider integrations live here.
No business logic -- just tool I/O.
"""

import os
import re
import subprocess
import sys
import logging

import httpx

from typing import Callable, Dict, Any, List

from charlie.config import config
from charlie.session_store import SessionStore

logger = logging.getLogger("charlie.tools")

# --- Search tuning ---
SEARCH_RESULT_LIMIT = 5
CONTENT_MAX_CHARS = 800
MIN_CLEANED_WORDS = 2

# --- HTTP timeouts (seconds) ---
SEARXNG_TIMEOUT = 10.0
EXA_TIMEOUT = 10.0
TAVILY_TIMEOUT = 10.0
DDG_TIMEOUT = 8.0

# --- DuckDuckGo ---
DDG_MIN_CONTENT_LEN = 20
DDG_ACCEPTED_STATUSES = (200, 202)
DDG_USER_AGENT = "Mozilla/5.0"

# --- Shell ---
SHELL_TIMEOUT = 10.0

# --- SearXNG keyword detection ---
_TIME_SENSITIVE_KEYWORDS = ("today", "new", "recent", "latest", "breaking")
_NEWS_KEYWORDS = ("news", "headline", "story", "stories")


# Pre-compiled regex for stripping conversational fluff from search queries.
_FLUFF_WORDS = re.compile(
    r'\b(please|could you|can you|tell me|what are|what is|what\'s|show me|find me|'
    r'i want to know|i need|i\'m looking for|the latest|from today|right now|currently|'
    r'breaking|just|top|best|good|great)\b',
    re.IGNORECASE,
)


# Windows CMD built-ins that hang subprocess.run(shell=True) because they
# prompt for user input.  Each entry: (compiled regex, PowerShell replacement).
# Using prefix patterns so "date +%H:%M" matches just like "date".
_WIN_CMD_PATTERNS = [
    (re.compile(r'^date\b', re.IGNORECASE),
     'powershell -NoProfile -Command "Get-Date -Format \\"yyyy-MM-dd HH:mm:ss\\""'),
    (re.compile(r'^time\b', re.IGNORECASE),
     'powershell -NoProfile -Command "Get-Date -Format \\"HH:mm:ss\\""'),
]


# Cross-platform volume command translations (wrong OS -> Windows equivalent)
_AMIXER_SET_RE = re.compile(r"amixer\s+set\s+Master\s+(\d+)\%", re.IGNORECASE)
_AMIXER_DOWN_RE = re.compile(r"amixer\s+set\s+Master\s+(\d+)\%-", re.IGNORECASE)
_OSCRIPT_VOL_RE = re.compile(r"osascript\s.*[Ss]et\s+[Vv]olume\s+([\d.]+)", re.IGNORECASE)




class ToolRegistry:
    """Registry of tools the LLM can call."""

    def __init__(self) -> None:
        self._tools: Dict[str, Dict[str, Any]] = {}

    def register_tool(self, name: str, description: str, schema: Dict[str, Any], is_interactive: bool = False):
        def decorator(func: Callable[..., Any]):
            self._tools[name] = {
                "func": func,
                "description": description,
                "schema": schema,
                "is_interactive": is_interactive,
            }
            return func
        return decorator

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": info["description"],
                    "parameters": info["schema"],
                },
            }
            for name, info in self._tools.items()
        ]

    def is_interactive(self, name: str) -> bool:
        return self._tools.get(name, {}).get("is_interactive", False)

    def build_tool_prompt(self) -> str:
        """Build a plain-text tool description for the system prompt."""
        lines = []
        for name, info in self._tools.items():
            params = info["schema"].get("properties", {})
            required = set(info["schema"].get("required", []))
            param_parts = [
                f"{pname}: {pinfo.get('description', '')}"
                + (" (required)" if pname in required else "")
                for pname, pinfo in params.items()
            ]
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
            return f"Error executing tool '{name}': {e}"


# Global tool registry
registry = ToolRegistry()


# ---------------------------------------------------------------------------
# Built-in tools
# ---------------------------------------------------------------------------

def _clean_search_query(query: str) -> str:
    """Strip conversational fluff from a search query."""
    cleaned = _FLUFF_WORDS.sub("", query).strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned if len(cleaned.split()) >= MIN_CLEANED_WORDS else query


def _is_ddg_result_valid(text: str) -> bool:
    """DuckDuckGo result is valid if it has meaningful content."""
    return bool(text) and len(text) >= DDG_MIN_CONTENT_LEN


def _truncate(text: str, limit: int = CONTENT_MAX_CHARS) -> str:
    return text[:limit] + "..." if len(text) > limit else text


@registry.register_tool(
    name="web_search",
    description="Search the web for up-to-date information.",
    schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query to run.",
            }
        },
        "required": ["query"],
    },
)
def web_search(query: str) -> str:
    cleaned = _clean_search_query(query)

    searxng_url = os.getenv("SEARXNG_URL", "")
    tavily_key = os.getenv("TAVILY_API_KEY")
    exa_key = os.getenv("EXA_API_KEY")

    # Tier 1: SearXNG (self-hosted, no API key needed)
    if searxng_url:
        try:
            logger.info("SearXNG search: original=%r cleaned=%r", query, cleaned)
            base = searxng_url.rstrip("/")
            q_lower = cleaned.lower()
            params: Dict[str, str] = {"q": cleaned, "format": "json", "language": "en"}
            if any(kw in q_lower for kw in _TIME_SENSITIVE_KEYWORDS):
                params["time_range"] = "day"
            if any(kw in q_lower for kw in _NEWS_KEYWORDS):
                params["categories"] = "news"
            response = httpx.get(f"{base}/search", params=params, timeout=SEARXNG_TIMEOUT)
            if response.status_code == 200:
                results = []
                for item in response.json().get("results", [])[:SEARCH_RESULT_LIMIT]:
                    content = item.get("content", "") or ""
                    if not _is_ddg_result_valid(content):
                        continue
                    results.append(
                        f"Title: {item.get('title', 'No Title')}\n"
                        f"URL: {item.get('url', 'No URL')}\n"
                        f"Content: {_truncate(content)}"
                    )
                if results:
                    return "\n\n".join(results)
            logger.error(
                "SearXNG failed with status %s for query %r: %s",
                response.status_code, cleaned, response.text,
            )
        except Exception:
            logger.exception("SearXNG search error for query: %s", cleaned)

    # Tier 2: Exa
    if exa_key:
        try:
            logger.info("Exa search: original=%r cleaned=%r", query, cleaned)
            response = httpx.post(
                "https://api.exa.ai/search",
                headers={"x-api-key": exa_key, "content-type": "application/json"},
                json={"query": cleaned, "numResults": SEARCH_RESULT_LIMIT, "text": True},
                timeout=EXA_TIMEOUT,
            )
            if response.status_code == 200:
                results = []
                for item in response.json().get("results", []):
                    results.append(
                        f"Title: {item.get('title', 'No Title')}\n"
                        f"URL: {item.get('url', 'No URL')}\n"
                        f"Content: {_truncate(item.get('text', '') or '')}"
                    )
                return "\n\n".join(results) or "No results found."
            logger.error(
                "Exa search failed with status %s for query %r: %s",
                response.status_code, cleaned, response.text,
            )
        except Exception:
            logger.exception("Exa search error for query: %s", cleaned)

    # Tier 3: Tavily
    if tavily_key:
        try:
            logger.info("Tavily search: original=%r cleaned=%r", query, cleaned)
            response = httpx.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": tavily_key,
                    "query": cleaned,
                    "max_results": SEARCH_RESULT_LIMIT,
                    "include_raw_content": False,
                },
                timeout=TAVILY_TIMEOUT,
            )
            if response.status_code == 200:
                results = []
                for item in response.json().get("results", []):
                    results.append(
                        f"Title: {item.get('title', 'No Title')}\n"
                        f"URL: {item.get('url', 'No URL')}\n"
                        f"Content: {item.get('content', '') or ''}"
                    )
                return "\n\n".join(results) or "No results found."
            logger.error(
                "Tavily search failed with status %s for query %r: %s",
                response.status_code, cleaned, response.text,
            )
        except Exception:
            logger.exception("Tavily search error for query: %s", cleaned)

    # Tier 4: DuckDuckGo fallback
    try:
        logger.info("DuckDuckGo fallback search: original=%r cleaned=%r", query, cleaned)
        from bs4 import BeautifulSoup

        for endpoint in ("lite", "html"):
            try:
                response = httpx.get(
                    f"https://{endpoint}.duckduckgo.com/{endpoint}/",
                    params={"q": cleaned},
                    headers={"User-Agent": DDG_USER_AGENT},
                    timeout=DDG_TIMEOUT,
                )
                if response.status_code in DDG_ACCEPTED_STATUSES:
                    soup = BeautifulSoup(response.text, "html.parser")
                    if endpoint == "lite":
                        snippets = soup.find_all("td", class_="result-snippet")[:SEARCH_RESULT_LIMIT]
                    else:
                        snippets = soup.find_all("a", class_="result__snippet")[:SEARCH_RESULT_LIMIT]
                    results = [s.get_text(strip=True) for s in snippets]
                    if results:
                        return "\n".join(results)
            except Exception:
                logger.warning(
                    "DuckDuckGo %s endpoint failed for query %r", endpoint, cleaned,
                    exc_info=True,
                )
                continue
    except ImportError:
        logger.warning("BeautifulSoup not installed, DuckDuckGo fallback unavailable")

    return "Error: Web search failed and no search API keys were configured."


@registry.register_tool(
    name="shell_execute",
    description="Run a shell command and get output. Risky commands are blocked.",
    schema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute.",
            }
        },
        "required": ["command"],
    },
    is_interactive=True,
)
def shell_execute(command: str) -> str:
    _BLOCKED_KEYWORDS = ("rm -rf", "mkfs", "dd if=", "format ", "shutdown", "reboot", "poweroff")
    lowered = command.lower().strip()
    for keyword in _BLOCKED_KEYWORDS:
        if keyword in lowered:
            return f"Error: Command blocked -- risky keyword '{keyword}'"

    # Block bare interactive shells and conversational nonsense
    _SHELL_NAMES = ("cmd", "cmd.exe", "powershell", "powershell.exe")
    if lowered in _SHELL_NAMES:
        return "Error: Cannot open an interactive shell. Specify a command."
    _CONVERSATIONAL = ("stop", "start", "cancel", "wait", "halt")
    if lowered in _CONVERSATIONAL:
        return f"Error: '{lowered}' is not a shell command."
    if "pkill" in lowered or "killall" in lowered:
        return "Error: Process kill commands are not allowed."

    # Cross-platform volume command translation (wrong OS -> Windows)
    m = _AMIXER_SET_RE.search(command)
    if m:
        pct = int(m.group(1))
        vol = int(pct / 100 * 65535)
        command = f"nircmd.exe setsysvolume {vol}"
        logger.info("Translated amixer to nircmd: %s", command)
    else:
        m = _OSCRIPT_VOL_RE.search(command)
        if m:
            frac = float(m.group(1))
            vol = int(min(max(frac, 0), 1) * 65535)
            command = f"nircmd.exe setsysvolume {vol}"
            logger.info("Translated osascript volume to nircmd: %s", command)

    # On Windows, CMD built-ins (date, time, dir, etc.) hang when run via
    # subprocess.run(shell=True) because they wait for interactive input.
    # Replace using prefix matching so "date +%H:%M" matches just like "date".
    if sys.platform == "win32":
        for pattern, replacement in _WIN_CMD_PATTERNS:
            if pattern.match(command.strip()):
                command = replacement
                break

    try:
        process = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=SHELL_TIMEOUT,
        )
        parts = []
        if process.stdout and process.stdout.strip():
            parts.append(f"STDOUT:\n{process.stdout.strip()}")
        if process.stderr and process.stderr.strip():
            parts.append(f"STDERR:\n{process.stderr.strip()}")
        return "\n".join(parts) or "Command executed successfully (no output)."
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {SHELL_TIMEOUT}s."
    except Exception as e:
        logger.exception("Shell command error: %s", command)
        return f"Error executing shell command: {e}"


@registry.register_tool(
    name="file_read",
    description="Read the text content of a file.",
    schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "The path to the file to read.",
            }
        },
        "required": ["path"],
    },
)
def file_read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except Exception as e:
        logger.exception("File read error: %s", path)
        return f"Error reading file: {e}"


@registry.register_tool(
    name="file_write",
    description="Write content to a file (creates or overwrites it).",
    schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "The path to the file to write.",
            },
            "content": {
                "type": "string",
                "description": "The text content to write to the file.",
            },
        },
        "required": ["path", "content"],
    },
)
def file_write(path: str, content: str) -> str:
    try:
        dest_dir = os.path.dirname(os.path.abspath(path))
        os.makedirs(dest_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return f"Successfully wrote to {path}"
    except Exception as e:
        logger.exception("File write error: %s", path)
        return f"Error writing file: {e}"


_MEMORY_MAX_CHARS = {
    "memory": 2200,
    "user": 1375,
}


@registry.register_tool(
    name="memory",
    description="Manage persistent memory files (MEMORY.md for system context, USER.md for user preferences). Actions: add appends text, replace swaps old_text with content, remove deletes a substring match.",
    schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "replace", "remove"],
                "description": "The action to perform: add appends, replace swaps a substring, remove deletes a substring."
            },
            "target": {
                "type": "string",
                "enum": ["memory", "user"],
                "description": "Which file to modify: memory = MEMORY.md (system context, max 2200 chars), user = USER.md (user preferences, max 1375 chars)."
            },
            "content": {
                "type": "string",
                "description": "Text content to add or use as replacement (required for add/replace, ignored for remove)."
            },
            "old_text": {
                "type": "string",
                "description": "Substring to find and replace or remove (required for replace/remove, ignored for add)."
            }
        },
        "required": ["action", "target"]
    },
)
def memory(action: str, target: str, content: str = "", old_text: str = "") -> str:
    if target not in _MEMORY_MAX_CHARS:
        return f"Error: target must be 'memory' or 'user', got '{target}'."

    max_chars = _MEMORY_MAX_CHARS[target]
    path = config.memory_file if target == "memory" else config.user_file

    try:
        existing = ""
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                existing = handle.read()

        if action == "add":
            updated = existing + content if existing else content
        elif action == "replace":
            if not old_text:
                return "Error: old_text is required for replace actions."
            if old_text not in existing:
                return f"Error: old_text not found in {path}."
            matches = existing.count(old_text)
            if matches > 1:
                return (
                    f"Error: old_text matched {matches} times in {path}; "
                    "provide a more specific string."
                )
            updated = existing.replace(old_text, content, 1)
        elif action == "remove":
            if not old_text:
                return "Error: old_text is required for remove actions."
            if old_text not in existing:
                return f"Error: old_text not found in {path}."
            matches = existing.count(old_text)
            if matches > 1:
                return (
                    f"Error: old_text matched {matches} times in {path}; "
                    "provide a more specific string."
                )
            updated = existing.replace(old_text, "", 1)
        else:
            return f"Error: Unsupported action '{action}'."

        if len(updated) > max_chars:
            return (
                f"Error: {path} would be {len(updated)} chars (max {max_chars}). "
                "Consolidate entries before adding more."
            )

        with open(path, "w", encoding="utf-8") as handle:
            handle.write(updated)

        return f"Updated {path}: {len(updated)}/{max_chars} chars."
    except Exception as e:
        logger.exception("Memory tool error: action=%s target=%s", action, target)
        return f"Error updating memory: {e}"


@registry.register_tool(
    name="session_search",
    description="Search past conversation history stored in the session database. Returns matching messages from previous sessions.",
    schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query to find in past conversations."
            }
        },
        "required": ["query"]
    },
)
def session_search(query: str) -> str:
    store = None
    try:
        store = SessionStore(db_path=config.session_db_path)
        results = store.search(query, limit=5)
    except Exception as e:
        logger.exception("Session search error: %s", query)
        return f"Error searching session history: {e}"
    finally:
        if store is not None:
            try:
                store.close()
            except Exception:
                logger.debug("Session store close failed", exc_info=True)

    if not results:
        return "No matching history found."

    lines = []
    for item in results:
        role = item.get("role", "unknown")
        message = item.get("message", "")
        lines.append(f"- [{role}]: {message}")
    return "\n".join(lines)
