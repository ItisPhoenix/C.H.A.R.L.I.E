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


class FastPathResult(str):
    """Text result plus structured data for runtime consumers."""

    data: Dict[str, Any]

    def __new__(cls, text: str, data: Optional[Dict[str, Any]] = None):
        result = super().__new__(cls, text)
        result.data = data or {}
        return result


# ---------------------------------------------------------------------------
# 1. System Telemetry Patterns
# ---------------------------------------------------------------------------
_CPU_RE = re.compile(
    r"\b(?:what(?:'s|\s+is)?\s+(?:the\s+)?cpu(?:\s+usage|\s+load|\s+percent|\s+utilization)?|"
    r"cpu\s+(?:usage|load|percent|utilization|status|metrics)|how\s+much\s+cpu)\b",
    re.IGNORECASE,
)
_CPU_TEMPERATURE_RE = re.compile(
    r"\b(?:what(?:'s|\s+is)?\s+(?:my\s+|the\s+)?(?:current\s+)?cpu\s+(?:temperature|temp)|"
    r"show(?:\s+me)?\s+(?:the\s+)?(?:current\s+)?cpu\s+(?:temperature|temp)|"
    r"(?:current|currently|now|right\s+now)\s+cpu\s+(?:temperature|temp)|"
    r"cpu\s+(?:temperature|temp)\s+(?:currently|now|right\s+now))\b",
    re.IGNORECASE,
)
_RAM_RE = re.compile(
    r"\b(?:what(?:'s|\s+is)?\s+(?:the\s+)?(?:ram|memory)(?:\s+usage|\s+utilization|\s+free|\s+used|\s+percent)?|"
    r"(?:ram|memory)\s+(?:usage|utilization|load|percent|free|status|metrics)|how\s+much\s+(?:ram|memory))\b",
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
_FULL_SYSTEM_RE = re.compile(
    r"\b(?:(?:show|give|report|display)\s+(?:me\s+)?|(?:what(?:'s|\s+is)|how\s+is)\s+)"
    r"(?:(?:the|my)\s+)?(?:full\s+)?(?:system|machine)\s+"
    r"(?:status|overview|health|diagnostics|telemetry|matrix)\b|"
    r"\bfull\s+system\s+(?:status|overview|health|diagnostics|telemetry|matrix)\b",
    re.IGNORECASE,
)

_CLOSE_WINDOW_RE = re.compile(
    r"^\s*(?:charlie[,:]?\s*)?close\s+(?:the\s+)?(?P<target>.+?)\s+"
    r"(?:window|popup|pop-up|(?:currently\s+)?on\s+(?:my\s+)?screen)\s*[.!?]*\s*$",
    re.IGNORECASE,
)


def match_close_window(query: str) -> Optional[FastPathMatch]:
    """Route an explicit close-window command to the observed desktop window surface."""
    match = _CLOSE_WINDOW_RE.match(query.strip())
    if not match:
        return None
    target = re.sub(r"\s+(?:window|popup|pop-up|currently)\s*$", "", match.group("target").strip(), flags=re.I)
    return FastPathMatch(
        intent="close_window",
        semantic_op_id="desktop.window.close",
        tool_name="desktop_window",
        arguments={"window": target, "action": "close"},
        target_domain="desktop",
    )


def _handle_full_system_status() -> FastPathResult:
    """Collect the fields required by SystemWorkspace from live host state."""
    try:
        import psutil

        cpu = float(psutil.cpu_percent(interval=0.1))
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(os.path.abspath(os.sep))
        process_rows = []
        for process in sorted(
            psutil.process_iter(["pid", "name", "status", "memory_percent"]),
            key=lambda item: item.info.get("memory_percent") or 0.0,
            reverse=True,
        )[:8]:
            info = process.info
            process_rows.append(
                {
                    "id": str(info.get("pid")),
                    "name": info.get("name") or "Unknown",
                    "status": str(info.get("status") or "running").upper(),
                    "memory_percent": round(float(info.get("memory_percent") or 0.0), 1),
                }
            )
        operations = []
        try:
            from charlie.task_journal import get_task_journal
            active_statuses = {"queued", "planning", "waiting", "running", "paused", "approval_required", "verifying"}
            for task in get_task_journal().list(include_terminal=False):
                if task.status.value not in active_statuses:
                    continue
                progress = round((task.current_step / task.total_steps) * 100) if task.total_steps else 0
                operations.append(
                    {
                        "id": task.id,
                        "title": task.title,
                        "subtitle": task.status.value.upper(),
                        "status": task.status.value.upper(),
                        "progress": progress,
                    }
                )
        except Exception as exc:
            logger.debug("Task journal unavailable for system workspace: %s", exc)
        data = {
                "title": "MACHINE DIAGNOSTICS & SYSTEM TELEMETRY",
                "subtitle": "ACTIVE RUNTIME // HOST HEALTH // PROCESS MESH",
                "vitals": {
                    "title": "SYSTEM STATUS",
                    "subtitle": "LIVE HOST TELEMETRY",
                    "gauges": [
                        {"id": "cpu", "label": "CPU", "value": cpu, "unit": "percent_0_100"},
                        {"id": "memory", "label": "MEMORY", "value": float(memory.percent), "unit": "percent_0_100"},
                        {"id": "disk", "label": "DISK", "value": float(disk.percent), "unit": "percent_0_100"},
                    ],
                },
                "processes": {
                    "title": "WHAT IS RUNNING",
                    "subtitle": "TOP LIVE PROCESSES BY MEMORY",
                    "processes": process_rows,
                },
            }
        if operations:
            data["operations"] = operations
        return FastPathResult(
            f"Your PC is using {cpu:.0f}% CPU and {memory.percent:.0f}% of its memory; "
            f"{disk.percent:.0f}% of disk space is used.",
            data,
        )
    except Exception as exc:
        logger.warning("Full system workspace telemetry unavailable: %s", exc)
        return FastPathResult(
            "Full system status is unavailable.",
            {"vitals": None, "processes": None},
        )


def match_system_workspace(query: str) -> Optional[FastPathMatch]:
    if not _FULL_SYSTEM_RE.search(query.strip()):
        return None
    return FastPathMatch(
        intent="system_workspace",
        semantic_op_id="system.workspace.read",
        tool_name="system_diagnostics",
        target_domain="system",
        direct_handler=_handle_full_system_status,
    )


def _handle_system_diagnostics(check: str) -> str:
    try:
        import psutil
        if check == "cpu":
            pct = psutil.cpu_percent(interval=0.1)
            cores = psutil.cpu_count(logical=True) or 1
            return FastPathResult(
                f"CPU Utilization: {pct:.1f}% ({cores} logical cores).",
                {
                    "metric_name": "CPU Utilization",
                    "value": float(pct),
                    "unit": "percent_0_100",
                    "sample": "interval_0.1s",
                },
            )
        if check == "cpu_temperature":
            sensors_temperatures = getattr(psutil, "sensors_temperatures", None)
            readings = sensors_temperatures() if callable(sensors_temperatures) else {}
            candidates = []
            for sensor_name, entries in (readings or {}).items():
                sensor_label = str(sensor_name).lower()
                if not any(token in sensor_label for token in ("cpu", "core", "package", "k10temp", "acpitz")):
                    continue
                for entry in entries or ():
                    current = getattr(entry, "current", None)
                    if current is not None:
                        candidates.append(float(current))
            if not candidates:
                return FastPathResult(
                    "CPU temperature is unavailable on this system.",
                    {
                        "metric_name": "CPU Temperature",
                        "value": None,
                        "unit": "celsius",
                        "available": False,
                        "reason": "unsupported_or_unavailable",
                    },
                )
            temperature = max(candidates)
            return FastPathResult(
                f"CPU Temperature: {temperature:.1f}°C.",
                {
                    "metric_name": "CPU Temperature",
                    "value": temperature,
                    "unit": "celsius",
                    "available": True,
                },
            )
        if check == "memory":
            vm = psutil.virtual_memory()
            used_gb = (vm.total - vm.available) / (1024 ** 3)
            total_gb = vm.total / (1024 ** 3)
            return FastPathResult(
                f"Memory Utilization: {vm.percent:.1f}% ({used_gb:.1f} GB / {total_gb:.1f} GB used).",
                {
                    "metric_name": "Memory Utilization",
                    "value": float(vm.percent),
                    "unit": "percent_0_100",
                    "used_bytes": int(vm.total - vm.available),
                    "total_bytes": int(vm.total),
                },
            )
        if check == "disk":
            disk = psutil.disk_usage("C:\\" if sys.platform == "win32" else "/")
            free_gb = disk.free / (1024 ** 3)
            total_gb = disk.total / (1024 ** 3)
            return FastPathResult(
                f"Primary Disk Utilization: {disk.percent:.1f}% ({free_gb:.1f} GB free of {total_gb:.1f} GB).",
                {
                    "metric_name": "Disk Utilization",
                    "value": float(disk.percent),
                    "unit": "percent_0_100",
                    "free_bytes": int(disk.free),
                    "total_bytes": int(disk.total),
                },
            )
        if check == "battery":
            battery = psutil.sensors_battery()
            if battery is None:
                return FastPathResult(
                    "Battery telemetry unavailable.",
                    {"metric_name": "Battery", "value": None, "unit": "percent_0_100"},
                )
            return FastPathResult(
                f"Battery Level: {battery.percent:.1f}%.",
                {"metric_name": "Battery Level", "value": float(battery.percent), "unit": "percent_0_100"},
            )
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

    return FastPathResult(registry.execute_tool("system_diagnostics", {"check": check}))


def match_system_telemetry(query: str) -> Optional[FastPathMatch]:
    q = query.strip()
    if _CPU_TEMPERATURE_RE.search(q):
        return FastPathMatch(
            intent="system_cpu_temperature",
            semantic_op_id="system.metrics.read",
            tool_name="system_diagnostics",
            arguments={"check": "cpu_temperature"},
            target_domain="system",
            direct_handler=lambda: _handle_system_diagnostics("cpu_temperature"),
        )
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
            arguments={"check": "battery"},
            target_domain="system",
            direct_handler=lambda: _handle_system_diagnostics("battery"),
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
    r"(?<!\w)(?:set\s+(?:the\s+)?volume\s+(?:to\s+)?(-?\d{1,3})%?|volume\s+(?:to\s+)?(-?\d{1,3})%?)(?!\w)",
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


def match_media_volume(query: str) -> Optional[FastPathMatch]:
    q = query.strip()
    m_num = _SET_VOL_NUM_RE.search(q)
    if m_num:
        val = int(m_num.group(1) or m_num.group(2))
        return FastPathMatch(
            intent="volume_set_percent",
            semantic_op_id="media.volume.set",
            tool_name="media_control",
            arguments={"action": "set_volume", "percent": val},
            target_domain="media",
        )
    if _MUTE_RE.search(q):
        return FastPathMatch(
            intent="volume_mute",
            semantic_op_id="media.mute.set",
            tool_name="media_control",
            arguments={"action": "mute"},
            target_domain="media",
        )
    if _UNMUTE_RE.search(q):
        return FastPathMatch(
            intent="volume_unmute",
            semantic_op_id="media.mute.set",
            tool_name="media_control",
            arguments={"action": "unmute"},
            target_domain="media",
        )
    if _VOL_UP_RE.search(q):
        return FastPathMatch(
            intent="volume_up",
            semantic_op_id="media.volume.adjust",
            tool_name="media_control",
            arguments={"action": "volume_up"},
            target_domain="media",
        )
    if _VOL_DOWN_RE.search(q):
        return FastPathMatch(
            intent="volume_down",
            semantic_op_id="media.volume.adjust",
            tool_name="media_control",
            arguments={"action": "volume_down"},
            target_domain="media",
        )
    if _PLAY_PAUSE_RE.search(q):
        return FastPathMatch(
            intent="media_play_pause",
            semantic_op_id="media.playback.toggle",
            tool_name="media_control",
            arguments={"action": "play_pause"},
            target_domain="media",
        )
    if _NEXT_TRACK_RE.search(q):
        return FastPathMatch(
            intent="media_next_track",
            semantic_op_id="media.playback.next",
            tool_name="media_control",
            arguments={"action": "next_track"},
            target_domain="media",
        )
    if _PREV_TRACK_RE.search(q):
        return FastPathMatch(
            intent="media_prev_track",
            semantic_op_id="media.playback.previous",
            tool_name="media_control",
            arguments={"action": "prev_track"},
            target_domain="media",
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


def match_windows_settings(query: str) -> Optional[FastPathMatch]:
    m = _SETTINGS_RE.match(query.strip())
    if not m:
        return None
    target = m.group(1).lower().strip()
    uri = _SETTINGS_MAP.get(target, "ms-settings:")
    return FastPathMatch(
        intent="open_windows_settings",
        semantic_op_id="system.settings.open",
        tool_name="open_windows_settings",
        arguments={"uri": uri, "name": target},
        target_domain="system",
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
    )


# ---------------------------------------------------------------------------
# 7. Weather, timers, and stopwatch
# ---------------------------------------------------------------------------
_WEATHER_QUERY_RE = re.compile(
    r"^\s*(?:charlie[,:]?\s*)?(?:(?:what(?:'s|\s+is)\s+)?(?:the\s+)?(?:current\s+)?"
    r"(?:weather|temperature)|how(?:'s|\s+is)\s+the\s+weather)"
    r"(?:\s+(?:today|now|currently))?(?:\s+(?:in|for)\s+(.+?))?"
    r"(?:\s+(?:today|now|currently|right\s+now))?\s*[.!?]*\s*$",
    re.IGNORECASE,
)
_WEATHER_MIXED_ACTION_RE = re.compile(
    r"\b(?:and\s+then|then|and)\s+(?:please\s+)?(?:show|open|display|find|plan|give|take|navigate|route|map)\b"
    r"|\b(?:by|via)\s+(?:public\s+)?(?:transit|bus|train|car|foot|walking|driving)\b",
    re.IGNORECASE,
)
_MINUTES = r"(?P<minutes>\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten)"
_DURATION_AMOUNT = r"\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten"
_REMIND_AFTER_RE = re.compile(
    rf"^\s*(?:charlie[,:]?\s*)?remind\s+me(?:\s+to\s+(?P<text>.+?))?\s+(?:in|after)\s+{_MINUTES}\s+minutes?\s*[.!?]*\s*$",
    re.IGNORECASE,
)
_TIMER_FOR_RE = re.compile(
    rf"^\s*(?:charlie[,:]?\s*)?(?:set|start)\s+(?:a\s+)?(?:timer\s+for\s+"
    rf"(?P<for_amount>{_DURATION_AMOUNT})\s+(?P<for_unit>seconds?|minutes?|hours?)|"
    rf"(?P<before_amount>{_DURATION_AMOUNT})\s+(?P<before_unit>seconds?|minutes?|hours?)\s+timer)"
    r"(?:\s+(?:named|called)\s+(?P<text>.+?))?\s*[.!?]*\s*$",
    re.IGNORECASE,
)
_TIMER_CANCEL_RE = re.compile(
    r"^\s*(?:charlie[,:]?\s*)?(?:cancel|stop)\s+(?:the\s+)?timer\s+([a-f0-9]{32})\s*[.!?]*\s*$",
    re.IGNORECASE,
)
_STOPWATCH_RE = re.compile(
    r"^\s*(?:charlie[,:]?\s*)?(?P<action>start|resume|stop|pause|reset|read|show|check)"
    r"\s+(?:(?:the|that)\s+)?stop\s*watch\s*[.!?]*\s*$|"
    r"^\s*(?:what(?:'s|\s+is)|how\s+long\s+has)\s+(?:the\s+)?(?:elapsed\s+time\s+on\s+)?"
    r"stop\s*watch(?:\s+been\s+running)?\s*\??\s*$",
    re.IGNORECASE,
)
_DASHBOARD_WIDGET_RE = re.compile(
    r"^\s*(?:charlie[,:]?\s*)?(?P<action>show|open|display|hide|close|dismiss|reopen|restore)\s+"
    r"(?:me\s+)?(?:(?:the|my)\s+)?(?P<target>.+?)\s*[.!?]*\s*$",
    re.IGNORECASE,
)
_MINUTE_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


def match_weather_timer_stopwatch(
    query: str, *, default_weather_location: Optional[str] = None
) -> Optional[FastPathMatch]:
    q = query.strip()
    match = _WEATHER_QUERY_RE.match(q)
    if match and not _WEATHER_MIXED_ACTION_RE.search(match.group(1) or ""):
        location = (match.group(1) or default_weather_location or "").strip().rstrip("?.!")
        if location:
            return FastPathMatch(
                intent="current_weather",
                semantic_op_id="weather.current.read",
                tool_name="weather_get",
                arguments={"location": location},
                target_domain="weather",
            )
        return FastPathMatch(
            intent="weather_location_required",
            semantic_op_id="weather.current.read",
            tool_name="",
            target_domain="weather",
            direct_handler=lambda: "Which city or location should I check the weather for?",
        )

    match = _REMIND_AFTER_RE.match(q)
    if match:
        amount = match.group("minutes").casefold()
        minutes = float(_MINUTE_WORDS[amount]) if amount in _MINUTE_WORDS else float(amount)
        delay_seconds = minutes * 60
        reminder = match.groupdict().get("text")
    else:
        match = _TIMER_FOR_RE.match(q)
        if not match:
            match = None
        else:
            amount = (match.group("for_amount") or match.group("before_amount")).casefold()
            amount_value = float(_MINUTE_WORDS[amount]) if amount in _MINUTE_WORDS else float(amount)
            unit = (match.group("for_unit") or match.group("before_unit")).casefold().rstrip("s")
            delay_seconds = amount_value * {"second": 1, "minute": 60, "hour": 3600}[unit]
            reminder = match.group("text")
    if match:
        return FastPathMatch(
            intent="create_timer",
            semantic_op_id="calendar.timer.create",
            tool_name="timer_create",
            arguments={"delay_seconds": delay_seconds, "text": reminder or "Timer finished"},
            target_domain="calendar",
        )

    match = _TIMER_CANCEL_RE.match(q)
    if match:
        return FastPathMatch(
            intent="cancel_timer",
            semantic_op_id="calendar.timer.cancel",
            tool_name="timer_cancel",
            arguments={"schedule_id": match.group(1)},
            target_domain="calendar",
        )

    match = _STOPWATCH_RE.match(q)
    if match:
        action = (match.groupdict().get("action") or "read").casefold()
        action = {"resume": "start", "pause": "stop", "show": "read", "check": "read"}.get(action, action)
        return FastPathMatch(
            intent=f"stopwatch_{action}",
            semantic_op_id="time.stopwatch.control",
            tool_name="stopwatch_control",
            arguments={"action": action},
            target_domain="tools",
        )
    return None


def last_explicit_weather_location(history: list[dict[str, Any]]) -> Optional[str]:
    for message in reversed(history):
        if message.get("role") != "user":
            continue
        match = _WEATHER_QUERY_RE.match(str(message.get("content") or "").strip())
        if not match or not match.group(1):
            continue
        location = match.group(1).strip().rstrip("?.!")
        if location and not _WEATHER_MIXED_ACTION_RE.search(location):
            return location
    return None


def match_dashboard_widget_visibility(query: str) -> Optional[FastPathMatch]:
    match = _DASHBOARD_WIDGET_RE.match(query)
    if not match:
        return None
    target_text = match.group("target").casefold().strip()
    has_widget_surface = bool(re.search(r"\s+(?:widgets?|cards?)\s*[.!?]*\s*$", target_text, re.I))
    target = target_text
    target = re.sub(r"\s+(?:widgets?|cards?)$", "", target)
    target = re.sub(r"^(?:all\s+)?", "", target)
    target = re.sub(r"\s+(?:again|back)$", "", target)
    target = re.sub(r"\s+", " ", target)
    widget = {
        "clock": "clock",
        "time": "clock",
        "weather": "weather",
        "temperature": "weather",
        "timer": "timer",
        "timers": "timer",
        "stopwatch": "stopwatch",
        "stop watch": "stopwatch",
        "music": "music",
        "media": "music",
        "music player": "music",
        "media player": "music",
        "now playing": "music",
        "system status": "system_status",
        "system health": "system_status",
    }.get(target)
    if widget is None or (widget != "clock" and not has_widget_surface):
        return None
    action = match.group("action").casefold()
    visible = action in {"show", "open", "display", "reopen", "restore"}
    return FastPathMatch(
        intent=f"{ 'show' if visible else 'hide' }_{widget}_widget",
        semantic_op_id="dashboard.widget.visibility",
        tool_name="dashboard_widget_control",
        arguments={"widget": widget, "visible": visible},
        target_domain="tools",
    )


# ---------------------------------------------------------------------------
# Master Fast-Path Matcher
# ---------------------------------------------------------------------------
def match_fast_path(
    query: str, *, default_weather_location: Optional[str] = None
) -> Optional[FastPathMatch]:
    """Pure matcher: returns FastPathMatch if query matches a deterministic intent, else None."""
    if not query or len(query.strip()) == 0:
        return None

    m = match_dashboard_widget_visibility(query)
    if m:
        return m

    m = match_close_window(query)
    if m:
        return m

    m = match_weather_timer_stopwatch(query, default_weather_location=default_weather_location)
    if m:
        return m

    # 1. Full system workspace before narrower telemetry matchers.
    m = match_system_workspace(query)
    if m:
        return m

    # 2. System Telemetry
    m = match_system_telemetry(query)
    if m:
        return m

    # 3. Media / Volume
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


def execute_fast_path(match: FastPathMatch) -> FastPathResult:
    """Execute a matched deterministic fast-path operation safely."""
    logger.info("Executing fast-path: intent=%s, op=%s", match.intent, match.semantic_op_id)
    if match.target_domain == "media":
        return FastPathResult(
            "Error: Media fast-paths require Brain's canonical media operation.",
            {"available": False, "reason": "Media fast-path execution is owned by Brain."},
        )
    if match.direct_handler is not None:
        try:
            result = match.direct_handler()
            return result if isinstance(result, FastPathResult) else FastPathResult(result)
        except Exception as e:
            logger.error("Fast-path direct execution failed: %s", e, exc_info=True)
            return f"Error executing {match.intent}: {e}"

    from charlie.tools import registry

    return FastPathResult(registry.execute_tool(match.tool_name, match.arguments))
