"""Charlie tool registry and built-in tools.

All tool definitions, execution logic, and provider integrations live here.
No business logic -- just tool I/O.
"""

import asyncio
import base64
import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

import httpx

from charlie import recovery, reminders
from charlie.config import config
from charlie.known_apps import APP_REGISTRY
from charlie.projects import Projects
from charlie.scratchpad import Scratchpad
from charlie.session_store import SessionStore
from charlie.utils import is_process_running

logger = logging.getLogger("charlie.tools")


# --- Vector memory store (set via set_memory_store at init) ---
_memory_store = None  # type: Optional[Any]
# --- Pending vision-tier screenshot: written by desktop_screenshot, consumed
# --- once by Brain._build_payload for the very next outgoing payload. ---
# FIFO -- concurrent desktop_screenshot calls (asyncio.gather in one turn, or a
# sub-agent's own call) must not silently overwrite each other
_pending_vision_images: List[str] = []
# --- Search tuning ---
SEARCH_RESULT_LIMIT = 5
CONTENT_MAX_CHARS = 800
MIN_CLEANED_WORDS = 2

# --- HTTP timeouts (seconds) ---
# Tuned against real observed latency, not guessed: live SearXNG calls this
# session consistently completed in 1.2-1.7s (server-timing header), so a
# 10s timeout was pure wasted wait if a tier ever actually goes down --
# tiers run sequentially, so each one's timeout is fully on the critical
# path before the next tier is even tried.
SEARXNG_TIMEOUT = 5.0
EXA_TIMEOUT = 6.0
TAVILY_TIMEOUT = 6.0
DDG_TIMEOUT = 5.0

# --- DuckDuckGo ---
DDG_MIN_CONTENT_LEN = 20
DDG_ACCEPTED_STATUSES = (200, 202)
DDG_USER_AGENT = "Mozilla/5.0"

# --- Shell ---
SHELL_TIMEOUT = 10.0
# Bound on the post-kill drain call below -- its return value is discarded,
# it only exists to reap the process, so it must never block indefinitely.
_SHELL_KILL_DRAIN_TIMEOUT = 2.0

# --- Dashboard live view (desktop_frame event) ---
_DESKTOP_FRAME_FPS = 2.0
_DESKTOP_FRAME_MAX_EDGE = 960

# --- SearXNG keyword detection ---
_TIME_SENSITIVE_KEYWORDS = ("today", "new", "recent", "latest", "breaking", "happening")
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

    def unregister_tool(self, name: str) -> bool:
        """Remove a tool so it no longer appears in get_tool_definitions()
        or is callable via execute_tool(). Returns whether it existed."""
        return self._tools.pop(name, None) is not None

    def get_tool_definitions(self, exclude: Optional[Set[str]] = None) -> List[Dict[str, Any]]:
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
            if not exclude or name not in exclude
        ]

    def is_interactive(self, name: str) -> bool:
        return self._tools.get(name, {}).get("is_interactive", False)

    def get_tool_names(self) -> List[str]:
        """All currently registered tool names (built-in + MCP + plugin +
        extension) -- used by charlie.core's text-based tool-call parser so
        it recognizes every live tool instead of a hand-maintained subset."""
        return list(self._tools.keys())

    def get_tool_param_names(self, name: str) -> Optional[List[str]]:
        """Ordered parameter names for `name`, read from its live JSON
        schema. None if `name` isn't registered, [] if it takes no
        arguments. Lets charlie.core map text-mode TOOL: call arguments
        onto real parameter names without a second, driftable list."""
        info = self._tools.get(name)
        if info is None:
            return None
        return list(info["schema"].get("properties", {}).keys())

    def build_tool_prompt(self, exclude: Optional[Set[str]] = None) -> str:
        """Build a plain-text tool description for the system prompt."""
        lines = []
        for name, info in self._tools.items():
            if exclude and name in exclude:
                continue
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
            valid = ", ".join(sorted(self._tools.keys()))
            return f"Error: Tool '{name}' is not registered. Available tools: {valid}."

        func = self._tools[name]["func"]
        schema = self._tools[name]["schema"]
        missing = [p for p in schema.get("required", []) if p not in arguments]
        if missing:
            logger.warning("Tool '%s' called without required args: %s", name, missing)
            params = ", ".join(schema.get("properties", {}).keys())
            return (
                f"Error: tool '{name}' is missing required argument(s) {missing}. "
                f"Its parameters are: {params}."
            )
        for pname, value in arguments.items():
            enum = schema.get("properties", {}).get(pname, {}).get("enum")
            if enum and value not in enum:
                logger.warning("Tool '%s' called with invalid %s=%r", name, pname, value)
                return (
                    f"Error: tool '{name}' argument '{pname}' must be one of {enum}, got {value!r}. "
                    f"If you meant to search/recall a stored fact, use the 'vector_memory' tool instead."
                    if name == "memory"
                    else f"Error: tool '{name}' argument '{pname}' must be one of {enum}, got {value!r}."
                )
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
    name="spawn_agent",
    description=(
        "Delegate a self-contained sub-task to an independent sub-agent that runs its own "
        "tool loop and reports back a result. Use only for genuinely delegable work (e.g. an "
        "independent research thread, a parallel multi-part job) -- most turns should not use "
        "this at all and should just answer directly."
    ),
    schema={
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "A clear, self-contained description of the sub-task to delegate.",
            }
        },
        "required": ["task"],
    },
)
def spawn_agent(task: str) -> str:
    return "Error: spawn_agent must be dispatched through Brain.spawn_agent, not called directly."


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


def _searxng_request(base: str, params: Dict[str, str], query: str, cleaned: str) -> Optional[str]:
    """One SearXNG query attempt. Returns formatted results, or None to let the caller retry/fall back."""
    try:
        logger.info("SearXNG search: original=%r cleaned=%r params=%r", query, cleaned, params)
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
            "SearXNG failed with status %s for query %r: %s", response.status_code, cleaned, response.text,
        )
    except Exception:
        logger.exception("SearXNG search error for query: %s", cleaned)
    return None


