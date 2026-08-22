"""Single source of truth for known local apps and websites.

Used to be three separately-maintained dicts in core.py (_POPULAR_WEBSITES,
_CLOSE_APP_MAP, _OPEN_APP_MAP) plus a fourth independent set in text_utils.py
(KNOWN_APPS) -- adding one app meant editing up to three of them by hand, and
they could silently drift out of sync. Now it's one entry here.
"""

from dataclasses import dataclass
from typing import Dict, Optional, Set


@dataclass(frozen=True)
class AppEntry:
    open_cmd: str
    close_process: Optional[str] = None  # None for websites -- nothing to taskkill
    is_website: bool = False
    is_productive: bool = False  # feeds charlie.context's focus-mode heuristic


APP_REGISTRY: Dict[str, AppEntry] = {
    "chrome": AppEntry("chrome", "chrome.exe"),
    "google chrome": AppEntry("chrome", "chrome.exe"),
    "browser": AppEntry("chrome", "chrome.exe"),
    "firefox": AppEntry("firefox", "firefox.exe"),
    "edge": AppEntry("msedge", "msedge.exe"),
    "microsoft edge": AppEntry("msedge", "msedge.exe"),
    "notepad": AppEntry("notepad", "notepad.exe"),
    "calculator": AppEntry("calc", "calc.exe"),
    "calc": AppEntry("calc", "calc.exe"),
    "spotify": AppEntry("spotify", "spotify.exe"),
    "discord": AppEntry("discord", "discord.exe"),
    "slack": AppEntry("slack", "slack.exe"),
    "vs code": AppEntry("code", "code.exe", is_productive=True),
    "vscode": AppEntry("code", "code.exe", is_productive=True),
    "code": AppEntry("code", "code.exe", is_productive=True),
    "terminal": AppEntry("wt", "WindowsTerminal.exe", is_productive=True),
    "powershell": AppEntry("powershell", "powershell.exe", is_productive=True),
    "cmd": AppEntry("cmd", "cmd.exe", is_productive=True),
    "command prompt": AppEntry("cmd", "cmd.exe", is_productive=True),
    "paint": AppEntry("mspaint", "mspaint.exe"),
    "mspaint": AppEntry("mspaint", "mspaint.exe"),
    "task manager": AppEntry("taskmgr", "taskmgr.exe"),
    "taskmgr": AppEntry("taskmgr", "taskmgr.exe"),
    "word": AppEntry("winword", "winword.exe", is_productive=True),
    "excel": AppEntry("excel", "excel.exe", is_productive=True),
    # Websites -- open_cmd is the full URL, no process to close.
    "instagram": AppEntry("https://instagram.com", is_website=True),
    "facebook": AppEntry("https://facebook.com", is_website=True),
    "twitter": AppEntry("https://x.com", is_website=True),
    "x": AppEntry("https://x.com", is_website=True),
    "youtube": AppEntry("https://youtube.com", is_website=True),
    "github": AppEntry("https://github.com", is_website=True),
    "google": AppEntry("https://google.com", is_website=True),
    "gmail": AppEntry("https://mail.google.com", is_website=True),
    "reddit": AppEntry("https://reddit.com", is_website=True),
    "wikipedia": AppEntry("https://wikipedia.org", is_website=True),
    "netflix": AppEntry("https://netflix.com", is_website=True),
    "amazon": AppEntry("https://amazon.com", is_website=True),
    "flipkart": AppEntry("https://www.flipkart.com", is_website=True),
}

# Recognized only for multi-app STT "and"-insertion (text_utils.normalize_app_list)
# -- no verified open/close command, so these don't fast-path launch/close yet.
# Add a real APP_REGISTRY entry above (with a confirmed exe/command) to upgrade one.
_RECOGNITION_ONLY: Set[str] = {
    "opera", "brave", "vivaldi", "explorer", "file",
    "powerpoint", "outlook", "teams", "vlc", "steam", "prompt",
}

KNOWN_APP_NAMES: Set[str] = set(APP_REGISTRY.keys()) | _RECOGNITION_ONLY
