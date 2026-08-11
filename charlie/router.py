"""Declarative fast-path router. Matchers here are pure -- given
an utterance they return a RouteMatch (intent name + extracted args) or
None, with no side effects. Anything that actually launches/kills a
process, talks to a background task, etc. is a separate execute_*
function, called by the caller (Brain.chat_stream) only once a match is
confirmed -- fixes the audit finding that _detect_open_app/_detect_close_app
used to run taskkill/Popen calls directly inside what was meant to be a pure
detector. Memory-writing detectors (correction, opinion teaching, standing
instruction, set goal, verbosity feedback) stay in core.py -- they need
direct access to Brain.history/memory files, and folding them in here would
force this module to depend on Brain.
"""

import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from charlie.known_apps import APP_REGISTRY as _APP_REGISTRY
from charlie.text_utils import format_app_list
from charlie.utils import is_process_running, make_id

logger = logging.getLogger("charlie.router")


@dataclass
class RouteMatch:
    name: str
    args: Dict[str, Any]


@dataclass
class Route:
    name: str
    matcher: Callable[[str], Optional[RouteMatch]]
    cost: str = "fast"


def resolve(query: str) -> Optional[RouteMatch]:
    """Try each route's matcher in order, return the first match, or None."""
    for route in _ROUTES:
        match = route.matcher(query)
        if match is not None:
            return match
    return None


_TIME_DATE_RE = re.compile(
    r"(?:what(?:'s|\s+is|\s+s)?\s+(?:the\s+)?(?:current\s+)?(?:time|date|day|today))"
    r"|(?:tell\s+(?:me\s+)?(?:the\s+)?(?:time|date|day))"
    r"|(?:what\s+(?:time|date|day)\s+is\s+it)"
    r"|(?:what\s+(?:day\s+of\s+the\s+week|month|year)\s+is\s+it)"
    r"|(?:what(?:'s|\s+is|\s+s)?\s+today(?:'s\s+date)?)"
    r"|(?:(?:current|right\s+now)\s+(?:time|date))",
    re.IGNORECASE,
)


def answer_time_date(query: str) -> Optional[str]:
    """Answer time/date queries directly from system clock. Returns None if not a time/date query."""
    if not _TIME_DATE_RE.search(query):
        return None
    now = datetime.now()
    q = query.lower().strip()
    if "time" in q:
        return f"It's {now.strftime('%I:%M %p')}."
    if "date" in q or "today" in q:
        return f"Today is {now.strftime('%A, %B %d, %Y')}."
    if "month" in q:
        return f"It's {now.strftime('%B')}."
    if "year" in q:
        return f"It's {now.strftime('%Y')}."
    if "week" in q:
        return f"Today is {now.strftime('%A')}."
    if "day" in q:
        return f"Today is {now.strftime('%A, %B %d, %Y')}."
    return None


def _match_time_date(query: str) -> Optional[RouteMatch]:
    answer = answer_time_date(query)
    return None if answer is None else RouteMatch("time_date", {"answer": answer})


_CLOSE_APP_MAP = {name: entry.close_process for name, entry in _APP_REGISTRY.items() if entry.close_process}


def match_close_app(query: str) -> Optional[Tuple[List[str], List[str]]]:
    """Pure: which known apps does `query` ask to close?

    Returns (matched_apps, launched_processes), or None if no close-app
    intent (or a compound instruction with extra, non-trivial words) is
    detected.
    """
    q = query.lower().strip()
    q_clean = re.sub(r"^(?:hey\s+charlie,?|ok\s+charlie,?|charlie,?)?\s*", "", q).strip()

    verbs = ("close", "kill", "stop", "exit", "quit")
    verb_matched = None
    for verb in verbs:
        if q_clean.startswith(verb + " ") or q_clean == verb:
            verb_matched = verb
            break
    if not verb_matched:
        return None

    target_text = q_clean[len(verb_matched):].strip()
    if not target_text:
        return None

    sorted_keys = sorted(_CLOSE_APP_MAP.keys(), key=len, reverse=True)
    matched_apps: List[str] = []
    launched_processes: List[str] = []
    remaining_text = " " + target_text + " "
    for key in sorted_keys:
        pattern = r"\b" + re.escape(key) + r"\b"
        if re.search(pattern, remaining_text):
            matched_apps.append(key)
            launched_processes.append(_CLOSE_APP_MAP[key])
            remaining_text = re.sub(pattern, " ", remaining_text)

    if not matched_apps:
        for key in sorted_keys:
            exe_key = f"{key}.exe"
            pattern = r"\b" + re.escape(exe_key) + r"\b"
            if re.search(pattern, remaining_text):
                matched_apps.append(exe_key)
                launched_processes.append(_CLOSE_APP_MAP[key])
                remaining_text = re.sub(pattern, " ", remaining_text)

    if not matched_apps:
        return None

    cleaned_remaining = re.sub(
        r"\b(and|or|then|please|also|to|write|save|type)\b|\.exe\b|[.,;&!?]",
        " ", remaining_text, flags=re.IGNORECASE,
    ).strip()
    if cleaned_remaining:
        logger.info("Extra instructions detected in close app query: '%s', bypassing fast-path", cleaned_remaining)
        return None

    return matched_apps, launched_processes


