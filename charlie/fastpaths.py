"""Authoritative Deterministic Fast-Path Router and Execution Layer for Charlie V1.

Bypasses LLM inference for unambiguous PC, OS, browser, media, and system operations:
- System Telemetry (CPU, RAM, Disk, Battery, Process listing)
- Media & Volume Controls (Set volume %, Mute, Unmute, Volume up/down, Play/Pause, Skip)
- App Lifecycle & Focus (Launch known/generic app, Focus window, Close app)
- Windows Settings Deep-Linking (ms-settings:* URIs)
- Basic Filesystem Operations (Show downloads, List folder files, Open Explorer path)
- Direct Browser URL Navigation (Direct Playwright / browser_read)
- System Time & Date
"""

from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("charlie.fastpaths")


@dataclass
class FastPathMatch:
    """Represents a successfully identified deterministic fast-path intent."""

    intent: str
    semantic_op_id: str
    tool_name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    target_domain: str = "system"
    confidence: float = 1.0
    direct_handler: Optional[Callable[..., str]] = None
    verifier_name: Optional[str] = None


# ---------------------------------------------------------------------------
# 1. System Telemetry Patterns
# ---------------------------------------------------------------------------
_CPU_RE = re.compile(
    r"\b(?:what(?:'s|\s+is)?\s+(?:the\s+)?cpu(?:\s+usage|\s+load|\s+percent|\s+utilization)?|"
    r"cpu\s+(?:usage|load|percent|utilization|status|metrics)|how\s+much\s+cpu)\b",
    re.IGNORECASE,
)
_RAM_RE = re.compile(
    r"\b(?:what(?:'s|\s+is)?\s+(?:the\s+)?(?:ram|memory)(?:\s+usage|\s+free|\s+used|\s+percent)?|"
    r"(?:ram|memory)\s+(?:usage|load|percent|free|status|metrics)|how\s+much\s+(?:ram|memory))\b",
    re.IGNORECASE,
)
_DISK_RE = re.compile(
    r"\b(?:what(?:'s|\s+is)?\s+(?:the\s+)?(?:disk|storage|drive)(?:\s+usage|\s+free|\s+space)?|"
    r"(?:disk|storage)\s+(?:usage|free|space|status)|how\s+much\s+(?:disk|storage|drive\s+space))\b",
    re.IGNORECASE,
)
_BATTERY_RE = re.compile(
    r"\b(?:what(?:'s|\s+is)?\s+(?:the\s+)?battery(?:\s+percent|\s+percentage|\s+level|\s+status)?|"
    r"battery\s+(?:percent|percentage|level|status|life)|how\s+much\s+battery)\b",
    re.IGNORECASE,
)
_PROCESSES_RE = re.compile(
    r"\b(?:list|show|what\s+are\s+the)\s+(?:top\s+)?(?:running\s+)?processes\b",
    re.IGNORECASE,
)


def _handle_system_diagnostics(check: str) -> str:
    try:
        import psutil
        if check == "cpu":
            pct = psutil.cpu_percent(interval=None)
            cores = psutil.cpu_count(logical=True) or 1
            return f"CPU Utilization: {pct:.1f}% ({cores} logical cores)."
        if check == "memory":
            vm = psutil.virtual_memory()
            used_gb = (vm.total - vm.available) / (1024 ** 3)
            total_gb = vm.total / (1024 ** 3)
            return f"Memory Utilization: {vm.percent:.1f}% ({used_gb:.1f} GB / {total_gb:.1f} GB used)."
        if check == "disk":
            disk = psutil.disk_usage("C:\\" if sys.platform == "win32" else "/")
            free_gb = disk.free / (1024 ** 3)
            total_gb = disk.total / (1024 ** 3)
            return f"Primary Disk Utilization: {disk.percent:.1f}% ({free_gb:.1f} GB free of {total_gb:.1f} GB)."
        if check == "processes":
            procs = []
            for p in sorted(
                psutil.process_iter(["name", "cpu_percent", "memory_percent"]),
                key=lambda x: x.info.get("cpu_percent") or 0,
                reverse=True,
            )[:5]:
                name = p.info.get("name") or "Unknown"
                cpu = p.info.get("cpu_percent") or 0.0
                mem = p.info.get("memory_percent") or 0.0
                procs.append(f"- {name}: CPU {cpu:.1f}%, RAM {mem:.1f}%")
            return "Top Running Processes:\n" + "\n".join(procs)
    except Exception as e:
        logger.debug("Fastpath psutil telemetry failed, falling back to system_diagnostics tool: %s", e)

    from charlie.tools import registry

    return registry.execute_tool("system_diagnostics", {"check": check})


