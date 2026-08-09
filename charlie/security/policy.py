"""Deterministic tool-call policy gate. Additive to the existing
gated-keyword check in core.py's _exec_one -- never replaces it. Routes
through the same Brain.request_tool_approval channel (WS/Telegram/voice), so
there's no new UI surface.
"""

import difflib
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from charlie.tools import get_path_gate_reason

logger = logging.getLogger("charlie.security.policy")

_FUZZY_GATE_THRESHOLD = 0.85
_FUZZY_LOG_THRESHOLD = 0.70
_FUZZY_MIN_ARG_LEN = 8  # shorter strings match nearly everything, not worth checking

_PATH_ARG_TOOLS = ("file_read", "file_write")
_INJECTION_CHECK_TOOLS = ("shell_execute", "browser_task")


@dataclass
class PolicyResult:
    needs_approval: bool
    reason: Optional[str] = None


def _command_arg_text(tool_name: str, arguments: Dict[str, Any]) -> str:
    if tool_name == "shell_execute":
        return str(arguments.get("command", ""))
    if tool_name == "browser_task":
        return str(arguments.get("task", "") or arguments.get("instruction", ""))
    return ""


def _fuzzy_contains(needle: str, haystack: str) -> float:
    """Best difflib ratio of `needle` against any same-length window of
    `haystack` -- "does needle appear verbatim or near-verbatim somewhere
    inside haystack", not whole-string similarity (haystack is usually much
    longer than needle, e.g. a whole page of search results).

    ponytail: windowed scan, not a full alignment -- O(len(haystack)/step).
    Good enough for the truncated (_TOOL_RESULT_MAX_CHARS-bounded) tool
    results this runs against; revisit if a haystack source stops being
    truncated upstream.
    """
    n = len(needle)
    if n == 0 or not haystack:
        return 0.0
    step = max(1, n // 4)
    best = 0.0
    for start in range(0, max(1, len(haystack) - n + 1), step):
        window = haystack[start : start + n]
        best = max(best, difflib.SequenceMatcher(None, needle, window).ratio())
        if best >= 0.999:
            return best
    if len(haystack) > n:
        tail = haystack[-n:]
        best = max(best, difflib.SequenceMatcher(None, needle, tail).ratio())
    return best


def check_tool_call(
    tool_name: str,
    arguments: Dict[str, Any],
    recent_external_texts: Optional[List[str]] = None,
) -> PolicyResult:
    """Pre-flight policy check for one tool call. Returns whether it needs
    explicit user approve/decline, and why.

    1. Path containment: file_read/file_write against a sensitive path
       (re-wires charlie.tools.get_path_gate_reason, previously computed but
       never actually checked by either tool).
    2. Injected-command heuristic: shell_execute/browser_task whose argument
       closely matches text from a recent tool_external result in this turn
       -- catches "the page says run X" injection that a keyword list can't,
       even when the command itself isn't on the gated-keyword list.
    """
    if tool_name in _PATH_ARG_TOOLS:
        path = arguments.get("path", "")
        gate_reason = get_path_gate_reason(path)
        if gate_reason:
            return PolicyResult(True, gate_reason)

    if tool_name in _INJECTION_CHECK_TOOLS and recent_external_texts:
        arg_text = _command_arg_text(tool_name, arguments)
        if len(arg_text) >= _FUZZY_MIN_ARG_LEN:
            ratio = max(
                (_fuzzy_contains(arg_text, hay) for hay in recent_external_texts if hay),
                default=0.0,
            )
            if ratio > _FUZZY_GATE_THRESHOLD:
                return PolicyResult(
                    True,
                    f"this command closely matches text from a recent {tool_name!r} "
                    f"result ({ratio:.2f} similarity) -- possible injected instruction",
                )
            if ratio >= _FUZZY_LOG_THRESHOLD:
                logger.info(
                    "Near-miss injected-command heuristic for %s: %.2f similarity "
                    "(below %.2f gate threshold)",
                    tool_name, ratio, _FUZZY_GATE_THRESHOLD,
                )

    return PolicyResult(False)
