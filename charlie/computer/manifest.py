"""Charlie-owned bounded capability manifests for the Cua runtime.

Bounded mode requires an exact, launch-approved ceiling of tools and resources, and it
refuses to start without one. This module writes those ceilings.

Two scopes, deliberately separate
---------------------------------
Cua refuses to combine origin-scoped browsing with generic native tools: an
origin-scoped manifest that allows ``get_window_state`` is rejected outright, because
that tool bypasses the typed browser origin adapter. So the two capabilities cannot
share one ceiling:

``native``
    Exact approved application identities plus the native input tools. No origin
    scoping, because nothing browser-shaped is reachable through it.

``browser``
    The browser executable, an explicit origin list, and only the typed browser tools.
    ``browser.existing_profiles`` is never granted, so an authenticated profile stays
    refused by the runtime itself rather than by Charlie's judgement.

Because the ceilings differ, they need separate runtimes. That is a real change from
the earlier single-runtime design and is deliberate: sharing one runtime would mean
either dropping origin scoping or dropping native control.

Executable paths are resolved from the installed browser at runtime. Nothing here is a
guessed path: a missing browser resolves to nothing and the scope simply is not granted.
"""

from __future__ import annotations

import json
import os
import tempfile
import winreg
from pathlib import Path
from typing import Iterable, Optional

__all__ = [
    "MANIFEST_VERSION",
    "NATIVE_SCOPE",
    "BROWSER_SCOPE",
    "NATIVE_TOOLS",
    "BROWSER_TOOLS",
    "resolve_browser_executable",
    "manifest_body",
    "scope_manifest_path",
]

MANIFEST_VERSION = 2

NATIVE_SCOPE = "native"
BROWSER_SCOPE = "browser"

# Native window control. Names are Cua's canonical tool names; an unrecognised name
# makes the driver reject the whole manifest, which is the intended fail-closed path.
NATIVE_TOOLS = (
    "get_window_state",
    "bring_to_front",
    "invoke_menu",
    "double_click",
    "move_cursor",
    "click",
    "drag",
    "scroll",
    "set_window_frame",
    "set_value",
    "type_text",
    "press_key",
    "hotkey",
    "check_permissions",
)

# Typed browser surface only. No native input tools may appear here.
BROWSER_TOOLS = (
    "list_windows",
    "browser_prepare",
    "get_browser_state",
    "browser_navigate",
    "browser_click",
    "browser_type",
    "browser_pointer",
    "end_session",
    "check_permissions",
)

# Origins the browser scope may touch. ``about:blank`` must be present because the
# origin check runs against the page currently loaded, which is about:blank after an
# isolated launch.
DEFAULT_ORIGINS = ("about:blank",)

_EXPIRES_AFTER = "30m"
_IDLE_TIMEOUT = "5m"

# Browsers Charlie knows how to find, in preference order.
_KNOWN_BROWSERS = (
    ("chrome", r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
    ("brave", r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\brave.exe"),
)


def resolve_browser_executable(name: str) -> Optional[str]:
    """Resolve one installed browser to a canonical absolute path.

    Prefers the App Paths registry value, which is what Windows itself uses to launch
    the browser, then falls back to the conventional install location. Returns None
    when the browser is not installed; the caller then simply does not grant it.
    """
    key_path = dict(_KNOWN_BROWSERS).get(name.lower())
    if key_path:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                value, _ = winreg.QueryValueEx(key, None)
                if value:
                    resolved = _canonical(Path(value))
                    if resolved:
                        return resolved
        except OSError:
            pass

    relative = Path("Google/Chrome/Application/chrome.exe") if name == "chrome" else (
        Path("BraveSoftware/Brave-Browser/Application/brave.exe") if name == "brave" else None
    )
    if relative is None:
        return None
    for base in (
        os.getenv("PROGRAMFILES"),
        os.getenv("PROGRAMFILES(X86)"),
        os.getenv("LOCALAPPDATA"),
    ):
        if not base:
            continue
        resolved = _canonical(Path(base) / relative)
        if resolved:
            return resolved
    return None


def _canonical(path: Path) -> Optional[str]:
    try:
        if not path.is_file():
            return None
        return str(path.resolve(strict=True))
    except OSError:
        return None


def native_body(window_target: Optional[dict] = None) -> dict:
    """Ceiling for one exact native window, never its shared host application."""
    if not isinstance(window_target, dict):
        raise ValueError("native Cua requires one exact positive PID and window_id")
    pid = window_target.get("pid")
    window_id = window_target.get("window_id")
    if (
        not isinstance(pid, int)
        or isinstance(pid, bool)
        or pid <= 0
        or not isinstance(window_id, int)
        or isinstance(window_id, bool)
        or window_id <= 0
    ):
        raise ValueError("native Cua requires one exact positive PID and window_id")
    target = {"pid": pid, "window_id": window_id}
    return {
        "version": MANIFEST_VERSION,
        "mode": "bounded",
        "expires_after": _EXPIRES_AFTER,
        "idle_timeout": _IDLE_TIMEOUT,
        "allow": {"tools": list(NATIVE_TOOLS)},
        "resources": {
            "desktop": {"display": False, "windows": [target]},
        },
    }


def browser_body(executable: Optional[str], origins: Iterable[str] = DEFAULT_ORIGINS) -> dict:
    """Ceiling for isolated, origin-scoped browsing.

    ``existing_profiles`` is intentionally absent. ``windows: "all"`` is the only value
    the installed schema accepts, so the executable is what scopes the grant.
    """
    resources: dict = {
        "desktop": {"display": True, "windows": []},
        "browser": {"profiles": [], "origins": list(origins)},
    }
    if executable:
        resources["apps"] = [{"executable": executable, "windows": "all"}]
    return {
        "version": MANIFEST_VERSION,
        "mode": "bounded",
        "expires_after": _EXPIRES_AFTER,
        "idle_timeout": _IDLE_TIMEOUT,
        "allow": {"tools": list(BROWSER_TOOLS)},
        "resources": resources,
    }


def manifest_body(
    scope: str,
    *,
    origins: tuple[str, ...] = DEFAULT_ORIGINS,
    window_target: Optional[dict] = None,
) -> dict:
    """Build the ceiling for one scope, resolving paths from the installed machine."""
    if scope == BROWSER_SCOPE:
        return browser_body(resolve_browser_executable("chrome"), origins)
    if scope == NATIVE_SCOPE:
        return native_body(window_target)
    raise ValueError(f"unknown manifest scope: {scope!r}")


def scope_manifest_path(
    scope: str,
    *,
    origins: tuple[str, ...] = DEFAULT_ORIGINS,
    window_target: Optional[dict] = None,
    directory: Optional[str] = None,
) -> Optional[str]:
    """Write one scope's ceiling and return its path, or None if it cannot be written."""
    body = manifest_body(scope, origins=origins, window_target=window_target)
    base = (
        Path(directory)
        if directory
        else Path(os.getenv("LOCALAPPDATA") or tempfile.gettempdir()) / "Charlie" / "cua"
    )
    try:
        base.mkdir(parents=True, exist_ok=True)
        path = base / f"bounded_manifest_{scope}.json"
        serialised = json.dumps(body, indent=2, sort_keys=True)
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current != serialised:
            path.write_text(serialised, encoding="utf-8")
        return str(path)
    except OSError:
        return None