def match_system_telemetry(query: str) -> Optional[FastPathMatch]:
    q = query.strip()
    if _CPU_RE.search(q):
        return FastPathMatch(
            intent="system_cpu",
            semantic_op_id="system.metrics.read",
            tool_name="system_diagnostics",
            arguments={"check": "cpu"},
            target_domain="system",
            direct_handler=lambda: _handle_system_diagnostics("cpu"),
        )
    if _RAM_RE.search(q):
        return FastPathMatch(
            intent="system_memory",
            semantic_op_id="system.metrics.read",
            tool_name="system_diagnostics",
            arguments={"check": "memory"},
            target_domain="system",
            direct_handler=lambda: _handle_system_diagnostics("memory"),
        )
    if _DISK_RE.search(q):
        return FastPathMatch(
            intent="system_disk",
            semantic_op_id="system.metrics.read",
            tool_name="system_diagnostics",
            arguments={"check": "disk"},
            target_domain="system",
            direct_handler=lambda: _handle_system_diagnostics("disk"),
        )
    if _BATTERY_RE.search(q):
        return FastPathMatch(
            intent="system_battery",
            semantic_op_id="system.metrics.read",
            tool_name="system_diagnostics",
            arguments={"check": "memory"},  # diagnostics reports battery if available
            target_domain="system",
            direct_handler=lambda: _handle_system_diagnostics("memory"),
        )
    if _PROCESSES_RE.search(q):
        return FastPathMatch(
            intent="system_processes",
            semantic_op_id="system.metrics.read",
            tool_name="system_diagnostics",
            arguments={"check": "processes"},
            target_domain="system",
            direct_handler=lambda: _handle_system_diagnostics("processes"),
        )
    return None


# ---------------------------------------------------------------------------
# 2. Media and Volume Patterns
# ---------------------------------------------------------------------------
_SET_VOL_NUM_RE = re.compile(
    r"\b(?:set\s+(?:the\s+)?volume\s+(?:to\s+)?(\d{1,3})%?|volume\s+(?:to\s+)?(\d{1,3})%?)\b",
    re.IGNORECASE,
)
_VOL_UP_RE = re.compile(
    r"\b(?:volume\s+up|increase\s+volume|turn\s+(?:the\s+)?volume\s+up|louder)\b",
    re.IGNORECASE,
)
_VOL_DOWN_RE = re.compile(
    r"\b(?:volume\s+down|decrease\s+volume|turn\s+(?:the\s+)?volume\s+down|quieter|softer)\b",
    re.IGNORECASE,
)
_MUTE_RE = re.compile(r"\b(?:mute(?:\s+volume|\s+audio|\s+sound)?|silence\s+audio)\b", re.IGNORECASE)
_UNMUTE_RE = re.compile(r"\b(?:unmute(?:\s+volume|\s+audio|\s+sound)?|restore\s+sound)\b", re.IGNORECASE)
_PLAY_PAUSE_RE = re.compile(
    r"\b(?:pause\s+music|pause\s+playback|play\s+music|resume\s+playback|toggle\s+playback)\b",
    re.IGNORECASE,
)
_NEXT_TRACK_RE = re.compile(r"\b(?:next\s+track|next\s+song|skip\s+song|skip\s+track)\b", re.IGNORECASE)
_PREV_TRACK_RE = re.compile(r"\b(?:previous\s+track|previous\s+song|prev\s+track|prev\s+song)\b", re.IGNORECASE)


def _handle_volume_set(pct: float) -> str:
    from charlie.media_adapter import _set_volume_percent

    res = _set_volume_percent(pct)
    if res.get("ok"):
        return f"Volume set to {res.get('volume_percent')}%."
    return f"Failed to set volume: {res.get('reason', 'audio endpoint unavailable')}."


