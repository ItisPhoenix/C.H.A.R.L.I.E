"""Charlie brain -- LLM orchestration, tool loop, streaming.

Single explicit backend (async httpx). No provider names in code.
Tiered prompt assembly for API prompt caching: Stable > Context > Volatile.
"""

import asyncio
import json
import logging
import os
import re
from typing import TYPE_CHECKING, Any, AsyncGenerator, Dict, List, Optional, Tuple
from uuid import uuid4

import httpx

from charlie import prompt_builder, router, telemetry
from charlie.budget import IterationBudget
from charlie.events import EventMeta, EventSource
from charlie.security import policy as security_policy
from charlie.security.provenance import trust_level_for_tool
from charlie.streaming import (
    FollowupStreamState,
    TextStreamFilter,
    collect_tool_calls,
    parse_sse_stream,
    stream_followup_content,
)
from charlie.tools import is_shell_command_gated, pop_pending_vision_image
from charlie.tools import registry as tool_registry
from charlie.utils import build_auth_headers, make_id

try:
    from charlie.desktop import DESKTOP_AVAILABLE as _DESKTOP_AVAILABLE
    from charlie.desktop import UIA_EXECUTOR as _UIA_EXECUTOR
    from charlie.desktop import actions as desktop_actions
    from charlie.desktop import session as desktop_session
    from charlie.desktop import uia as desktop_uia
except ImportError:  # pragma: no cover - guard mirrors charlie/desktop/__init__.py
    _DESKTOP_AVAILABLE = False
    _UIA_EXECUTOR = None
    desktop_actions = None
    desktop_session = None
    desktop_uia = None

logger = logging.getLogger("charlie.core")
if TYPE_CHECKING:
    from charlie.config import Config


async def _record_llm_response(response: httpx.Response) -> None:
    """httpx response event hook: records every LLM call's outcome for GET /api/health and /api/metrics."""
    telemetry.record_llm_call(success=response.status_code < 400)

# --- LLM tuning ---
_LLM_TEMPERATURE = 0.3
_TOOL_TIMEOUT_SEC = 15.0
_DESKTOP_CONTROL_TOOLS = frozenset({
    "desktop_click", "desktop_type", "desktop_invoke", "desktop_key",
    "desktop_click_at", "desktop_move", "desktop_drag", "desktop_scroll",
    "desktop_focus", "desktop_window", "desktop_move_window", "system_control",
})
# All tools that touch the UIA/comtypes COM apartment -- perception too, not
# just the gated effectors -- must run on the single dedicated COM thread.
_DESKTOP_COM_TOOLS = _DESKTOP_CONTROL_TOOLS | frozenset(
    {"desktop_observe", "desktop_read_screen", "desktop_screenshot"}
)
# Narrower sibling of router.SCREEN_QUERY_RE: phrasing that implies the user wants
# graphical/visual understanding (an icon, photo, game frame) that OCR/UIA
# marks can't describe. When this matches and a vision model is configured,
# desktop_screenshot is pre-called so the vision-routed follow-up (see
# _select_followup_route) has a real image queued -- see
# _should_queue_visual_screenshot below and its call site in chat_stream.
_VISUAL_CONTENT_QUERY_RE = re.compile(
    r"\bwhat am i looking at\b"
    r"|\bdescribe (this|the) (image|photo|picture|screen|window|page)\b"
    r"|\bwhat does this look like\b"
    r"|\bwho('?s| is) (this|that|he|she)\b"
    r"|\bwhat (do|can) you see\b"
    r"|\bwhat(?:'s| is) (wrong|going on) (with|on) (this|my|the)\b"
    r"|\bwhat(?:'s| is) (the |this )?(error|message|popup|dialog|problem|issue)\b"
    r"|\bwhat (is|does) (this|that) (error|message|popup|dialog|icon|button|image)\b"
    r"|\bwhat(?:'s| is) this\b"
    r"|\bhelp me (understand|fix) (this|what)\b",
    re.IGNORECASE,
)
_TOOL_TIMEOUTS = {
    "web_search": 15.0,
    "file_read": 10.0,
    "file_write": 10.0,
    "shell_execute": 30.0,
    "desktop_observe": 15.0,
    "desktop_click": 15.0,
    "desktop_type": 15.0,
    "desktop_invoke": 15.0,
    "desktop_key": 15.0,
    "desktop_click_at": 15.0,
    "desktop_move": 15.0,
    "desktop_drag": 15.0,
    "desktop_scroll": 15.0,
    "desktop_windows": 15.0,
    "desktop_focus": 15.0,
    "desktop_window": 15.0,
    "desktop_move_window": 15.0,
    "system_control": 15.0,
    # Recursive filesystem search (esp. with PLUGIN_ALLOW_DIRS="*", full-disk
    # access) needs far more than the 15s default -- scanning a whole drive
    # tree routinely takes longer than that.
    "plugin_fs_search": 120.0,
}
_TOOL_RESULT_MAX_CHARS = 2000
# Router classifier fallback (0.1): must fit well inside the ~1s time-to-first-audio budget.
_ROUTER_CLASSIFIER_TIMEOUT_S = 0.6
# How long a gated tool call waits for an approve/decline before it's treated
# as declined (matches charlie.recovery.request_recovery_approval's 30s, plus
# headroom for the voice fallback's speak-prompt-then-listen round trip).
_TOOL_APPROVAL_TIMEOUT_SEC = 45.0

# request_id -> Future[bool], resolved by main.py:consume_web_commands (web
# "tool_approve"/"tool_reject" commands) or by the voice yes/no fallback in
# main.py:_process. Mirrors charlie.recovery.pending_proposals.
pending_tool_approvals: Dict[str, "asyncio.Future[bool]"] = {}
# request_id of the tool approval currently waiting on a spoken yes/no, or
# None. Single-slot: the tool loop runs gated calls sequentially (see
# is_interactive handling below), so at most one voice approval is ever
# outstanding at a time.
_active_voice_approval_id: Optional[str] = None


def get_active_voice_approval() -> Optional[str]:
    """The request_id currently waiting on a spoken yes/no, or None.

    Checked by main.py's speech handler before routing a transcript to a
    normal chat turn -- if set, the transcript is parsed as an approval
    answer instead.
    """
    return _active_voice_approval_id


def resolve_tool_approval(request_id: str, approved: bool) -> bool:
    """Resolve a pending tool approval. Returns True if a matching pending
    request was found and resolved, False otherwise (already resolved,
    timed out, or unknown id).
    """
    global _active_voice_approval_id
    fut = pending_tool_approvals.get(request_id)
    if fut is None or fut.done():
        return False
    fut.set_result(approved)
    if _active_voice_approval_id == request_id:
        _active_voice_approval_id = None
    return True


# --- Time-sensitive query detection (deterministic pre-search) ---
_TIME_SENSITIVE_RE = re.compile(
    r"\b("
    r"latest|newest|recent|current|today|yesterday|this\s+(?:week|month|year)"
    r"|breaking|just\s+(?:happened|announced|released|launched)"
    r"|stock\s+price|share\s+price|market|trading"
    r"|weather|temperature|forecast"
    r"|cryptocurrency|bitcoin|ethereum"
    r")",
    re.IGNORECASE,
)


# --- Follow-up detection (skip web search for repeat/clarification requests) ---
_FOLLOWUP_RE = re.compile(
    r"^(?:"
    r"what|come again|repeat|say that again|pardon|sorry|excuse me|"
    r"what was that|what did you say|tell me again|once more|go on|"
    r"continue|and then|what else|what else did you say|anything else|"
    r"elaborate|more info|no[,.]?\s|that's\s+wrong|that's\s+not\s+right|actually|I\s+meant"
    r"|(?:tell me|explain|give me|show me)\s+(?:more\b\s*)?(?:details?\b|info\b)?"
    r"(?:\s*(?:about|on))?"
    r"(?:\s*(?:this|these|that|those|them|it|this\s+news|these\s+news|the\s+news))?"
    r"|(?:details?|more\s+details?|more\s+info)(?:\s*(?:on|about))?"
    r"(?:\s*(?:this|these|that|those|them|it|this\s+news|these\s+news|the\s+news))?"
    r")\s*[?.!]?\s*",
    re.IGNORECASE,
)
_FOLLOWUP_MAX_LEN = 40


# Strip vocatives like ", Charlie" from end before follow-up test
_VOCATIVE_RE = re.compile(r"[,?\s]+(?:hey\s+)?charlie\s*[?.!\s]*$", re.IGNORECASE)


def _strip_vocatives(query: str) -> str:
    """Remove trailing vocatives like ', Charlie' from the query."""
    return _VOCATIVE_RE.sub("", query).strip()

def _is_followup(query: str) -> bool:
    """Check if a query is a short follow-up/clarification that should not trigger web search."""
    q = _strip_vocatives(query)
    if len(q) > _FOLLOWUP_MAX_LEN:
        return False
    return bool(_FOLLOWUP_RE.match(q))


def _needs_web_search(query: str) -> bool:
    """Check if a query is time-sensitive and needs web search. Skips follow-up requests."""
    if _is_followup(query):
        return False
    return bool(_TIME_SENSITIVE_RE.search(query))


def _pre_search(query: str) -> str:
    """Run web_search for time-sensitive queries. Returns search results or empty string."""
    if not _needs_web_search(query):
        return ""
    try:
        result = tool_registry.execute_tool("web_search", {"query": query})
        if result and not result.startswith("Error") and len(result) > 50:
            logger.info("Pre-search completed for time-sensitive query: %s", query[:60])
            return result
        logger.debug("Pre-search returned no useful results for: %s", query[:60])
        return ""
    except Exception as e:
        logger.debug("Pre-search failed (non-fatal): %s", e)
        return ""


# --- Opinion teaching detection (deterministic, no LLM needed) ---
_CHARLIE_ADDR = r"(?:hey\s+charlie[,.!\s]*|ok\s+charlie[,.!\s]*|charlie[,.!\s]+)"
_OPINION_TEACH_RE = re.compile(
    rf"^{_CHARLIE_ADDR}?\s*"
    r"(?:you\s+(?:should|must|need\s+to)\s+"
    r"|you\s+(?:prefer|like|love|enjoy|favor)\s+"
    r"|you\s+(?:think|believe|feel)\s+.+(?:is|are)\s+better"
    r"|you(?:'re| are)\s+(?:a|an)\s+.+(?:person|fan|lover))",
    re.IGNORECASE,
)
_OPINION_EXTRACT_RE = re.compile(
    r"(?:you\s+(?:should|must|need\s+to)\s+)(like|prefer|love|enjoy|favor)\s+(.+)",
    re.IGNORECASE,
)
# Behavior-rule teaching ("always reply short on Telegram"), distinct from opinion phrasing above.
_STANDING_INSTRUCTION_RE = re.compile(
    rf"^{_CHARLIE_ADDR}?\s*(?:always|from\s+now\s+on|in\s+the\s+future|whenever\s+(?:i|you)\b)",
    re.IGNORECASE,
)


