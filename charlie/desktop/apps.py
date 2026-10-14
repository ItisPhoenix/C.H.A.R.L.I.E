"""Runtime Windows application discovery.

The alias registry remains a fast hint. This module answers what is available
now, without building a permanent installed-application database.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from charlie.known_apps import APP_REGISTRY
from charlie.text_utils import format_app_list
from charlie.utils import is_process_running

logger = logging.getLogger("charlie.desktop.apps")


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
        os.startfile(target)  # type: ignore[attr-defined]
    except OSError:
        return False

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        current = resolve_local_app(resolution.name)
        if current and current.window_title:
            return True
        time.sleep(0.1)
    return False


def _wait_for_process_exit(process_name: str, timeout_s: float = 0.75) -> bool:
    """Allow Windows' asynchronous task termination to settle before judging it."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            if not is_process_running(process_name):
                return True
        except Exception:
            return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _basename(value: str) -> str:
    return value.replace("\\", "/").rsplit("/", 1)[-1].casefold()


def _close_window_targets(app: str, titles: tuple[str, ...], process: str = "") -> list[dict]:
    """Live window identities matching an app's resolved titles or exact process name."""
    try:
        from charlie.desktop.windows import list_windows, window_identity, window_target_is_current

        wanted_titles = tuple(title.strip().casefold() for title in titles if title.strip())
        wanted_process = _basename(process)
        matches: list[dict] = []
        for item in list_windows():
            hwnd = int(item.get("hwnd") or 0)
            title = str(item.get("title") or "")
            if not hwnd or not title:
                continue
            identity = window_identity(hwnd, title)
            if identity is None or not window_target_is_current(identity):
                continue
            by_title = any(wanted in title.casefold() for wanted in wanted_titles)
            by_process = bool(wanted_process) and wanted_process in {
                _basename(str(identity.get("process_name") or "")),
                _basename(str(identity.get("executable") or "")),
            }
            if by_title or by_process:
                matches.append(identity)
        return matches
    except Exception:
        logger.warning("Close target resolution failed for %s (%s)", app, process, exc_info=True)
        return []