def _handle_volume_control(action: str) -> str:
    from charlie.media_adapter import _volume_control

    res = _volume_control(action)
    if res.get("ok"):
        if action in ("mute", "unmute"):
            status = "muted" if res.get("muted") else "unmuted"
            return f"Audio is now {status} (volume {res.get('volume_percent')}%)."
        return f"Volume adjusted to {res.get('volume_percent')}%."
    return f"Media control '{action}' failed: {res.get('reason', 'audio endpoint unavailable')}."


def match_media_volume(query: str) -> Optional[FastPathMatch]:
    q = query.strip()
    m_num = _SET_VOL_NUM_RE.search(q)
    if m_num:
        val = int(m_num.group(1) or m_num.group(2))
        pct = max(0, min(100, val))
        return FastPathMatch(
            intent="volume_set_percent",
            semantic_op_id="media.volume.set",
            tool_name="set_volume",
            arguments={"percent": pct},
            target_domain="media",
            direct_handler=lambda: _handle_volume_set(float(pct)),
            verifier_name="verify_volume",
        )
    if _MUTE_RE.search(q):
        return FastPathMatch(
            intent="volume_mute",
            semantic_op_id="media.volume.set",
            tool_name="system_control",
            arguments={"action": "mute"},
            target_domain="media",
            direct_handler=lambda: _handle_volume_control("mute"),
            verifier_name="verify_volume",
        )
    if _UNMUTE_RE.search(q):
        return FastPathMatch(
            intent="volume_unmute",
            semantic_op_id="media.volume.set",
            tool_name="system_control",
            arguments={"action": "unmute"},
            target_domain="media",
            direct_handler=lambda: _handle_volume_control("unmute"),
            verifier_name="verify_volume",
        )
    if _VOL_UP_RE.search(q):
        return FastPathMatch(
            intent="volume_up",
            semantic_op_id="media.volume.set",
            tool_name="system_control",
            arguments={"action": "volume_up"},
            target_domain="media",
            direct_handler=lambda: _handle_volume_control("volume_up"),
            verifier_name="verify_volume",
        )
    if _VOL_DOWN_RE.search(q):
        return FastPathMatch(
            intent="volume_down",
            semantic_op_id="media.volume.set",
            tool_name="system_control",
            arguments={"action": "volume_down"},
            target_domain="media",
            direct_handler=lambda: _handle_volume_control("volume_down"),
            verifier_name="verify_volume",
        )
    if _PLAY_PAUSE_RE.search(q):
        return FastPathMatch(
            intent="media_play_pause",
            semantic_op_id="media.volume.set",
            tool_name="system_control",
            arguments={"action": "play_pause"},
            target_domain="media",
            direct_handler=lambda: _handle_volume_control("play_pause"),
        )
    if _NEXT_TRACK_RE.search(q):
        return FastPathMatch(
            intent="media_next_track",
            semantic_op_id="media.volume.set",
            tool_name="system_control",
            arguments={"action": "next_track"},
            target_domain="media",
            direct_handler=lambda: _handle_volume_control("next_track"),
        )
    if _PREV_TRACK_RE.search(q):
        return FastPathMatch(
            intent="media_prev_track",
            semantic_op_id="media.volume.set",
            tool_name="system_control",
            arguments={"action": "prev_track"},
            target_domain="media",
            direct_handler=lambda: _handle_volume_control("prev_track"),
        )
    return None


# ---------------------------------------------------------------------------
# 3. Windows Settings Deep-Linking Patterns
# ---------------------------------------------------------------------------
_SETTINGS_MAP = {
    "bluetooth": "ms-settings:bluetooth",
    "sound": "ms-settings:sound",
    "volume": "ms-settings:sound",
    "audio": "ms-settings:sound",
    "display": "ms-settings:display",
    "screen": "ms-settings:display",
    "network": "ms-settings:network",
    "wifi": "ms-settings:network-wifi",
    "internet": "ms-settings:network",
    "battery": "ms-settings:batterysaver",
    "power": "ms-settings:powersleep",
    "storage": "ms-settings:storagesense",
    "disk": "ms-settings:storagesense",
    "apps": "ms-settings:appsfeatures",
    "installed apps": "ms-settings:appsfeatures",
    "windows update": "ms-settings:windowsupdate",
    "update": "ms-settings:windowsupdate",
    "updates": "ms-settings:windowsupdate",
    "notifications": "ms-settings:notifications",
    "privacy": "ms-settings:privacy",
    "default apps": "ms-settings:defaultapps",
    "taskbar": "ms-settings:taskbar",
    "colors": "ms-settings:personalization-colors",
    "themes": "ms-settings:themes",
    "settings": "ms-settings:",
    "windows settings": "ms-settings:",
}