def _detect_standing_instruction(query: str) -> Optional[str]:
    """Detect a general behavior-rule teaching phrase. Returns the rule text or None."""
    if not _STANDING_INSTRUCTION_RE.search(query) or _OPINION_TEACH_RE.search(query):
        return None
    cleaned = re.sub(rf"^{_CHARLIE_ADDR}?\s*", "", query, flags=re.IGNORECASE).strip().rstrip(".")
    return cleaned or None


_REVIEW_RULES_RE = re.compile(
    r"what (?:have you|'ve you) learned about me"
    r"|what do you know about me"
    r"|show me what you'?ve learned"
    r"|list (?:your|the) (?:rules|things you'?ve learned)",
    re.IGNORECASE,
)
_FORGET_RULE_RE = re.compile(
    r"forget (?:that|what you learned about|the rule about)\s+(.+)", re.IGNORECASE
)


def _detect_review_rules(query: str) -> bool:
    return bool(_REVIEW_RULES_RE.search(query))


def _detect_forget_rule(query: str) -> Optional[str]:
    """Extracts the search text from a 'forget that/about X' command, or None."""
    m = _FORGET_RULE_RE.search(query.strip())
    return m.group(1).strip().rstrip(".") if m else None
# --- Correction detection (auto-learn from user corrections) ---
_CORRECTION_RE = re.compile(
    r"(?:"
    r"no[,.]?\s+(?:I\s+mean|I\s+meant|that's|it's|I\s+think)|"
    r"that's\s+(?:wrong|incorrect|not\s+right|not\s+what\s+I)|"
    r"^\s*actually[,.]|"
    r"not\s+(?:quite|exactly|really|that)|"
    r"I\s+(?:said|asked|meant)"
    r")",
    re.IGNORECASE,
)


def _detect_opinion_teaching(query: str) -> Optional[str]:
    """Detect if the user is teaching Charlie an opinion. Returns opinion text or None."""
    if not _OPINION_TEACH_RE.search(query):
        return None

    q_lower = query.lower().strip()

    # Extract the opinion content
    # Pattern: "you should like X" -> "I like X"
    # Pattern: "you prefer X over Y" -> "I prefer X over Y"
    # Pattern: "you think X is better than Y" -> "I think X is better than Y"

    # Try to extract the core opinion
    opinion = None

    # "you should like X" / "you prefer X" / "you like X"
    m = _OPINION_EXTRACT_RE.search(query)
    if m:
        verb = m.group(1)
        rest = m.group(2).strip().rstrip(".")
        opinion = f"I {verb} {rest}"
    else:
        # "you think X is better than Y"
        m = re.search(r"you\s+(?:think|believe|feel)\s+(.+)", q_lower)
        if m:
            opinion = f"I think {m.group(1).strip().rstrip('.')}"
        else:
            # Fallback: just use the user's phrase as-is
            opinion = query.strip().rstrip(".")
            # Normalize "you" to "I"
            opinion = re.sub(r"\byou\b", "I", opinion, count=1, flags=re.IGNORECASE)

    # Capitalize first letter
    if opinion:
        opinion = opinion[0].upper() + opinion[1:]

    return opinion


def _detect_correction(query: str) -> bool:
    """Detect if the user is correcting a previous response."""
    return bool(_CORRECTION_RE.search(query.strip()))


def _apply_correction_to_memory(
    query: str, assistant_response: str, opinions_path: str = "OPINIONS.md", world_model: Optional[Any] = None
) -> Optional[str]:
    """Write a correction entry to OPINIONS.md, plus a structural rules-table
    row when world_model is given -- a queryable, confidence-scored row
    beats an unstructured markdown line. Returns the entry or None.
    """
    if not _detect_correction(query):
        return None
    short_resp = assistant_response[:120].strip()
    if len(assistant_response) > 120:
        short_resp += "..."
    entry = f"Correction by user: {query.strip()}. Previous answer: '{short_resp}'."
    try:
        from pathlib import Path as _P
        p = _P(opinions_path)
        existing = p.read_text(encoding="utf-8") if p.exists() else ""
        if entry in existing:
            logger.debug("Correction already in opinions, skipping")
            return None
        with open(opinions_path, "a", encoding="utf-8") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write(f"{entry}\n")
        logger.info("Correction stored: %s", entry[:80])
        if world_model is not None:
            world_model.add_rule(f"Corrected: {query.strip()}", "correction")
        return entry
    except Exception as exc:
        logger.warning("Failed to store correction: %s", exc)
        return None




# --- Fast-path: close/open app (deterministic, no LLM needed) ---
_CLOSE_APP_RE = re.compile(
    r"^(?:hey\s+charlie,?|ok\s+charlie,?|charlie,?)?\s*"
    r"(?:close|kill|stop|exit|quit)\s+(.+?)\s*[.!?]?\s*$",
    re.IGNORECASE,
)

_OPEN_APP_RE = re.compile(
    r"^(?:hey\s+charlie,?|ok\s+charlie,?|charlie,?)?\s*"
    r"(?:open|start|launch|run)\s+(.+?)\s*[.!?]?\s*$",
    re.IGNORECASE,
)


def _is_low_confidence_desktop_call(tool_name: str, arguments: Dict[str, Any]) -> bool:
    """True for raw-coordinate clicks or OCR/vision-grounded (non-UIA-backed) marks."""
    if tool_name == "desktop_click_at":
        return True
    if tool_name in ("desktop_click", "desktop_type", "desktop_invoke") and desktop_uia is not None:
        mark_id = arguments.get("mark_id")
        if isinstance(mark_id, int):
            try:
                return desktop_uia.is_low_confidence_mark(mark_id)
            except Exception:
                return False
    return False


_DESKTOP_RESUME_RE = re.compile(r"^(continue|resume|keep going)\b", re.IGNORECASE)


def _detect_desktop_resume(text: str) -> bool:
    """True if `text` is a resume phrase.

    Matched only for dispatch -- the caller must also confirm desktop
    control is actually halted before treating this as the resume fast-path.
    """
    return bool(_DESKTOP_RESUME_RE.match(text.strip()))


