"""Charlie tool registry and built-in tools.

All tool definitions, execution logic, and provider integrations live here.
No business logic -- just tool I/O.
"""

import asyncio
import hashlib
import http.client
import inspect
import ipaddress
import json
import logging
import os
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote, urljoin, urlsplit

from charlie.config import config
from charlie.execution_context import ExecutionContext, get_current_execution_context, terminate_process_tree
from charlie.known_apps import APP_REGISTRY
from charlie.results import ResultsStore
from charlie.session_store import SessionStore
from charlie.utils import is_process_running

logger = logging.getLogger("charlie.tools")


# --- Process-local facade (set via set_memory_service at init) ---
_memory_service = None  # type: Optional[Any]
_self_extension_orchestrator = None  # type: Optional[Any]
_runtime_introspector = None  # type: Optional[Any]
_self_knowledge_service = None  # type: Optional[Any]
_charlie_doctor = None  # type: Optional[Any]
# --- Pending vision-tier screenshot: written by desktop_screenshot, consumed
# --- once by Brain._build_payload for the very next outgoing payload. ---
_pending_vision_image = None  # type: Optional[str]
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
_SHELL_POLL_INTERVAL = 0.05
_SHELL_CANCEL_DRAIN_TIMEOUT = 0.5

# --- SearXNG keyword detection ---
_TIME_SENSITIVE_KEYWORDS = (
    "today", "new", "recent", "latest", "breaking", "now", "live", "news",
    "headline", "headlines", "update", "updates", "score", "scores", "standings", "schedule",
)
_NEWS_KEYWORDS = ("news", "headline", "story", "stories")

# --- Query decomposition ---
_DECOMPOSE_KEYWORDS = ("compare", "versus", "vs", "or", "and")
_DECOMPOSE_MIN_WORDS = 10
_DECOMPOSE_MAX_QUERIES = 3


@dataclass(frozen=True)
class ToolExecutionResult:
    """Structured tool result bridge; ordinary ToolRegistry callers still receive strings."""

    model_text: str
    structured_data: Any = None
    result_kind: Optional[str] = None


def configure_runtime_services(
    *,
    self_extension_orchestrator: Any,
    runtime_introspector: Any,
    self_knowledge_service: Any,
    doctor: Any,
) -> None:
    """Install main-owned services used by runtime tools."""
    global _self_extension_orchestrator, _runtime_introspector, _self_knowledge_service, _charlie_doctor
    _self_extension_orchestrator = self_extension_orchestrator
    _runtime_introspector = runtime_introspector
    _self_knowledge_service = self_knowledge_service
    _charlie_doctor = doctor


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
        owner: str = "",
        risk_class: Optional[str] = None,
    ):
        """owner/risk_class are plain strings (not charlie.autonomy.RiskClass) so this module
        never has to import autonomy.py, which already imports from here. Known built-ins
        mirror semantic metadata from charlie.capabilities; owner remains a compatibility
        label for registry callers. Dynamic tools keep their explicit metadata."""
        from charlie.capabilities import get_builtin_tool_metadata

        canonical = get_builtin_tool_metadata(name)
        effective_owner = owner or (canonical or {}).get("tool_registry_owner", "")
        effective_risk = canonical["risk_class"] if canonical is not None else risk_class
        self._tools[name] = {
            "func": None,
            "description": description,
            "schema": schema,
            "is_interactive": is_interactive,
            "owner": effective_owner,
            "risk_class": effective_risk,
        }

        def decorator(func: Callable[..., Any]):
            self._tools[name]["func"] = func
            from charlie.capabilities import register_tool_in_index
            register_tool_in_index(
                name=name,
                description=description,
                schema=schema,
                func=func,
                owner=owner or effective_owner,
                risk_class=risk_class,
                is_interactive=is_interactive,
            )
            return func

        return decorator

    def get_owner(self, name: str) -> str:
        return self._tools.get(name, {}).get("owner", "")

    def list_metadata(self) -> List[Dict[str, Any]]:
        """name/description/owner/risk_class for every registered tool -- what a Tools-grid UI needs."""
        return [
            {
                "name": name,
                "description": info["description"],
                "owner": info["owner"],
                "risk_class": info["risk_class"],
            }
            for name, info in self._tools.items()
        ]

    def get_risk_class(self, name: str) -> Optional[str]:
        return self._tools.get(name, {}).get("risk_class")

    def unregister_tool(self, name: str) -> bool:
        """Remove a tool so it no longer appears in get_tool_definitions()
        or is callable via execute_tool(). Returns whether it existed."""
        existed = self._tools.pop(name, None) is not None
        if existed:
            from charlie.capabilities import unregister_tool_from_index
            unregister_tool_from_index(name)
        return existed

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
            if name == "system_control":
                return system_control(**arguments).model_text
            logger.error("Tool '%s' not found.", name)
            return f"Error: Tool '{name}' is not registered."

        func = self._tools[name]["func"]
        try:
            params = inspect.signature(func).parameters
            if not any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
                arguments = {k: v for k, v in arguments.items() if k in params}
            logger.info("Executing tool '%s' with arguments: %s", name, arguments)
            result = func(**arguments)
            if isinstance(result, ToolExecutionResult):
                return result.model_text
            return str(result)
        except Exception as e:  # pragma: no cover - defensive
            logger.exception("Error executing tool '%s': %s", name, e)
            return f"Error executing tool '{name}': {e}"

    def execute_tool_structured(self, name: str, arguments: Dict[str, Any]) -> ToolExecutionResult:
        """Return model text plus structured metadata for structured-capable tools."""
        # Preserve test/integration adapters that monkeypatch execute_tool directly.
        if getattr(self.execute_tool, "__func__", None) is not ToolRegistry.execute_tool:
            return ToolExecutionResult(str(self.execute_tool(name, arguments)))
        if name in {"web_search", "web_research"}:
            report = _run_research_report(name, arguments)
            return ToolExecutionResult(report.legacy_text(), report, "research_report")
        if name == "system_control":
            return system_control(**arguments)
        if name in {
            "desktop_open_app",
            "desktop_close_app",
            "desktop_open_url",
            "file_write",
            "shell_execute",
            "download_public_pdf",
            "media_control",
            "media_snapshot",
            "calendar_list",
            "calendar_create",
            "calendar_update",
            "calendar_delete",
            "calendar_get",
            "automation_create",
            "automation_update",
            "automation_get",
            "automation_list",
            "automation_cancel",
        }:
            func = self._tools[name]["func"]
            if name == "file_write" and func is file_write:
                return _write_file_atomically(
                    path=arguments["path"], content=arguments["content"]
                )
            result = func(**arguments)
            return result if isinstance(result, ToolExecutionResult) else ToolExecutionResult(str(result))
        return ToolExecutionResult(self.execute_tool(name, arguments))

    def set_memory_service(self, service: Any) -> None:
        """Inject the process-composed long-term memory facade."""
        global _memory_service
        _memory_service = service


# Global tool registry
registry = ToolRegistry()

_media_adapter = None
MEDIA_ACTIONS = frozenset(
    {
        "volume_up",
        "volume_down",
        "set_volume",
        "mute",
        "unmute",
        "play_pause",
        "next_track",
        "prev_track",
        "stop",
    }
)
MEDIA_OPERATION_IDS = {
    "volume_up": "media.volume.adjust",
    "volume_down": "media.volume.adjust",
    "set_volume": "media.volume.set",
    "mute": "media.mute.set",
    "unmute": "media.mute.set",
    "play_pause": "media.playback.toggle",
    "next_track": "media.playback.next",
    "prev_track": "media.playback.previous",
    "stop": "media.playback.stop",
}


def media_operation_id(action: str) -> str | None:
    return MEDIA_OPERATION_IDS.get(action)


def _get_media_adapter():
    global _media_adapter
    if _media_adapter is None:
        from charlie.media_adapter import WindowsMediaAdapter

        _media_adapter = WindowsMediaAdapter()
    return _media_adapter


def _on_media_executor_thread() -> bool:
    from charlie.media_runtime import media_executor_thread_id

    return media_executor_thread_id() == threading.get_ident()


