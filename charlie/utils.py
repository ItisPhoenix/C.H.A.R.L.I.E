"""Shared utility functions for the Charlie package."""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict


def json_dumps(obj: Any) -> str:
    """Serialize an object to JSON string."""
    return json.dumps(obj, ensure_ascii=False, default=str)


def json_loads(s: str) -> Any:
    """Deserialize a JSON string; return the string itself on failure."""
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return s


def make_id(length: int = 12) -> str:
    """Generate a short unique id for nodes/edges/tasks."""
    return uuid.uuid4().hex[:length]


def build_auth_headers(api_key: str) -> Dict[str, str]:
    """Build an Authorization header for a configured API key.

    Uses the exact ``("no-key", "no_key")`` tuple per AGENTS.md §5 so a
    sentinel key never produces a bogus Bearer header. Returns an empty
    dict when no real key is configured.
    """
    if api_key and api_key not in ("no-key", "no_key"):
        return {"Authorization": f"Bearer {api_key}"}
    return {}


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string (space replaced by T, Z suffix)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