def strip_internal_reasoning(text: str) -> str:
    """Remove model reasoning/thinking tags before user-facing output."""
    text = re.sub(
        r"<(thought|thinking|longcat_tool_call)>.*?</\1>",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return text.strip()


def _token_count(messages: List[Dict[str, Any]]) -> int:
    """Approximate token count of messages."""
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        total = 0
        for msg in messages:
            total += len(enc.encode(msg.get("content", "") or ""))
            total += 4
        return total
    except Exception:
        count = 0
        for msg in messages:
            text = msg.get("content", "") or ""
            count += len(text) // 4 + 1
        return count


def _sanitize_roles(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ensure messages alternate correctly.

    Collapses consecutive assistants and inserts stub assistants
    before tools.
    """
    cleaned = []
    for msg in messages:
        role = msg.get("role")
        if not cleaned:
            if role == "tool":
                logger.error("Role violation: first message is tool")
                raise ValueError("First message cannot be tool")
            cleaned.append(msg)
            continue

        last_role = cleaned[-1].get("role")

        if role == "assistant":
            if last_role == "assistant":
                # Collapse consecutive assistant messages
                prev = cleaned[-1]
                if msg.get("content") and prev.get("content"):
                    prev["content"] += msg["content"]
                elif msg.get("content") and not prev.get("content"):
                    prev["content"] = msg["content"]
                if msg.get("tool_calls") and prev.get("tool_calls"):
                    prev["tool_calls"].extend(msg["tool_calls"])
                elif msg.get("tool_calls") and not prev.get("tool_calls"):
                    prev["tool_calls"] = msg["tool_calls"]
                continue
            cleaned.append(msg)
            continue

        if role == "tool":
            if last_role != "assistant":
                cleaned.append({"role": "assistant", "content": None})
            cleaned.append(msg)
            continue

        if role == "user":
            if last_role == "user":
                cleaned[-1]["content"] = (cleaned[-1].get("content") or "") + (
                    msg.get("content") or ""
                )
                continue
            cleaned.append(msg)
            continue

        cleaned.append(msg)
    return cleaned


def _prune_old_tool_results(
    messages: List[Dict[str, Any]], keep_last: int = 2
) -> List[Dict[str, Any]]:
    """Keep only the last N tool-result turns. older ones are pruned."""
    prefix_end = 0
    for i, msg in enumerate(messages):
        if msg.get("role") == "user":
            prefix_end = i
            break
    prefix = messages[: prefix_end + 1]
    rest = messages[prefix_end + 1 :]

    turns = []
    current_turn = []
    for msg in rest:
        role = msg.get("role")
        if role in ("assistant", "user"):
            if current_turn:
                turns.append(current_turn)
            current_turn = [msg]
        elif role == "tool":
            current_turn.append(msg)
        else:
            if current_turn:
                turns.append(current_turn)
            current_turn = [msg]
    if current_turn:
        turns.append(current_turn)

    tool_turn_indices = [
        idx for idx, t in enumerate(turns) if any(m.get("role") == "tool" for m in t)
    ]
    kept_indices = (
        set(tool_turn_indices[-keep_last:])
        if len(tool_turn_indices) > keep_last
        else set(tool_turn_indices)
    )

    result = prefix[:]
    for idx, t in enumerate(turns):
        if any(m.get("role") == "tool" for m in t):
            if idx in kept_indices:
                result.extend(t)
        else:
            result.extend(t)
    return result


async def _halve_history(messages: List[Dict[str, Any]], config: Any) -> List[Dict[str, Any]]:
    """Summarize dropped history instead of hard-truncating.

    Keeps: first system msg + last N messages verbatim.
    Dropped middle -> single LLM-generated summary (capped at config limit).
    Falls back to a stub if the summary LLM call fails.
    """
    keep_recent = getattr(config, "history_keep_recent", 4)
    summary_max = getattr(config, "history_summary_max_chars", 400)

    system_msg = (
        messages[0] if messages and messages[0].get("role") == "system" else None
    )

    # Split: prefix (system), middle (dropped), tail (recent verbatim)
    if len(messages) <= keep_recent + (1 if system_msg else 0):
        return messages

    tail = messages[-keep_recent:]
    middle_start = 1 if system_msg else 0
    middle = messages[middle_start : len(messages) - keep_recent]

    if not middle:
        return messages

    # Build summary from dropped messages
    summary = await _generate_summary(middle, config, summary_max)

    result = []
    if system_msg:
        result.append(system_msg)
    result.append({"role": "system", "content": f"[Earlier conversation summary: {summary}]"})
    result.extend(tail)
    return result


async def _generate_summary(
    messages: List[Dict[str, Any]], config: Any, max_chars: int
) -> str:
    """Ask the LLM to summarize dropped messages. Returns stub on failure."""
    lines = []
    for m in messages:
        role = m.get("role", "?")
        content = (m.get("content") or "")[:200]
        lines.append(f"{role}: {content}")
    text_block = "\n".join(lines)

    prompt = (
        "Summarize this conversation excerpt in under "
        f"{max_chars} characters. "
        "Focus on key decisions, facts established, and current task state. "
        "Be specific with names, numbers, and conclusions. No preamble.\n\n"
        f"{text_block}"
    )

    try:
        import httpx

        url = getattr(config, "llm_url", "")
        key = getattr(config, "llm_key", "no-key")
        model = getattr(config, "llm_model", "")

        if not url:
            return f"{len(messages)} earlier messages omitted due to length."

        headers: Dict[str, str] = {"Content-Type": "application/json"}
        headers.update(build_auth_headers(key))

        payload = {
            "model": model,
            "messages": [
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": max_chars // 4,
            "stream": False,
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{url.rstrip('/')}/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return content[:max_chars]
    except Exception:
        logger.debug("History summary generation failed, using stub", exc_info=True)
        return f"{len(messages)} earlier messages omitted due to length."


async def _compress_messages(
    messages: List[Dict[str, Any]], config: "Config"
) -> List[Dict[str, Any]]:
    total = _token_count(messages)
    window = getattr(config, "context_window", 32000)
    compression_threshold = getattr(config, "compression_threshold", 0.8)
    threshold = int(compression_threshold * window)
    if total <= threshold:
        return messages

    pruned = _prune_old_tool_results(messages, keep_last=2)
    if _token_count(pruned) <= threshold:
        return pruned

    return await _halve_history(pruned, config)


async def _prep_messages(
    messages: List[Dict[str, Any]], config: "Config"
) -> List[Dict[str, Any]]:
    """Sanitize roles then compress to fit the context window."""
    return await _compress_messages(_sanitize_roles(messages), config)



# --- Verbosity preference detection ---
_VERBOSITY_SHORT_RE = re.compile(
    r"\b(?:too\s+long|shorter|be\s+brief|keep\s+it\s+short|just\s+the\s+answer|"
    r"too\s+verbose|more\s+concise|quick\s+answer|tldr|tl;dr)\b",
    re.IGNORECASE,
)
_VERBOSITY_LONG_RE = re.compile(
    r"\b(?:more\s+detail|elaborate|tell\s+me\s+more|go\s+deeper|in\s+depth|"
    r"full\s+explanation|explain\s+in\s+detail|longer\s+answer)\b",
    re.IGNORECASE,
)

_GOAL_RE = re.compile(
    r"^(?:hey\s+charlie,?|ok\s+charlie,?|charlie,?)?\s*"
    r"set\s+goal:\s*(.+)",
    re.IGNORECASE,
)


def _detect_verbosity_feedback(query: str) -> Optional[str]:
    """Detect explicit verbosity feedback. Returns 'short', 'long', or None."""
    if _VERBOSITY_SHORT_RE.search(query):
        return "short"
    if _VERBOSITY_LONG_RE.search(query):
        return "long"
    return None


def _detect_set_goal(query: str) -> Optional[str]:
    """Detect 'set goal: X' command. Returns goal text or None."""
    m = _GOAL_RE.match(query.strip())
    return m.group(1).strip().rstrip(".") if m else None


# --- Helm operator persona (desktop-control identity) ---
_HELM_ADDRESS_RE = re.compile(r"^\s*helm\b[,:]?\s*", re.IGNORECASE)
_HELM_ACTION_RE = re.compile(
    r"\b(click|double.?click|drag(?!\s+(queen|racing|race|on\b))|scroll|type in(to)?|on (the |my )?screen)\b",
    re.IGNORECASE,
)


def _detect_operator_persona(query: str) -> bool:
    """True if the user addressed Helm by name, or the query implies
    direct desktop-action intent (click/drag/scroll/type on screen)."""
    stripped = query.strip()
    return bool(_HELM_ADDRESS_RE.match(stripped)) or bool(_HELM_ACTION_RE.search(stripped))


_UNINFORMATIVE_PATTERNS = re.compile(
    r"^(?:Error|No results found|<html|404|500|empty|None|N/A)",
    re.IGNORECASE,
)
_TOOL_RESULT_MIN_CHARS = 50


def _assess_tool_result_relevance(tool_name: str, tool_result: str) -> bool:
    """Heuristic: is this tool result useful? Returns True if relevant."""
    if not tool_result or len(tool_result.strip()) < _TOOL_RESULT_MIN_CHARS:
        return False
    if _UNINFORMATIVE_PATTERNS.match(tool_result.strip()):
        return False
    return True


def _should_queue_visual_screenshot(user_input: str, config: "Config") -> bool:
    """True if this turn should pre-call desktop_screenshot to queue a vision
    image for the follow-up (see _VISUAL_CONTENT_QUERY_RE). Also fires for the
    broader router.SCREEN_QUERY_RE phrasing ("what's on my screen") -- when a vision
    model is configured, a real fresh screenshot beats the UIA/OCR text summary
    injected below, which was the only signal these queries got before. Requires
    both a configured vision model and desktop control -- otherwise a no-op."""
    return bool(
        (_VISUAL_CONTENT_QUERY_RE.search(user_input) or router.SCREEN_QUERY_RE.search(user_input))
        and config.vision_enabled
        and config.desktop_control_enabled
    )




def _with_vision_image(messages: List[Dict[str, Any]], image_url: str) -> List[Dict[str, Any]]:
    """Return a copy of `messages` with the last user message's content turned
    multimodal, for this one outgoing payload only. Never mutates `messages`
    or its dicts in place -- history persistence (string-only) stays untouched."""
    last_user_idx = next(
        (i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "user"),
        None,
    )
    if last_user_idx is None:
        return messages
    out = list(messages)
    original = out[last_user_idx]
    out[last_user_idx] = {
        **original,
        "content": [
            {"type": "text", "text": original.get("content", "")},
            {"type": "image_url", "image_url": {"url": image_url}},
        ],
    }
    return out


def _payload_is_vision(payload: Dict[str, Any]) -> bool:
    """True if _build_payload injected an image block into this payload."""
    return any(isinstance(m.get("content"), list) for m in payload.get("messages", []))


# =====================================================================
# Brain
# =====================================================================


class Brain:
    """Minimal voice-first brain: single explicit backend."""

    def __init__(
        self,
        config: "Config",
        on_thought_callback: Optional[callable] = None,
        session_store=None,
        memory_store=None,
        on_tool_call: Optional[callable] = None,
        on_tool_result: Optional[callable] = None,
        on_thinking_update: Optional[callable] = None,
        register_panic_hotkey: bool = True,
        approval_timeout: Optional[float] = _TOOL_APPROVAL_TIMEOUT_SEC,
        is_background: bool = False,
    ):
        self.config = config
        self.on_thought_callback = on_thought_callback
        self._is_background = is_background
        self.session_store = session_store
        self.memory_store = memory_store
        self.on_tool_call = on_tool_call
        self.on_tool_result = on_tool_result
        self.on_thinking_update = on_thinking_update
        self._approval_timeout = approval_timeout
        llm_headers: Dict[str, str] = build_auth_headers(config.llm_key)
        self.client = httpx.AsyncClient(
            base_url=config.llm_url,
            headers=llm_headers,
            timeout=60.0,
            event_hooks={"response": [_record_llm_response]},
        )
        self._chat_generation = 0
        # Per-turn halt; module-global _HALT is reserved for the physical panic hotkey.
        self._turn_halted: bool = False
        # Per-instance, not the shared tools.py global -- see _exec_one's immediate pop.
        self._pending_vision_image_url: Optional[str] = None
        self._panic_hotkey_listener = None
        if register_panic_hotkey and self.config.desktop_control_enabled and _DESKTOP_AVAILABLE:
            try:
                from pynput import keyboard as _pynput_keyboard
                hotkey_str = "+".join(
                    f"<{p}>" if p in ("ctrl", "alt", "shift", "cmd") else p
                    for p in self.config.desktop_panic_hotkey.lower().split("+")
                )
                self._panic_hotkey_listener = _pynput_keyboard.GlobalHotKeys(
                    {hotkey_str: self._panic}
                )
                self._panic_hotkey_listener.start()
                logger.info("Desktop panic hotkey armed: %s", self.config.desktop_panic_hotkey)
            except Exception:
                logger.warning("Failed to start desktop panic hotkey listener", exc_info=True)
        self._tool_locks: Dict[str, asyncio.Lock] = {}
        self.history: List[Dict[str, Any]] = []
        self._history_max_turns = 5
        self._turns_since_nudge: int = 0
        self._active_goal: Optional[str] = None
        self._goal_turns_remaining: int = 0
        self._reflect_turn_counter: int = 0
        self._reflect_interval: int = 5  # reflect every N turns

        # --- Hybrid tool calling: detect native support ---
        # Auto-detect local model servers -- they ignore the native tools payload
        _url = config.llm_url.lower()
        _is_local = any(h in _url for h in ("127.0.0.1", "localhost"))
        if _is_local:
            self._use_native_tools = False
            logger.info("Local model detected - using text-based tool calling")
        else:
            self._use_native_tools: bool = getattr(config, "native_tool_calling", True)

        # --- Frozen tiers (cached once at init for prompt cache stability) ---
        soul_text = config.soul or "You are Charlie. Be concise and warm."
        self._stable_tier: str = prompt_builder.build_stable_tier(
            soul_text, prompt_builder.build_capabilities_block(config), self._use_native_tools
        )

        # --- Frozen context tier (read once, reloaded only on explicit request) ---
        # Populated by add_installed_skill_block() when the web dashboard's
        # Extensions flow installs a "skill" kind extension -- that flow lives
        # entirely in the web-server subprocess, so main.py mirrors installs
        # here over the EventBus (see main.py's "extension_installed" command).
        self._installed_skill_blocks: Dict[str, str] = {}
        max_chars = config.prompt_memory_max // 2
        memory_content = self._read_file_safe(config.memory_file, max_chars)
        user_content = self._read_file_safe(config.user_file, max_chars)
        opinions_content = self._read_file_safe(config.opinions_file, max_chars)
        self._context_tier: str = prompt_builder.build_context_tier(
            memory_content, user_content, opinions_content, self._installed_skill_blocks
        )

        # --- Vision LLM client (separate, opt-in endpoint for desktop_screenshot) ---
        self._vision_client = None
        if (
            config.vision_enabled
            and config.vision_llm_url
            and config.vision_llm_key
            and config.vision_llm_key not in ("no-key", "no_key")
        ):
            self._vision_client = httpx.AsyncClient(
                base_url=config.vision_llm_url,
                headers=build_auth_headers(config.vision_llm_key),
                timeout=60.0,
            )
            self._vision_model = config.vision_llm_model
            logger.info("Vision LLM configured: %s", config.vision_llm_url)

        # --- Knowledge graph memory ---
        from charlie.memory_graph import MemoryGraph
        self.memory_graph = MemoryGraph(db_path=config.memory_graph_db)

        # --- World model: open threads + machine events (Phase 1a) ---
        from charlie.world_model import WorldModel
        self.world_model = WorldModel(db_path=config.world_model_db_path)

    @staticmethod
    def _read_file_safe(path: str, max_chars: int) -> str:
        """Read a file, creating it if missing. Returns truncated content."""
        from pathlib import Path

        try:
            p = Path(path)
            if not p.exists():
                p.write_text("", encoding="utf-8")
            return p.read_text(encoding="utf-8")[:max_chars]
        except Exception as e:
            logger.warning("Error reading %s: %s", path, e)
            return ""

    def reload_context(self) -> None:
        """Re-read memory/user/opinions files into the context tier. Call after writes."""
        max_chars = self.config.prompt_memory_max // 2
        memory_content = self._read_file_safe(self.config.memory_file, max_chars)
        user_content = self._read_file_safe(self.config.user_file, max_chars)
        opinions_content = self._read_file_safe(self.config.opinions_file, max_chars)
        self._context_tier = prompt_builder.build_context_tier(
            memory_content, user_content, opinions_content, self._installed_skill_blocks
        )

    def rebuild_stable_tier(self) -> None:
        """Rebuild the stable tier after a live config change (e.g. the
        dashboard's system_restart reload flow) so capability claims reflect
        the new config instead of what was true at process start."""
        soul_text = self.config.soul or "You are Charlie. Be concise and warm."
        self._stable_tier = prompt_builder.build_stable_tier(
            soul_text, prompt_builder.build_capabilities_block(self.config), self._use_native_tools
        )

    def add_installed_skill_block(self, name: str, block: str) -> None:
        """Add a runtime-installed SKILL.md's instructions to the context
        tier and rebuild it immediately. Called from main.py when the web
        dashboard mirrors an "extension_installed" (kind="skill") command
        over the EventBus -- ExtensionManager itself only lives in the web
        server's process, so this is how its instructions ever reach the
        actual chat Brain."""
        self._installed_skill_blocks[name] = block
        self.reload_context()

    def remove_installed_skill_block(self, name: str) -> None:
        """Drop a previously-installed skill's context block (mirrors
        /api/extensions/{name} DELETE) and rebuild the context tier."""
        if self._installed_skill_blocks.pop(name, None) is not None:
            self.reload_context()

    async def _check_memory_capacity(self) -> None:
        """Review memory files and consolidate when near capacity."""
        self._turns_since_nudge += 1
        nudge_interval = getattr(self.config, "memory_nudge_interval", 5)
        if self._turns_since_nudge < nudge_interval:
            return
        self._turns_since_nudge = 0

        # Run consolidation in background to prevent blocking the user response
        asyncio.create_task(self._background_check_and_consolidate())

    async def _background_check_and_consolidate(self) -> None:
        """Helper to run check and consolidation in the background."""
        # Concurrency guard
        if getattr(self, "_is_consolidating", False):
            logger.debug("Memory consolidation already in progress, skipping")
            return
        self._is_consolidating = True
        try:
            threshold = getattr(self.config, "memory_capacity_threshold", 0.8)
            files = {
                "memory": (self.config.memory_file, 2200),
                "user": (self.config.user_file, 1375),
                "opinions": (self.config.opinions_file, 800),
            }
            needs_review = False
            for target, (path_val, max_chars) in files.items():
                if not os.path.exists(path_val):
                    continue
                content = self._read_file_safe(path_val, max_chars)
                from charlie.tools import _parse_memory_entries
                entries = _parse_memory_entries(content)
                current_len = sum(len(e) for e in entries) + (len(entries) - 1 if entries else 0)
                if current_len / max_chars >= threshold:
                    needs_review = True
                    break

            if needs_review:
                logger.info("Memory near capacity, consolidating in background...")
                await self._consolidate_memory()
                self.reload_context()
                logger.info("Memory consolidated and context reloaded in background")
        except Exception as exc:
            logger.warning("Background memory consolidation failed: %s", exc)
        finally:
            self._is_consolidating = False

    async def _consolidate_memory(self) -> None:
        """Send memory files to LLM for consolidation when near capacity."""
        from charlie.tools import _MEMORY_SEP, _parse_memory_entries

        files = {
            "memory": (self.config.memory_file, 2200),
            "user": (self.config.user_file, 1375),
            "opinions": (self.config.opinions_file, 800),
        }
        for target, (path_val, max_chars) in files.items():
            if not os.path.exists(path_val):
                continue
            content = self._read_file_safe(path_val, max_chars)
            entries = _parse_memory_entries(content)
            current_len = sum(len(e) for e in entries) + (len(entries) - 1 if entries else 0)
            if current_len / max_chars < 0.8:
                continue
            # Skip if no LLM URL configured for consolidation
            if not self.config.llm_url:
                logger.debug("Skipping consolidation: no LLM URL configured")
                continue

            prompt = (
                f"You are a memory consolidation engine. "
                f"Below are {len(entries)} memory entries (delimited by section sign). "
                f"Current size: {current_len}/{max_chars} chars.\n\n"
                f"Rules:\n"
                f"- Merge entries that say the same thing with different wording\n"
                f"- Drop entries that are clearly outdated or contradicted by newer entries\n"
                f"- Keep the most specific and actionable version\n"
                f"- Preserve all user-expressed preferences and corrections\n"
                f"- Return ONLY the consolidated entries, each separated by section sign\n"
                f"- Do NOT add explanations or commentary\n\n"
                f"Entries:\n{_MEMORY_SEP.join(entries)}"
            )
            try:
                import httpx as _httpx
                llm_headers = build_auth_headers(self.config.llm_key)
                payload = {
                    "model": self.config.llm_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": max_chars,
                }
                async with _httpx.AsyncClient(
                    base_url=self.config.llm_url,
                    headers=llm_headers,
                    timeout=90.0,
                ) as client:
                    resp = await client.post("chat/completions", json=payload)
                    if resp.status_code != 200:
                        logger.error("Consolidation API failed status %d: %s", resp.status_code, resp.text)
                    resp.raise_for_status()
                    result = resp.json()["choices"][0]["message"]["content"]
                with open(path_val, "w", encoding="utf-8") as f:
                    f.write(result)
                logger.info("Consolidated %s: %d -> %d chars", target, current_len, len(result))
            except Exception as exc:
                logger.warning("Failed to consolidate %s: %s", target, exc)

    def cancel_chat(self) -> None:
        """Cancel the current chat generation (barge-in support)."""
        self._chat_generation += 1

    def _panic(self) -> None:
        """Global panic hotkey handler: halt desktop motion and cancel the turn."""
        if desktop_actions is not None:
            desktop_actions.halt()
        self.cancel_chat()
        logger.warning("Desktop panic hotkey triggered -- halting desktop control and cancelling chat.")

    def _is_desktop_halted(self) -> bool:
        """True if the physical panic hotkey or this instance's own turn-halt tripped."""
        return (desktop_actions is not None and desktop_actions.is_halted()) or self._turn_halted

    async def _handle_propose_new_tool(self, arguments: Dict[str, Any]) -> str:
        """Tier-3 self-extension: validate the authored code, then queue it on
        the dashboard's pending-extensions state -- never runs it, never waits
        for approval here (that's "queues by voice, approves by screen": the
        actual install only happens via the existing /api/extensions/confirm
        flow once a human has read the code).
        """
        from charlie import recovery
        from charlie.extensions import build_skill_card
        from charlie.extensions.generated import parse_generated_tool

        name = arguments.get("name", "")
        description = arguments.get("description", "")
        code = arguments.get("code", "")
        try:
            parse_generated_tool(name, code)
        except (ValueError, SyntaxError) as exc:
            return f"Error: generated tool code is invalid, fix and retry: {exc}"

        card = build_skill_card(name, "chat", [name], code)
        try:
            if recovery._event_bus:
                await recovery._event_bus.emit(
                    "extension_proposed",
                    {"kind": "generated", "name": name, "source": "chat", "raw_text": code,
                     "description": description, "declared_tools": [name], "warnings": card.warnings},
                    meta=EventMeta(
                        source=EventSource.BRAIN,
                        rationale=f"drafted a new tool '{name}' from a chat request, pending review",
                    ),
                )
        except Exception:
            logger.warning("Failed to broadcast extension_proposed event", exc_info=True)
        return f"Drafted a new tool called '{name}' and sent it for your review on the dashboard."

    async def request_tool_approval(self, tool_name: str, arguments: Dict[str, Any], reason: str) -> bool:
        """Ask the user to approve/decline a gated tool call and wait for the
        answer. Web dashboard is primary: broadcasts a "tool_approval_request"
        event and waits for a "tool_approve"/"tool_reject" WS command. If no
        dashboard is connected, falls back to voice: speaks the prompt via
        `on_thought_callback` and waits for main.py's speech handler to route
        the next transcript here as a yes/no (see get_active_voice_approval).
        Times out to declined (safe default) after self._approval_timeout seconds
        (matching charlie.recovery.request_recovery_approval's fail-safe stance),
        or parks indefinitely if approval_timeout=None (background tasks). Background
        Brains omit session_id from the broadcast -- the dashboard filters
        tool_approval_request by "is this the session I'm currently viewing,"
        and a background task has no chat session tab open at all, so tagging
        it with the foreground's active session would get it silently dropped.
        """
        global _active_voice_approval_id
        from charlie import recovery

        request_id = f"tool_{make_id(6)}"
        prompt = f"Need your OK: {reason}. Yes or no?"

        loop = asyncio.get_running_loop()
        fut: "asyncio.Future[bool]" = loop.create_future()
        pending_tool_approvals[request_id] = fut

        try:
            if recovery.get_active_ws_count() > 0 and recovery._event_bus:
                await recovery._event_bus.emit(
                    "tool_approval_request",
                    {
                        "request_id": request_id,
                        "tool_name": tool_name,
                        "arguments": arguments,
                        "reason": reason,
                        "session_id": None if self._is_background else recovery.get_active_session_id(),
                    },
                    meta=EventMeta(source=EventSource.BRAIN, rationale=reason),
                )
            elif self.on_thought_callback:
                _active_voice_approval_id = request_id
                self.on_thought_callback(prompt)
            else:
                logger.warning("Gated tool call with no approval channel available -- declining safely.")
                return False

            try:
                return await asyncio.wait_for(fut, timeout=self._approval_timeout)
            except asyncio.TimeoutError:
                logger.warning("Tool approval %s timed out, declining", request_id)
                return False
        finally:
            pending_tool_approvals.pop(request_id, None)
            if _active_voice_approval_id == request_id:
                _active_voice_approval_id = None

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()
        if self._vision_client:
            await self._vision_client.aclose()
        if self._panic_hotkey_listener is not None:
            self._panic_hotkey_listener.stop()

    async def _stream_completion(
        self,
        payload: Dict[str, Any],
        generation: int,
    ) -> tuple:
        """Stream a chat completion. Returns (accumulated_text, tool_calls_list)."""
        async with self.client.stream(
            "POST", "chat/completions", json=payload
        ) as response:
            response.raise_for_status()
            accumulated, tc_by_index, cancelled = await parse_sse_stream(
                response, generation, lambda: self._chat_generation
            )
            if cancelled:
                logger.info("Chat generation cancelled (barge-in)")
                return ("", [])
            tool_calls = collect_tool_calls(tc_by_index)
            return (accumulated, tool_calls)

    def _build_payload(
        self,
        messages: List[Dict[str, Any]],
        skip_tools: bool = False,
    ) -> Dict[str, Any]:
        """Build the API payload for chat completions."""
        payload: Dict[str, Any] = {
            "model": self.config.llm_model,
            "messages": messages,
            "temperature": _LLM_TEMPERATURE,
            "stream": True,
        }
        if self._use_native_tools and not skip_tools:
            payload["tools"] = tool_registry.get_tool_definitions()
            payload["tool_choice"] = "auto"
        if getattr(self.config, "llm_disable_reasoning", False):
            payload["reasoning"] = {"effort": "none"}
        if self.config.vision_enabled and self._use_native_tools:
            image_url, self._pending_vision_image_url = self._pending_vision_image_url, None
            if image_url:
                payload["messages"] = _with_vision_image(messages, image_url)
        return payload

    def _select_followup_route(
        self, payload: Dict[str, Any]
    ) -> Tuple[httpx.AsyncClient, str, bool]:
        """Pick which endpoint serves a follow-up completion: vision (if this
        payload carries an image block from desktop_screenshot), else small.
        Returns (client, model, is_vision)."""
        if self._vision_client is not None and _payload_is_vision(payload):
            return self._vision_client, self._vision_model, True
        return self.client, self.config.llm_model, False

    async def _stream_followup_once(
        self,
        client: httpx.AsyncClient,
        model: str,
        payload: Dict[str, Any],
        generation: int,
        state: FollowupStreamState,
    ) -> AsyncGenerator[str, None]:
        """Run one tool-followup completion attempt against `client`.

        Yields filtered content chunks live (low Time-To-First-Audio); writes
        accumulated text / tool-call deltas / cancellation onto `state` since
        an async generator can't return extra values through `async for`.
        Raises on HTTP/connection errors so the caller can retry against the
        fallback client -- this is the one piece shared by all three
        follow-up attempts (primary, on-error fallback, empty-response retry).
        """
        payload = dict(payload, model=model)
        stream_filter = TextStreamFilter()
        async with client.stream("POST", "chat/completions", json=payload) as response:
            response.raise_for_status()
            async for content in stream_followup_content(
                response, generation, lambda: self._chat_generation, state
            ):
                filtered = stream_filter.push(content)
                if filtered:
                    yield filtered
        if not state.cancelled:
            filtered = stream_filter.flush()
            if filtered:
                yield filtered

    async def _classify_router_intent(self, user_input: str) -> Optional[router.RouteMatch]:
        """One bounded LLM call for short phrasings that miss every router.py regex.
        Fails silently (returns None) on timeout, bad JSON, or intent 'none' -- the turn just
        falls through to normal tool-calling, never blocked by this.
        """
        if not self.config.llm_url:
            return None
        apps = router.known_app_names()
        prompt = (
            "Classify this command into exactly one intent. Reply with ONLY compact JSON: "
            '{"intent": "open_app"|"close_app"|"time_date"|"background_task_status"|"none", '
            '"app": "<known app name or empty>"}.\n'
            f"Known apps: {', '.join(apps)}.\n"
            f'Command: "{user_input}"'
        )
        try:
            headers = build_auth_headers(self.config.llm_key)
            payload = {
                "model": self.config.llm_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
                "max_tokens": 40,
            }
            async with httpx.AsyncClient(base_url=self.config.llm_url, headers=headers) as client:
                resp = await asyncio.wait_for(
                    client.post("chat/completions", json=payload), timeout=_ROUTER_CLASSIFIER_TIMEOUT_S
                )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            data = json.loads(content)
        except Exception as exc:
            logger.info("Router classifier skipped: %s", exc)
            return None

        intent = data.get("intent")
        app = str(data.get("app") or "").strip().lower()

        if intent == "time_date":
            answer = router.answer_time_date(user_input)
            return router.RouteMatch("time_date", {"answer": answer}) if answer else None
        if intent == "background_task_status":
            answer = router.current_task_status_text()
            return router.RouteMatch("background_task_status", {"answer": answer}) if answer else None
        if intent in ("open_app", "close_app") and app in apps:
            return router.RouteMatch(intent, {"app": app})
        return None

    async def chat_stream(
        self,
        user_input: str,
        platform: str = "voice",
        skip_pre_search: bool = False,
        session_id: str = "default",
        skip_tools: bool = False,
    ) -> AsyncGenerator[str, None]:
        from datetime import datetime

        # Load session-specific history from SQLite store at the start of the turn
        if self.session_store:
            try:
                raw_messages = self.session_store.get_session_messages(session_id, limit=self._history_max_turns)
                self.history = []
                for role, content in raw_messages:
                    self.history.append({"role": role, "content": content})
                logger.debug("Loaded %d history messages for session: %s", len(self.history), session_id)
            except Exception as e:
                logger.warning("Failed to load session history for %s: %s", session_id, e)
        # --- Auto-learn: detect corrections and store in opinions memory ---
        if _detect_correction(user_input) and self.history:
            last_assistant = ""
            for msg in reversed(self.history):
                if msg.get("role") == "assistant":
                    last_assistant = msg.get("content", "")
                    break
            if last_assistant:
                asyncio.get_event_loop().run_in_executor(
                    None,
                    _apply_correction_to_memory,
                    user_input,
                    last_assistant,
                    self.config.opinions_file,
                    self.world_model,
                )



        generation = self._chat_generation
        turn_id = str(uuid4())
        # Preserved for history/memory even if a fast-path below rebinds user_input
        # to a compound instruction's leftover text (see the open-app fast-path).
        original_user_input = user_input
        fast = router.answer_time_date(user_input)
        if fast is not None:
            logger.info("Fast-path time/date: %s -> %s", user_input, fast)
            yield fast
            return
        # --- Fast-path: opinion teaching (deterministic, no LLM needed) ---
        opinion = _detect_opinion_teaching(user_input)
        if opinion is not None:
            logger.info("Opinion teaching detected: %s -> %s", user_input, opinion)
            try:
                result = await asyncio.to_thread(
                    tool_registry.execute_tool,
                    "memory",
                    {
                        "action": "add",
                        "target": "opinions",
                        "content": opinion,
                    },
                )
                logger.info("Opinion stored: %s", result)
                yield "Got it, I'll remember that."
            except Exception as e:
                logger.error("Failed to store opinion: %s", e, exc_info=True)
                yield "I tried to remember that, but something went wrong."
            return
        # --- Fast-path: standing instruction (behavior rule, no LLM needed) ---
        instruction = _detect_standing_instruction(user_input)
        if instruction is not None:
            self.world_model.add_rule(instruction, "teaching")
            logger.info("Standing instruction learned: %s", instruction)
            yield "Got it, I'll remember that."
            return
        # --- Fast-path: review learned rules (deterministic, no LLM needed) ---
        if _detect_review_rules(user_input):
            rules = self.world_model.list_rules(include_decayed=True)
            if not rules:
                yield "I haven't learned anything from you yet."
            else:
                lines = [f"{text} ({status}, {source})" for _id, text, _conf, source, status in rules[:10]]
                yield "Here's what I've learned: " + "; ".join(lines) + "."
            return
        # --- Fast-path: forget a learned rule (deterministic, no LLM needed) ---
        forget_text = _detect_forget_rule(user_input)
        if forget_text is not None:
            matches = self.world_model.find_rules_matching(forget_text)
            for rule_id, _text in matches:
                self.world_model.delete_rule(rule_id)
            if matches:
                logger.info("Forgot %d rule(s) matching '%s'", len(matches), forget_text)
                yield f"Forgot {len(matches)} thing{'s' if len(matches) != 1 else ''} about that."
            else:
                yield "I couldn't find anything matching that to forget."
            return
        # --- Fast-path: set goal (deterministic, no LLM needed) ---
        goal_text = _detect_set_goal(user_input)
        if goal_text is not None:
            self._active_goal = goal_text
            self._goal_turns_remaining = 5
            logger.info("Goal set: %s", goal_text)
            yield f"Got it, I'll focus on: {goal_text}."
            return

        # --- Verbosity preference update ---
        verbosity = _detect_verbosity_feedback(user_input)
        if verbosity is not None:
            try:
                from pathlib import Path as _VP
                up = _VP(self.config.user_file)
                existing = up.read_text(encoding="utf-8") if up.exists() else ""
                # Replace or append verbosity line
                new_lines = []
                found = False
                for line in existing.splitlines():
                    if line.strip().startswith("verbosity:"):
                        new_lines.append(f"verbosity: {verbosity}")
                        found = True
                    else:
                        new_lines.append(line)
                if not found:
                    new_lines.append(f"verbosity: {verbosity}")
                up.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                self.reload_context()
                logger.info("Verbosity preference set to: %s", verbosity)
            except Exception as ve:
                logger.warning("Failed to update verbosity: %s", ve)


        # --- Fast-path: resume desktop control after the panic hotkey (deterministic, no LLM needed) ---
        if desktop_actions is not None and desktop_actions.is_halted() and _detect_desktop_resume(user_input):
            desktop_actions.clear_halt()
            logger.info("Desktop control resumed by user command: %s", user_input)
            yield "Desktop control resumed."
            return

        # --- Fast-path: close app (matcher pure, taskkill runs only after a confirmed match) ---
        close_match = await asyncio.to_thread(router.match_close_app, user_input)
        if close_match is not None:
            close_res = await asyncio.to_thread(router.execute_close_app, close_match[0], close_match[1])
            logger.info("Fast-path close app result: %s -> %s", user_input, close_res)
            self.world_model.record_event("app_close", close_res)
            yield close_res
            return

        # --- Fast-path: open app (matcher pure, launch/focus runs only after a confirmed match) ---
        open_match = await asyncio.to_thread(router.match_open_app, user_input)
        if open_match is not None:
            open_apps, open_commands, open_remaining = open_match
            open_msg = await asyncio.to_thread(router.execute_open_app, open_apps, open_commands)
            self.world_model.record_event("app_open", open_msg)
            if open_remaining is None:
                logger.info("Fast-path open app result: %s -> %s", user_input, open_msg)
                yield open_msg
                return
            # Compound instruction: apps are already open, stream confirmation and keep going with the leftover text.
            logger.info(
                "Fast-path partial open: %s -> opened=%s, continuing with: %s",
                user_input, open_msg, open_remaining,
            )
            yield open_msg + " "
            user_input = open_remaining

        # --- Fast-path: live background-task progress query (deterministic, no LLM needed) ---
        task_status_res = router.answer_background_task_status(user_input)
        if task_status_res is not None:
            logger.info("Fast-path background-task status: %s -> %s", user_input, task_status_res)
            yield task_status_res
            return

        # --- Fast-path fallback: cheap classifier for short phrasings the table above missed ---
        if self.config.router_classifier_enabled and router.is_router_classifier_candidate(user_input):
            classifier_match = await self._classify_router_intent(user_input)
            if classifier_match is not None:
                logger.info("Fast-path (classifier): %s -> %s", user_input, classifier_match.name)
                if classifier_match.name in ("time_date", "background_task_status"):
                    yield classifier_match.args["answer"]
                    return
                if classifier_match.name == "open_app":
                    app = classifier_match.args["app"]
                    msg = await asyncio.to_thread(router.execute_open_app, [app], [router.open_command_for(app)])
                    self.world_model.record_event("app_open", msg)
                    yield msg
                    return
                if classifier_match.name == "close_app":
                    app = classifier_match.args["app"]
                    msg = await asyncio.to_thread(router.execute_close_app, [app], [router.close_process_for(app)])
                    self.world_model.record_event("app_close", msg)
                    yield msg
                    return

        search_results = (
            "" if skip_pre_search else await asyncio.to_thread(_pre_search, user_input)
        )

        # --- Force a fresh screen observation for screen-content questions ---
        # Injected the same way as web search results (below) so the model is
        # told, in its own prompt, not to answer from training data / memory --
        # relying on the model to decide to call desktop_observe itself isn't
        # reliable enough: it has repeated a stale answer from history instead.
        # Uses desktop_observe (UIA + OCR text), not desktop_screenshot -- the
        # initial completion isn't vision-routed (only follow-ups are, via
        # _select_followup_route), so queuing an image here would just send
        # it to the wrong, text-only client.
        if self.config.desktop_control_enabled and router.SCREEN_QUERY_RE.search(user_input):
            try:
                screen_observation = await asyncio.get_running_loop().run_in_executor(
                    _UIA_EXECUTOR, tool_registry.execute_tool, "desktop_observe", {}
                )
                search_results = (
                    f"{search_results}\n\n{screen_observation}" if search_results else screen_observation
                )
                logger.info("Forced fresh screen observation for screen-content query")
            except Exception:
                logger.warning("Forced screen observation failed", exc_info=True)

        # --- Flag ambiguous visual-content queries for a queued screenshot ---
        # Separate mechanism from the desktop_observe block above: this later
        # injects a synthetic desktop_screenshot tool call (see
        # _maybe_inject_visual_screenshot_call below) so the image is queued
        # by the SAME tool-execution-loop machinery that handles a model-
        # initiated desktop_screenshot call -- queuing it here, before the
        # initial payload is built, would have _build_payload's
        # pop_pending_vision_image() immediately consume it into the
        # non-vision-routed initial request instead of the follow-up.
        queue_visual_screenshot = _should_queue_visual_screenshot(user_input, self.config)
        if queue_visual_screenshot:
            logger.info("Visual-content query detected -- will queue desktop_screenshot for follow-up")
        elif _VISUAL_CONTENT_QUERY_RE.search(user_input):
            logger.debug("Visual-content query detected but vision/desktop control unavailable")

        # --- Assemble system prompt from frozen tiers + volatile tier ---
        now = datetime.now()
        budget = IterationBudget(max_turns=self.config.iteration_budget_max)
        # Detect which evidence blocks are present for volatile tier
        _ct = self._context_tier or ""
        _mem_parts = _ct.split("[MEMORY]\n", 1)
        has_memory = len(_mem_parts) > 1 and _mem_parts[1].split("\n")[0].strip() != ""
        _usr_parts = _ct.split("[USER]\n", 1)
        has_user = len(_usr_parts) > 1 and _usr_parts[1].split("\n")[0].strip() != ""
        has_opinions = "[OPINIONS]\n" in _ct
        # Read verbosity hint from USER.md context tier
        verbosity_hint = None
        for line in _ct.splitlines():
            stripped = line.strip()
            if stripped.startswith("verbosity:"):
                verbosity_hint = stripped.split(":", 1)[1].strip()
                break
        # Goal expiry: decrement turns remaining each turn
        if self._active_goal and self._goal_turns_remaining > 0:
            self._goal_turns_remaining -= 1
            if self._goal_turns_remaining <= 0:
                logger.debug("Goal expired: %s", self._active_goal)
                self._active_goal = None
        volatile = prompt_builder.build_volatile_tier(
            platform, now, budget.remaining,
            has_search=bool(search_results), has_memory=has_memory,
            has_user=has_user, has_opinions=has_opinions,
            verbosity_hint=verbosity_hint,
            active_goal=self._active_goal,
            operator_persona=_detect_operator_persona(user_input),
            tool_catalog="" if self._use_native_tools else tool_registry.build_tool_prompt(),
            idle_seconds=(
                desktop_session.user_idle_seconds()
                if _DESKTOP_AVAILABLE and desktop_session is not None
                else None
            ),
            world_model_slice=self.world_model.context_slice(),
        )
        system_msg = prompt_builder.assemble_system_prompt(
            self._stable_tier, self._context_tier, volatile
        )

        # Inject search results so LLM answers from fresh data
        effective_input = user_input
        if search_results:
            effective_input = (
                f"{user_input}\n\n"
                f"[SEARCH RESULTS - USE THESE TO ANSWER]\n"
                f"{search_results}\n"
                f"[END SEARCH RESULTS]\n"
                f"\nUse the search results above to answer the user question. "
                f"Do NOT use your training data for this answer."
            )

        # Retrieve relevant memories from vector store (skip for follow-up or short
        # queries, and for screen-content queries -- past screen descriptions stored
        # as memories are never relevant to "what's on my screen right now" and have
        # been observed to override the freshly forced observation above).
        if self.memory_store and self.memory_store.is_available:
            if (
                not _is_followup(user_input)
                and len(user_input.strip()) >= 10
                and not router.SCREEN_QUERY_RE.search(user_input)
            ):
                try:
                    memory_results = self.memory_store.search(user_input, n_results=3)
                    memory_block = self.memory_store.format_for_prompt(memory_results)
                    if memory_block:
                        effective_input = memory_block + "\n\n" + effective_input
                except Exception as mem_exc:
                    logger.debug("Memory retrieval skipped: %s", mem_exc)

        # Build messages with conversation history
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_msg},
        ]
        # Prepend last N turns of history
        if self.history:
            messages.extend(self.history[-(self._history_max_turns * 2) :])
        messages.append({"role": "user", "content": effective_input})
        messages = await _prep_messages(messages, self.config)

        # Save user message to history -- the full original utterance, even if a
        # fast-path above rebound user_input to a compound instruction's leftover.
        self.history.append({"role": "user", "content": original_user_input})

        payload = self._build_payload(messages, skip_tools=skip_tools)
        accumulated, tool_calls = await self._stream_completion(
            payload, generation
        )

        # Hybrid fallback: try text-based extraction if native returned nothing
        if not tool_calls and accumulated and not skip_tools:
            tool_calls = self._extract_tool_calls(accumulated)

        if skip_tools:
            tool_calls = []

        tool_calls = router.maybe_inject_visual_screenshot_call(
            tool_calls, queue_visual_screenshot and not skip_tools
        )

        if not tool_calls:
            if accumulated:
                stream_filter = TextStreamFilter()
                filtered = stream_filter.push(accumulated) + stream_filter.flush()
                # Save assistant response to history
                self.history.append({"role": "assistant", "content": filtered})
                # Trim history to max turns (keep pairs: user + assistant)
                max_messages = self._history_max_turns * 2
                if len(self.history) > max_messages:
                    self.history = self.history[-max_messages:]
                if filtered:
                    yield filtered
                # Save to vector memory (fire-and-forget)
                self._save_to_memory(filtered, "assistant")
                asyncio.ensure_future(self._extract_thread_update(original_user_input, filtered, session_id))
            await self._check_memory_capacity()
            return

        # --- Tool execution loop ---
        _seen_tool_calls: Dict[str, str] = {}
        # Desktop clicks/types are not idempotent -- two identical calls are
        # two real actions, not a cache hit. Desktop perception (observe/
        # read_screen/screenshot) isn't cacheable either -- the screen can
        # change between calls even with identical (empty) arguments, e.g.
        # another tool call opening/closing a window in between; a cached
        # mark would then resolve to a dead COM proxy. Tracks consecutive
        # failures of the same call for anomaly auto-halt.
        _desktop_fail_counts: Dict[str, int] = {}
        _desktop_action_count = [0]  # mutable cell, closed over by _exec_one
        _turn_external_texts: List[str] = []  # tool_external results, fed to security_policy's injected-command check

        async def _exec_one(call: Dict[str, Any]) -> str:
            tool_name = call["name"]
            ck = f"{call['name']}({json.dumps(call['arguments'], sort_keys=True)})"
            if tool_name not in _DESKTOP_COM_TOOLS and ck in _seen_tool_calls:
                logger.info("Tool %s already executed, reusing result", call["name"])
                return _seen_tool_calls[ck]
            timeout = _TOOL_TIMEOUTS.get(tool_name, _TOOL_TIMEOUT_SEC)
            lock = self._tool_locks.setdefault(tool_name, asyncio.Lock())

            async def _run() -> str:
                executor = _UIA_EXECUTOR if tool_name in _DESKTOP_COM_TOOLS else None
                return await asyncio.get_running_loop().run_in_executor(
                    executor, tool_registry.execute_tool, call["name"], call["arguments"]
                )

            if self.on_thinking_update:
                self.on_thinking_update(call["name"], call["arguments"])
            if self.on_tool_call:
                self.on_tool_call(call["name"], call["arguments"])

            if tool_name == "shell_execute":
                # voice_mode is derived from the real turn platform, never trusted from the LLM-supplied call args.
                call["arguments"]["voice_mode"] = platform == "voice"

            # Gated keywords, sensitive paths, or commands lifted from untrusted output require approve/decline.
            gate_reason: Optional[str] = None
            if tool_name == "shell_execute":
                gate_reason = is_shell_command_gated(call["arguments"].get("command", ""))
            if not gate_reason:
                policy_result = security_policy.check_tool_call(tool_name, call["arguments"], _turn_external_texts)
                if policy_result.needs_approval:
                    gate_reason = policy_result.reason

            approved = True
            if gate_reason:
                approved = await self.request_tool_approval(tool_name, call["arguments"], gate_reason)

            if gate_reason and not approved:
                r = f"Error: Command declined by user (required approval: {gate_reason})."
            elif tool_name == "propose_new_tool":
                r = await self._handle_propose_new_tool(call["arguments"])
            elif tool_name in _DESKTOP_CONTROL_TOOLS and self._is_desktop_halted():
                r = "Error: Desktop control is halted (panic or repeated failure). Say 'continue' to resume."
            elif tool_name in _DESKTOP_CONTROL_TOOLS and _desktop_action_count[0] >= self.config.desktop_max_actions:
                r = f"Error: Desktop action limit reached ({self.config.desktop_max_actions} for this turn)."
                self._turn_halted = True
            else:
                if tool_name in _DESKTOP_CONTROL_TOOLS:
                    _desktop_action_count[0] += 1
                try:
                    if tool_registry.is_interactive(tool_name):
                        async with lock:
                            r = await asyncio.wait_for(_run(), timeout=timeout)
                    else:
                        r = await asyncio.wait_for(_run(), timeout=timeout)

                    # Check for standard returned shell/file failures to attempt recovery
                    if tool_name == "shell_execute" and r.startswith("Error"):
                        logger.info("Shell execution returned an error. Running recovery pipeline...")
                        from charlie.recovery import recover_tool
                        recovered_res = await recover_tool(self, tool_name, call["arguments"], RuntimeError(r))
                        if recovered_res is not None:
                            r = recovered_res
                    elif tool_name == "file_write" and r.startswith("Error"):
                        logger.info("File write returned an error. Running recovery pipeline...")
                        from charlie.recovery import recover_tool
                        recovered_res = await recover_tool(self, tool_name, call["arguments"], RuntimeError(r))
                        if recovered_res is not None:
                            r = recovered_res
                except asyncio.TimeoutError as te:
                    if tool_name in ("shell_execute", "file_write"):
                        logger.info("Tool %s timed out. Running recovery pipeline...", tool_name)
                        from charlie.recovery import recover_tool
                        recovered_res = await recover_tool(self, tool_name, call["arguments"], te)
                        if recovered_res is not None:
                            r = recovered_res
                        else:
                            r = f"Error: Tool '{tool_name}' timed out after {timeout}s"
                    else:
                        r = f"Error: Tool '{tool_name}' timed out after {timeout}s"
                    logger.warning("Tool %s timed out", tool_name)
                except Exception as e:
                    if tool_name in ("shell_execute", "file_write"):
                        logger.info("Tool %s raised exception. Running recovery pipeline...", tool_name)
                        from charlie.recovery import recover_tool
                        recovered_res = await recover_tool(self, tool_name, call["arguments"], e)
                        if recovered_res is not None:
                            r = recovered_res
                        else:
                            r = f"Error executing tool '{tool_name}': {e}"
                    else:
                        r = f"Error executing tool '{tool_name}': {e}"
                    logger.warning("Tool %s raised an exception: %s", tool_name, e)

            if r.startswith("Error"):
                self.world_model.record_event("tool_error", f"{tool_name}: {r[:200]}")

            # Anomaly auto-halt: repeated failure of the same call means looping, not progress.
            if tool_name in _DESKTOP_CONTROL_TOOLS:
                if r.startswith("Error"):
                    _desktop_fail_counts[ck] = _desktop_fail_counts.get(ck, 0) + 1
                    threshold = 1 if _is_low_confidence_desktop_call(tool_name, call["arguments"]) else 2
                    if _desktop_fail_counts[ck] >= threshold:
                        self._turn_halted = True
                        logger.warning(
                            "Desktop action %s failed %d time(s) (threshold %d) -- auto-halting.",
                            tool_name, _desktop_fail_counts[ck], threshold,
                        )
                else:
                    _desktop_fail_counts[ck] = 0

            # Pop immediately (no await above) so a concurrent Brain can't overwrite it first.
            if tool_name == "desktop_screenshot":
                self._pending_vision_image_url = pop_pending_vision_image()

            if self.on_tool_result:
                self.on_tool_result(call["name"], r)

            # Persist tool result to session store (truncated)
            if self.session_store:
                try:
                    self.session_store.append_tool(
                        turn_id=turn_id,
                        tool_name=call["name"],
                        args=call["arguments"],
                        result=r,
                        session_id=session_id,
                    )
                except Exception as persist_exc:
                    logger.debug("Tool result persist skipped: %s", persist_exc)

            if tool_name not in _DESKTOP_COM_TOOLS:
                _seen_tool_calls[ck] = r
            telemetry.record_tool_call(tool_name, success=not r.startswith("Error"))
            return r

        while True:
            # Re-check cancellation at the top of every tool cycle so a turn
            # cancelled mid-stream does not run another tool round before
            # streaming. Matches the generation guard used further below.
            if self._chat_generation != generation:
                logger.debug(
                    "Chat generation changed (%s != %s), aborting tool loop",
                    self._chat_generation,
                    generation,
                )
                break
            if self._is_desktop_halted():
                logger.info("Desktop control halted -- stopping tool loop.")
                yield "Desktop control halted (panic hotkey or repeated failure). Stopping here."
                # Physical panic latch only clears via an explicit resume action (_detect_desktop_resume), never here.
                self._turn_halted = False
                break
            if not tool_calls:
                break

            # Enforce iteration budget
            allowed_calls = []
            for call in tool_calls:
                if budget.try_spend(call["name"]):
                    allowed_calls.append(call)
                else:
                    yield "I've reached my tool limit for this turn. Let me know if you want me to continue."
                    return

            tool_calls = allowed_calls
            results_map: Dict[int, str] = {}
            for idx, call in enumerate(tool_calls):
                if not tool_registry.is_interactive(call["name"]):
                    results_map[idx] = await _exec_one(call)

            # Interactive tools run sequentially after read-only tools complete.
            for idx, call in enumerate(tool_calls):
                if tool_registry.is_interactive(call["name"]):
                    results_map[idx] = await _exec_one(call)

            exec_results = [results_map[i] for i in range(len(tool_calls))]
            # Step 3: Post-tool confidence gate - replace low-quality results
            exec_results = [
                r if _assess_tool_result_relevance(c["name"], r)
                else "Error: Search returned no useful results. Proceed with general knowledge."
                for c, r in zip(tool_calls, exec_results)
            ]

            for c, r in zip(tool_calls, exec_results):
                if trust_level_for_tool(c["name"]) == "tool_external":
                    _turn_external_texts.append(r)

            tool_results = _build_native_tool_results(tool_calls, exec_results)

            # Format results based on native vs text-based calling
            is_text_based = any(c.get("id") is None for c in tool_calls)
            if is_text_based:
                messages.append({"role": "assistant", "content": accumulated})
                tool_summary = _format_text_tool_summary(tool_calls, exec_results)
                messages.append({"role": "tool", "content": tool_summary})
            else:
                messages.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": r["tool_call_id"],
                                "type": "function",
                                "function": {
                                    "name": c["name"],
                                    "arguments": json.dumps(c["arguments"]),
                                },
                            }
                            for c, r in zip(tool_calls, tool_results)
                        ],
                    }
                )
                messages.extend(tool_results)

            messages = await _prep_messages(messages, self.config)

            followup_payload = self._build_payload(messages)
            followup_client, followup_model, is_vision = self._select_followup_route(
                followup_payload
            )

            state = FollowupStreamState()
            try:
                async for filtered in self._stream_followup_once(
                    followup_client, followup_model, followup_payload, generation, state
                ):
                    yield filtered
            except Exception as tool_exc:
                logger.warning(
                    "%s follow-up LLM error: %s", "Vision" if is_vision else "Tool", tool_exc
                )
                break

            if state.cancelled:
                logger.info("Tool follow-up cancelled (barge-in)")
                return

            accumulated = state.accumulated
            tool_calls = collect_tool_calls(state.tc_by_index)
            # Save final follow-up response to history (after tool loop)
            if accumulated:
                hist_filter = TextStreamFilter()
                clean_accumulated = hist_filter.push(accumulated) + hist_filter.flush()
                self.history.append({"role": "assistant", "content": clean_accumulated})
                # Save to vector memory (fire-and-forget)
                self._save_to_memory(clean_accumulated, "assistant")
                asyncio.ensure_future(
                    self._extract_thread_update(original_user_input, clean_accumulated, session_id)
                )
            await self._check_memory_capacity()
            # --- Periodic reflection and knowledge graph update ---
            self._reflect_turn_counter += 1
            if self._reflect_turn_counter % self._reflect_interval == 0:
                asyncio.ensure_future(self._reflect_and_consolidate())
                self._check_outcome_feedback()
                self._check_observed_patterns()
                self.world_model.decay_stale_rules()
            # Trim history to max turns (keep pairs: user + assistant)
            max_messages = self._history_max_turns * 2
            if len(self.history) > max_messages:
                self.history = self.history[-max_messages:]

    async def _extract_thread_update(self, user_input: str, response: str, session_id: str) -> None:
        """Fire-and-forget: ask the LLM whether this turn touches an open thread.
        Best-effort -- failure never affects the visible turn.
        """
        if not self.config.llm_url or len(user_input) < 15:
            return
        open_threads = self.world_model.list_open_threads(limit=5)
        threads_desc = "\n".join(f"{tid}: {title}" for tid, title, _ in open_threads) or "(none)"
        prompt = (
            "Given this exchange, decide if it belongs to one of the open threads below, "
            "starts a new open-ended task worth tracking, or is a one-off needing no thread.\n"
            f"Open threads:\n{threads_desc}\n\n"
            f"User: {user_input}\nAssistant: {response[:300]}\n\n"
            'Reply with ONLY compact JSON: {"action": "none"|"new"|"update"|"resolve", '
            '"thread_id": "<id or empty>", "title": "<short title or empty>", "summary": "<one line or empty>"}.'
        )
        try:
            headers = build_auth_headers(self.config.llm_key)
            payload = {
                "model": self.config.llm_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
                "max_tokens": 100,
            }
            async with httpx.AsyncClient(base_url=self.config.llm_url, headers=headers, timeout=10.0) as client:
                resp = await client.post("chat/completions", json=payload)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            data = json.loads(content)
        except Exception as exc:
            logger.debug("Thread extraction skipped: %s", exc)
            return

        action = data.get("action")
        thread_id = data.get("thread_id") or ""
        summary = data.get("summary") or ""
        if action == "new":
            self.world_model.open_thread(data.get("title") or user_input[:60], session_id)
        elif action == "update" and thread_id:
            self.world_model.update_thread(thread_id, summary, resolved=False)
        elif action == "resolve" and thread_id:
            self.world_model.update_thread(thread_id, summary, resolved=True)

    def _check_outcome_feedback(self) -> None:
        """Outcome-feedback learning signal: a tool that keeps failing earns a
        rule flagging it, sourced from telemetry.py's rolling error rates.
        Sync and cheap (in-memory deque scan + a few sqlite rows) -- called
        on the same periodic cadence as memory reflection, not per-turn.
        """
        existing_texts = [text for _id, text, _c, _s, _st in self.world_model.list_rules(include_decayed=True)]
        for tool_name, error_rate, calls in telemetry.unreliable_tools():
            marker = f"Tool '{tool_name}'"
            if any(t.startswith(marker) for t in existing_texts):
                continue
            self.world_model.add_rule(
                f"{marker} has failed {error_rate:.0%} of its last {calls} calls -- "
                "double-check its result or prefer an alternative when one exists.",
                "outcome",
            )

    def _check_observed_patterns(self) -> None:
        """Observed-pattern learning signal: an app-open sequence repeated
        often enough gets proposed as a candidate rule, never auto-applied
        (see WorldModel.propose_rule) -- the highest-risk signal earns the
        most caution, per the plan's propose-don't-apply design.
        """
        pattern = self.world_model.detect_app_sequence_pattern()
        if pattern is None:
            return
        app_a, app_b, _count = pattern
        marker = f"open {app_b} shortly after {app_a}"
        existing_texts = [text for _id, text, _c, _s, _st in self.world_model.list_rules(include_decayed=True)]
        if any(marker in t for t in existing_texts):
            return
        self.world_model.propose_rule(
            f"You often {marker} -- want me to do that automatically when you open {app_a}?",
            "pattern",
        )

    def _save_to_memory(self, text: str, source: str) -> None:
        """Fire-and-forget: extract and store facts from assistant response."""
        if not self.memory_store or not self.memory_store.is_available:
            return
        if len(text) < 30:
            return
        try:
            loop = asyncio.get_running_loop()
            loop.run_in_executor(
                None,
                self.memory_store.add_memory,
                text,
                source,
                "auto",
            )
        except Exception as e:
            logger.debug("Memory save skipped: %s", e)


    async def _reflect_and_consolidate(self) -> None:
        """Periodically reflect on recent conversation and consolidate the knowledge graph."""
        try:
            # Get recent conversation context
            recent = self.history[-6:] if len(self.history) >= 6 else self.history
            if len(recent) < 2:
                return

            conversation_text = "\n".join(
                f"{m['role']}: {m['content'][:200]}" for m in recent
            )

            client = self.client
            model = self.config.llm_model

            prompt = (
                "Review this recent conversation and extract key facts. "
                "For each fact, output a line in the format:\n"
                "SUBJECT | PREDICATE | OBJECT\n\n"
                "Focus on: user preferences, environment facts, corrections, goals.\n"
                "Skip trivial/chit-chat. Max 10 facts.\n\n"
                f"Conversation:\n{conversation_text}\n\n"
                "Facts (one per line, format: S | P | O):"
            )

            response = await client.post(
                "chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 500,
                    "temperature": 0.3,
                },
            )
            if response.status_code != 200:
                logger.debug("Reflection LLM call failed: %s", response.status_code)
                return

            content = response.json()["choices"][0]["message"]["content"]

            # Parse facts and add to graph
            added = 0
            for line in content.strip().splitlines():
                line = line.strip()
                if "|" in line and not line.startswith("#"):
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) == 3 and all(parts):
                        try:
                            self.memory_graph.add_fact(parts[0], parts[1], parts[2])
                            added += 1
                        except Exception:
                            logger.debug("Failed to add fact: %s", line)

            if added > 0:
                logger.info("Reflection: added %d facts to knowledge graph", added)

            # Periodically consolidate
            if self._reflect_turn_counter % (self._reflect_interval * 3) == 0:
                removed = self.memory_graph.consolidate()
                if removed:
                    logger.info("Reflection: consolidated graph, removed %d stale facts", removed)

        except Exception as e:
            logger.debug("Reflection failed: %s", e, exc_info=True)
    @staticmethod
    def _resolve_tool_arguments(tool_name: str, raw_args: str) -> Dict[str, Any]:
        """Map a text-mode TOOL: call's raw argument string onto `tool_name`'s
        real parameter names, read live from the registry (see
        ToolRegistry.get_tool_param_names) instead of a hand-maintained dict.
        That dict previously covered only 6 of the 19+ registered tools --
        every other tool (all desktop_* tools, the graph/vector-memory tools,
        any MCP/plugin/extension tool) fell through to a generic `query`
        kwarg and crashed with a TypeError at call time."""
        params_list = tool_registry.get_tool_param_names(tool_name)
        if not params_list:
            # Unknown tool name, or a registered tool that takes no
            # arguments (e.g. graph_consolidate) -- nothing to map onto.
            if params_list is None and raw_args:
                return {"query": raw_args.strip("'\"")}
            return {}
        if not raw_args:
            return {params_list[0]: raw_args.strip("'\"")}
        quoted = re.findall(r'["\']([^"\']*)["\']', raw_args)
        if len(quoted) == 1:
            return {params_list[0]: quoted[0]}
        if len(quoted) > 1:
            return {
                params_list[i]: val for i, val in enumerate(quoted) if i < len(params_list)
            }
        return {params_list[0]: raw_args}

    def _extract_tool_calls(self, text: str) -> List[Dict[str, Any]]:
        """Extract tool calls from both JSON and text-based TOOL: format."""
        calls = []
        if not text:
            return calls

        if "tool_calls" in text:
            try:
                data = json.loads(text)
                if isinstance(data, dict) and data.get("tool_calls"):
                    for tc in data["tool_calls"]:
                        function = tc.get("function", {})
                        arguments = {}
                        if isinstance(function.get("arguments"), str):
                            try:
                                arguments = (
                                    json.loads(function["arguments"])
                                    if function["arguments"]
                                    else {}
                                )
                            except json.JSONDecodeError:
                                arguments = {}
                        calls.append(
                            {
                                "id": tc.get("id"),
                                "name": function.get("name"),
                                "arguments": arguments,
                            }
                        )
                    return calls
            except json.JSONDecodeError:
                pass

        # Match TOOL: prefix format (explicit)
        tool_pattern = re.compile(r"TOOL:\s*(\w+)\(([^)]*)\)")
        for match in tool_pattern.finditer(text):
            tool_name = match.group(1)
            raw_args = match.group(2).strip()
            calls.append(
                {
                    "id": None,
                    "name": tool_name,
                    "arguments": self._resolve_tool_arguments(tool_name, raw_args),
                }
            )
        # Fallback: match bare tool calls without TOOL: prefix (text-mode only).
        # Native-tool providers parse structured tool_calls directly;
        # bare-pattern matching on prose causes false tool invocations.
        if not self._use_native_tools:
            known_names = "|".join(re.escape(n) for n in tool_registry.get_tool_names())
            seen_signatures = {
                (c["name"], json.dumps(c["arguments"], sort_keys=True)) for c in calls
            }
            if known_names:
                bare_pattern = re.compile(r"\b(" + known_names + r")\s*\(([^)]*)\)")
                for match in bare_pattern.finditer(text):
                    tname = match.group(1)
                    raw = match.group(2).strip()
                    args = self._resolve_tool_arguments(tname, raw)
                    sig = (tname, json.dumps(args, sort_keys=True))
                    if sig not in seen_signatures:
                        seen_signatures.add(sig)
                        calls.append({"id": None, "name": tname, "arguments": args})
        return calls