def _media_result_text(action: str, result: dict) -> str:
    if not result.get("ok"):
        failure_kind = result.get("failure_kind")
        prefix = "unsupported" if failure_kind == "unsupported" else "failed"
        return f"Error: Media control '{action}' {prefix}: {result.get('reason', 'unavailable')}."
    if action == "set_volume":
        if result.get("verified") is False:
            return "Volume command accepted; resulting volume could not be verified."
        return f"Volume set to {result.get('volume_percent')}%."
    if action in {"volume_up", "volume_down"}:
        if result.get("verified") is False:
            return "Volume adjustment accepted; resulting volume could not be verified."
        return f"Volume adjusted to {result.get('volume_percent')}%."
    if action in {"mute", "unmute"}:
        if result.get("verified") is False:
            return "Mute command accepted; resulting mute state could not be verified."
        state = "muted" if result.get("muted") else "unmuted"
        return f"Audio is now {state} (volume {result.get('volume_percent')}%)."
    if result.get("verified") is False:
        return f"Media action '{action}' accepted; playback state could not be verified."
    return f"Media action '{action}' completed."


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
    """Backward-compatible quick-search wrapper over ResearchEngine."""
    return _run_research_report("web_search", {"query": query}).legacy_text()


@registry.register_tool(
    name="web_research",
    description=(
        "Research the public web and return bounded evidence with source IDs. "
        "Use quick, standard, or deep mode; ordinary research does not launch the interactive browser."
    ),
    schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Research goal."},
            "mode": {
                "type": "string",
                "enum": ["auto", "quick", "standard", "deep"],
                "description": "Research depth. Auto selects from the request.",
            },
            "domain": {"type": "string", "description": "Optional comma-separated domain constraint."},
        },
        "required": ["query"],
    },
)
def web_research(query: str, mode: str = "auto", domain: str = "") -> str:
    """Run bounded structured research while retaining string tool compatibility."""
    return _run_research_report(
        "web_research", {"query": query, "mode": mode, "domain": domain}
    ).legacy_text()


def _run_research_report(name: str, arguments: Dict[str, Any]):
    from charlie.research.engine import ResearchEngine

    query = str(arguments.get("query", ""))
    mode = "quick" if name == "web_search" else str(arguments.get("mode", "auto"))
    domain = str(arguments.get("domain", ""))
    domain_filters = list(dict.fromkeys(part.strip() for part in domain.split(",") if part.strip()))
    return ResearchEngine(config).run_sync(query, mode, domain_filters=domain_filters or None)


def _single_search(query: str) -> str:
    """Compatibility helper retained for callers of the old private function."""
    from charlie.research.engine import ResearchEngine

    return ResearchEngine(config).run_sync(query, "quick").legacy_text()

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
_SAFE_SHELL_COMMAND = re.compile(
    r"(?:python\s+(?:--version|-V)|python3\s+--version|where\s+python|taskkill\s+/\?|git\s+rev-parse\s+HEAD|"
    r"exit\s+(?:[1-9]|[1-9]\d|1\d\d|2[0-4]\d|25[0-5]))\Z",
    re.IGNORECASE,
)


def is_acceptance_safe_shell_command(command: str) -> bool:
    """Match the few harmless commands that need no shell approval."""
    return bool(_SAFE_SHELL_COMMAND.fullmatch(command.strip()))


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
    if is_acceptance_safe_shell_command(command):
        return None
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


@registry.register_tool(
    name="shell_execute",
    description=(
        "Run one shell command and return its output. Submit exactly one command per call; "
        "do not chain commands or use shell metacharacters such as ;, |, &, `, $, (, or ). "
        "If multiple commands are needed, call this tool multiple times. Risky/destructive commands remain blocked."
    ),
    schema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": (
                    "Exactly one shell command. Do not chain commands or use ;, |, &, `, $, (, or ); "
                    "make separate tool calls for separate commands."
                ),
            },
        },
        "required": ["command"],
    },
    is_interactive=True,
)
def shell_execute(command: str, *, voice_mode: bool = False) -> ToolExecutionResult | str:
    lowered = command.lower().strip()

    # An already-running known app gets focused instead of relaunched -- applies
    # regardless of voice_mode, and before the allowlist check below so a blocked
    # wrapper (e.g. "powershell -Command Start-Process notepad") never even gets
    # a chance to fail: if the app's already open, there's nothing to launch.
    app_entry = _detect_app_launch(command)
    if app_entry and sys.platform == "win32" and is_process_running(app_entry.close_process):
        from charlie.desktop.windows import focus_window
        focused = focus_window(app_entry.close_process.removesuffix(".exe"))
        verified = "(verified)" in focused.casefold()
        return ToolExecutionResult(
            focused,
            {"ok": True, "verified": verified, "goal_verified": verified},
            "terminal_result",
        )

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
        if not is_acceptance_safe_shell_command(command) and not any(
            lowered == prefix.strip() or lowered.startswith(prefix) for prefix in allowed_prefixes
        ):
            return (
                "Error: Command not on the allowed list for voice mode. "
                "Unrestricted shell access is not available through the voice safety policy."
            )

    # Universal guards: apply in every mode.
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

    context = get_current_execution_context()
    try:
        if context is not None:
            return _shell_execute_owned(command, voice_mode=voice_mode, context=context)

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
            return ToolExecutionResult(
                (
                    f"Command is still running after {SHELL_TIMEOUT}s with no output "
                    "(left running -- if this opened an app or window, it launched "
                    "successfully)."
                ),
                {"ok": True, "exit_code": None, "running": True},
                "terminal_result",
            )
        parts = []
        if stdout and stdout.strip():
            parts.append(f"STDOUT:\n{stdout.strip()}")
        if stderr and stderr.strip():
            parts.append(f"STDERR:\n{stderr.strip()}")
        rendered = "\n".join(parts) if parts else _render_shell_result(stdout, stderr, process.returncode, voice_mode)
        return ToolExecutionResult(
            rendered,
            {
                "ok": process.returncode == 0,
                "exit_code": process.returncode,
                "stdout": stdout or "",
                "stderr": stderr or "",
            },
            "terminal_result",
        )
    except Exception as e:
        logger.exception("Shell command error: %s", command)
        return f"Error executing shell command: {e}"


def _render_shell_result(stdout: str, stderr: str, returncode: Optional[int], voice_mode: bool) -> str:
    parts = []
    if stdout and stdout.strip():
        parts.append(f"STDOUT:\n{stdout.strip()}")
    if stderr and stderr.strip():
        parts.append(f"STDERR:\n{stderr.strip()}")
    if parts:
        return "\n".join(parts)
    # Many commands (start, taskkill, etc.) return empty on success
    if returncode == 0:
        result = "Command succeeded (exit code 0). No output."
    else:
        result = f"Command finished with exit code {returncode}. No output."
    if not voice_mode:
        result = "WARNING: Shell commands are powerful. Be careful with destructive operations.\n\n" + result
    return result


def _shell_process_creation_kwargs() -> dict[str, Any]:
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def _cancel_owned_shell_process(owned_process: Any) -> bool:
    process = owned_process.popen
    quiescent = terminate_process_tree(owned_process, timeout=_SHELL_CANCEL_DRAIN_TIMEOUT)
    try:
        process.communicate(timeout=_SHELL_CANCEL_DRAIN_TIMEOUT)
    except subprocess.TimeoutExpired:
        quiescent = terminate_process_tree(owned_process, timeout=_SHELL_CANCEL_DRAIN_TIMEOUT)
        try:
            process.communicate(timeout=_SHELL_CANCEL_DRAIN_TIMEOUT)
        except subprocess.TimeoutExpired:
            quiescent = False
    return quiescent


