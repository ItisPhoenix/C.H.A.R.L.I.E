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

from typing import Any, Callable, Dict, List

from charlie.config import config
from charlie.session_store import SessionStore

logger = logging.getLogger("charlie.tools")

# --- Vector memory store (set via set_memory_store at init) ---
_memory_store = None  # type: Optional[Any]

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

# --- Query decomposition ---
_DECOMPOSE_KEYWORDS = ("compare", "versus", "vs", "or", "and")
_DECOMPOSE_MIN_WORDS = 10
_DECOMPOSE_MAX_QUERIES = 3


# Pre-compiled regex for stripping conversational fluff from search queries.
_FLUFF_WORDS = re.compile(
    r"\b(please|could you|can you|tell me|what are|what is|what\'s|show me|find me|"
    r"i want to know|i need|i\'m looking for|right now|currently)\b",
    re.IGNORECASE,
)


# Windows CMD built-ins that hang subprocess.run(shell=True) because they
# prompt for user input.  Each entry: (compiled regex, PowerShell replacement).
# Using prefix patterns so "date +%H:%M" matches just like "date".
_WIN_CMD_PATTERNS = [
    (
        re.compile(r"^date\b", re.IGNORECASE),
        'powershell -NoProfile -Command "Get-Date -Format \\"yyyy-MM-dd HH:mm:ss\\""',
    ),
    (
        re.compile(r"^time\b", re.IGNORECASE),
        'powershell -NoProfile -Command "Get-Date -Format \\"HH:mm:ss\\""',
    ),
]


# Cross-platform volume command translations (wrong OS -> Windows equivalent)
_AMIXER_SET_RE = re.compile(r"amixer\s+set\s+Master\s+(\d+)\%", re.IGNORECASE)
_AMIXER_DOWN_RE = re.compile(r"amixer\s+set\s+Master\s+(\d+)\%-", re.IGNORECASE)
_OSCRIPT_VOL_RE = re.compile(
    r"osascript\s.*[Ss]et\s+[Vv]olume\s+([\d.]+)", re.IGNORECASE
)


class ToolRegistry:
    """Registry of tools the LLM can call."""

    def __init__(self) -> None:
        self._tools: Dict[str, Dict[str, Any]] = {}

    def register_tool(
        self,
        name: str,
        description: str,
        schema: Dict[str, Any],
        is_interactive: bool = False,
    ):
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

    def set_memory_store(self, store: Any) -> None:
        """Inject vector memory store for vector_memory tool."""
        global _memory_store
        _memory_store = store


# Global tool registry
registry = ToolRegistry()


# ---------------------------------------------------------------------------
# Built-in tools
# ---------------------------------------------------------------------------


def _clean_search_query(query: str) -> str:
    """Strip conversational fluff from a search query."""
    cleaned = _FLUFF_WORDS.sub("", query).strip()
    # Strip trailing punctuation (question marks, exclamation, etc.)
    cleaned = re.sub(r"[?!.,;:]+$", "", cleaned).strip()
    # Strip leading articles
    cleaned = re.sub(r"^(?:the|a|an)\s+", "", cleaned, flags=re.IGNORECASE).strip()
    # Collapse multiple spaces
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned if len(cleaned.split()) >= MIN_CLEANED_WORDS else query


def _is_ddg_result_valid(text: str) -> bool:
    """DuckDuckGo result is valid if it has meaningful content."""
    return bool(text) and len(text) >= DDG_MIN_CONTENT_LEN


def _truncate(text: str, limit: int = CONTENT_MAX_CHARS) -> str:
    return text[:limit] + "..." if len(text) > limit else text


def _needs_decomposition(query: str) -> bool:
    """Check if a query is complex enough to benefit from decomposition."""
    words = query.lower().split()
    if len(words) > _DECOMPOSE_MIN_WORDS:
        return True
    return any(kw in query.lower() for kw in _DECOMPOSE_KEYWORDS)