# =====================================================================
# Module-level helpers (kept outside Brain to avoid duplication)
# =====================================================================


def _build_native_tool_results(
    tool_calls: List[Dict[str, Any]], exec_results: List[str]
) -> List[Dict[str, Any]]:
    """Build native (OpenAI-style) tool role messages for the follow-up
    payload. Truncates to _TOOL_RESULT_MAX_CHARS like _format_text_tool_summary
    already does for the text-based path -- an MCP tool (e.g. a screenshot)
    can return a raw, unbounded blob, and sending that straight into the
    payload 400s against the API."""
    return [
        {
            "tool_call_id": c.get("id"),
            "role": "tool",
            "name": c["name"],
            "content": r[:_TOOL_RESULT_MAX_CHARS],
        }
        for c, r in zip(tool_calls, exec_results)
    ]


def _format_text_tool_summary(
    tool_calls: List[Dict[str, Any]],
    exec_results: List[str],
) -> str:
    """Format tool results as a summary for text-based (local model) follow-up."""
    lines: List[str] = []
    for call, result in zip(tool_calls, exec_results):
        content = result[:_TOOL_RESULT_MAX_CHARS]
        if call["name"] == "shell_execute":
            args = call.get("arguments", {})
            cmd = args.get("command", args) if isinstance(args, dict) else args
            if "Command executed successfully" in content:
                lines.append(
                    f"shell_execute {cmd} executed successfully. The command is now running."
                )
            else:
                lines.append(f"shell_execute {cmd} returned: {content}")
        else:
            args = call.get("arguments", {})
            arg_str = args.get("command", args) if isinstance(args, dict) else args
            lines.append(f"{call['name']}({arg_str}) returned: {content}")
    lines.append(
        "\nIMPORTANT: The tools above have been executed. "
        "Do NOT mention to the user that you ran tools or what tools were executed. "
        "Directly provide the final answer and results based on the tool return values. "
        "Do NOT call any more tools."
    )
    return "\n".join(lines)