def _shell_execute_owned(
    command: str,
    *,
    voice_mode: bool,
    context: ExecutionContext,
) -> ToolExecutionResult | str:
    """Run shell with prompt cancellation and explicit process ownership."""
    process = subprocess.Popen(
        command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **_shell_process_creation_kwargs(),
    )
    try:
        owned_process = context.register_process(process)
    except Exception:
        try:
            process.kill()
            process.wait(timeout=_SHELL_CANCEL_DRAIN_TIMEOUT)
        except Exception:
            logger.error("Owned shell process identity could not be captured", exc_info=True)
        raise
    deadline = time.monotonic() + SHELL_TIMEOUT
    try:
        while True:
            if context.cancellation_requested:
                if _cancel_owned_shell_process(owned_process):
                    return "Command cancelled."
                time.sleep(_SHELL_POLL_INTERVAL)
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                try:
                    process.communicate(timeout=_SHELL_KILL_DRAIN_TIMEOUT)
                except subprocess.TimeoutExpired:
                    pass
                return ToolExecutionResult(
                    (
                        f"Command is still running after {SHELL_TIMEOUT}s with no output "
                        "(left running -- if this opened an app or window, it launched "
                        "successfully)."
                    ),
                    {"ok": True, "exit_code": None, "running": True},
                    "terminal_result",
                )
            try:
                stdout, stderr = process.communicate(timeout=min(_SHELL_POLL_INTERVAL, remaining))
                if context.cancellation_requested:
                    return "Command cancelled."
                return ToolExecutionResult(
                    _render_shell_result(stdout, stderr, process.returncode, voice_mode),
                    {
                        "ok": process.returncode == 0,
                        "exit_code": process.returncode,
                        "stdout": stdout or "",
                        "stderr": stderr or "",
                    },
                    "terminal_result",
                )
            except subprocess.TimeoutExpired:
                if context.cancellation_requested:
                    if _cancel_owned_shell_process(owned_process):
                        return "Command cancelled."
                    continue
    finally:
        context.unregister_process(owned_process)


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
        if process.returncode and output:
            return f"Diagnostic '{check}' unavailable: host command returned exit code {process.returncode}."
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
    "id_rsa",
    "id_ed25519",
    os.path.sep + "etc" + os.path.sep,
    os.path.sep + "proc" + os.path.sep,
    os.path.sep + "sys" + os.path.sep,
    os.path.sep + "registry" + os.path.sep,
    os.path.sep + ".ssh" + os.path.sep,
    os.path.sep + ".aws" + os.path.sep,
    os.path.sep + ".kube" + os.path.sep,
    os.path.sep + ".gnupg" + os.path.sep,
    os.path.sep + "credentials",
)


def _resolve_safe_path(path_str: str) -> Path:
    target = Path(path_str)
    if target.is_absolute():
        resolved = target.resolve(strict=False)
    else:
        resolved = (_WORKSPACE_DIR / path_str).resolve(strict=False)
    return resolved


def get_path_gate_reason(path_str: str, *, tool_name: Optional[str] = None) -> Optional[str]:
    """Return an approval reason for a sensitive or non-local file operation.

    Callers without a tool name retain the legacy sensitive-path probe used by
    background-task classification; the live file tools pass their operation.
    """
    try:
        resolved = _resolve_safe_path(_resolve_user_placeholders(path_str))
    except Exception:
        return "file path could not be resolved and requires approval" if tool_name else None

    from charlie.config import config
    path_lower = str(resolved).lower()
    system_root = config.system_root.lower()
    if system_root and system_root in path_lower:
        return f"system root path '{config.system_root}' at '{resolved}'"
    sensitive_directories = {".ssh", ".aws", ".kube", ".gnupg"}
    sensitive_directory = next(
        (part for part in resolved.parts if part.casefold() in sensitive_directories),
        None,
    )
    if sensitive_directory:
        return f"sensitive path '{sensitive_directory}' at '{resolved}'"
    for blocked in _GATED_PATH_SUBSTRINGS:
        if blocked.lower() in path_lower:
            return f"sensitive path '{blocked}' at '{resolved}'"

    if tool_name is None:
        return None

    home = Path.home()
    roots = (
        _WORKSPACE_DIR,
        *(home / name for name in ("Documents", "Downloads", "Desktop")),
    )
    within_approved_root = False
    for root in roots:
        try:
            resolved.relative_to(root.resolve(strict=False))
            within_approved_root = True
            break
        except ValueError:
            continue

    if tool_name in {"file_write", "download_public_pdf"} and resolved.is_file():
        return f"overwrite of existing file '{resolved}' requires approval"
    if not within_approved_root:
        return (
            f"{tool_name} path '{resolved}' is outside Charlie's workspace, Documents, "
            "Downloads, and Desktop"
        )
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
    description="Create a file or atomically replace one after exact-path approval.",
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
    return _write_file_atomically(path, content).model_text


def _write_file_atomically(path: str, content: str) -> ToolExecutionResult:
    temp_path: Optional[Path] = None
    try:
        path = _resolve_user_placeholders(path)
        safe_path = _resolve_safe_path(path)
        if safe_path.is_dir():
            return ToolExecutionResult(
                f"Error: Cannot write to a directory ({safe_path}). Please specify a file path.",
                {"ok": False, "verified": False},
                "file_write",
            )

        safe_path.parent.mkdir(parents=True, exist_ok=True)
        expected = content.encode("utf-8")
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=safe_path.parent,
            prefix=".charlie-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(expected)
            handle.flush()
            os.fsync(handle.fileno())

        if safe_path.exists():
            os.chmod(temp_path, safe_path.stat().st_mode & 0o777)
        os.replace(temp_path, safe_path)
        temp_path = None

        actual = safe_path.read_bytes()
        expected_hash = hashlib.sha256(expected).hexdigest()
        actual_hash = hashlib.sha256(actual).hexdigest()
        verified = actual == expected and actual_hash == expected_hash
        structured = {
            "ok": verified,
            "verified": verified,
            "byte_count": len(actual),
            "sha256": actual_hash,
        }
        text = (
            f"Successfully wrote to {safe_path} and verified {len(actual)} bytes."
            if verified
            else f"Error: File write verification failed for '{safe_path}'."
        )
        return ToolExecutionResult(text, structured, "file_write")
    except ValueError as e:
        return ToolExecutionResult(f"Error: {e}", {"ok": False, "verified": False}, "file_write")
    except Exception as e:
        logger.exception("File write error: %s", path)
        return ToolExecutionResult(
            f"Error writing file: {e}", {"ok": False, "verified": False}, "file_write"
        )
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                logger.warning("Could not remove failed file-write temporary: %s", temp_path)


_PUBLIC_PDF_MAX_BYTES = 25 * 1024 * 1024
_PUBLIC_PDF_MAX_REDIRECTS = 5
_PUBLIC_PDF_CHUNK_SIZE = 64 * 1024
_PUBLIC_PDF_TIMEOUT = 15.0
_PUBLIC_NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")


def _validate_public_pdf_url(url: str):
    if not isinstance(url, str) or not url or any(ord(char) < 32 for char in url):
        raise ValueError("A valid public HTTPS URL is required.")
    try:
        parsed = urlsplit(url)
        port = parsed.port if parsed.port is not None else 443
        host = parsed.hostname
    except ValueError as exc:
        raise ValueError("A valid public HTTPS URL is required.") from exc
    if parsed.scheme.lower() != "https":
        raise ValueError("Only HTTPS URLs are allowed.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL credentials are not allowed.")
    if not host or not 1 <= port <= 65535:
        raise ValueError("A valid public HTTPS host and port are required.")
    try:
        host = host.encode("idna").decode("ascii")
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as exc:
        raise ValueError("The PDF host could not be resolved as public HTTPS.") from exc

    public_addresses = []
    for address_info in addresses:
        family, socktype, protocol, _, sockaddr = address_info
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        try:
            ip = ipaddress.ip_address(sockaddr[0].split("%", 1)[0])
        except ValueError as exc:
            raise ValueError("The PDF host resolved to an invalid IP address.") from exc
        if ip.version == 6 and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip.version == 6 and ip in _PUBLIC_NAT64_PREFIX:
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if not ip.is_global:
            raise ValueError("The PDF host must resolve only to public IP addresses.")
        public_addresses.append((family, socktype, protocol, sockaddr))
    if not public_addresses:
        raise ValueError("The PDF host has no public IP address.")

    host_header = f"[{host}]" if ":" in host else host
    if port != 443:
        host_header = f"{host_header}:{port}"
    path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
    request_target = path + (f"?{query}" if query else "")
    return host, host_header, request_target, tuple(public_addresses)