def execute_close_app(matched_apps: List[str], launched_processes: List[str]) -> str:
    """Side effects: taskkill each matched app, build the status message."""
    logger.info("Fast-path close apps: apps=%s, processes=%s", matched_apps, launched_processes)
    if sys.platform != "win32":
        return f"App closing is only supported on Windows (detected {sys.platform})."

    success_apps, not_running_apps, failed_apps = [], [], []
    for app, process in zip(matched_apps, launched_processes):
        try:
            cmd = f"taskkill /IM {process} /F"
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                success_apps.append(app)
            elif "not found" in res.stderr.lower() or res.returncode == 128:
                not_running_apps.append(app)
            else:
                failed_apps.append(app)
        except Exception as e:
            logger.error("Failed to taskkill %s (%s): %s", app, process, e, exc_info=True)
            failed_apps.append(app)

    parts = []
    if success_apps:
        parts.append(f"{format_app_list(success_apps)} has been closed for you.")
    if not_running_apps:
        parts.append(f"{format_app_list(not_running_apps)} is not currently running.")
    if failed_apps:
        parts.append(f"Failed to close {format_app_list(failed_apps)}.")
    return " ".join(parts)


def _match_route_close_app(query: str) -> Optional[RouteMatch]:
    matched = match_close_app(query)
    if matched is None:
        return None
    apps, processes = matched
    return RouteMatch("close_app", {"matched_apps": apps, "launched_processes": processes})


def close_process_for(app: str) -> Optional[str]:
    return _CLOSE_APP_MAP.get(app)


_BROWSER_TASK_VERB_RE = re.compile(r"^\s*(?:play|watch|search|find|look up|browse|check)\b", re.IGNORECASE)
_BROWSER_TASK_ON_SITE_RE = re.compile(r"\bon\s+([a-z0-9][\w.]*)", re.IGNORECASE)


def match_browser_task(query: str) -> Optional[str]:
    """Pure: deterministic '<verb> ... on <site>' detection -- models skip prompted tool calls."""
    q = query.lower().strip()
    if not _BROWSER_TASK_VERB_RE.match(q):
        return None
    on_match = _BROWSER_TASK_ON_SITE_RE.search(q)
    if not on_match:
        return None
    site = on_match.group(1).strip(".,!?")
    entry = _APP_REGISTRY.get(site)
    if not entry or not entry.is_website:
        return None
    return query.strip()


_URL_RE = re.compile(
    r"\b((?:https?://)?(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)+)\b", re.IGNORECASE
)
_FILE_EXTENSIONS = frozenset({
    "txt", "doc", "docx", "pdf", "csv", "xlsx", "xls", "ppt", "pptx",
    "png", "jpg", "jpeg", "gif", "bmp", "svg", "ico",
    "mp3", "mp4", "wav", "avi", "mov", "mkv",
    "py", "js", "ts", "jsx", "tsx", "json", "xml", "yaml", "yml", "toml",
    "zip", "rar", "7z", "tar", "gz", "exe", "msi", "dll", "bat", "ps1",
    "log", "md", "ini", "cfg", "env",
})
_OPEN_APP_MAP = {name: entry.open_cmd for name, entry in _APP_REGISTRY.items()}


def _is_probable_domain(text: str) -> bool:
    """Validate if a token looks like a real domain name (not a float, version number, or file path)."""
    if "." not in text:
        return False
    clean = text.replace(".", "")
    if clean.isdigit():
        return False
    parts = text.split(".")
    ext = parts[-1].lower()
    if ext in _FILE_EXTENSIONS:
        return False
    return ext.isalpha() and 2 <= len(ext) <= 6