def _single_search(query: str) -> str:
    """Execute a single search query across all providers."""
    cleaned = _clean_search_query(query)
    q_lower = cleaned.lower()
    # Reused below -- only SearXNG applied a freshness filter before, leaving the DuckDuckGo fallback tier stale.
    is_time_sensitive = any(kw in q_lower for kw in _TIME_SENSITIVE_KEYWORDS)

    searxng_url = config.searxng_url
    tavily_key = config.tavily_api_key
    exa_key = config.exa_api_key

    # Tier 1: SearXNG (self-hosted, no API key needed)
    if searxng_url:
        base = searxng_url.rstrip("/")
        params: Dict[str, str] = {"q": cleaned, "format": "json", "language": "en"}
        if is_time_sensitive:
            params["time_range"] = "day"
        is_news_query = any(kw in q_lower for kw in _NEWS_KEYWORDS)
        if is_news_query:
            params["categories"] = "news"

        searxng_result = _searxng_request(base, params, query, cleaned)
        if searxng_result is not None:
            return searxng_result

        # News category has few engines and can zero out when all are blocked at once -- general aggregates more.
        if is_news_query:
            general_params = {k: v for k, v in params.items() if k != "categories"}
            searxng_result = _searxng_request(base, general_params, query, cleaned)
            if searxng_result is not None:
                return searxng_result

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

        ddg_params = {"q": cleaned, **({"df": "d"} if is_time_sensitive else {})}
        for endpoint in ("lite", "html"):
            try:
                response = httpx.get(
                    f"https://{endpoint}.duckduckgo.com/{endpoint}/",
                    params=ddg_params,
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
# Keywords that are always refused outright, no approval can override them:
# irreversible disk/OS-level destruction or a live system going down.
_HARD_BLOCKED_KEYWORDS = (
    "mkfs",
    "dd if=",
    "format ",
    "shutdown",
    "reboot",
    "poweroff",
    "diskpart",
    "certutil",
    "bitsadmin",
)
# Keywords that require explicit user approve/decline before running (see
# charlie.core.request_tool_approval). These delete, kill, or reconfigure
# something, but are recoverable/scoped -- unlike the hard-blocked set above.
_GATED_KEYWORDS = (
    "rm -rf",
    "rm -r -f",
    "rd /s /q",
    "del /f /s",
    "pkill",
    "killall",
    "reg delete",
    "net user",
    "wmic",
    "schtasks",
    "takeown",
    "icacls",
    "taskkill",
)
# Shell metacharacters used for command chaining / substitution. Blocked in
# every mode to prevent injection (e.g. "echo a & type secrets.txt").
_SHELL_METACHARS = (";", "|", "&", "`", "$", "(", ")")
_SHELL_NAMES = ("cmd", "cmd.exe", "powershell", "powershell.exe")
_CONVERSATIONAL = ("stop", "start", "cancel", "wait", "halt")


def is_command_keyword_blocked(command: str) -> Optional[str]:
    """Keyword-only half of is_shell_command_blocked, without the shell-
    metacharacter check -- for callers that exec argv directly (no
    shell=True, so metacharacters are inert literal characters, not command
    chaining/injection vectors). Used by
    charlie.extensions.install.run_skill_script, whose script paths may
    legitimately contain parentheses etc. from the filesystem."""
    lowered = command.lower().strip()
    for keyword in _HARD_BLOCKED_KEYWORDS:
        if keyword in lowered:
            return f"Command blocked -- risky keyword '{keyword}'"
    return None


def is_command_keyword_gated(command: str) -> Optional[str]:
    """Keyword-only half of is_shell_command_gated. See
    is_command_keyword_blocked for why this is split out."""
    lowered = command.lower().strip()
    for keyword in _GATED_KEYWORDS:
        if keyword in lowered:
            return f"risky keyword '{keyword}'"
    return None


def is_shell_command_blocked(command: str) -> Optional[str]:
    """Check `command` against the hard shell-execute safety guards
    (metacharacters and the irreversible-keyword list). Returns a
    human-readable block reason, or None if the command passes. No approval
    flow can override a hard block.

    Shared with charlie.recovery so LLM-suggested and strategy-rewritten
    recovery commands go through the exact same guard as direct
    shell_execute calls, instead of only the narrower path/process/port
    checks in recovery.is_safe_to_recover.
    """
    if any(ch in command for ch in _SHELL_METACHARS):
        return "Shell metacharacters (;, |, &, `, $, (, )) are not allowed."
    return is_command_keyword_blocked(command)


def is_shell_command_gated(command: str) -> Optional[str]:
    """Check `command` against the gated (approve/decline) keyword list.
    Returns a human-readable reason the command needs user approval, or None
    if it doesn't. Only meaningful once `is_shell_command_blocked` has
    already passed -- gating never overrides a hard block.
    """
    return is_command_keyword_gated(command)


# Wrapper prefixes the model tends to reach for when a plain "start <app>" gets
# blocked (see _detect_app_launch below) -- stripped one at a time so any
# combination still reduces to the bare app token underneath.
_LAUNCH_WRAPPER_RES = [
    re.compile(r"^cmd(?:\.exe)?\s*/c\s+", re.IGNORECASE),
    re.compile(r"^powershell(?:\.exe)?\s+-command\s+", re.IGNORECASE),
    re.compile(r"^start-process\s+", re.IGNORECASE),
    re.compile(r"^start\s+(?:\"\"\s+)?", re.IGNORECASE),
]


def _detect_app_launch(command: str):
    """If `command` is a bare launch of a known local app (optionally wrapped
    in "start"/"cmd /c start"/"powershell Start-Process"), return its
    known_apps.AppEntry. Deliberately conservative -- only a bare launch
    matches, e.g. "notepad" or "start notepad", not "notepad file.txt" (a
    real file argument means a genuinely new instance may be wanted)."""
    token = command.strip()
    for wrapper_re in _LAUNCH_WRAPPER_RES:
        token = wrapper_re.sub("", token).strip()
    token = token.strip("\"'").strip()
    token = re.sub(r"\.exe$", "", token, flags=re.IGNORECASE).strip("\"'").strip()
    if not token:
        return None
    token_lower = token.lower()
    for entry in APP_REGISTRY.values():
        if entry.close_process and token_lower == entry.open_cmd.lower():
            return entry
    return None


_MAX_WAIT_SECONDS = 10


@registry.register_tool(
    name="wait_seconds",
    description=(
        "Pause before re-checking something that needs time (a page still loading, "
        "a download in progress). Use this then call the check tool again in the same "
        "turn -- never promise the user you'll check again later, that follow-up never happens."
    ),
    schema={
        "type": "object",
        "properties": {
            "seconds": {
                "type": "number",
                "description": f"How long to wait, max {_MAX_WAIT_SECONDS}.",
            },
        },
        "required": ["seconds"],
    },
)
def wait_seconds(seconds: float) -> str:
    capped = max(0.0, min(float(seconds), _MAX_WAIT_SECONDS))
    time.sleep(capped)
    return f"Waited {capped}s."


@registry.register_tool(
    name="set_reminder",
    description=(
        "Set a one-off reminder that fires (spoken + dashboard toast) after a delay. "
        f"Max {reminders.MAX_REMINDER_SECONDS}s (24h). Not recurring, lost on restart."
    ),
    schema={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "What to remind the user of."},
            "seconds": {"type": "number", "description": "Delay in seconds before firing."},
        },
        "required": ["text", "seconds"],
    },
)
def set_reminder(text: str, seconds: float) -> str:
    try:
        reminder_id = reminders.set_reminder(text, float(seconds))
    except ValueError as e:
        return f"Reminder rejected: {e}"
    return f"Reminder set (id={reminder_id}), fires in {seconds:g}s: {text}"


@registry.register_tool(
    name="list_reminders",
    description="List all pending (not yet fired) reminders.",
    schema={"type": "object", "properties": {}},
)
def list_reminders() -> str:
    pending = reminders.list_reminders()
    if not pending:
        return "No pending reminders."
    now = time.time()
    lines = [
        f"- {rid}: \"{entry['text']}\" (fires in {max(0, round(entry['fire_at'] - now))}s)"
        for rid, entry in pending.items()
    ]
    return "\n".join(lines)