@contextmanager
def _open_public_pdf_response(host, host_header, request_target, addresses):
    sock = None
    last_error = None
    context = ssl.create_default_context()
    for family, socktype, protocol, sockaddr in addresses:
        connection = socket.socket(family, socktype, protocol)
        try:
            connection.settimeout(_PUBLIC_PDF_TIMEOUT)
            connection.connect(sockaddr)
            sock = context.wrap_socket(connection, server_hostname=host)
            break
        except OSError as exc:
            last_error = exc
            connection.close()
    if sock is None:
        raise OSError("Could not establish a verified public HTTPS connection.") from last_error

    try:
        request = (
            f"GET {request_target} HTTP/1.1\r\n"
            f"Host: {host_header}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii")
        sock.sendall(request)
        response = http.client.HTTPResponse(sock)
        try:
            response.begin()
            yield response
        finally:
            response.close()
    finally:
        sock.close()


def _readback_public_pdf(path: Path):
    digest = hashlib.sha256()
    byte_count = 0
    with path.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise ValueError("Downloaded file does not have a PDF signature.")
        handle.seek(0)
        while chunk := handle.read(_PUBLIC_PDF_CHUNK_SIZE):
            byte_count += len(chunk)
            if byte_count > _PUBLIC_PDF_MAX_BYTES:
                raise ValueError("PDF exceeds the 25 MiB download limit.")
            digest.update(chunk)
    return byte_count, digest.hexdigest()


@registry.register_tool(
    name="download_public_pdf",
    description="Download a public HTTPS PDF to a local path and verify its saved bytes.",
    schema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Public HTTPS URL of a PDF."},
            "path": {"type": "string", "description": "Local destination path for the PDF."},
        },
        "required": ["url", "path"],
    },
)
def download_public_pdf(url: str, path: str) -> ToolExecutionResult:
    temp_path = None
    try:
        safe_path = _resolve_safe_path(_resolve_user_placeholders(path))
        if safe_path.is_dir():
            raise ValueError("The destination must be a file path.")
        safe_path.parent.mkdir(parents=True, exist_ok=True)

        current_url = url
        stream_hash = hashlib.sha256()
        written = 0
        expected_length = None
        for redirects_followed in range(_PUBLIC_PDF_MAX_REDIRECTS + 1):
            target = _validate_public_pdf_url(current_url)
            with _open_public_pdf_response(*target) as response:
                if response.status in {301, 302, 303, 307, 308}:
                    if redirects_followed >= _PUBLIC_PDF_MAX_REDIRECTS:
                        raise ValueError("The PDF URL exceeded the redirect limit.")
                    location = response.getheader("Location")
                    if not location:
                        raise ValueError("The PDF server returned a redirect without a destination.")
                    current_url = urljoin(current_url, location)
                    continue
                if response.status != 200:
                    raise ValueError(f"The PDF server returned HTTP {response.status}.")

                raw_length = response.getheader("Content-Length")
                if raw_length is not None:
                    try:
                        expected_length = int(raw_length)
                    except (TypeError, ValueError) as exc:
                        raise ValueError("The PDF server returned an invalid byte count.") from exc
                    if expected_length < 0:
                        raise ValueError("The PDF server returned an invalid byte count.")
                    if expected_length > _PUBLIC_PDF_MAX_BYTES:
                        raise ValueError("PDF exceeds the 25 MiB download limit.")

                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    prefix=f".{safe_path.name}.",
                    suffix=".tmp",
                    dir=safe_path.parent,
                    delete=False,
                ) as temp:
                    temp_path = Path(temp.name)
                    while chunk := response.read(_PUBLIC_PDF_CHUNK_SIZE):
                        written += len(chunk)
                        if written > _PUBLIC_PDF_MAX_BYTES:
                            raise ValueError("PDF exceeds the 25 MiB download limit.")
                        if expected_length is not None and written > expected_length:
                            raise ValueError("Downloaded byte count exceeds Content-Length.")
                        temp.write(chunk)
                        stream_hash.update(chunk)
                    temp.flush()
                    os.fsync(temp.fileno())
            break
        else:
            raise ValueError("The PDF URL exceeded the redirect limit.")

        if expected_length is not None and written != expected_length:
            raise ValueError("Downloaded byte count does not match Content-Length.")
        if written != temp_path.stat().st_size:
            raise OSError("Temporary PDF byte count did not match disk size.")

        stream_sha256 = stream_hash.hexdigest()
        temp_count, temp_sha256 = _readback_public_pdf(temp_path)
        if temp_count != written or temp_sha256 != stream_sha256:
            raise OSError("Temporary PDF readback did not match downloaded bytes.")
        if expected_length is not None and temp_count != expected_length:
            raise OSError("Temporary PDF readback did not match Content-Length.")

        os.replace(temp_path, safe_path)
        temp_path = None
        final_count, final_sha256 = _readback_public_pdf(safe_path)
        if final_count != written or final_sha256 != stream_sha256:
            raise OSError("Saved PDF readback did not match downloaded bytes.")
        if expected_length is not None and final_count != expected_length:
            raise OSError("Saved PDF readback did not match Content-Length.")

        return ToolExecutionResult(
            f"Downloaded and verified PDF ({final_count} bytes, SHA-256 {final_sha256}) to {safe_path}.",
            {
                "ok": True,
                "verified": True,
                "path": str(safe_path),
                "byte_count": final_count,
                "sha256": final_sha256,
                "url": current_url,
            },
            "public_pdf_download",
        )
    except Exception as exc:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove incomplete PDF temp file: %s", temp_path)
        logger.warning("Public PDF download failed: %s", type(exc).__name__)
        return ToolExecutionResult(
            f"Error downloading public PDF: {exc}",
            {"ok": False, "failure_kind": type(exc).__name__},
            "public_pdf_download",
        )


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


_MEMORY_SECRET_RE = re.compile(
    r"(?i)\b(?:password|passcode|api[\s_-]?key|access[\s_-]?token|refresh[\s_-]?token|"
    r"client[\s_-]?secret|secret|private[\s_-]?key|credential)\b\s*(?:is|:|=)\s*\S+"
)
_MEMORY_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.IGNORECASE)
_MEMORY_GOV_ID_RE = re.compile(
    r"(?i)\b(?:aadhaar|aadhar|uidai)\b.{0,24}\b\d{4}[ -]?\d{4}[ -]?\d{4}\b|"
    r"\bPAN\b.{0,16}\b[A-Z]{5}\d{4}[A-Z]\b|"
    r"\bSSN\b.{0,16}\b\d{3}-\d{2}-\d{4}\b|"
    r"\bpassport(?:\s+(?:number|no\.?))?\b.{0,16}\b[A-Z]\d{7}\b"
)
_MEMORY_CARD_RE = re.compile(
    r"(?i)\b(?:credit|debit)\s+card(?:\s+(?:number|no\.?))?\s*[:#-]?\s*"
    r"\d[\d -]{11,22}\d\b|(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)"
)


def _contains_sensitive_memory_content(*values: str) -> bool:
    """Refuse obvious secrets and regulated identifiers at the persistent-write boundary."""
    text = " ".join(value for value in values if value)
    if _MEMORY_SECRET_RE.search(text) or _MEMORY_PRIVATE_KEY_RE.search(text) or _MEMORY_GOV_ID_RE.search(text):
        return True
    for candidate in _MEMORY_CARD_RE.findall(text):
        digits = [int(digit) for digit in re.sub(r"\D", "", candidate)]
        if 13 <= len(digits) <= 19:
            checksum = sum(
                (digit * 2 - 9 if digit * 2 > 9 else digit * 2) if index % 2 else digit
                for index, digit in enumerate(reversed(digits))
            )
            if checksum % 10 == 0:
                return True
    return False