def match_open_app(query: str) -> Optional[Tuple[List[str], List[str], Optional[str]]]:
    """Pure: which known apps/URLs does `query` ask to open?

    Returns (matched_apps, launched_commands, leftover_instruction), or None
    if no open-app intent is detected. leftover_instruction is the
    compound-instruction remainder past the matched app name(s), or None for
    an open-only query.
    """
    q = query.lower().strip()
    q_clean = re.sub(r"^(?:hey\s+charlie,?|ok\s+charlie,?|charlie,?)?\s*", "", q).strip()

    verbs = ("open", "start", "launch", "run")
    verb_matched = None
    for verb in verbs:
        if q_clean.startswith(verb + " ") or q_clean == verb:
            verb_matched = verb
            break
    if not verb_matched:
        return None

    target_text = q_clean[len(verb_matched):].strip()
    if not target_text:
        return None

    matched_apps: List[str] = []
    launched_commands: List[str] = []
    is_website_flags: List[bool] = []
    remaining_text = " " + target_text + " "

    for match in _URL_RE.findall(remaining_text):
        if _is_probable_domain(match):
            matched_apps.append(match)
            cmd_url = match if match.startswith(("http://", "https://")) else f"https://{match}"
            launched_commands.append(cmd_url)
            is_website_flags.append(True)
            remaining_text = re.sub(r"\b" + re.escape(match) + r"\b", " ", remaining_text)

    sorted_keys = sorted(_OPEN_APP_MAP.keys(), key=len, reverse=True)
    for key in sorted_keys:
        pattern = r"\b" + re.escape(key) + r"\b"
        if re.search(pattern, remaining_text):
            matched_apps.append(key)
            launched_commands.append(_OPEN_APP_MAP[key])
            entry = _APP_REGISTRY.get(key)
            is_website_flags.append(bool(entry and entry.is_website))
            remaining_text = re.sub(pattern, " ", remaining_text)

    if not matched_apps:
        return None

    cleaned_remaining = re.sub(
        r"\b(and|or|then|please|also|to|write|save|type)\b|\.exe\b|[.,;&!?]",
        " ", remaining_text, flags=re.IGNORECASE,
    ).strip()
    leftover_instruction = remaining_text.strip() if cleaned_remaining else None

    if leftover_instruction:
        # Website + leftover text is a "do X on this site" request -- defer to browser_task, don't launch a bare tab.
        kept = [(a, c) for a, c, w in zip(matched_apps, launched_commands, is_website_flags) if not w]
        if not kept:
            logger.info("Deferring website+leftover query to browser_task: '%s'", query)
            return [], [], query.strip()
        matched_apps = [a for a, _ in kept]
        launched_commands = [c for _, c in kept]
        logger.info(
            "Compound open-app query: '%s' -- opening app(s) now, continuing with: '%s'",
            query, leftover_instruction,
        )

    return matched_apps, launched_commands, leftover_instruction