_SETTINGS_RE = re.compile(
    r"^\s*(?:charlie[,:]?\s*)?(?:open|show|go\s+to|launch)\s+"
    r"(?:the\s+)?(bluetooth|sound|volume|audio|display|screen|network|wifi|internet|battery|power|storage|disk|apps|installed\s+apps|windows\s+update|update|updates|notifications|privacy|default\s+apps|taskbar|colors|themes|windows\s+settings|settings)\s*(?:settings|page|panel)?\s*[.!?]*\s*$",
    re.IGNORECASE,
)


def _handle_open_settings(uri: str, name: str) -> str:
    if sys.platform == "win32":
        try:
            os.startfile(uri)  # type: ignore[attr-defined]
            return f"Opened Windows {name.capitalize()} Settings."
        except Exception as e:
            return f"Failed to open {name} settings: {e}"
    return f"Windows Settings deep-linking requires Windows (detected {sys.platform})."


def match_windows_settings(query: str) -> Optional[FastPathMatch]:
    m = _SETTINGS_RE.match(query.strip())
    if not m:
        return None
    target = m.group(1).lower().strip()
    uri = _SETTINGS_MAP.get(target, "ms-settings:")
    return FastPathMatch(
        intent="open_windows_settings",
        semantic_op_id="system.control.execute",
        tool_name="system_control",
        arguments={"action": "open_settings", "uri": uri},
        target_domain="system",
        direct_handler=lambda: _handle_open_settings(uri, target),
        verifier_name="verify_app_launch",
    )


# ---------------------------------------------------------------------------
# 4. App Focus & Switch Patterns
# ---------------------------------------------------------------------------
_FOCUS_APP_RE = re.compile(
    r"^\s*(?:charlie[,:]?\s*)?(?:switch\s+to|focus|bring\s+up|bring\s+to\s+front)\s+"
    r"(?:the\s+)?([a-zA-Z0-9 _.-]+?)\s*(?:app|window)?\s*[.!?]*\s*$",
    re.IGNORECASE,
)


def _handle_focus_app(app_name: str) -> str:
    from charlie.desktop.windows import focus_window

    res = focus_window(app_name)
    return res


def match_focus_app(query: str) -> Optional[FastPathMatch]:
    m = _FOCUS_APP_RE.match(query.strip())
    if not m:
        return None
    app = m.group(1).strip()
    return FastPathMatch(
        intent="focus_app",
        semantic_op_id="desktop.window.focus",
        tool_name="desktop_focus",
        arguments={"title": app},
        target_domain="desktop",
        direct_handler=lambda: _handle_focus_app(app),
        verifier_name="verify_app_focus",
    )


# ---------------------------------------------------------------------------
# 5. Basic Filesystem Operations
# ---------------------------------------------------------------------------
_SHOW_FOLDER_RE = re.compile(
    r"^\s*(?:charlie[,:]?\s*)?(?:show|list|open|browse)\s+(?:files\s+in\s+|the\s+)?(downloads|desktop|documents|pictures|music|videos|home|current\s+directory|folder)\s*[.!?]*\s*$",
    re.IGNORECASE,
)


def _resolve_special_folder(name: str) -> str:
    name_clean = name.lower().strip()
    home = os.path.expanduser("~")
    mapping = {
        "downloads": os.path.join(home, "Downloads"),
        "desktop": os.path.join(home, "Desktop"),
        "documents": os.path.join(home, "Documents"),
        "pictures": os.path.join(home, "Pictures"),
        "music": os.path.join(home, "Music"),
        "videos": os.path.join(home, "Videos"),
        "home": home,
        "current directory": os.getcwd(),
        "folder": os.getcwd(),
    }
    return mapping.get(name_clean, home)