@registry.register_tool(
    name="memory",
    description=(
        "Manage persistent memory files and structured graph memories. For structured target, "
        "add only durable user preferences or environment facts; use update for explicit corrections. "
        "Never store task outputs or full conversations. Use search/undo with the managed memory service; "
        "use target=all to search both structured and semantic saved memories. "
        "File actions: add appends an entry, "
        "replace swaps an entry containing old_text, remove drops an entry, "
        "consolidate returns all entries with capacity for review. "
        "Entries are delimited by section sign."
    ),
    schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "replace", "remove", "consolidate", "search", "update", "undo"],
                "description": (
                    "For structured target: add/search/update/undo a graph memory item. "
                    "For file targets: add/replace/remove/consolidate entries."
                ),
            },
            "target": {
                "type": "string",
                "enum": ["memory", "user", "opinions", "structured", "all"],
                "description": (
                    "memory/user/opinions are persistent text files; "
                    "structured uses the managed memory graph; all searches structured and semantic saved memories."
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
            "item_id": {"type": "string", "description": "Structured memory item id for update or undo."},
            "query": {"type": "string", "description": "Search text for structured memory."},
            "category": {
                "type": "string",
                "enum": ["preference", "fact", "person", "place", "concept", "task", "event"],
                "description": "Structured memory category for add; defaults to fact.",
            },
            "subject": {
                "type": "string",
                "description": "Fact subject; provide all three triple fields to correct a graph fact.",
            },
            "predicate": {
                "type": "string",
                "description": "Fact relation; provide all three triple fields to correct a graph fact.",
            },
            "object": {
                "type": "string",
                "description": "Fact object; provide all three triple fields to correct a graph fact.",
            },
        },
        "required": ["action", "target"],
    },
)
def memory(
    action: str,
    target: str,
    content: str = "",
    old_text: str = "",
    item_id: str = "",
    query: str = "",
    category: str = "fact",
    subject: str = "",
    predicate: str = "",
    object: str = "",
) -> str:
    if target == "all":
        if action != "search":
            return "Error: target 'all' supports search only."
        if not query.strip():
            return "Error: query is required for memory search."
        if _memory_service is None:
            return "Saved memory is not available."
        try:
            results = _memory_service.recall(query, n_results=5)
            if results is None:
                return "Memory search failed; the memory service may be unavailable."
            if not results:
                return "No relevant saved memories found."
            lines = []
            for item in results:
                content = str(item.get("content") or item.get("text") or "").strip()
                if content:
                    lines.append(f"- [{item.get('source', 'saved')}] {content}")
            return "Saved memories:\n" + "\n".join(lines) if lines else "No relevant saved memories found."
        except Exception as e:
            logger.exception("Combined memory search error")
            return f"Memory search failed ({type(e).__name__})."

    if target == "structured":
        if _memory_service is None:
            return "Structured memory is not available."
        try:
            if action == "add":
                if not content and not (subject and predicate and object):
                    return "Error: content or a complete subject/predicate/object is required."
                if _contains_sensitive_memory_content(content, subject, predicate, object):
                    return "Error: Sensitive credentials, payment data, or government IDs cannot be stored."
                item = _memory_service.add_item(
                    category=category,
                    content=content,
                    subject=subject,
                    predicate=predicate,
                    obj=object,
                    provenance="tool_explicit_add",
                )
                if not item.get("id"):
                    return "Error: Structured memory could not be saved."
                return f"Remembered structured memory id={item['id']}; {item.get('content', content)}"
            if action == "search":
                if not query.strip():
                    return "Error: query is required for structured memory search."
                items = _memory_service.search_items(query, limit=10)
                if not items:
                    return "No matching structured memories found."
                return "Structured memories:\n" + "\n".join(
                    f"- id={item['id']}; {item['category']}: {item['content']}" for item in items
                )
            if action == "update":
                if not item_id:
                    return "Error: item_id is required for structured memory update."
                if not content:
                    return "Error: content is required for structured memory update."
                if _contains_sensitive_memory_content(content, subject, predicate, object):
                    return "Error: Sensitive credentials, payment data, or government IDs cannot be stored."
                item = _memory_service.update_item(
                    item_id,
                    content=content,
                    provenance="tool_explicit_correction",
                    subject=subject or None,
                    predicate=predicate or None,
                    obj=object or None,
                )
                if item is None:
                    return "Error: Memory item was not found."
                return f"Updated structured memory id={item_id}; {item['content']}"
            if action == "undo":
                if not item_id:
                    return "Error: item_id is required for structured memory undo."
                item = _memory_service.undo_item_update(item_id, provenance="tool_owner_undo")
                if item is None:
                    if not any(row.get("id") == item_id for row in _memory_service.list_items(limit=1000)):
                        return "Error: Memory item was not found."
                    return "No structured memory update is available to undo."
                return f"Restored structured memory id={item_id}; {item['content']}"
            return f"Error: Unsupported structured memory action '{action}'."
        except ValueError as e:
            if "Triple-backed facts require subject, predicate, and object" in str(e):
                return f"Error: {e}"
            logger.exception("Structured memory tool error: action=%s", action)
            return f"Error updating structured memory: {type(e).__name__}."
        except Exception as e:
            logger.exception("Structured memory tool error: action=%s", action)
            return f"Error updating structured memory: {type(e).__name__}."

    if target not in _MEMORY_MAX_CHARS:
        return f"Error: target must be 'memory', 'user', or 'opinions', got '{target}'."
    if action in ("add", "replace") and _contains_sensitive_memory_content(content, old_text):
        return "Error: Sensitive credentials, payment data, or government IDs cannot be stored."

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
    name="propose_new_tool",
    description=(
        "Tier-3 self-extension: author a brand-new tool in Python and queue it for "
        "your review. Use only when no existing tool covers the request and you're asked "
        "to 'learn' or permanently gain a new capability -- never for a one-off task. "
        "Always requires explicit owner approval before it can run."
    ),
    schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Tool name, snake_case, matching the function name in code."},
            "description": {"type": "string", "description": "One line: what the tool does."},
            "code": {
                "type": "string",
                "description": "Full Python source: exactly one top-level function named `name`, with a docstring.",
            },
        },
        "required": ["name", "description", "code"],
    },
)
def propose_new_tool(name: str, description: str, code: str) -> str:
    """Never actually reached -- charlie.core._exec_one intercepts this tool name before
    dispatch here (it needs Brain/event-bus access this plain registry func doesn't have),
    the same pattern shell_execute's voice_mode gets special-cased."""
    return "Error: propose_new_tool must be intercepted by the tool loop, not executed directly."


@registry.register_tool(
    name="start_background_task",
    description=(
        "Start a multi-step goal running in the background while you keep talking normally -- "
        "for tasks worth minutes, not a single instant action another tool already covers. "
        "Reports progress via alerts; ask 'how's the task going' to check status."
    ),
    schema={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The goal to accomplish, in plain language."},
            "priority": {
                "type": "integer",
                "description": "Higher runs first if another task is already queued. Default 0.",
            },
            "depends_on": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Task ids that must finish first, if any.",
            },
        },
        "required": ["text"],
    },
)
def start_background_task(text: str, priority: int = 0, depends_on=None) -> str:
    """Never actually reached -- charlie.core._exec_one intercepts this tool name before
    dispatch here (it needs Brain/event-bus access this plain registry func doesn't have),
    same pattern as propose_new_tool."""
    return "Error: start_background_task must be intercepted by the tool loop, not executed directly."


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
    if action == "remember" and _contains_sensitive_memory_content(content):
        return "Error: Sensitive credentials, payment data, or government IDs cannot be stored."
    if _memory_service is None or not _memory_service.semantic_available():
        return "Vector memory is not available. Embedding service may be offline."

    if action == "remember":
        count = _memory_service.remember_semantic(
            text=content,
            source="user",
            session_id="explicit",
            auto_extract=False,
        )
        if count > 0:
            return f"Remembered: {content[:100]}"
        return "Failed to store memory."

    elif action == "recall":
        results = _memory_service.search_semantic(content, n_results=3)
        if results is None:
            return "Memory search failed -- the embedding service may be down. Try again shortly."
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


@registry.register_tool(
    name="recall_results",
    description=(
        "Recall what a background task found or accomplished earlier. Use for "
        "'what did you find' / 'how did that task go' style questions."
    ),
    schema={
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "How many recent results to return. Default 5.",
            }
        },
        "required": [],
    },
)
def recall_results(limit: int = 5) -> str:
    store = None
    try:
        store = ResultsStore(db_path=config.session_db_path)
        results = store.get_recent(limit=limit)
    except Exception as e:
        logger.exception("recall_results error")
        return f"Error recalling results: {e}"
    finally:
        if store is not None:
            try:
                store.close()
            except Exception:
                logger.debug("Results store close failed", exc_info=True)

    if not results:
        return "No task results recorded yet."

    lines = [f"- [{r.created_at}] {r.summary} -- {r.full_result[:200]}" for r in results]
    return "\n".join(lines)


