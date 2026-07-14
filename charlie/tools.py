"""Charlie tool registry and built-in tools.

All tool definitions, execution logic, and provider integrations live here.
No business logic -- just tool I/O.
"""

import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx

from charlie.agents import AGENT_REGISTRY
from charlie.config import config
from charlie.session_store import SessionStore

logger = logging.getLogger("charlie.tools")


# --- Vector memory store (set via set_memory_store at init) ---
_memory_store = None  # type: Optional[Any]
# --- Knowledge graph store (set via set_memory_graph at init) ---
_memory_graph = None  # type: Optional[Any]
# --- Blackboard for agent swarm (set via set_blackboard at init) ---
_blackboard = None  # type: Optional[Any]
# --- Pending vision-tier screenshot: written by desktop_screenshot, consumed
# --- once by Brain._build_payload for the very next outgoing payload. ---
_pending_vision_image = None  # type: Optional[str]
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
    def set_memory_graph(self, graph: Any) -> None:
        """Inject knowledge graph store for graph tools."""
        global _memory_graph
        _memory_graph = graph
    def set_blackboard(self, blackboard: Any) -> None:
        """Inject Blackboard for delegate_to_agent tool."""
        global _blackboard
        _blackboard = blackboard


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
    lowered = command.lower().strip()
    for keyword in _HARD_BLOCKED_KEYWORDS:
        if keyword in lowered:
            return f"Command blocked -- risky keyword '{keyword}'"
    return None


def is_shell_command_gated(command: str) -> Optional[str]:
    """Check `command` against the gated (approve/decline) keyword list.
    Returns a human-readable reason the command needs user approval, or None
    if it doesn't. Only meaningful once `is_shell_command_blocked` has
    already passed -- gating never overrides a hard block.
    """
    lowered = command.lower().strip()
    for keyword in _GATED_KEYWORDS:
        if keyword in lowered:
            return f"risky keyword '{keyword}'"
    return None


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
        )
        if not any(lowered.startswith(prefix) for prefix in allowed_prefixes):
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
            result = "Command succeeded (exit code 0). No output."
        else:
            result = f"Command finished with exit code {process.returncode}. No output."
        if not voice_mode:
            result = "WARNING: Shell commands are powerful. Be careful with destructive operations.\n\n" + result
        return result
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {SHELL_TIMEOUT}s."
    except Exception as e:
        logger.exception("Shell command error: %s", command)
        return f"Error executing shell command: {e}"


