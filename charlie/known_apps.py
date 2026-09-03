"""Single source of truth for known local apps and websites.

Used to be three separately-maintained dicts in core.py (_POPULAR_WEBSITES,
_CLOSE_APP_MAP, _OPEN_APP_MAP) plus a fourth independent set in text_utils.py
(KNOWN_APPS) -- adding one app meant editing up to three of them by hand, and
they could silently drift out of sync. Now it's one entry here.
"""

from dataclasses import dataclass
from ipaddress import ip_address
from typing import Dict, Optional, Set, Tuple
from urllib.parse import urlsplit, urlunsplit


@dataclass(frozen=True)
class AppEntry:
    open_cmd: str
    close_process: Optional[str] = None  # None for websites -- nothing to taskkill
    close_process_candidates: Tuple[str, ...] = ()
    is_website: bool = False
    is_productive: bool = False  # feeds charlie.context's focus-mode heuristic
    close_window_titles: Tuple[str, ...] = ()

    @property
    def close_processes(self) -> Tuple[str, ...]:
        """Return ordered process candidates, preserving the legacy primary name."""
        if not self.close_process:
            return self.close_process_candidates
        return tuple(dict.fromkeys((self.close_process, *self.close_process_candidates)))


APP_REGISTRY: Dict[str, AppEntry] = {
    "chrome": AppEntry("chrome", "chrome.exe"),
    "google chrome": AppEntry("chrome", "chrome.exe"),
    "browser": AppEntry("chrome", "chrome.exe"),
    "firefox": AppEntry("firefox", "firefox.exe"),
    "edge": AppEntry("msedge", "msedge.exe"),
    "microsoft edge": AppEntry("msedge", "msedge.exe"),
    "notepad": AppEntry("notepad", "notepad.exe"),
    "calculator": AppEntry(
        "calc",
        "calc.exe",
        ("CalculatorApp.exe",),
        close_window_titles=("Calculator",),
    ),
    "calc": AppEntry(
        "calc",
        "calc.exe",
        ("CalculatorApp.exe",),
        close_window_titles=("Calculator",),
    ),
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
    "amazon india": AppEntry("https://www.amazon.in", is_website=True),
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


def resolve_website_url(target: str) -> Optional[str]:
    """Resolve alias hints or any valid HTTP(S) host without a TLD allowlist."""
    value = target.strip().strip("<>\"'.,!? ")
    if not value:
        return None
    entry = APP_REGISTRY.get(value.lower())
    if entry and entry.is_website:
        value = entry.open_cmd
    explicit_scheme = "://" in value
    candidate = value if explicit_scheme else f"https://{value}"
    try:
        parsed = urlsplit(candidate)
        hostname = parsed.hostname
        if parsed.scheme.lower() not in {"http", "https"} or not hostname:
            return None
        if parsed.username or parsed.password:
            return None
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            return None
        normalized_host = hostname.encode("idna").decode("ascii").lower().rstrip(".")
        is_ip = False
        try:
            ip_address(normalized_host)
            is_ip = True
        except ValueError:
            pass
        if not explicit_scheme and normalized_host != "localhost" and not is_ip and "." not in normalized_host:
            return None
        netloc = normalized_host
        if parsed.port is not None:
            netloc = f"{netloc}:{parsed.port}"
        return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "", parsed.query, parsed.fragment))
    except (TypeError, ValueError):
        return None
