"""
charlie/utils/autostart.py

Windows Startup folder shortcut management for Charlie daemon.
Default OFF — user opts in via settings, tray menu, or first-run prompt.
"""

import json
import os
import sys
from pathlib import Path

from charlie.utils.logger import get_logger

logger = get_logger("Autostart")

# Path to Windows Startup folder
STARTUP_DIR = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"

# Config key
CONFIG_FILE = Path(__file__).parent.parent.parent / "charlie_config.json"
SHORTCUT_NAME = "CharlieDaemon.lnk"


def _get_daemon_path() -> str:
    """Get the path to charlie-daemon.exe or charlie-daemon.py."""
    # Check for packaged exe first
    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys.executable).parent
        daemon_exe = exe_dir / "charlie-daemon.exe"
        if daemon_exe.exists():
            return str(daemon_exe)

    # Fall back to script
    root_dir = Path(__file__).parent.parent.parent
    daemon_py = root_dir / "charlie-daemon.py"
    if daemon_py.exists():
        venv_python = root_dir / ".venv" / "Scripts" / "python.exe"
        if venv_python.exists():
            return f'"{venv_python}" "{daemon_py}"'
        return f'"{sys.executable}" "{daemon_py}"'

    return ""


def _load_config() -> dict:
    """Load charlie_config.json."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_config(config: dict):
    """Save charlie_config.json."""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        logger.error("config_save_failed", error=str(e))


def is_enabled() -> bool:
    """Check if auto-start is enabled in config."""
    config = _load_config()
    return config.get("startup", {}).get("auto_start_daemon", False)


def enable():
    """Enable auto-start: create shortcut in Windows Startup folder."""
    if not STARTUP_DIR.exists():
        logger.warning("startup_folder_not_found")
        return False

    daemon_path = _get_daemon_path()
    if not daemon_path:
        logger.warning("daemon_path_not_found")
        return False

    shortcut_path = STARTUP_DIR / SHORTCUT_NAME

    try:
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(str(shortcut_path))
        if daemon_path.endswith(".exe"):
            shortcut.Targetpath = daemon_path
        else:
            # Python script — use pythonw to avoid console window
            parts = daemon_path.split('" "')
            shortcut.Targetpath = parts[0].strip('"')
            if len(parts) > 1:
                shortcut.Arguments = parts[1].strip('"')
        shortcut.WorkingDirectory = str(Path(__file__).parent.parent.parent)
        shortcut.save()
        logger.info("autostart_enabled")
    except ImportError:
        # Fallback: create a .bat file
        bat_path = STARTUP_DIR / "CharlieDaemon.bat"
        with open(bat_path, "w") as f:
            f.write(f'@echo off\nstart "" {daemon_path}\n')
        logger.info("autostart_enabled_via_bat")
    except Exception as e:
        logger.error("autostart_enable_failed", error=str(e))
        return False

    # Update config
    config = _load_config()
    if "startup" not in config:
        config["startup"] = {}
    config["startup"]["auto_start_daemon"] = True
    _save_config(config)
    return True


def disable():
    """Disable auto-start: remove shortcut from Windows Startup folder."""
    shortcut_path = STARTUP_DIR / SHORTCUT_NAME
    bat_path = STARTUP_DIR / "CharlieDaemon.bat"

    removed = False
    if shortcut_path.exists():
        try:
            shortcut_path.unlink()
            removed = True
        except Exception as e:
            logger.error("shortcut_remove_failed", error=str(e))

    if bat_path.exists():
        try:
            bat_path.unlink()
            removed = True
        except Exception as e:
            logger.error("bat_remove_failed", error=str(e))

    if removed:
        logger.info("autostart_disabled")

    # Update config
    config = _load_config()
    if "startup" not in config:
        config["startup"] = {}
    config["startup"]["auto_start_daemon"] = False
    _save_config(config)
    return removed


def sync_shortcut():
    """Sync shortcut with config setting. Called on daemon startup."""
    if is_enabled():
        if not (STARTUP_DIR / SHORTCUT_NAME).exists():
            enable()
    else:
        if (STARTUP_DIR / SHORTCUT_NAME).exists():
            disable()