@registry.register_tool(
    name="cancel_reminder",
    description="Cancel a pending reminder by its id (from set_reminder or list_reminders).",
    schema={
        "type": "object",
        "properties": {
            "reminder_id": {"type": "string", "description": "The reminder id to cancel."},
        },
        "required": ["reminder_id"],
    },
)
def cancel_reminder(reminder_id: str) -> str:
    if reminders.cancel_reminder(reminder_id):
        return f"Reminder {reminder_id} cancelled."
    return f"No pending reminder with id {reminder_id}."


def _get_scratchpad() -> Scratchpad:
    return Scratchpad(db_path=config.scratchpad_db_path)


@registry.register_tool(
    name="scratchpad_add",
    description="Append a freeform note to the shared scratchpad (persists across sessions).",
    schema={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The note text to add."},
        },
        "required": ["text"],
    },
)
def scratchpad_add(text: str) -> str:
    pad = _get_scratchpad()
    try:
        index = pad.add(text)
    except ValueError as e:
        return f"Error: {e}"
    finally:
        pad.close()
    return f"Added as entry {index}."


@registry.register_tool(
    name="scratchpad_list",
    description="List all entries currently in the shared scratchpad.",
    schema={"type": "object", "properties": {}},
)
def scratchpad_list() -> str:
    pad = _get_scratchpad()
    try:
        entries = pad.list()
    finally:
        pad.close()
    if not entries:
        return "Scratchpad is empty."
    return "\n".join(f"{i}. {text}" for i, text, _created_at in entries)


@registry.register_tool(
    name="scratchpad_edit",
    description="Replace the text of a scratchpad entry by its 1-based index (see scratchpad_list).",
    schema={
        "type": "object",
        "properties": {
            "index": {"type": "integer", "description": "1-based entry index."},
            "text": {"type": "string", "description": "New text for that entry."},
        },
        "required": ["index", "text"],
    },
)
def scratchpad_edit(index: int, text: str) -> str:
    pad = _get_scratchpad()
    try:
        ok = pad.edit(int(index), text)
    except ValueError as e:
        return f"Error: {e}"
    finally:
        pad.close()
    return f"Entry {index} updated." if ok else f"No entry at index {index}."


@registry.register_tool(
    name="scratchpad_delete",
    description="Delete a scratchpad entry by its 1-based index (see scratchpad_list).",
    schema={
        "type": "object",
        "properties": {
            "index": {"type": "integer", "description": "1-based entry index."},
        },
        "required": ["index"],
    },
)
def scratchpad_delete(index: int) -> str:
    pad = _get_scratchpad()
    try:
        ok = pad.delete(int(index))
    finally:
        pad.close()
    return f"Entry {index} deleted." if ok else f"No entry at index {index}."


@registry.register_tool(
    name="scratchpad_clear",
    description="Delete all entries in the shared scratchpad.",
    schema={"type": "object", "properties": {}},
)
def scratchpad_clear() -> str:
    pad = _get_scratchpad()
    try:
        pad.clear()
    finally:
        pad.close()
    return "Scratchpad cleared."


@registry.register_tool(
    name="shell_execute",
    description="Run a shell command and get output. Risky commands are blocked.",
    schema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute.",
            },
            "voice_mode": {
                "type": "boolean",
                "description": "Restrict to a safe command allowlist for voice input.",
            },
        },
        "required": ["command"],
    },
    is_interactive=True,
)
def shell_execute(command: str, *, voice_mode: bool = False) -> str:
    lowered = command.lower().strip()

    # An already-running known app gets focused instead of relaunched -- applies
    # regardless of voice_mode, and before the allowlist check below so a blocked
    # wrapper (e.g. "powershell -Command Start-Process notepad") never even gets
    # a chance to fail: if the app's already open, there's nothing to launch.
    app_entry = _detect_app_launch(command)
    if app_entry and sys.platform == "win32" and is_process_running(app_entry.close_process):
        from charlie.desktop.windows import focus_window
        return focus_window(app_entry.close_process.removesuffix(".exe"))

    if voice_mode:
        if not lowered:
            return "Error: No command provided."
        allowed_prefixes = (
            "start ",
            "taskkill ",
            "code ",
            "explorer ",
            "calc ",
            "notepad ",
            "dir ",
            "cmd ",
            "move ",
            "copy ",
        )
        # Accept the bare command too (e.g. "notepad" with no args), not just "notepad <arg>".
        if not any(lowered == prefix.strip() or lowered.startswith(prefix) for prefix in allowed_prefixes):
            return (
                "Error: Command not on the allowed list for voice mode. "
                "Use the web UI for unrestricted shell access."
            )

    # Universal guards: apply in every mode (voice and web UI).
    blocked_reason = is_shell_command_blocked(command)
    if blocked_reason:
        return f"Error: {blocked_reason}"

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
        process = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=SHELL_TIMEOUT)
        except subprocess.TimeoutExpired:
            # A bare foreground-app launch (e.g. "notepad", not `start ""
            # notepad`) keeps its parent shell alive until the app closes,
            # so this fires even when the app opened successfully. Reporting
            # it as "Error" made the caller retry and spawn a duplicate
            # instance. Kill the now-idle wrapper shell but report this as
            # still-running, not a failure.
            process.kill()
            try:
                # A detached grandchild (e.g. "start notepad") can keep the
                # stdout/stderr pipe open past the killed parent's exit, so
                # this drain must stay bounded too -- its result is unused.
                process.communicate(timeout=_SHELL_KILL_DRAIN_TIMEOUT)
            except subprocess.TimeoutExpired:
                pass
            return (
                f"Command is still running after {SHELL_TIMEOUT}s with no output "
                "(left running -- if this opened an app or window, it launched "
                "successfully)."
            )
        parts = []
        if stdout and stdout.strip():
            parts.append(f"STDOUT:\n{stdout.strip()}")
        if stderr and stderr.strip():
            parts.append(f"STDERR:\n{stderr.strip()}")
        if parts:
            return "\n".join(parts)
        # Many commands (start, taskkill, etc.) return empty on success
        if process.returncode == 0:
            result = "Command succeeded (exit code 0). No output."
        else:
            result = f"Command finished with exit code {process.returncode}. No output."
        if not voice_mode:
            result = "WARNING: Shell commands are powerful. Be careful with destructive operations.\n\n" + result
        return result
    except Exception as e:
        logger.exception("Shell command error: %s", command)
        return f"Error executing shell command: {e}"


# --- System diagnostics: fixed commands only, no user-supplied string ever reaches the shell.
_DIAGNOSTIC_COMMANDS: Dict[str, str] = {
    "disk": (
        'powershell -NoProfile -Command "Get-PSDrive -PSProvider FileSystem | '
        'Select-Object Name,Used,Free | Format-Table -AutoSize | Out-String -Width 200"'
    ),
    "memory": (
        'powershell -NoProfile -Command "Get-CimInstance Win32_OperatingSystem | '
        'Select-Object FreePhysicalMemory,TotalVisibleMemorySize | Format-List | Out-String -Width 200"'
    ),
    "cpu": (
        'powershell -NoProfile -Command "Get-CimInstance Win32_Processor | '
        'Select-Object Name,LoadPercentage | Format-List | Out-String -Width 200"'
    ),
    "processes": (
        'powershell -NoProfile -Command "Get-Process | Sort-Object CPU -Descending | '
        'Select-Object -First 10 Name,CPU,WorkingSet | Format-Table -AutoSize | Out-String -Width 200"'
    ),
    "network": (
        "powershell -NoProfile -Command \"Get-NetAdapter | Where-Object Status -eq 'Up' | "
        "Select-Object Name,LinkSpeed,Status | Format-Table -AutoSize | Out-String -Width 200\""
    ),
}