def _decompose_query(query: str) -> List[str]:
    """Break a complex query into 2-3 sub-queries for better coverage.
    Returns [original] if decomposition is not needed."""
    if not _needs_decomposition(query):
        return [query]

    q_lower = query.lower()
    sub_queries = []

    # Pattern: "compare X and Y" or "X versus Y" or "X vs Y"
    compare_match = re.search(
        r"(?:compare|versus|vs\.?)\s+(.+?)\s+(?:and|vs\.?|versus)\s+(.+?)(?:\s+for\s+.+)?$",
        q_lower,
    )
    if compare_match:
        a, b = compare_match.group(1).strip(), compare_match.group(2).strip()
        # Extract the context (e.g., "for web development")
        context_match = re.search(r"\s+for\s+(.+)$", q_lower)
        context = f" for {context_match.group(1)}" if context_match else ""
        sub_queries = [
            f"{a}{context}",
            f"{b}{context}",
        ]
    else:
        # Pattern: "X or Y" or "X and Y" - split on the conjunction
        or_match = re.search(r"^(.+?)\s+or\s+(.+?)(?:\s+for\s+.+)?$", q_lower)
        and_match = re.search(r"^(.+?)\s+and\s+(.+?)(?:\s+for\s+.+)?$", q_lower)
        match = or_match or and_match
        if match:
            a, b = match.group(1).strip(), match.group(2).strip()
            context_match = re.search(r"\s+for\s+(.+)$", q_lower)
            context = f" for {context_match.group(1)}" if context_match else ""
            sub_queries = [
                f"{a}{context}",
                f"{b}{context}",
            ]
        else:
            # No clear pattern - return original
            return [query]

    return sub_queries[:_DECOMPOSE_MAX_QUERIES]


def _merge_search_results(results: List[str]) -> str:
    """Merge multiple search result strings, deduplicating by URL."""
    seen_urls: set = set()
    merged: List[str] = []

    for result_block in results:
        # Split by double newline to get individual results
        for result in result_block.split("\n\n"):
            result = result.strip()
            if not result:
                continue
            # Extract URL for deduplication
            url_match = re.search(r"URL:\s*(.+)", result)
            url = url_match.group(1).strip() if url_match else result[:100]
            if url not in seen_urls:
                seen_urls.add(url)
                merged.append(result)

    # Truncate total length
    output = "\n\n".join(merged)
    if len(output) > 2000:
        output = output[:2000] + "..."
    return output


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
    # Check if query needs decomposition
    sub_queries = _decompose_query(query)
    if len(sub_queries) > 1:
        logger.info(
            "Decomposing query into %d sub-queries: %s", len(sub_queries), sub_queries
        )
        # Execute sub-queries in parallel using thread pool
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=len(sub_queries)) as executor:
            futures = [executor.submit(_single_search, q) for q in sub_queries]
            results = [f.result() for f in futures if f.result()]
        if results:
            merged = _merge_search_results(results)
            return (
                f"[Multi-query search: {len(sub_queries)} sub-queries]\n\n{merged}"
                if merged
                else "No results found."
            )
        return "No results found for any sub-query."
    return _single_search(query)


def _single_search(query: str) -> str:
    """Execute a single search query across all providers."""
    cleaned = _clean_search_query(query)

    searxng_url = config.searxng_url
    tavily_key = config.tavily_api_key
    exa_key = config.exa_api_key

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
            response = httpx.get(
                f"{base}/search", params=params, timeout=SEARXNG_TIMEOUT
            )
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
                response.status_code,
                cleaned,
                response.text,
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
                json={
                    "query": cleaned,
                    "numResults": SEARCH_RESULT_LIMIT,
                    "text": True,
                },
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
                response.status_code,
                cleaned,
                response.text,
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
                response.status_code,
                cleaned,
                response.text,
            )
        except Exception:
            logger.exception("Tavily search error for query: %s", cleaned)

    # Tier 4: DuckDuckGo fallback
    try:
        logger.info(
            "DuckDuckGo fallback search: original=%r cleaned=%r", query, cleaned
        )
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
                        snippets = soup.find_all("td", class_="result-snippet")[
                            :SEARCH_RESULT_LIMIT
                        ]
                    else:
                        snippets = soup.find_all("a", class_="result__snippet")[
                            :SEARCH_RESULT_LIMIT
                        ]
                    results = [s.get_text(strip=True) for s in snippets]
                    if results:
                        return "\n".join(results)
            except Exception:
                logger.warning(
                    "DuckDuckGo %s endpoint failed for query %r",
                    endpoint,
                    cleaned,
                    exc_info=True,
                )
                continue
    except ImportError:
        logger.warning("BeautifulSoup not installed, DuckDuckGo fallback unavailable")

    return "Error: Web search failed and no search API keys were configured."