def execute_open_app(matched_apps: List[str], launched_commands: List[str]) -> str:
    """Side effects: focus already-running apps, launch the rest, build the status message."""
    logger.info("Fast-path open apps: apps=%s, commands=%s", matched_apps, launched_commands)
    if sys.platform != "win32":
        return f"App launching is only supported on Windows (detected {sys.platform})."

    success_apps, already_open_apps, failed_apps = [], [], []
    for app, cmd in zip(matched_apps, launched_commands):
        process_name = _CLOSE_APP_MAP.get(app)
        if process_name and is_process_running(process_name):
            from charlie.desktop.windows import focus_window
            focus_window(process_name.removesuffix(".exe"))
            already_open_apps.append(app)
            continue

        launched = False
        last_error = None
        try:
            full_cmd = f'start "" {cmd}'
            subprocess.Popen(full_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            launched = True
        except Exception as e:
            last_error = e
            logger.debug("start command failed for %s: %s", app, e)
        if not launched and not cmd.startswith(("http://", "https://")):
            try:
                os.startfile(cmd)
                launched = True
            except Exception as e:
                last_error = e
                logger.debug("os.startfile failed for %s: %s", app, e)
        if launched:
            success_apps.append(app)
        else:
            error_detail = type(last_error).__name__ if last_error else "unknown error"
            logger.error("Failed to launch %s (%s): %s", app, cmd, last_error)
            failed_apps.append((app, error_detail))

    if not success_apps and not already_open_apps:
        failed_names = [f"{name} ({err})" for name, err in failed_apps]
        return f"I could not open {', '.join(failed_names)}."

    msg_parts = []
    if success_apps:
        msg_parts.append(f"I've opened {format_app_list(success_apps)} for you.")
    if already_open_apps:
        msg_parts.append(f"{format_app_list(already_open_apps)} was already open -- switched to it.")
    if failed_apps:
        failed_names = [name for name, _ in failed_apps]
        msg_parts.append(f"(Failed to open: {format_app_list(failed_names)})")
    return " ".join(msg_parts)


def _match_route_open_app(query: str) -> Optional[RouteMatch]:
    matched = match_open_app(query)
    if matched is None:
        return None
    apps, commands, leftover = matched
    args = {"matched_apps": apps, "launched_commands": commands, "leftover_instruction": leftover}
    return RouteMatch("open_app", args)


def open_command_for(app: str) -> Optional[str]:
    return _OPEN_APP_MAP.get(app)


def known_app_names() -> List[str]:
    """Union of known open/close app names -- the only values the router classifier fallback may act on."""
    return sorted(set(_OPEN_APP_MAP) | set(_CLOSE_APP_MAP))


_BACKGROUND_TASK_STATUS_RE = re.compile(
    r"\bwhat are you doing\b"
    r"|\bhow'?s (it|the task|your task|the background task) (going|doing)\b"
    r"|\bwhat('?s| is) the status of\b.*\btask\b"
    r"|\bwhat step (are you on|is the task on)\b"
    r"|\b(is|has) the (background )?task (done|finished|complete)\b",
    re.IGNORECASE,
)


def current_task_status_text() -> Optional[str]:
    """Progress reply for whatever background task is running right now, independent of query phrasing."""
    from charlie import background_task  # lazy: background_task imports Brain from charlie.core

    task = background_task.get_current_task()
    if task is None or task.status in ("done", "failed", "cancelled"):
        return None

    total = len(task.steps)
    if task.status == "paused":
        return f'Background task "{task.text}" is paused, waiting for you to step away from the keyboard.'
    step_desc = task.steps[task.current_step] if task.current_step < total else "wrapping up"
    return f'Background task "{task.text}" is on step {task.current_step + 1} of {total}: {step_desc}.'


def answer_background_task_status(query: str) -> Optional[str]:
    """Fast-path progress reply for a running background task; None if none is active."""
    if not _BACKGROUND_TASK_STATUS_RE.search(query):
        return None
    return current_task_status_text()


def _match_background_task_status(query: str) -> Optional[RouteMatch]:
    answer = answer_background_task_status(query)
    return None if answer is None else RouteMatch("background_task_status", {"answer": answer})


SCREEN_QUERY_RE = re.compile(
    r"\bwhat(?:'s| is) (on|happening on) (my |the )?screen\b"
    r"|\bwhat (do|can) you see\b"
    r"|\b(read|look at|check) (my |the )?screen\b",
    re.IGNORECASE,
)


def maybe_inject_visual_screenshot_call(
    tool_calls: List[Dict[str, Any]], queue_visual_screenshot: bool
) -> List[Dict[str, Any]]:
    """Append a synthetic desktop_screenshot call when queue_visual_screenshot is
    True and the model's own tool_calls don't already include one -- see the
    chat_stream call site for why this runs post-dispatch instead of pre-payload.
    """
    if not queue_visual_screenshot:
        return tool_calls
    if any(c.get("name") == "desktop_screenshot" for c in tool_calls):
        return tool_calls
    return tool_calls + [{"id": make_id(), "name": "desktop_screenshot", "arguments": {}}]


_ROUTER_CLASSIFIER_MAX_WORDS = 12


def is_router_classifier_candidate(text: str) -> bool:
    """Cheap pre-filter: only short, non-question phrasings are worth a classifier round-trip."""
    stripped = text.strip()
    if not stripped or stripped.endswith("?"):
        return False
    return len(stripped.split()) <= _ROUTER_CLASSIFIER_MAX_WORDS


_ROUTES: List[Route] = [
    Route("time_date", _match_time_date),
    Route("close_app", _match_route_close_app),
    Route("open_app", _match_route_open_app),
    Route("background_task_status", _match_background_task_status),
]