@registry.register_tool(
    name="system_diagnostics",
    description=(
        "Run a fixed, safe system diagnostic check (disk, memory, cpu, processes, "
        "or network). No user-supplied command reaches the shell -- each check maps "
        "to one hardcoded, read-only command."
    ),
    schema={
        "type": "object",
        "properties": {
            "check": {
                "type": "string",
                "enum": list(_DIAGNOSTIC_COMMANDS.keys()),
                "description": "Which diagnostic to run.",
            }
        },
        "required": ["check"],
    },
)
def system_diagnostics(check: str) -> str:
    if sys.platform != "win32":
        return f"System diagnostics are only supported on Windows (detected {sys.platform})."

    command = _DIAGNOSTIC_COMMANDS.get(check)
    if command is None:
        return f"Error: unknown diagnostic check '{check}'. Valid checks: {', '.join(_DIAGNOSTIC_COMMANDS)}."

    try:
        process = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=SHELL_TIMEOUT,
        )
        output = (process.stdout or "").strip() or (process.stderr or "").strip()
        return output or f"Diagnostic '{check}' completed with no output."
    except subprocess.TimeoutExpired:
        return f"Error: diagnostic '{check}' timed out after {SHELL_TIMEOUT}s."
    except Exception as e:
        logger.exception("system_diagnostics error: check=%s", check)
        return f"Error running diagnostic '{check}': {e}"


_WORKSPACE_DIR = Path(__file__).parent.parent.resolve()

# Sensitive path substrings that require explicit user approve/decline before
# a file_read/file_write call touches them (see
# charlie.core.request_tool_approval). Not a hard block -- unlike the shell
# hard-blocked keywords, there's no path that's dangerous to even read once
# approved, so everything here is gate-only.
_GATED_PATH_SUBSTRINGS = (
    ".env",
    "sessions.db",
    os.path.sep + "etc" + os.path.sep,
    os.path.sep + "proc" + os.path.sep,
    os.path.sep + "sys" + os.path.sep,
    os.path.sep + "registry" + os.path.sep,
    os.path.sep + ".ssh" + os.path.sep,
    os.path.sep + ".aws" + os.path.sep,
)


def _resolve_safe_path(path_str: str) -> Path:
    target = Path(path_str)
    if target.is_absolute():
        resolved = target.resolve(strict=False)
    else:
        resolved = (_WORKSPACE_DIR / path_str).resolve(strict=False)
    return resolved


def get_path_gate_reason(path_str: str) -> Optional[str]:
    """Pure pre-flight check: does this path need approve/decline before a
    file_read/file_write call touches it? Returns a human-readable reason, or
    None if the path is clear. Resolves the same way file_read/file_write do
    (user-placeholder substitution + _resolve_safe_path) so the reason
    reflects the actual path that will be opened, not the raw argument.
    """
    try:
        resolved = _resolve_safe_path(_resolve_user_placeholders(path_str))
    except Exception:
        return None

    from charlie.config import config
    path_lower = str(resolved).lower()
    system_root = config.system_root.lower()
    if system_root and system_root in path_lower:
        return f"system root path '{config.system_root}'"
    for blocked in _GATED_PATH_SUBSTRINGS:
        if blocked.lower() in path_lower:
            return f"sensitive path '{blocked}'"
    return None


def _resolve_user_placeholders(path: str) -> str:
    """Replace Windows user-folder placeholders (e.g. C:\\Users\\YourUsername\\...)
    with the real username. Splits on a literal backslash rather than
    os.path.sep -- Charlie targets Windows paths regardless of the host
    platform this runs on (e.g. pure-logic tests on Linux CI).

    Also catches the case where the model wrote a real-looking but wrong
    username (e.g. C:\\Users\\Charlie -- guessing its own name instead of the
    actual account) rather than an obvious <placeholder>: if the segment
    right after "Users" doesn't match an existing directory, it's swapped for
    the real one too."""
    import getpass
    placeholders = {"yourusername", "username", "user"}
    current_user = getpass.getuser()
    parts = path.split("\\")
    for i, part in enumerate(parts):
        clean_part = part.strip("<>")
        if clean_part.lower() in placeholders:
            parts[i] = current_user
        elif (
            i > 0
            and parts[i - 1].lower() == "users"
            and clean_part
            and clean_part.lower() != current_user.lower()
            and not os.path.isdir("\\".join(parts[: i + 1]))
        ):
            parts[i] = current_user
    return "\\".join(parts)


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
        path = _resolve_user_placeholders(path)
        safe_path = _resolve_safe_path(path)
        with open(safe_path, "r", encoding="utf-8") as handle:
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
        path = _resolve_user_placeholders(path)
        path = os.path.abspath(path)
        safe_path = _resolve_safe_path(path)
        if safe_path.is_dir():
            return f"Error: Cannot write to a directory ({path}). Please specify a file path."

        dest_dir = os.path.dirname(safe_path)
        os.makedirs(dest_dir, exist_ok=True)
        with open(safe_path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return f"Successfully wrote to {path}"
    except ValueError as e:
        return f"Error: {e}"
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
    description=(
        "Manage persistent memory files. Actions: add appends an entry, "
        "replace swaps an entry containing old_text, remove drops an entry, "
        "consolidate returns all entries with capacity for review. "
        "Entries are delimited by section sign."
    ),
    schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "replace", "remove", "consolidate"],
                "description": "add = append, replace = swap entry, remove = drop, consolidate = list all.",
            },
            "target": {
                "type": "string",
                "enum": ["memory", "user", "opinions"],
                "description": (
                    "memory (max 2200, global facts), user (max 1375, about the user), "
                    "opinions (max 800). For facts scoped to one project workspace, use "
                    "project_memory_add instead."
                ),
            },
            "content": {
                "type": "string",
                "description": "Text to add or use as replacement (required for add/replace).",
            },
            "old_text": {
                "type": "string",
                "description": "Substring to find in an entry (required for replace/remove).",
            },
        },
        "required": ["action", "target"],
    },
)
def memory(action: str, target: str, content: str = "", old_text: str = "", **kwargs: str) -> str:
    content = content or kwargs.get("new_text", "")
    if target not in _MEMORY_MAX_CHARS:
        return f"Error: target must be one of {sorted(_MEMORY_MAX_CHARS)}, got '{target}'."

    max_chars = _MEMORY_MAX_CHARS[target]
    path = getattr(config, f"{target}_file")

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


def _get_projects() -> Projects:
    return Projects(config.projects_dir)