def _wait_for_instance_exit(identity: dict, timeout_s: float = 0.75) -> bool:
    """Wait for the exact resolved PID to terminate, not for a matching name to disappear."""
    import psutil

    pid = int(identity["pid"])
    created = float(identity.get("create_time") or 0.0)
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            alive = not created or abs(psutil.Process(pid).create_time() - created) < 0.01
        except psutil.NoSuchProcess:
            return True
        except Exception:
            alive = True
        if not alive:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def close_apps(
    matched_apps: list[str], launched_processes: list[str], *, approved: bool = False
) -> str:
    """Close resolved apps and report per-app process/window verification."""
    if sys.platform != "win32":
        return f"App closing is only supported on Windows (detected {sys.platform})."
    if not approved:
        return f"Closing {format_app_list([str(app) for app in matched_apps])} needs approval; nothing was closed."

    success_apps: list[str] = []
    not_running_apps: list[str] = []
    failed_apps: list[str] = []
    ambiguous_apps: list[str] = []
    for index, app in enumerate(matched_apps):
        key = str(app).strip().casefold()
        entry = APP_REGISTRY.get(key)
        if entry is None or not entry.close_processes:
            failed_apps.append(str(app))
            continue

        window_closed: Optional[bool] = None
        if entry.close_window_titles:
            matches = _close_window_targets(str(app), entry.close_window_titles)
            if len(matches) > 1:
                ambiguous_apps.append(str(app))
                continue
            if matches:
                from charlie.desktop import windows as desktop_windows

                title = str(matches[0].get("title") or "")
                logger.info("Closing %s through resolved window identity %r", app, title)
                try:
                    window_closed = desktop_windows.close_window_and_verify(title)
                except Exception:
                    logger.warning("Window close failed for %s", app, exc_info=True)
                    window_closed = False
        if window_closed is True:
            success_apps.append(str(app))
            continue
        if window_closed is False:
            failed_apps.append(str(app))
            continue

        candidates = entry.close_processes
        fallback = launched_processes[index] if index < len(launched_processes) else None
        if fallback in candidates:
            candidates = (fallback, *(candidate for candidate in candidates if candidate != fallback))
        closed = False
        failed = False
        for process in candidates:
            matches = _close_window_targets(str(app), (), process)
            if len(matches) > 1:
                logger.info("Refusing to close %s: %d live instances match", app, len(matches))
                ambiguous_apps.append(str(app))
                break
            target = matches[0] if matches else None
            if target is None:
                try:
                    running = is_process_running(process)
                except Exception:
                    running = False
                if running:
                    logger.info("Refusing to close %s: %s runs without one identifiable window", app, process)
                    failed = True
                    break
                continue
            try:
                result = subprocess.run(
                    ["taskkill", "/IM", process, "/F"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                stderr = (result.stderr or "").lower()
                if result.returncode == 0:
                    if _wait_for_instance_exit(target):
                        closed = True
                    else:
                        failed = True
                    break
                if "not found" in stderr or result.returncode == 128:
                    continue
                failed = True
                break
            except Exception as exc:
                logger.error("Failed to taskkill %s (%s): %s", app, process, exc, exc_info=True)
                failed = True
                break
        if closed:
            success_apps.append(str(app))
        elif failed:
            failed_apps.append(str(app))
        elif str(app) not in ambiguous_apps:
            not_running_apps.append(str(app))

    parts: list[str] = []
    if success_apps:
        parts.append(f"{format_app_list(success_apps)} has been closed for you.")
    if not_running_apps:
        parts.append(f"{format_app_list(not_running_apps)} is not currently running.")
    if ambiguous_apps:
        parts.append(
            f"I left {format_app_list(ambiguous_apps)} open because more than one instance matched."
        )
    if failed_apps:
        parts.append(f"Failed to close {format_app_list(failed_apps)}.")
    return " ".join(parts) or "Error: no app was provided to close."


def launch_apps(matched_apps: list[str], launched_commands: Optional[list[str]] = None) -> str:
    """Focus or launch resolved local apps and verify the resulting host state."""
    if sys.platform != "win32":
        return f"App launching is only supported on Windows (detected {sys.platform})."

    success_apps: list[str] = []
    already_open_apps: list[str] = []
    failed_apps: list[tuple[str, str]] = []
    for app in matched_apps:
        name = str(app).strip()
        key = name.casefold()
        entry = APP_REGISTRY.get(key)
        if entry is None or entry.is_website:
            resolution = resolve_local_app(name)
            if resolution is None or not launch_and_verify(resolution):
                failed_apps.append((name, "runtime-verification-failed"))
            else:
                already_open = bool(resolution.window_title or resolution.process_name)
                (already_open_apps if already_open else success_apps).append(name)
            continue

        process_name = entry.close_process
        if process_name and is_process_running(process_name):
            from charlie.desktop.windows import focus_window

            focus_window(process_name.removesuffix(".exe"))
            already_open_apps.append(name)
            continue

        resolution = resolve_local_app(name)
        if resolution and (resolution.launch_target or resolution.window_title):
            already_open = bool(resolution.window_title or resolution.process_name)
            if launch_and_verify(resolution):
                (already_open_apps if already_open else success_apps).append(name)
            else:
                failed_apps.append((name, "runtime-verification-failed"))
            continue

        try:
            launched = launch_and_verify(
                AppResolution(name=name, launch_target=entry.open_cmd, process_name=entry.close_process)
            )
        except Exception as exc:
            logger.debug("App launch failed for %s: %s", name, exc)
            launched = False
        if launched:
            success_apps.append(name)
        else:
            failed_apps.append((name, "runtime-verification-failed"))

    if not success_apps and not already_open_apps:
        failed_names = [f"{name} ({error})" for name, error in failed_apps]
        return f"I could not open {', '.join(failed_names)}."

    parts: list[str] = []
    if success_apps:
        parts.append(f"I've opened {format_app_list(success_apps)} for you.")
    if already_open_apps:
        parts.append(f"{format_app_list(already_open_apps)} was already open -- switched to it.")
    if failed_apps:
        parts.append(f"(Failed to open: {format_app_list([name for name, _ in failed_apps])})")
    return " ".join(parts)


def open_url_in_default_browser(url: str) -> bool:
    """Open one validated URL through Windows' default browser association."""
    if sys.platform != "win32":
        return False
    from charlie.known_apps import resolve_website_url

    normalized = resolve_website_url(url)
    if normalized is None:
        return False
    try:
        os.startfile(normalized)  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False
    return True