# --- Shell safety ---
_BLOCKED_KEYWORDS = (
    "rm -rf",
    "mkfs",
    "dd if=",
    "format ",
    "shutdown",
    "reboot",
    "poweroff",
    "pkill",
    "killall",
)
_SHELL_NAMES = ("cmd", "cmd.exe", "powershell", "powershell.exe")
_CONVERSATIONAL = ("stop", "start", "cancel", "wait", "halt")


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
    lowered = command.lower().strip()
    for keyword in _BLOCKED_KEYWORDS:
        if keyword in lowered:
            return f"Error: Command blocked -- risky keyword '{keyword}'"

    # Block bare interactive shells and conversational nonsense
    if lowered in _SHELL_NAMES:
        return "Error: Cannot open an interactive shell. Specify a command."
    if lowered in _CONVERSATIONAL:
        return f"Error: '{lowered}' is not a shell command."

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
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=SHELL_TIMEOUT,
        )
        parts = []
        if process.stdout and process.stdout.strip():
            parts.append(f"STDOUT:\n{process.stdout.strip()}")
        if process.stderr and process.stderr.strip():
            parts.append(f"STDERR:\n{process.stderr.strip()}")
        if parts:
            return "\n".join(parts)
        # Many commands (start, taskkill, etc.) return empty on success
        if process.returncode == 0:
            return "Command succeeded (exit code 0). No output."
        return f"Command finished with exit code {process.returncode}. No output."
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
        abs_path = os.path.abspath(path)
        # Block traversal and sensitive paths
        _BLOCKED_WRITE_PATHS = (
            ".env",
            "sessions.db",
            os.path.sep + "etc" + os.path.sep,
            os.path.sep + "proc" + os.path.sep,
            os.path.sep + "sys" + os.path.sep,
            os.path.sep + "registry" + os.path.sep,
        )
        path_lower = abs_path.lower()
        for blocked in _BLOCKED_WRITE_PATHS:
            if blocked in path_lower:
                return f"Error: Writing to '{blocked}' paths is blocked for safety."
        dest_dir = os.path.dirname(abs_path)
        os.makedirs(dest_dir, exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return f"Successfully wrote to {path}"
    except Exception as e:
        logger.exception("File write error: %s", path)
        return f"Error writing file: {e}"


_MEMORY_MAX_CHARS = {
    "memory": 2200,
    "user": 1375,
    "opinions": 800,
}
_MEMORY_SEP = "\u00a7"  # section sign - unambiguous entry delimiter


def _parse_memory_entries(text: str) -> list:
    """Parse memory file into individual entries using section sign delimiter."""
    if not text.strip():
        return []
    if _MEMORY_SEP not in text:
        # Legacy file without separators - treat as single entry
        return [text.strip()] if text.strip() else []
    return [e.strip() for e in text.split(_MEMORY_SEP) if e.strip()]


def _format_capacity(target: str, entries: list, max_chars: int) -> str:
    """Format capacity header showing usage and entries."""
    current = sum(len(e) for e in entries)
    if entries:
        current += len(entries) - 1  # separators
    pct = int(current / max_chars * 100) if max_chars > 0 else 0
    lines = [f"[{target.upper()}] {current}/{max_chars} chars ({pct}%) - {len(entries)} entries"]
    for i, entry in enumerate(entries, 1):
        lines.append(f"  {i}. {entry}")
    return "\n".join(lines)


def _memory_capacity_error(target: str, entries: list, max_chars: int, new_len: int) -> str:
    """Return capacity error with full entry listing."""
    return (
        f"Memory full: {target} at capacity. Cannot add {new_len} chars.\n"
        "Consolidate first: use 'replace' to merge overlapping entries, "
        "or 'remove' to drop stale ones.\n\n"
        + _format_capacity(target, entries, max_chars)
    )


@registry.register_tool(
    name="memory",
    description="Manage persistent memory files. Actions: add appends an entry, replace swaps an entry containing old_text, remove drops an entry, consolidate returns all entries with capacity for review. Entries are delimited by section sign.",
    schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "replace", "remove", "consolidate"],
                "description": "add = append entry, replace = swap entry containing old_text, remove = drop entry, consolidate = list all entries with capacity info.",
            },
            "target": {
                "type": "string",
                "enum": ["memory", "user", "opinions"],
                "description": "Which file: memory = MEMORY.md (system context, max 2200 chars), user = USER.md (user preferences, max 1375 chars), opinions = OPINIONS.md (personal opinions, max 800 chars).",
            },
            "content": {
                "type": "string",
                "description": "Text content to add or use as replacement (required for add/replace, ignored for remove/consolidate).",
            },
            "old_text": {
                "type": "string",
                "description": "Substring to find in an entry (required for replace/remove, ignored for add/consolidate).",
            },
        },
        "required": ["action", "target"],
    },
)
def memory(action: str, target: str, content: str = "", old_text: str = "") -> str:
    if target not in _MEMORY_MAX_CHARS:
        return f"Error: target must be 'memory', 'user', or 'opinions', got '{target}'."

    max_chars = _MEMORY_MAX_CHARS[target]
    path = (
        config.memory_file
        if target == "memory"
        else config.opinions_file
        if target == "opinions"
        else config.user_file
    )

    try:
        existing = ""
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                existing = handle.read()

        entries = _parse_memory_entries(existing)

        # consolidate: return current entries for review
        if action == "consolidate":
            return _format_capacity(target, entries, max_chars)

        if action == "add":
            if not content:
                return "Error: content is required for add actions."
            new_entry = content.strip()
            new_len = len(new_entry) + (1 if entries else 0)
            current_len = sum(len(e) for e in entries)
            if entries:
                current_len += len(entries) - 1
            if current_len + new_len > max_chars:
                return _memory_capacity_error(target, entries, max_chars, len(new_entry))
            entries.append(new_entry)
        elif action == "replace":
            if not old_text:
                return "Error: old_text is required for replace actions."
            if not content:
                return "Error: content is required for replace actions."
            matches = [i for i, e in enumerate(entries) if old_text in e]
            if not matches:
                return (
                    f"Error: no entry contains '{old_text}'.\n"
                    + _format_capacity(target, entries, max_chars)
                )
            if len(matches) > 1:
                return f"Error: '{old_text}' matched {len(matches)} entries. Provide a more specific string."
            entries[matches[0]] = content.strip()
        elif action == "remove":
            if not old_text:
                return "Error: old_text is required for remove actions."
            matches = [i for i, e in enumerate(entries) if old_text in e]
            if not matches:
                return (
                    f"Error: no entry contains '{old_text}'.\n"
                    + _format_capacity(target, entries, max_chars)
                )
            if len(matches) > 1:
                return f"Error: '{old_text}' matched {len(matches)} entries. Provide a more specific string."
            entries.pop(matches[0])
        else:
            return f"Error: Unsupported action '{action}'."

        updated = _MEMORY_SEP.join(entries) if entries else ""
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(updated)

        current_len = sum(len(e) for e in entries)
        if entries:
            current_len += len(entries) - 1
        return f"Updated {target}: {current_len}/{max_chars} chars ({len(entries)} entries)."
    except Exception as e:
        logger.exception("Memory tool error: action=%s target=%s", action, target)
        return f"Error updating memory: {e}"