@registry.register_tool(
    name="capabilities",
    description=(
        "List what you can actually do right now, derived live from your registered tools. "
        "Use this when asked 'what can you do' instead of guessing from memory of your prompt."
    ),
    schema={"type": "object", "properties": {}, "required": []},
)
def capabilities() -> str:
    from charlie.capabilities import build_capability_roster, capability_index

    return build_capability_roster(capability_index, config)


# ---------------------------------------------------------------------------
# Knowledge graph tools
# ---------------------------------------------------------------------------


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
    if _memory_service is None:
        return "Knowledge graph is not available."
    if _contains_sensitive_memory_content(subject, predicate, object):
        return "Error: Sensitive credentials, payment data, or government IDs cannot be stored."
    try:
        item = _memory_service.add_item(
            category="fact",
            content=f"{subject} {predicate} {object}",
            subject=subject,
            predicate=predicate,
            obj=object,
            provenance="tool_explicit_add",
        )
        if item.get("id") is None:
            return "Knowledge graph is not available."
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
    if _memory_service is None:
        return "Knowledge graph is not available."
    try:
        results = _memory_service.search_facts(query, subject_filter=subject_filter or None)
        if results is None:
            return "Knowledge graph is not available."
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
    if _memory_service is None:
        return "Knowledge graph is not available."
    try:
        removed = _memory_service.consolidate_graph()
        if removed is None:
            return "Knowledge graph is not available."
        return f"Consolidated graph. Removed {removed} stale/duplicate facts."
    except Exception as e:
        logger.exception("graph_consolidate error")
        return f"Error consolidating graph: {e}"


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
    "fs_search": "Search the local filesystem for files matching a glob pattern.",
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
            owner="plugins",
            risk_class="security_sensitive",
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


# UIA tree element threshold above which OCR is skipped as unnecessary
_UIA_RICH_THRESHOLD = 5


def _ocr_fallback_marks(uia_elements: List[Any]) -> List[Any]:
    """Progressive OCR: only run OCR pass when UIA accessibility tree is sparse or missing."""
    if len(uia_elements) >= _UIA_RICH_THRESHOLD or not config.desktop_ocr_enabled:
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


def _coerce_desktop_strings(value: Any, field: str) -> tuple[list[str], Optional[str]]:
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, (list, tuple)) or not values:
        return [], f"Error: '{field}' must contain at least one string."
    cleaned = [item.strip() if isinstance(item, str) else "" for item in values]
    if any(not item for item in cleaned):
        return [], f"Error: '{field}' must contain only non-empty strings."
    return cleaned, None


@registry.register_tool(
    name="desktop_open_app",
    description="Open or focus one or more local Windows applications.",
    schema={
        "type": "object",
        "properties": {
            "apps": {"type": "array", "items": {"type": "string"}, "description": "App names to open."},
            "commands": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional compatibility hints from deterministic app matching.",
            },
        },
        "required": ["apps"],
        "additionalProperties": False,
    },
    is_interactive=True,
)
def desktop_open_app(apps: list[str] | str, commands: list[str] | str | None = None) -> ToolExecutionResult:
    app_list, error = _coerce_desktop_strings(apps, "apps")
    if error:
        return ToolExecutionResult(error, {"ok": False, "failure_kind": "invalid_arguments"}, "desktop_app_open")
    command_list: list[str] = []
    if commands is not None:
        command_list, error = _coerce_desktop_strings(commands, "commands")
        if error or len(command_list) != len(app_list):
            message = error or "Error: 'commands' must match 'apps' length."
            return ToolExecutionResult(message, {"ok": False, "failure_kind": "invalid_arguments"}, "desktop_app_open")
    if not _desktop_ready():
        return ToolExecutionResult(
            _DESKTOP_DISABLED_MSG, {"ok": False, "failure_kind": "unavailable"}, "desktop_app_open"
        )
    from charlie.desktop.apps import launch_apps

    result = launch_apps(app_list, command_list)
    ok = "could not open" not in result.lower()
    return ToolExecutionResult(result, {"ok": ok, "verified": ok, "apps": app_list}, "desktop_app_open")


@registry.register_tool(
    name="desktop_close_app",
    description="Close one or more local Windows applications; closing can lose unsaved state.",
    schema={
        "type": "object",
        "properties": {
            "apps": {"type": "array", "items": {"type": "string"}, "description": "App names to close."},
            "processes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional compatibility process hints from deterministic app matching.",
            },
        },
        "required": ["apps"],
        "additionalProperties": False,
    },
    is_interactive=True,
)
def desktop_close_app(apps: list[str] | str, processes: list[str] | str | None = None) -> ToolExecutionResult:
    app_list, error = _coerce_desktop_strings(apps, "apps")
    if error:
        return ToolExecutionResult(error, {"ok": False, "failure_kind": "invalid_arguments"}, "desktop_app_close")
    process_list: list[str] = []
    if processes is not None:
        process_list, error = _coerce_desktop_strings(processes, "processes")
        if error or len(process_list) != len(app_list):
            message = error or "Error: 'processes' must match 'apps' length."
            return ToolExecutionResult(message, {"ok": False, "failure_kind": "invalid_arguments"}, "desktop_app_close")
    if not _desktop_ready():
        return ToolExecutionResult(
            _DESKTOP_DISABLED_MSG, {"ok": False, "failure_kind": "unavailable"}, "desktop_app_close"
        )
    from charlie.desktop.apps import close_apps

    result = close_apps(app_list, process_list)
    ok = "failed to close" not in result.lower()
    return ToolExecutionResult(result, {"ok": ok, "verified": ok, "apps": app_list}, "desktop_app_close")


@registry.register_tool(
    name="desktop_open_url",
    description="Open one validated HTTP(S) URL in the user's real default browser.",
    schema={
        "type": "object",
        "properties": {"url": {"type": "string", "description": "HTTP(S) URL to open."}},
        "required": ["url"],
        "additionalProperties": False,
    },
    is_interactive=True,
)
def desktop_open_url(url: str) -> ToolExecutionResult:
    if not isinstance(url, str) or not url.strip():
        return ToolExecutionResult(
            "Error: 'url' must be a non-empty HTTP(S) URL.",
            {"ok": False, "failure_kind": "invalid_arguments"},
            "desktop_url_open",
        )
    if not _desktop_ready():
        return ToolExecutionResult(
            _DESKTOP_DISABLED_MSG, {"ok": False, "failure_kind": "unavailable"}, "desktop_url_open"
        )
    from charlie.desktop.apps import open_url_in_default_browser

    opened = open_url_in_default_browser(url)
    if not opened:
        return ToolExecutionResult(
            f"Error: Could not open {url} in the default browser.",
            {"ok": False, "verified": False, "url": url},
            "desktop_url_open",
        )
    return ToolExecutionResult(
        f"Opened {url} in the default browser.",
        {
            "ok": True,
            "verified": False,
            "verification_status": "executed_unverified",
            "url": url,
        },
        "desktop_url_open",
    )


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
    elements = _grounding_marks(_ocr_fallback_marks(snapshot_tree()))
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
        "reply if a vision model is configured."
    ),
    schema={"type": "object", "properties": {}, "required": []},
)
def desktop_screenshot() -> str:
    if not _desktop_ready():
        return _DESKTOP_DISABLED_MSG
    from charlie.desktop.uia import serialize_marks, snapshot_tree
    elements = _grounding_marks(_ocr_fallback_marks(snapshot_tree()))
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
    name="media_control",
    description=(
        "Canonical OS media control: volume_up, volume_down, set_volume, mute, unmute, "
        "play_pause, next_track, prev_track, or stop."
    ),
    schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "volume_up",
                    "volume_down",
                    "set_volume",
                    "mute",
                    "unmute",
                    "play_pause",
                    "next_track",
                    "prev_track",
                    "stop",
                ],
            },
            "percent": {"type": "number", "minimum": 0, "maximum": 100},
        },
        "required": ["action"],
    },
    is_interactive=True,
)
def media_control(action: str, percent: float | None = None) -> ToolExecutionResult:
    if not isinstance(action, str) or action not in MEDIA_ACTIONS:
        result = {
            "ok": False,
            "available": True,
            "failure_kind": "unsupported",
            "reason": "Unsupported media action",
        }
    elif action == "set_volume" and (
        percent is None or isinstance(percent, bool) or not isinstance(percent, (int, float)) or not 0 <= percent <= 100
    ):
        result = {
            "ok": False,
            "available": True,
            "failure_kind": "invalid_arguments",
            "reason": "Volume percent must be between 0 and 100",
        }
    else:
        if not _on_media_executor_thread():
            from charlie.media_runtime import get_media_executor

            return get_media_executor().submit(media_control, action, percent).result()
        result = asyncio.run(_get_media_adapter().control(action, percent=percent))
    return ToolExecutionResult(_media_result_text(action, result), result, "media_control")


