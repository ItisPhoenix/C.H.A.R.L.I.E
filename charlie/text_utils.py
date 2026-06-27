"""Shared text utilities for voice command normalization and domain detection."""

import re
from typing import Set

# --- Text normalization for multi-app commands ---
# When user says "Open Chrome calculator notepad", insert "and" between items
# so the model treats them as separate commands
_APP_LIST_PATTERN = re.compile(
    r"(?:open|start|launch|run)\s+"
    r"([a-zA-Z][a-zA-Z0-9]*"
    r"(?:\s+(?:and\s+)?[a-zA-Z][a-zA-Z0-9]*)*)",
    re.IGNORECASE,
)

KNOWN_APPS: Set[str] = {
    "chrome",
    "firefox",
    "edge",
    "opera",
    "brave",
    "vivaldi",
    "notepad",
    "calculator",
    "calc",
    "paint",
    "explorer",
    "file",
    "word",
    "excel",
    "powerpoint",
    "outlook",
    "teams",
    "slack",
    "discord",
    "spotify",
    "vlc",
    "steam",
    "code",
    "vscode",
    "terminal",
    "powershell",
    "cmd",
    "prompt",
}


def normalize_app_list(text: str) -> str:
    """Insert 'and' between app names in commands like 'Open Chrome calculator notepad'."""

    def _replace_match(m: re.Match) -> str:
        prefix = m.group(0)[: m.start(1) - m.start(0)]
        items_str = m.group(1)
        items = items_str.split()
        if len(items) <= 1:
            return m.group(0)
        # Separate known apps from unknown words
        apps = []
        others = []
        for item in items:
            if item.lower() in KNOWN_APPS:
                apps.append(item)
            else:
                others.append(item)
        if len(apps) < 2:
            return m.group(0)
        # Rebuild with "and" between apps
        normalized_apps = " and ".join(apps)
        if others:
            return f"{prefix}{normalized_apps} {' '.join(others)}"
        return f"{prefix}{normalized_apps}"

    return _APP_LIST_PATTERN.sub(_replace_match, text)