@registry.register_tool(
    name="list_projects",
    description="List all project workspaces and which one (if any) is active.",
    schema={"type": "object", "properties": {}},
)
def list_projects() -> str:
    store = _get_projects()
    names = store.list()
    if not names:
        return "No projects exist yet. Use create_project to make one."
    active = store.get_active()
    lines = [f"{'* ' if n == active else '  '}{n}" for n in names]
    return "Projects (* = active):\n" + "\n".join(lines)


@registry.register_tool(
    name="create_project",
    description="Create a new project workspace and make it active.",
    schema={
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Project name, e.g. 'Charlie Dev'."}},
        "required": ["name"],
    },
)
def create_project(name: str) -> str:
    store = _get_projects()
    try:
        slug = store.create(name)
        store.set_active(slug)
    except ValueError as e:
        return f"Error: {e}"
    return f"Created and switched to project '{slug}'."


@registry.register_tool(
    name="switch_project",
    description="Switch the active project workspace, or pass 'none' to go back to global (no project).",
    schema={
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Project slug from list_projects, or 'none'."}},
        "required": ["name"],
    },
)
def switch_project(name: str) -> str:
    store = _get_projects()
    if name.strip().lower() == "none":
        store.set_active(None)
        return "Switched to global (no active project)."
    try:
        store.set_active(name)
    except ValueError as e:
        return f"Error: {e}"
    return f"Switched to project '{name}'."


@registry.register_tool(
    name="project_memory_add",
    description="Add a fact scoped to a project workspace. Defaults to the active project.",
    schema={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The fact to remember."},
            "project": {"type": "string", "description": "Project slug; omit to use the active project."},
        },
        "required": ["text"],
    },
)
def project_memory_add(text: str, project: str = "") -> str:
    store = _get_projects()
    slug = project.strip() or store.get_active()
    if not slug:
        return "Error: no active project and none named. Use create_project or switch_project first."
    try:
        store.add_entry(slug, text)
    except ValueError as e:
        return f"Error: {e}"
    return f"Added to project '{slug}'."


