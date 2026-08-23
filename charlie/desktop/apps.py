"""Runtime Windows application discovery.

The alias registry remains a fast hint. This module answers what is available
now, without building a permanent installed-application database.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class AppResolution:
    name: str
    launch_target: Optional[str] = None
    process_name: Optional[str] = None
    window_title: Optional[str] = None
    source: str = "runtime"


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _matching_process(name: str) -> Optional[AppResolution]:
    try:
        import psutil

        wanted = _normalize(name)
        for process in psutil.process_iter(["name"]):
            process_name = str(process.info.get("name") or "")
            stem = Path(process_name).stem
            normalized = _normalize(stem)
            if normalized and (normalized == wanted or wanted in normalized or normalized in wanted):
                return AppResolution(name=name, process_name=process_name, source="running-process")
    except Exception:
        return None
    return None


def _path_target(name: str) -> Optional[str]:
    candidates = (name, name.replace(" ", ""), f"{name}.exe")
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def _app_paths_target(name: str) -> Optional[str]:
    if sys.platform != "win32":
        return None
    try:
        import winreg

        wanted = _normalize(name)
        roots = (
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\App Paths"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\App Paths"),
        )
        for root, base in roots:
            try:
                with winreg.OpenKey(root, base) as key:
                    for index in range(winreg.QueryInfoKey(key)[0]):
                        subkey_name = winreg.EnumKey(key, index)
                        if wanted not in _normalize(Path(subkey_name).stem):
                            continue
                        with winreg.OpenKey(key, subkey_name) as subkey:
                            target = winreg.QueryValue(subkey, None)
                        if target:
                            return str(target)
            except OSError:
                continue
    except (ImportError, OSError):
        return None
    return None


def _start_menu_target(name: str) -> Optional[str]:
    if sys.platform != "win32":
        return None
    wanted = _normalize(name)
    roots = [
        Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
        Path(os.environ.get("ProgramData", "")) / "Microsoft/Windows/Start Menu/Programs",
    ]
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for index, candidate in enumerate(root.rglob("*")):
                if index >= 500:
                    break
                if candidate.suffix.lower() not in {".lnk", ".url"}:
                    continue
                if wanted in _normalize(candidate.stem):
                    return str(candidate)
        except OSError:
            continue
    return None


def resolve_local_app(name: str) -> Optional[AppResolution]:
    """Find a currently available app/window using live OS sources."""
    clean = name.strip().strip("\"'")
    if not clean or sys.platform != "win32":
        return None

    try:
        from charlie.desktop.windows import find_window

        window = find_window(clean)
    except Exception:
        window = None
    if window:
        return AppResolution(name=clean, window_title=window["title"], source="visible-window")

    running = _matching_process(clean)
    if running:
        return running

    target = _path_target(clean)
    if target:
        return AppResolution(name=clean, launch_target=target, process_name=Path(target).name, source="path")

    target = _app_paths_target(clean)
    if target:
        return AppResolution(name=clean, launch_target=target, process_name=Path(target).name, source="app-paths")

    target = _start_menu_target(clean)
    if target:
        return AppResolution(name=clean, launch_target=target, source="start-menu")
    return None


def launch_and_verify(resolution: AppResolution, timeout_s: float = 3.0) -> bool:
    """Launch/focus one resolved target and verify process/window evidence."""
    if resolution.window_title:
        from charlie.desktop.windows import find_window, focus_window

        focus_window(resolution.window_title)
        return find_window(resolution.window_title) is not None
    if not resolution.launch_target:
        return False
    try:
        target = resolution.launch_target
        if Path(target).suffix.lower() in {".lnk", ".url"}:
            os.startfile(target)  # type: ignore[attr-defined]
            process = None
        else:
            process = subprocess.Popen([target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if process is not None and process.poll() is None:
            return True
        current = resolve_local_app(resolution.name)
        if current and (current.window_title or current.process_name):
            return True
        time.sleep(0.1)
    return False