def system_control(action: str, percent: float | None = None) -> ToolExecutionResult:
    """Legacy Python compatibility shim; intentionally not model-facing."""
    return media_control(action, percent=percent)


from charlie.capabilities import register_tool_in_index as _register_compatibility_operation

_register_compatibility_operation(
    name="system_control",
    description="Legacy Python compatibility alias for canonical media_control.",
    schema={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": sorted(MEDIA_ACTIONS)},
            "percent": {"type": "number", "minimum": 0, "maximum": 100},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
    owner="media",
    is_interactive=True,
)


@registry.register_tool(
    name="media_snapshot",
    description="Read the current OS media session, playback, artwork, volume, and mute state.",
    schema={"type": "object", "properties": {}, "additionalProperties": False},
)
def media_snapshot() -> ToolExecutionResult:
    if not _on_media_executor_thread():
        from charlie.media_runtime import get_media_executor

        return get_media_executor().submit(media_snapshot).result()
    result = asyncio.run(_get_media_adapter().snapshot())
    if result.get("adapter_available") is False:
        text = "Error: Media snapshot unavailable."
    elif result.get("status") == "no_session":
        text = "No active media session."
    elif result.get("status") == "failed":
        text = "Error: Media snapshot read failed."
    else:
        text = "Current media session state read."
    return ToolExecutionResult(text, result, "media_snapshot")


@registry.register_tool(
    name="open_windows_settings",
    description="Open a validated Windows Settings deep link.",
    schema={
        "type": "object",
        "properties": {
            "uri": {"type": "string", "description": "Validated ms-settings URI."},
            "name": {"type": "string", "description": "Human-readable settings name."},
        },
        "required": ["uri"],
        "additionalProperties": False,
    },
    is_interactive=True,
)
def open_windows_settings(uri: str, name: str = "settings") -> str:
    if not isinstance(uri, str) or not uri.startswith("ms-settings:"):
        return "Error: invalid Windows Settings URI."
    if sys.platform != "win32":
        return f"Windows Settings deep-linking requires Windows (detected {sys.platform})."
    try:
        os.startfile(uri)  # type: ignore[attr-defined]
        return f"Opened Windows {name.capitalize()} Settings."
    except Exception as exc:
        return f"Failed to open {name} settings: {exc}"


def _calendar_tool_result(operation: str, action: Callable[[], Any]) -> ToolExecutionResult:
    try:
        result = action()
        return ToolExecutionResult(f"Calendar {operation} completed.", result, operation)
    except KeyError as exc:
        result = {"ok": False, "failure_kind": "not_found", "reason": f"Calendar event not found: {exc.args[0]}"}
        return ToolExecutionResult(f"Error: {result['reason']}", result, operation)
    except ValueError as exc:
        result = {"ok": False, "failure_kind": "invalid_arguments", "reason": str(exc)}
        return ToolExecutionResult(f"Error: {exc}", result, operation)
    except RuntimeError as exc:
        result = {"ok": False, "failure_kind": "unavailable", "reason": str(exc)}
        return ToolExecutionResult(f"Error: {exc}", result, operation)


@registry.register_tool(
    name="calendar_list",
    description="List events from Charlie's canonical local calendar.",
    schema={
        "type": "object",
        "properties": {"day": {"type": "string", "description": "UTC calendar day YYYY-MM-DD."}},
        "additionalProperties": False,
    },
)
def calendar_list(day: str | None = None) -> ToolExecutionResult:
    from charlie.calendar_runtime import calendar_runtime_required

    return _calendar_tool_result(
        "list",
        lambda: {"events": calendar_runtime_required().execute_sync("list_events", day)},
    )


@registry.register_tool(
    name="calendar_create",
    description="Create an event or reminder in Charlie's canonical local calendar.",
    schema={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "start_at": {"type": "string"},
            "end_at": {"type": "string"},
            "reminder_at": {"type": "string"},
        },
        "required": ["title", "start_at"],
        "additionalProperties": False,
    },
)
def calendar_create(
    title: str,
    start_at: str,
    end_at: str | None = None,
    reminder_at: str | None = None,
) -> ToolExecutionResult:
    from charlie.calendar_runtime import calendar_runtime_required

    return _calendar_tool_result(
        "create",
        lambda: calendar_runtime_required().execute_sync(
            "create_event", title, start_at, end_at=end_at, reminder_at=reminder_at
        ),
    )


@registry.register_tool(
    name="calendar_update",
    description="Update an event or reminder in Charlie's canonical local calendar.",
    schema={
        "type": "object",
        "properties": {
            "event_id": {"type": "string"},
            "title": {"type": "string"},
            "start_at": {"type": "string"},
            "end_at": {"type": "string"},
            "reminder_at": {"type": "string"},
            "completed": {"type": "integer", "enum": [0, 1]},
        },
        "required": ["event_id"],
        "additionalProperties": False,
    },
)
def calendar_update(event_id: str, **values: Any) -> ToolExecutionResult:
    from charlie.calendar_runtime import calendar_runtime_required

    return _calendar_tool_result(
        "update",
        lambda: calendar_runtime_required().execute_sync("update_event", event_id, values),
    )


@registry.register_tool(
    name="calendar_delete",
    description="Delete an event or reminder from Charlie's canonical local calendar.",
    schema={
        "type": "object",
        "properties": {"event_id": {"type": "string"}},
        "required": ["event_id"],
        "additionalProperties": False,
    },
)
def calendar_delete(event_id: str) -> ToolExecutionResult:
    from charlie.calendar_runtime import calendar_runtime_required

    def delete() -> dict:
        calendar_runtime_required().execute_sync("delete_event", event_id)
        return {"status": "deleted", "id": event_id}

    return _calendar_tool_result("delete", delete)


@registry.register_tool(
    name="calendar_get",
    description="Read one event from Charlie's canonical local calendar.",
    schema={
        "type": "object",
        "properties": {"event_id": {"type": "string"}},
        "required": ["event_id"],
        "additionalProperties": False,
    },
)
def calendar_get(event_id: str) -> ToolExecutionResult:
    from charlie.calendar_runtime import calendar_runtime_required

    return _calendar_tool_result(
        "get",
        lambda: calendar_runtime_required().execute_sync("get_event", event_id),
    )