@registry.register_tool(
    name="vector_memory",
    description=(
        "Semantic memory: remember facts or recall them across sessions. "
        "'remember' stores a fact. 'recall' searches past conversations."
    ),
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
    description="Search past conversation history. Returns matching messages.",
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
    # Scope FTS to the active launch when one is known, to avoid leaking
    # history from other launches. Empty string means "no launch" -> global.
    launch_id = config.charlie_launch_id or None
    try:
        store = SessionStore(db_path=config.session_db_path)
        results = store.search(query, limit=5, launch_id=launch_id)
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



# ---------------------------------------------------------------------------
# Plugin system bridge
# ---------------------------------------------------------------------------
# Plugins are only wired into the LLM when config.plugins_enabled is true
# (off by default). When active, every plugin action is exposed as a
# registry tool named `plugin_<action>` so the model can call it directly.
# The underlying PluginManager/plugins are never instantiated unless the
# flag is set (the plugins module is otherwise dead weight).

# Maps each plugin action to a human-honest tool description. Keys are the
# raw plugin tool names (e.g. "fs_read_file") so wrappers can look them up.
_PLUGIN_ACTION_DESCRIPTIONS: Dict[str, str] = {
    "fs_list_dir": "List files and subdirectories inside a local directory.",
    "fs_read_file": "Read the text contents of a file on the local filesystem.",
    "fs_write_file": "Write text content to a file on the local filesystem.",
    "fs_search": "Search the local filesystem for files matching a glob pattern.",
    "cal_list_events": "List events from the local calendar store.",
    "code_exec_python": (
        "Execute a snippet of Python in a sandboxed interpreter. "
        "Network and system-level calls are blocked. Use only when the user "
        "explicitly asks to run code."
    ),
}


def _build_plugin_manager(
    allow_dirs: List[str],
) -> Any:
    """Construct a fully-populated PluginManager.

    Imports are local so the rest of tools.py never depends on the plugins
    module unless plugins are actually enabled.
    """
    from charlie.plugins import (
        CalendarPlugin,
        CodeExecPlugin,
        FilesystemPlugin,
        PluginManager,
    )

    manager = PluginManager()
    manager.register(FilesystemPlugin(allowed_dirs=allow_dirs))
    manager.register(CalendarPlugin())
    manager.register(CodeExecPlugin())
    return manager


def enable_plugin(reg: "ToolRegistry", manager: Any, plugin: Any) -> List[str]:
    """Register one plugin's tools into the shared registry, adding it to
    `manager` first if it isn't already there. Runtime equivalent of what
    register_plugin_tools_into() does for every built-in plugin at boot --
    lets a single plugin be turned on without restarting Charlie."""
    if manager.get_plugin(plugin.name) is None:
        manager.register(plugin)
    registered: List[str] = []
    for tool_def in plugin.get_tools():
        action = tool_def["name"]
        description = _PLUGIN_ACTION_DESCRIPTIONS.get(action, tool_def["description"])
        reg.register_tool(
            name=f"plugin_{action}",
            description=description,
            schema=tool_def["parameters"],
        )(_make_plugin_runner(manager, action))
        registered.append(f"plugin_{action}")
    return registered


def disable_plugin(reg: "ToolRegistry", manager: Any, plugin_name: str) -> List[str]:
    """Remove a plugin's tools from the shared registry and unregister it
    from `manager`. Returns the removed tool names; empty if it wasn't active."""
    plugin = manager.get_plugin(plugin_name)
    if plugin is None:
        return []
    removed: List[str] = []
    for tool_def in plugin.get_tools():
        full_name = f"plugin_{tool_def['name']}"
        if reg.unregister_tool(full_name):
            removed.append(full_name)
    manager.unregister(plugin_name)
    return removed


def register_plugin_tools_into(reg: "ToolRegistry", cfg: Any) -> Optional[Any]:
    """Register every built-in plugin's actions into `reg` if
    `cfg.plugins_enabled` is true.

    Returns the active PluginManager when plugins are enabled, otherwise None.
    The returned manager is the single source of truth used to execute the
    registered `plugin_*` tools.
    """
    if not getattr(cfg, "plugins_enabled", False):
        logger.debug("Plugin system disabled (plugins_enabled=false); skipping.")
        return None

    manager = _build_plugin_manager(getattr(cfg, "plugin_allow_dirs", []))
    registered: List[str] = []
    # _build_plugin_manager already called manager.register() for each
    # built-in plugin, so enable_plugin() here only needs to bridge their
    # already-registered tools into the shared registry.
    for plugin in list(manager._plugins.values()):
        registered.extend(enable_plugin(reg, manager, plugin))

    logger.info(
        "Plugin system enabled: registered %d plugin tools (plugin_*).",
        len(registered),
    )
    return manager


def _make_plugin_runner(manager: Any, action: str) -> Callable[..., str]:
    """Build a registry-tool wrapper that delegates to a plugin action."""

    def _runner(**arguments: Any) -> str:
        try:
            result = manager.call_tool(action, arguments)
        except Exception as exc:  # surface, never swallow
            logger.error("Plugin tool %s failed", action, exc_info=True)
            return f"Plugin {action} error: {exc}"
        if isinstance(result, dict) and result.get("success") is False:
            return f"Plugin {action} failed: {result.get('error', 'unknown error')}"
        return str(result)

    _runner.__name__ = f"plugin_{action}"
    return _runner


# ---------------------------------------------------------------------------
# Desktop control tools (Windows UI Automation) -- gated, off by default.
# ---------------------------------------------------------------------------

_DESKTOP_DISABLED_MSG = (
    "Desktop control is disabled (set DESKTOP_CONTROL_ENABLED=true and install "
    "uiautomation/pyautogui to enable)."
)


def _desktop_ready() -> bool:
    if not config.desktop_control_enabled:
        return False
    from charlie.desktop import DESKTOP_AVAILABLE
    return DESKTOP_AVAILABLE


# --- Dashboard live-view event bus bridge (set via set_event_bus at init) ---
_event_bus = None  # type: Optional[Any]
_event_loop = None  # type: Optional[Any]
_last_frame_emit_at = 0.0


def set_event_bus(bus: Any, loop: Any) -> None:
    """Wire the producer-side EventBus + its asyncio loop so desktop tool
    functions (running on UIA_EXECUTOR, not the asyncio loop) can bridge a
    desktop_frame event across threads. Called once from main.py at startup."""
    global _event_bus, _event_loop
    _event_bus = bus
    _event_loop = loop


def _downscale_png(png_bytes: bytes, max_edge: int = _DESKTOP_FRAME_MAX_EDGE) -> bytes:
    """Resize `png_bytes` so its longer edge is `max_edge`, preserving aspect
    ratio. Keeps the desktop_frame event payload small at the dashboard's
    throttled frame rate."""
    from io import BytesIO

    from PIL import Image

    img = Image.open(BytesIO(png_bytes))
    scale = max_edge / max(img.size)
    if scale < 1:
        img = img.resize(
            (round(img.size[0] * scale), round(img.size[1] * scale)), Image.LANCZOS
        )
    out = BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _emit_desktop_frame(png_bytes: bytes, elements: List[Any]) -> None:
    """Best-effort, throttled, fire-and-forget: push a desktop_frame event to
    the dashboard's "Watch It Drive" live view. Never raises -- a failure
    here must not affect the calling tool's return value."""
    global _last_frame_emit_at
    if _event_bus is None or _event_loop is None or not config.desktop_frame_capture_enabled:
        return
    now = time.time()
    if now - _last_frame_emit_at < 1.0 / _DESKTOP_FRAME_FPS:
        return
    _last_frame_emit_at = now
    try:
        image_b64 = base64.b64encode(_downscale_png(png_bytes)).decode("ascii")
        payload = {
            "session_id": recovery.get_active_session_id(),
            "image_b64": image_b64,
            "marks": [
                {"mark_id": e.mark_id, "name": e.name, "bounds": list(e.bounds)}
                for e in elements
            ],
        }
        asyncio.run_coroutine_threadsafe(
            _event_bus.emit("desktop_frame", payload), _event_loop
        )
        turn_platform, turn_session_id = recovery.get_current_turn()
        if turn_platform == "telegram":
            from charlie.telegram_bot import get_active_bot
            bot = get_active_bot()
            if bot is not None and turn_session_id and ":" in turn_session_id:
                chat_id = turn_session_id.split(":", 1)[1]
                asyncio.run_coroutine_threadsafe(bot.send_photo(chat_id, png_bytes), _event_loop)
    except Exception:
        logger.warning("desktop_frame emit failed", exc_info=True)


def _capture_and_emit_frame(elements: List[Any]) -> None:
    """Fire-and-forget: capture + annotate + downscale + emit a desktop_frame
    for the dashboard live view, off the calling thread so it never adds
    latency to desktop_observe/desktop_read_screen/desktop_screenshot's
    return. No-ops silently if OCR/vision deps are missing."""
    from charlie.desktop import ocr as desktop_ocr
    from charlie.desktop import vision as desktop_vision
    if not desktop_ocr.OCR_AVAILABLE or not desktop_vision.VISION_AVAILABLE:
        return

    def _work():
        try:
            png = desktop_ocr.capture()
            annotated = desktop_vision.annotate_som(png, elements)
            _emit_desktop_frame(annotated, elements)
        except Exception:
            logger.warning("desktop frame capture failed", exc_info=True)

    threading.Thread(target=_work, daemon=True).start()


def _ocr_fallback_marks(uia_elements: List[Any]) -> List[Any]:
    """Merge an OCR pass into uia_elements.

    Always runs, not just when the UIA tree looks sparse -- a browser's
    toolbar can hand back a couple of real UIA elements while the entire
    page content underneath is invisible to UIA, so an element-count
    threshold can't reliably tell "UIA-blind" from "just a toolbar."
    """
    if not config.desktop_ocr_enabled:
        return uia_elements
    from charlie.desktop import ocr as desktop_ocr
    if not desktop_ocr.OCR_AVAILABLE:
        return uia_elements
    from charlie.desktop.uia import merge_ocr_elements
    try:
        ocr_elements = desktop_ocr.ocr_marks(desktop_ocr.capture())
    except Exception:
        logger.warning("OCR fallback pass failed", exc_info=True)
        return uia_elements
    return merge_ocr_elements(uia_elements, ocr_elements) if ocr_elements else uia_elements


# Below this many merged UIA+OCR elements, the window is probably a
# non-UIA surface (Electron/canvas content OCR can't read either) rather
# than just a sparse toolbar -- worth the vision-LLM round trip.
_GROUNDING_FALLBACK_THRESHOLD = 3


def _grounding_marks(elements: List[Any]) -> List[Any]:
    """Vision-LLM fallback for surfaces UIA+OCR can't see into.

    Unlike _ocr_fallback_marks this is a real vision-LLM round trip (not
    free), so it only runs when the merged pass came back too sparse to be
    useful, not on every observe/screenshot call.
    """
    if len(elements) >= _GROUNDING_FALLBACK_THRESHOLD or not config.vision_enabled:
        return elements
    from charlie.desktop import ocr as desktop_ocr
    if not desktop_ocr.OCR_AVAILABLE:
        return elements
    from charlie.desktop import grounding as desktop_grounding
    from charlie.desktop.uia import merge_ocr_elements
    try:
        png = desktop_ocr.capture()
        grounded = desktop_grounding.detect(png, config)
    except Exception:
        logger.warning("Grounding fallback pass failed", exc_info=True)
        return elements
    return merge_ocr_elements(elements, grounded) if grounded else elements


@registry.register_tool(
    name="desktop_observe",
    description=(
        "Observe the foreground window and return a numbered list of clickable "
        "UI elements (set-of-marks text, e.g. '[3] Button \"Save\"'). Also OCRs "
        "the window so on-screen text with no accessible UI tree is included "
        "(e.g. browser page content, canvases)."
    ),
    schema={"type": "object", "properties": {}, "required": []},
)
def desktop_observe() -> str:
    if not _desktop_ready():
        return _DESKTOP_DISABLED_MSG
    from charlie.desktop.uia import serialize_marks, snapshot_tree
    elements = _grounding_marks(_ocr_fallback_marks(snapshot_tree(max_depth=8)))
    _capture_and_emit_frame(elements)
    if not elements:
        return "No UI elements found in the foreground window."
    return serialize_marks(elements)


@registry.register_tool(
    name="desktop_read_screen",
    description=(
        "Force an OCR pass over the foreground window and return recognized text as "
        "set-of-marks, regardless of whether it has an accessible UI tree. Use for "
        "'read what's on my screen' requests."
    ),
    schema={"type": "object", "properties": {}, "required": []},
)
def desktop_read_screen() -> str:
    if not _desktop_ready():
        return _DESKTOP_DISABLED_MSG
    if not config.desktop_ocr_enabled:
        return "OCR is disabled (set DESKTOP_OCR_ENABLED=true and install pytesseract/mss/Pillow)."
    from charlie.desktop import ocr as desktop_ocr
    if not desktop_ocr.OCR_AVAILABLE:
        return "OCR dependencies not installed (pytesseract/mss/Pillow)."
    from charlie.desktop.uia import merge_ocr_elements, serialize_marks
    try:
        elements = merge_ocr_elements([], desktop_ocr.ocr_marks(desktop_ocr.capture()))
    except Exception:
        logger.warning("desktop_read_screen OCR pass failed", exc_info=True)
        return "Error: OCR pass failed."
    _capture_and_emit_frame(elements)
    if not elements:
        return "No readable text found on screen."
    return serialize_marks(elements)


@registry.register_tool(
    name="desktop_click",
    description="Click a UI element by its mark id (from desktop_observe).",
    schema={
        "type": "object",
        "properties": {
            "mark_id": {"type": "integer", "description": "Mark id from desktop_observe."},
        },
        "required": ["mark_id"],
    },
    is_interactive=True,
)
def desktop_click(mark_id: int) -> str:
    if not _desktop_ready():
        return _DESKTOP_DISABLED_MSG
    from charlie.desktop.actions import click_mark
    return click_mark(mark_id)


@registry.register_tool(
    name="desktop_type",
    description="Type text into a UI element by its mark id. Refuses password/payment fields.",
    schema={
        "type": "object",
        "properties": {
            "mark_id": {"type": "integer", "description": "Mark id from desktop_observe."},
            "text": {"type": "string", "description": "Text to type."},
        },
        "required": ["mark_id", "text"],
    },
    is_interactive=True,
)
def desktop_type(mark_id: int, text: str) -> str:
    if not _desktop_ready():
        return _DESKTOP_DISABLED_MSG
    from charlie.desktop.actions import type_text
    return type_text(mark_id, text)


@registry.register_tool(
    name="desktop_invoke",
    description="Invoke the default action (toggle/expand/select) of a UI element by its mark id.",
    schema={
        "type": "object",
        "properties": {
            "mark_id": {"type": "integer", "description": "Mark id from desktop_observe."},
        },
        "required": ["mark_id"],
    },
    is_interactive=True,
)
def desktop_invoke(mark_id: int) -> str:
    if not _desktop_ready():
        return _DESKTOP_DISABLED_MSG
    from charlie.desktop.actions import invoke_mark
    return invoke_mark(mark_id)


@registry.register_tool(
    name="desktop_key",
    description="Send a keyboard chord to the foreground window, e.g. 'ctrl+s'.",
    schema={
        "type": "object",
        "properties": {
            "keys": {"type": "string", "description": "Key chord, e.g. 'ctrl+s' or 'enter'."},
        },
        "required": ["keys"],
    },
    is_interactive=True,
)
def desktop_key(keys: str) -> str:
    if not _desktop_ready():
        return _DESKTOP_DISABLED_MSG
    from charlie.desktop.actions import key_press
    return key_press(keys)


@registry.register_tool(
    name="desktop_click_at",
    description=(
        "Click a raw pixel coordinate from the most recent desktop_observe or "
        "desktop_screenshot capture. Prefer desktop_click with a mark id when "
        "one exists -- use this only for targets with no accessible mark "
        "(icons, canvases, images, game content). Coordinates are image "
        "pixels from that capture, not physical screen pixels; passing "
        "coordinates from stale or hallucinated positions will click the "
        "wrong place, so always re-observe or re-screenshot immediately "
        "before using this."
    ),
    schema={
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "X pixel coordinate from the latest capture."},
            "y": {"type": "integer", "description": "Y pixel coordinate from the latest capture."},
            "button": {"type": "string", "enum": ["left", "right"], "description": "Mouse button. Defaults to left."},
            "double": {"type": "boolean", "description": "Double-click instead of single-click. Defaults to false."},
        },
        "required": ["x", "y"],
    },
    is_interactive=True,
)
def desktop_click_at(x: int, y: int, button: str = "left", double: bool = False) -> str:
    if not _desktop_ready():
        return _DESKTOP_DISABLED_MSG
    from charlie.desktop.actions import click_at
    return click_at(x, y, button=button, double=double)


