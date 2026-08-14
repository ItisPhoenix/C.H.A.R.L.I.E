"""Shared utility functions for the Charlie package."""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def json_dumps(obj: Any) -> str:
    """Serialize an object to JSON string."""
    return json.dumps(obj, ensure_ascii=False, default=str)


def json_loads(s: str) -> Any:
    """Deserialize a JSON string; return the string itself on failure."""
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return s


def parse_json_object(content: Any) -> Optional[Dict[str, Any]]:
    """Parse a JSON object from model content without trusting its formatting.

    Local model servers may return empty content, a fenced code block, or a
    short explanation around otherwise valid JSON.  This helper accepts those
    harmless variations while rejecting non-object output and never raises.
    """
    if not isinstance(content, str):
        return None

    text = content.strip()
    if not text:
        return None
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2:
            text = "\n".join(lines[1:])
            if text.rstrip().endswith("```"):
                text = text.rstrip()[:-3].rstrip()

    decoder = json.JSONDecoder()
    candidate: Optional[Dict[str, Any]] = None
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict):
            candidate = value
    return candidate


def make_id(length: int = 12) -> str:
    """Generate a short unique id for nodes/edges/tasks."""
    return uuid.uuid4().hex[:length]


def build_auth_headers(api_key: str) -> Dict[str, str]:
    """Build an Authorization header for a configured API key.

    Uses the exact ``("no-key", "no_key")`` tuple so a
    sentinel key never produces a bogus Bearer header. Returns an empty
    dict when no real key is configured.
    """
    if api_key and api_key not in ("no-key", "no_key"):
        return {"Authorization": f"Bearer {api_key}"}
    return {}


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string (space replaced by T, Z suffix)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def is_process_running(process_name: str) -> bool:
    """True if a process named `process_name` (e.g. "notepad.exe") is currently running."""
    import psutil

    target = process_name.lower()
    for proc in psutil.process_iter(["name"]):
        try:
            if (proc.info["name"] or "").lower() == target:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def open_url_in_browser(url: str) -> bool:
    """Open url in the user's real default browser via `start ""`, same mechanism as router.py's app opener."""
    import subprocess

    try:
        subprocess.Popen(f'start "" {url}', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False