def _automation_tool_result(operation: str, action: Callable[[], Any]) -> ToolExecutionResult:
    try:
        result = action()
        data = {"ok": True, **result}

        def describe_schedule(schedule: dict) -> str:
            text = str(schedule.get("text", ""))[:120]
            return (
                f"id={schedule.get('id')}; {schedule.get('kind')}: {text}; "
                f"{schedule.get('status')}; run={schedule.get('active_run_status') or 'idle'}; "
                f"{schedule.get('recurrence')}; "
                f"next_run_at={schedule.get('next_run_at')} ({schedule.get('timezone')})"
            )

        if operation == "list":
            data["observed"] = True
            schedules = result.get("automations", [])
            if not schedules:
                message = "No automation schedules are configured."
            else:
                limit = 25
                lines = [describe_schedule(schedule) for schedule in schedules[:limit]]
                message = "Automation schedules:\n" + "\n".join(lines)
                if len(schedules) > limit:
                    message += f"\nShowing {limit} of {len(schedules)} schedules."
        elif operation == "get":
            data["observed"] = True
            message = f"Automation schedule observed: {describe_schedule(result)}"
        elif operation == "cancel" and result.get("status") == "completed":
            reason = f"Automation schedule {result.get('id')} is completed and was not cancelled."
            return ToolExecutionResult(
                f"Error: {reason}",
                {
                    **data,
                    "ok": False,
                    "failure_kind": "already_completed",
                    "verification_status": "verified_failure",
                    "reason": reason,
                },
                operation,
            )
        elif operation == "cancel" and result.get("status") == "cancelled":
            data["verified"] = True
            data["verification_status"] = "verified_success"
            message = f"Automation schedule is already cancelled: {describe_schedule(result)}"
        else:
            data["verified"] = True
            data["verification_status"] = "verified_success"
            if operation == "create":
                message = f"Automation schedule created and verified: {describe_schedule(result)}"
            elif operation == "update":
                message = f"Automation schedule updated and verified: {describe_schedule(result)}"
            elif operation == "cancel" and result.get("status") == "cancelled":
                message = f"Automation schedule cancelled and verified: {describe_schedule(result)}"
            else:
                message = f"Automation schedule state verified: {describe_schedule(result)}"
        return ToolExecutionResult(message, data, operation)
    except KeyError as exc:
        reason = f"Automation schedule not found: {exc.args[0]}"
        return ToolExecutionResult(
            f"Error: {reason}", {"ok": False, "failure_kind": "not_found", "reason": reason}, operation
        )
    except ValueError as exc:
        return ToolExecutionResult(
            f"Error: {exc}", {"ok": False, "failure_kind": "invalid_arguments", "reason": str(exc)}, operation
        )
    except RuntimeError as exc:
        return ToolExecutionResult(
            f"Error: {exc}", {"ok": False, "failure_kind": "unavailable", "reason": str(exc)}, operation
        )


@registry.register_tool(
    name="automation_create",
    description="Create a durable reminder or task schedule. Recurring times follow the IANA timezone's local clock.",
    schema={
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["reminder", "task"]},
            "text": {"type": "string"},
            "first_run_at": {"type": "string", "description": "Timezone-aware ISO-8601 timestamp."},
            "recurrence": {"type": "string", "enum": ["once", "daily", "weekly"]},
            "timezone": {"type": "string", "default": "Asia/Kolkata", "description": "IANA timezone."},
        },
        "required": ["kind", "text", "first_run_at", "recurrence"],
        "additionalProperties": False,
    },
)
def automation_create(
    kind: str,
    text: str,
    first_run_at: str,
    recurrence: str,
    timezone: str = "Asia/Kolkata",
) -> ToolExecutionResult:
    from charlie.calendar_runtime import calendar_runtime_required

    return _automation_tool_result(
        "create",
        lambda: calendar_runtime_required().execute_sync(
            "create_automation", kind, text, first_run_at, recurrence, timezone_name=timezone
        ),
    )


@registry.register_tool(
    name="automation_update",
    description="Update a durable reminder or task schedule without running it.",
    schema={
        "type": "object",
        "properties": {
            "schedule_id": {"type": "string"},
            "kind": {"type": "string", "enum": ["reminder", "task"]},
            "text": {"type": "string"},
            "first_run_at": {"type": "string", "description": "Timezone-aware ISO-8601 timestamp."},
            "recurrence": {"type": "string", "enum": ["once", "daily", "weekly"]},
            "timezone": {"type": "string", "description": "IANA timezone."},
        },
        "required": ["schedule_id"],
        "additionalProperties": False,
    },
)
def automation_update(
    schedule_id: str,
    kind: str | None = None,
    text: str | None = None,
    first_run_at: str | None = None,
    recurrence: str | None = None,
    timezone: str | None = None,
) -> ToolExecutionResult:
    from charlie.calendar_runtime import calendar_runtime_required

    values = {
        key: value
        for key, value in {
            "kind": kind,
            "text": text,
            "first_run_at": first_run_at,
            "recurrence": recurrence,
            "timezone": timezone,
        }.items()
        if value is not None
    }
    return _automation_tool_result(
        "update", lambda: calendar_runtime_required().execute_sync("update_automation", schedule_id, values)
    )


@registry.register_tool(
    name="automation_get",
    description="Read one durable reminder or task schedule.",
    schema={
        "type": "object",
        "properties": {"schedule_id": {"type": "string"}},
        "required": ["schedule_id"],
        "additionalProperties": False,
    },
)
def automation_get(schedule_id: str) -> ToolExecutionResult:
    from charlie.calendar_runtime import calendar_runtime_required

    return _automation_tool_result(
        "get", lambda: calendar_runtime_required().execute_sync("get_automation", schedule_id)
    )


@registry.register_tool(
    name="automation_list",
    description="List durable reminder and task schedules, including cancelled records.",
    schema={"type": "object", "properties": {}, "additionalProperties": False},
)
def automation_list() -> ToolExecutionResult:
    from charlie.calendar_runtime import calendar_runtime_required

    return _automation_tool_result(
        "list", lambda: {"automations": calendar_runtime_required().execute_sync("list_automations")}
    )


@registry.register_tool(
    name="automation_cancel",
    description="Cancel a durable reminder or task schedule.",
    schema={
        "type": "object",
        "properties": {"schedule_id": {"type": "string"}},
        "required": ["schedule_id"],
        "additionalProperties": False,
    },
)
def automation_cancel(schedule_id: str) -> ToolExecutionResult:
    from charlie.calendar_runtime import calendar_runtime_required

    return _automation_tool_result(
        "cancel", lambda: calendar_runtime_required().execute_sync("cancel_automation", schedule_id)
    )


# --- Browser tools (Playwright + Chrome) -- gated, off by default.

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
        "Do something inside a website in Charlie's controlled browser -- search, click through, play a "
        "video, fill a form -- and report back. Exposes the controlled browser when the "
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
    title = f"Title: {result['title']}\n" if result.get("title") else ""
    return f"{title}URL: {result['url']}\n\n{result['content']}"


@registry.register_tool(
    name="charlie_self_query",
    description=(
        "Ask a question about Charlie's own identity, configured models, live capabilities, "
        "codebase implementation, MCP servers, or runtime health."
    ),
    schema={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "Question about Charlie's runtime, capabilities, or codebase.",
            },
        },
        "required": ["question"],
    },
)
def charlie_self_query(question: str) -> str:
    from charlie.self_knowledge import SelfKnowledgeService

    service = _self_knowledge_service or SelfKnowledgeService()
    res = service.answer_self_question(question)
    return res.get("answer", "No self-knowledge answer available.")


@registry.register_tool(
    name="charlie_doctor_diagnose",
    description=(
        "Run structured Charlie Doctor diagnostics across all subsystems, verifying "
        "health, configuration, models, leases, and MCP."
    ),
    schema={
        "type": "object",
        "properties": {},
    },
)
def charlie_doctor_diagnose() -> str:
    from charlie.doctor import CharlieDoctor

    doctor = _charlie_doctor or CharlieDoctor()
    report = doctor.diagnose()
    return doctor.format_report(report)


@registry.register_tool(
    name="charlie_self_extension_propose",
    description=(
        "Propose a controlled self-extension (config update, reusable skill, MCP tool connection, "
        "or small Python code tool). The main runtime stages an instructions-only candidate from "
        "a durable owner correction; use this tool for an explicit request to create a skill or "
        "another self-extension. Do not stage one-off facts or sensitive content. Reusable skills "
        "stay inactive until the owner approves the exact content hash."
    ),
    schema={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "The exact extension or capability request",
            },
        },
        "required": ["prompt"],
    },
)
def charlie_self_extension_propose(prompt: str) -> str:
    if _self_extension_orchestrator is None:
        return json.dumps(
            {
                "success": False,
                "status": "failed",
                "message": "Self-extension runtime service is not initialized; no mutation was attempted.",
            }
        )
    req = _self_extension_orchestrator.plan_request(prompt, explicit_user_request=True)
    res = _self_extension_orchestrator.execute_transaction(req)
    return json.dumps(res.to_dict())


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