@registry.register_tool(
    name="desktop_move",
    description=(
        "Move the mouse cursor to a raw pixel coordinate from the most recent "
        "desktop_observe or desktop_screenshot capture, without clicking. "
        "Coordinates are image pixels from that capture, not physical screen pixels."
    ),
    schema={
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "X pixel coordinate from the latest capture."},
            "y": {"type": "integer", "description": "Y pixel coordinate from the latest capture."},
        },
        "required": ["x", "y"],
    },
    is_interactive=True,
)
def desktop_move(x: int, y: int) -> str:
    if not _desktop_ready():
        return _DESKTOP_DISABLED_MSG
    from charlie.desktop.actions import move_to
    return move_to(x, y)


@registry.register_tool(
    name="desktop_drag",
    description=(
        "Drag the mouse from one raw pixel coordinate to another, from the "
        "most recent desktop_observe or desktop_screenshot capture. Use for "
        "sliders, canvases, drawing, or drag-and-drop where no mark id "
        "applies. Coordinates are image pixels from that capture, not "
        "physical screen pixels."
    ),
    schema={
        "type": "object",
        "properties": {
            "x1": {"type": "integer", "description": "Start X pixel coordinate."},
            "y1": {"type": "integer", "description": "Start Y pixel coordinate."},
            "x2": {"type": "integer", "description": "End X pixel coordinate."},
            "y2": {"type": "integer", "description": "End Y pixel coordinate."},
        },
        "required": ["x1", "y1", "x2", "y2"],
    },
    is_interactive=True,
)
def desktop_drag(x1: int, y1: int, x2: int, y2: int) -> str:
    if not _desktop_ready():
        return _DESKTOP_DISABLED_MSG
    from charlie.desktop.actions import drag
    return drag(x1, y1, x2, y2)


@registry.register_tool(
    name="desktop_scroll",
    description=(
        "Scroll the foreground window at the current cursor position. "
        "Positive notches scroll up, negative scroll down. Roughly 3 notches "
        "moves one screen section."
    ),
    schema={
        "type": "object",
        "properties": {
            "notches": {"type": "integer", "description": "Scroll amount; positive=up, negative=down."},
        },
        "required": ["notches"],
    },
    is_interactive=True,
)
def desktop_scroll(notches: int) -> str:
    if not _desktop_ready():
        return _DESKTOP_DISABLED_MSG
    from charlie.desktop.actions import scroll
    return scroll(notches)