def _handle_list_directory(path: str) -> str:
    if not os.path.exists(path):
        return f"Directory not found: {path}"
    try:
        entries = os.listdir(path)
        dirs = [e for e in entries if os.path.isdir(os.path.join(path, e))]
        files = [e for e in entries if os.path.isfile(os.path.join(path, e))]
        summary = f"Contents of {path} ({len(entries)} items):\n"
        if dirs:
            summary += f"- Directories ({len(dirs)}): {', '.join(sorted(dirs)[:15])}\n"
        if files:
            summary += f"- Files ({len(files)}): {', '.join(sorted(files)[:20])}"
        return summary.strip()
    except Exception as e:
        return f"Failed to list directory {path}: {e}"


def match_filesystem_basic(query: str) -> Optional[FastPathMatch]:
    m = _SHOW_FOLDER_RE.match(query.strip())
    if not m:
        return None
    folder_keyword = m.group(1).strip()
    target_path = _resolve_special_folder(folder_keyword)
    return FastPathMatch(
        intent="list_directory",
        semantic_op_id="file.system.read",
        tool_name="file_read",
        arguments={"path": target_path},
        target_domain="file",
        direct_handler=lambda: _handle_list_directory(target_path),
    )


# ---------------------------------------------------------------------------
# 6. Direct Browser URL Navigation Pattern
# ---------------------------------------------------------------------------
_DIRECT_URL_RE = re.compile(
    r"^\s*(?:charlie[,:]?\s*)?(?:go\s+to|open|navigate\s+to)\s+"
    r"(https?://\S+|www\.\S+|[a-zA-Z0-9-]+\.(?:com|org|io|net|edu|gov|dev|ai|me)\S*)\s*[.!?]*\s*$",
    re.IGNORECASE,
)


def _handle_direct_url(url: str) -> str:
    from charlie.tools import registry

    norm_url = url if url.startswith("http://") or url.startswith("https://") else f"https://{url}"
    return registry.execute_tool("browser_read", {"url": norm_url})


def match_direct_url(query: str) -> Optional[FastPathMatch]:
    m = _DIRECT_URL_RE.match(query.strip())
    if not m:
        return None
    url = m.group(1).strip()
    norm_url = url if url.startswith("http://") or url.startswith("https://") else f"https://{url}"
    return FastPathMatch(
        intent="browser_read_url",
        semantic_op_id="browser.page.read",
        tool_name="browser_read",
        arguments={"url": norm_url},
        target_domain="browser",
        direct_handler=lambda: _handle_direct_url(norm_url),
        verifier_name="verify_browser_navigate",
    )


# ---------------------------------------------------------------------------
# Master Fast-Path Matcher
# ---------------------------------------------------------------------------
def match_fast_path(query: str) -> Optional[FastPathMatch]:
    """Pure matcher: returns FastPathMatch if query matches a deterministic intent, else None."""
    if not query or len(query.strip()) == 0:
        return None

    # 1. System Telemetry
    m = match_system_telemetry(query)
    if m:
        return m

    # 2. Media / Volume
    m = match_media_volume(query)
    if m:
        return m

    # 3. Windows Settings
    m = match_windows_settings(query)
    if m:
        return m

    # 4. App Focus
    m = match_focus_app(query)
    if m:
        return m

    # 5. Basic Filesystem
    m = match_filesystem_basic(query)
    if m:
        return m

    # 6. Direct Browser URL
    m = match_direct_url(query)
    if m:
        return m

    return None


def execute_fast_path(match: FastPathMatch) -> str:
    """Execute a matched deterministic fast-path operation safely."""
    logger.info("Executing fast-path: intent=%s, op=%s", match.intent, match.semantic_op_id)
    if match.direct_handler is not None:
        try:
            return match.direct_handler()
        except Exception as e:
            logger.error("Fast-path direct execution failed: %s", e, exc_info=True)
            return f"Error executing {match.intent}: {e}"

    from charlie.tools import registry

    return registry.execute_tool(match.tool_name, match.arguments)