# --- System diagnostics: fixed commands only, no user-supplied string ever
# reaches the shell. Used by the K.A.R.E.N. swarm agent, which is not
# supervised turn-by-turn like shell_execute's caller, so it must not be
# given an open command parameter.
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
    platform this runs on (e.g. pure-logic tests on Linux CI)."""
    import getpass
    placeholders = {"yourusername", "username", "user"}
    current_user = getpass.getuser()
    parts = []
    for part in path.split("\\"):
        clean_part = part.strip("<>").lower()
        if clean_part in placeholders:
            parts.append(current_user)
        else:
            parts.append(part)
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
                "description": "memory (max 2200), user (max 1375), opinions (max 800 chars).",
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
# Knowledge graph tools
# ---------------------------------------------------------------------------


def _graph_available() -> bool:
    """Check if the memory graph is loaded."""
    return _memory_graph is not None


@registry.register_tool(
    name="graph_add_fact",
    description=(
        "Add a fact to the knowledge graph. "
        "A fact is a relationship: subject -> predicate -> object."
    ),
    schema={
        "type": "object",
        "properties": {
            "subject": {"type": "string", "description": "The subject entity (e.g. 'user')"},
            "predicate": {"type": "string", "description": "The relationship (e.g. 'prefers')"},
            "object": {"type": "string", "description": "The object entity (e.g. 'dark mode')"},
        },
        "required": ["subject", "predicate", "object"],
    },
)
def graph_add_fact(subject: str, predicate: str, object: str) -> str:
    if not _graph_available():
        return "Knowledge graph is not available."
    try:
        _memory_graph.add_fact(subject, predicate, object)
        return f"Added: {subject} -> {predicate} -> {object}"
    except Exception as e:
        logger.exception("graph_add_fact error")
        return f"Error adding fact: {e}"


@registry.register_tool(
    name="graph_query",
    description="Query the knowledge graph. Find facts related to a subject, object, or pattern.",
    schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search term to find in facts"},
            "subject_filter": {"type": "string", "description": "Optional: filter by subject"},
        },
        "required": ["query"],
    },
)
def graph_query(query: str, subject_filter: str = "") -> str:
    if not _graph_available():
        return "Knowledge graph is not available."
    try:
        results = _memory_graph.search_facts(query, subject_filter=subject_filter or None)
        if not results:
            return "No matching facts found."
        lines = []
        for s, p, o, score in results:
            lines.append(f"- {s} -> {p} -> {o} (relevance: {score:.2f})")
        return "\n".join(lines)
    except Exception as e:
        logger.exception("graph_query error")
        return f"Error querying graph: {e}"


@registry.register_tool(
    name="graph_consolidate",
    description=(
        "Consolidate the knowledge graph: merge duplicates, "
        "remove stale facts, and update importance scores."
    ),
    schema={
        "type": "object",
        "properties": {},
        "required": [],
    },
)
def graph_consolidate() -> str:
    if not _graph_available():
        return "Knowledge graph is not available."
    try:
        removed = _memory_graph.consolidate()
        return f"Consolidated graph. Removed {removed} stale/duplicate facts."
    except Exception as e:
        logger.exception("graph_consolidate error")
        return f"Error consolidating graph: {e}"


# ---------------------------------------------------------------------------
# Swarm delegation tool (delegate tasks to MARVEL agents)
# ---------------------------------------------------------------------------

# Derived from AGENT_REGISTRY (the swarm's single source of truth for
# registered agents) so this list can never drift out of sync with which
# agents actually exist -- it previously hand-listed only 5 of the 7
# registered agents, silently excluding J.A.R.V.I.S. and Vision.
_VALID_AGENTS = tuple(AGENT_REGISTRY.keys())
_POLL_INTERVAL_S = 0.5
_POLL_TIMEOUT_S = 60.0


@registry.register_tool(
    name="delegate_to_agent",
    description=(
        "Delegate a task to a MARVEL agent for parallel execution. "
        "Use when a subtask requires deep focus (research, analysis, file ops) "
        "while you continue the main conversation. Returns the agent's result."
    ),
    schema={
        "type": "object",
        "properties": {
            "agent_name": {
                "type": "string",
                "enum": list(_VALID_AGENTS),
                "description": "Which MARVEL agent to assign the task to",
            },
            "task_description": {
                "type": "string",
                "description": "Clear, self-contained description of what to do",
            },
        },
        "required": ["agent_name", "task_description"],
    },
)
def delegate_to_agent(agent_name: str, task_description: str) -> str:
    """Add a task to the blackboard, poll up to 60s, and return the result."""
    global _blackboard
    if _blackboard is None:
        return (
            "Error: Swarm orchestrator is not running. "
            "Cannot delegate tasks without an active blackboard."
        )

    if agent_name not in _VALID_AGENTS:
        agents_str = ", ".join(_VALID_AGENTS)
        return f"Error: Unknown agent '{agent_name}'. Valid agents: {agents_str}"

    try:
        task = _blackboard.add_task(
            name=task_description,
            assigned_to=agent_name,
            column="todo",
        )
        task_id = task.id
        logger.info(
            "Delegated task [%s] to %s: %s",
            task_id, agent_name, task_description,
        )

        # Poll loop: wait for status to be 'done' or 'failed'
        deadline = time.monotonic() + _POLL_TIMEOUT_S
        last_status = "pending"
        while time.monotonic() < deadline:
            time.sleep(_POLL_INTERVAL_S)
            current = _blackboard.get_task(task_id)
            if current is None:
                return f"Error: Task {task_id} was removed from the blackboard."
            if current.status == "done":
                logger.info(
                    "Task [%s] completed by %s (result length=%d)",
                    task_id, agent_name, len(current.result or ""),
                )
                result = current.result or "(no result content)"
                elapsed = _POLL_TIMEOUT_S - (deadline - time.monotonic())
                return (
                    f"Agent {agent_name} completed task in {elapsed:.1f}s. "
                    f"Result:\n{result}"
                )
            if current.status == "failed":
                logger.warning("Task [%s] failed for %s", task_id, agent_name)
                return (
                    f"Agent {agent_name} failed to complete the task. "
                    f"Status: {current.status}. Result: {current.result or 'N/A'}"
                )
            if current.status != last_status:
                logger.info(
                    "Task [%s] status changed: %s -> %s",
                    task_id, last_status, current.status,
                )
                last_status = current.status

        # Timeout
        logger.warning(
            "Task [%s] timed out after %.1fs (status=%s)",
            task_id, _POLL_TIMEOUT_S, last_status,
        )
        return (
            f"Agent {agent_name} did not complete the task within "
            f"{_POLL_TIMEOUT_S:.0f} seconds. The task ({task_id}) is still "
            f"in status '{last_status}' and will continue running."
        )
    except Exception as e:
        logger.exception("delegate_to_agent error")
        return f"Error delegating to agent: {e}"


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
    "browser_fetch": "Fetch and return the rendered HTML/text of a web URL.",
    "browser_screenshot": "Capture a screenshot image of a web URL.",
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
        BrowserPlugin,
        CalendarPlugin,
        CodeExecPlugin,
        FilesystemPlugin,
        PluginManager,
    )

    manager = PluginManager()
    manager.register(FilesystemPlugin(allowed_dirs=allow_dirs))
    manager.register(BrowserPlugin())
    manager.register(CalendarPlugin())
    manager.register(CodeExecPlugin())
    return manager


def register_plugin_tools_into(reg: "ToolRegistry", cfg: Any) -> Optional[Any]:
    """Register plugin actions into `reg` if `cfg.plugins_enabled` is true.

    Returns the active PluginManager when plugins are enabled, otherwise None.
    The returned manager is the single source of truth used to execute the
    registered `plugin_*` tools.
    """
    if not getattr(cfg, "plugins_enabled", False):
        logger.debug("Plugin system disabled (plugins_enabled=false); skipping.")
        return None

    manager = _build_plugin_manager(getattr(cfg, "plugin_allow_dirs", []))

    tool_defs = manager.get_all_tool_definitions()
    for tool_def in tool_defs:
        action = tool_def["name"]
        description = _PLUGIN_ACTION_DESCRIPTIONS.get(action, tool_def["description"])
        runner = _make_plugin_runner(manager, action)

        reg.register_tool(
            name=f"plugin_{action}",
            description=description,
            schema=tool_def["parameters"],
        )(runner)

    logger.info(
        "Plugin system enabled: registered %d plugin tools (plugin_*).",
        len(tool_defs),
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
    elements = _ocr_fallback_marks(snapshot_tree(max_depth=8))
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
    name="desktop_screenshot",
    description=(
        "Capture the foreground window as an annotated screenshot for the vision model, "
        "for graphical targets desktop_observe can't describe (icons, canvases, images). "
        "Always returns the current set-of-marks text; also queues the image for the next "
        "reply if a vision model is configured."
    ),
    schema={"type": "object", "properties": {}, "required": []},
)
def desktop_screenshot() -> str:
    if not _desktop_ready():
        return _DESKTOP_DISABLED_MSG
    from charlie.desktop.uia import serialize_marks, snapshot_tree
    elements = _ocr_fallback_marks(snapshot_tree(max_depth=8))
    text_result = serialize_marks(elements) if elements else "No UI elements found in the foreground window."
    if not config.vision_enabled:
        return text_result
    from charlie.desktop import ocr as desktop_ocr
    from charlie.desktop import vision as desktop_vision
    if not desktop_ocr.OCR_AVAILABLE or not desktop_vision.VISION_AVAILABLE:
        return text_result
    try:
        png = desktop_ocr.capture()
        annotated = desktop_vision.annotate_som(png, elements)
        set_pending_vision_image(desktop_vision.to_data_url(annotated))
    except Exception:
        logger.warning("desktop_screenshot vision annotation failed", exc_info=True)
    return text_result


def set_pending_vision_image(url: Optional[str]) -> None:
    """Queue an image data URL for the very next outgoing LLM payload."""
    global _pending_vision_image
    _pending_vision_image = url


def pop_pending_vision_image() -> Optional[str]:
    """Read and clear the queued vision image -- consumed exactly once."""
    global _pending_vision_image
    url, _pending_vision_image = _pending_vision_image, None
    return url


def register_plugin_tools(cfg: Any = None) -> Optional[Any]:
    """Register plugin tools into the global `registry` if enabled.

    Convenience wrapper used by main.py and the test suite. Returns the
    active PluginManager (or None when disabled).
    """
    if cfg is None:
        from charlie.config import config as cfg
    return register_plugin_tools_into(registry, cfg)