@registry.register_tool(
    name="desktop_screenshot",
    description=(
        "Capture the foreground window as an annotated screenshot for the vision model, "
        "for graphical targets desktop_observe can't describe (icons, canvases, images). "
        "Always returns the current set-of-marks text; also queues the image for the next "
        "reply if a vision model is configured. On a multi-monitor setup this captures only "
        "the monitor showing the foreground window by default -- pass all_monitors=true when "
        "the user explicitly asks about another/other/both/all screens."
    ),
    schema={
        "type": "object",
        "properties": {
            "all_monitors": {
                "type": "boolean",
                "description": "Capture every monitor instead of just the active one.",
            },
        },
        "required": [],
    },
)
def desktop_screenshot(all_monitors: bool = False) -> str:
    if not _desktop_ready():
        return _DESKTOP_DISABLED_MSG
    from charlie.desktop.uia import serialize_marks, snapshot_tree
    elements = _grounding_marks(_ocr_fallback_marks(snapshot_tree(max_depth=8)))
    text_result = serialize_marks(elements) if elements else "No UI elements found in the foreground window."
    if not config.vision_enabled:
        _capture_and_emit_frame(elements)
        return text_result
    from charlie.desktop import ocr as desktop_ocr
    from charlie.desktop import vision as desktop_vision
    if not desktop_ocr.OCR_AVAILABLE or not desktop_vision.VISION_AVAILABLE:
        return text_result
    try:
        png = desktop_ocr.capture(monitor=0) if all_monitors else desktop_ocr.capture()
        annotated = desktop_vision.annotate_som(png, elements)
        set_pending_vision_image(desktop_vision.to_data_url(annotated))
        _emit_desktop_frame(annotated, elements)
    except Exception:
        logger.warning("desktop_screenshot vision annotation failed", exc_info=True)
    return text_result


@registry.register_tool(
    name="desktop_windows",
    description=(
        "List all visible top-level windows by title, for switching between "
        "apps or finding a window to focus/move."
    ),
    schema={"type": "object", "properties": {}, "required": []},
)
def desktop_windows() -> str:
    if not _desktop_ready():
        return _DESKTOP_DISABLED_MSG
    from charlie.desktop.windows import list_windows
    windows = list_windows()
    if not windows:
        return "No visible windows found."
    return "\n".join(w["title"] for w in windows)


@registry.register_tool(
    name="desktop_focus",
    description=(
        "Bring a window to the foreground by title substring "
        "(case-insensitive). Use desktop_windows first to see available titles."
    ),
    schema={
        "type": "object",
        "properties": {
            "window": {"type": "string", "description": "Title substring to match, e.g. 'Notepad' or 'Chrome'."},
        },
        "required": ["window"],
    },
    is_interactive=True,
)
def desktop_focus(window: str) -> str:
    if not _desktop_ready():
        return _DESKTOP_DISABLED_MSG
    from charlie.desktop.windows import focus_window
    return focus_window(window)


@registry.register_tool(
    name="desktop_window",
    description="Minimize, maximize, restore, or close a window by title substring.",
    schema={
        "type": "object",
        "properties": {
            "window": {"type": "string", "description": "Title substring to match."},
            "action": {"type": "string", "enum": ["minimize", "maximize", "restore", "close"]},
        },
        "required": ["window", "action"],
    },
    is_interactive=True,
)
def desktop_window(window: str, action: str) -> str:
    if not _desktop_ready():
        return _DESKTOP_DISABLED_MSG
    from charlie.desktop.windows import manage_window
    return manage_window(window, action)


@registry.register_tool(
    name="desktop_move_window",
    description="Move and resize a window by title substring, e.g. to arrange two windows side by side.",
    schema={
        "type": "object",
        "properties": {
            "window": {"type": "string", "description": "Title substring to match."},
            "x": {"type": "integer", "description": "New left position in screen pixels."},
            "y": {"type": "integer", "description": "New top position in screen pixels."},
            "width": {"type": "integer", "description": "New width in pixels."},
            "height": {"type": "integer", "description": "New height in pixels."},
        },
        "required": ["window", "x", "y", "width", "height"],
    },
    is_interactive=True,
)
def desktop_move_window(window: str, x: int, y: int, width: int, height: int) -> str:
    if not _desktop_ready():
        return _DESKTOP_DISABLED_MSG
    from charlie.desktop.windows import move_resize_window
    return move_resize_window(window, x, y, width, height)


@registry.register_tool(
    name="system_control",
    description=(
        "Control system volume and media playback via keyboard media keys: "
        "volume_up, volume_down, mute, play_pause, next_track, prev_track."
    ),
    schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["volume_up", "volume_down", "mute", "play_pause", "next_track", "prev_track"],
            },
        },
        "required": ["action"],
    },
    is_interactive=True,
)
def system_control(action: str) -> str:
    if not _desktop_ready():
        return _DESKTOP_DISABLED_MSG
    from charlie.desktop.actions import system_control as _system_control
    return _system_control(action)


# --- Headless browser tools (Playwright + Chrome) -- gated, off by default.

_BROWSER_DISABLED_MSG = (
    "Browser control is disabled (set BROWSER_ENABLED=true and install the "
    "browser extra: uv sync --extra browser)."
)


def _browser_ready() -> bool:
    if not config.browser_enabled:
        return False
    from charlie.browser import BROWSER_AVAILABLE
    return BROWSER_AVAILABLE


@registry.register_tool(
    name="browser_task",
    description=(
        "Do something inside a website in a headless browser -- search, click through, play a "
        "video, fill a form -- and report back. Opens the user's real browser only when the "
        "request implies it (play/watch/listen, or 'show me'/'open it'). Use for anything that "
        "requires being on a site; use web_search for questions answerable from search snippets, "
        "and browser_read to read one specific known URL."
    ),
    schema={
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "A clear, self-contained description of the browsing task."},
        },
        "required": ["task"],
    },
)
def browser_task(task: str) -> str:
    return "Error: browser_task must be dispatched through Brain.browser_task, not called directly."


@registry.register_tool(
    name="browser_read",
    description="Fetch one specific known URL and return its extracted text content.",
    schema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch."},
        },
        "required": ["url"],
    },
)
def browser_read(url: str) -> str:
    if not _browser_ready():
        return _BROWSER_DISABLED_MSG
    from charlie.browser.actions import read_url
    result = read_url(url)
    if "error" in result:
        return f"Error: {result['error']}"
    return f"URL: {result['url']}\n\n{result['content']}"


def set_pending_vision_image(url: Optional[str]) -> None:
    """Queue an image data URL for an upcoming outgoing LLM payload."""
    if url is not None:
        _pending_vision_images.append(url)


def pop_pending_vision_image() -> Optional[str]:
    """Read and remove the oldest queued vision image -- FIFO, each consumed exactly once."""
    return _pending_vision_images.pop(0) if _pending_vision_images else None


def register_plugin_tools(cfg: Any = None) -> Optional[Any]:
    """Register plugin tools into the global `registry` if enabled.

    Convenience wrapper used by main.py and the test suite. Returns the
    active PluginManager (or None when disabled).
    """
    if cfg is None:
        from charlie.config import config as cfg
    return register_plugin_tools_into(registry, cfg)