@registry.register_tool(
    name="vector_memory",
    description="Semantic memory: remember facts or recall them across sessions. 'remember' stores a fact for future retrieval. 'recall' searches past conversations semantically.",
    schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["remember", "recall"],
                "description": "remember = store a fact, recall = search past memories.",
            },
            "content": {
                "type": "string",
                "description": "For 'remember': the fact to store. For 'recall': the query to search for.",
            },
        },
        "required": ["action", "content"],
    },
)
def vector_memory(action: str, content: str) -> str:
    if _memory_store is None or not _memory_store.is_available:
        return "Vector memory is not available. Embedding service may be offline."

    if action == "remember":
        count = _memory_store.add_memory(
            text=content,
            source="user",
            session_id="explicit",
            auto_extract=False,
        )
        if count > 0:
            return f"Remembered: {content[:100]}"
        return "Failed to store memory."

    elif action == "recall":
        results = _memory_store.search(content, n_results=3)
        if not results:
            return "No relevant memories found."
        lines = []
        for r in results:
            lines.append(f"- {r['text']}")
        return "\n".join(lines)

    return f"Unknown action: {action}"


@registry.register_tool(
    name="session_search",
    description="Search past conversation history stored in the session database. Returns matching messages from previous sessions.",
    schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query to find in past conversations.",
            }
        },
        "required": ["query"],
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
    for role, message in results:
        lines.append(f"- [{role}]: {message}")
    return "\n".join(lines)
