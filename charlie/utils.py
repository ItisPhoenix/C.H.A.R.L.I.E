"""Shared utility functions for the Charlie package."""

import json
import uuid
from typing import Any


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
