"""
C.H.A.R.L.I.E. — Universal App Controller
Provides cross-platform app launching, management, and interaction capabilities.
"""

import platform
import subprocess
from typing import Any, Dict

import psutil
import pygetwindow as gw

from charlie.security.tiers import RiskTier, risk_tier
from charlie.utils.logger import get_logger

logger = get_logger("AppController")


class UniversalAppController:
    """Cross-platform application management and control."""

    def __init__(self):
        self.system = platform.system().lower()
        self._app_registry = self._build_app_registry()

    def _build_app_registry(self) -> Dict[str, Dict[str, Any]]:
        """Builds a registry of common applications with their launch commands."""
        registry = {}

        if self.system == "windows":
            registry.update({
                "chrome": {"cmd": "chrome.exe", "args": [], "display": "Google Chrome"},
                "firefox": {"cmd": "firefox.exe", "args": [], "display": "Mozilla Firefox"},
                "edge": {"cmd": "msedge.exe", "args": [], "display": "Microsoft Edge"},
                "notepad": {"cmd": "notepad.exe", "args": [], "display": "Notepad"},
                "calculator": {"cmd": "calc.exe", "args": [], "display": "Calculator"},
                "explorer": {"cmd": "explorer.exe", "args": [], "display": "File Explorer"},
                "cmd": {"cmd": "cmd.exe", "args": [], "display": "Command Prompt"},
                "powershell": {"cmd": "powershell.exe", "args": [], "display": "PowerShell"},
                "vscode": {"cmd": "code.cmd", "args": [], "display": "Visual Studio Code"},
                "word": {"cmd": "winword.exe", "args": [], "display": "Microsoft Word"},
                "excel": {"cmd": "excel.exe", "args": [], "display": "Microsoft Excel"},
                "powerpoint": {"cmd": "powerpnt.exe", "args": [], "display": "Microsoft PowerPoint"},
                "spotify": {"cmd": "spotify.exe", "args": [], "display": "Spotify"},
                "discord": {"cmd": "discord.exe", "args": [], "display": "Discord"},
                "slack": {"cmd": "slack.exe", "args": [], "display": "Slack"},
                "zoom": {"cmd": "zoom.exe", "args": [], "display": "Zoom"},
                "teams": {"cmd": "teams.exe", "args": [], "display": "Microsoft Teams"},
            })
        elif self.system == "darwin":  # macOS
            registry.update({
                "chrome": {"cmd": "open", "args": ["-a", "Google Chrome"], "display": "Google Chrome"},
                "firefox": {"cmd": "open", "args": ["-a", "Firefox"], "display": "Mozilla Firefox"},
                "safari": {"cmd": "open", "args": ["-a", "Safari"], "display": "Safari"},
                "vscode": {"cmd": "open", "args": ["-a", "Visual Studio Code"], "display": "Visual Studio Code"},
                "terminal": {"cmd": "open", "args": ["-a", "Terminal"], "display": "Terminal"},
                "spotify": {"cmd": "open", "args": ["-a", "Spotify"], "display": "Spotify"},
                "discord": {"cmd": "open", "args": ["-a", "Discord"], "display": "Discord"},
                "slack": {"cmd": "open", "args": ["-a", "Slack"], "display": "Slack"},
            })
        else:  # Linux
            registry.update({
                "chrome": {"cmd": "google-chrome", "args": [], "display": "Google Chrome"},
                "firefox": {"cmd": "firefox", "args": [], "display": "Mozilla Firefox"},
                "vscode": {"cmd": "code", "args": [], "display": "Visual Studio Code"},
                "terminal": {"cmd": "gnome-terminal", "args": [], "display": "Terminal"},
                "spotify": {"cmd": "spotify", "args": [], "display": "Spotify"},
                "discord": {"cmd": "discord", "args": [], "display": "Discord"},
            })

        return registry

    @risk_tier(RiskTier.TIER_0)
    def launch_app(self, args: Dict[str, Any]) -> str:
        """Launch an application by name."""
        app_name = args.get("name", "").strip().lower()
        if not app_name:
            return "No application name provided."

        # Try exact match first
        if app_name in self._app_registry:
            app_config = self._app_registry[app_name]
            try:
                cmd = [app_config["cmd"]] + app_config["args"]
                subprocess.Popen(cmd, shell=False)
                return f"Launched {app_config['display']}."
            except Exception as e:
                return f"Failed to launch {app_config['display']}: {e}"

        # Try fuzzy matching
        for key, config in self._app_registry.items():
            if app_name in key or key in app_name:
                try:
                    cmd = [config["cmd"]] + config["args"]
                    subprocess.Popen(cmd, shell=False)
                    return f"Launched {config['display']}."
                except Exception as e:
                    return f"Failed to launch {config['display']}: {e}"

        # Fallback to system command
        try:
            if self.system == "windows":
                subprocess.Popen(["cmd", "/c", "start", app_name], shell=False)
            elif self.system == "darwin":
                subprocess.Popen(["open", "-a", app_name], shell=False)
            else:
                subprocess.Popen([app_name], shell=False)
            return f"Attempted to launch '{app_name}'."
        except Exception as e:
            return f"Failed to launch '{app_name}': {e}"

    @risk_tier(RiskTier.TIER_1)
    def close_app(self, args: Dict[str, Any]) -> str:
        """Close an application by name (tree-kill)."""
        app_name = args.get("name", "").strip().lower()
        if not app_name:
            return "No application name provided."

        try:
            if self.system == "windows":
                # Tree-kill for Windows
                result = subprocess.run(
                    ["taskkill", "/F", "/T", "/IM", f"{app_name}*" if not app_name.endswith('.exe') else app_name],
                    capture_output=True, text=True, check=False
                )
                if result.returncode == 0:
                    return f"Successfully closed app tree: {app_name}."
                return f"Close failed: {result.stderr.strip()}"

            else:
                # Fallback for Linux/macOS
                closed_count = 0
                for proc in psutil.process_iter(['pid', 'name']):
                    try:
                        if app_name.lower() in proc.info['name'].lower():
                            proc.terminate()
                            closed_count += 1
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                return f"Closed {closed_count} instances."
        except Exception as e:
            return f"Failed to close '{app_name}': {e}"

    @risk_tier(RiskTier.TIER_0)
    def list_running_apps(self, args: Dict[str, Any] = None) -> str:
        """List currently running applications."""
        try:
            apps = []
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    name = proc.info['name']
                    if name and not name.startswith(('System', 'svchost', 'csrss', 'lsass')):
                        apps.append(name)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            # Remove duplicates and sort
            unique_apps = sorted(list(set(apps)))
            return f"Running applications ({len(unique_apps)}):\n" + "\n".join(unique_apps[:20])  # Limit to 20
        except Exception as e:
            return f"Failed to list running apps: {e}"

    @risk_tier(RiskTier.TIER_0)
    def focus_window(self, args: Dict[str, Any]) -> str:
        """Focus/bring to front a specific application window."""
        app_name = args.get("name", "").strip().lower()
        if not app_name:
            return "No application name provided."

        try:
            windows = gw.getWindowsWithTitle('')
            for window in windows:
                if app_name in window.title.lower() and window.visible:
                    window.activate()
                    return f"Focused window: {window.title}"
            return f"No visible window found for '{app_name}'."
        except Exception as e:
            return f"Failed to focus window: {e}"

    @risk_tier(RiskTier.TIER_1)
    def kill_process(self, args: Dict[str, Any]) -> str:
        """Force kill a process by name."""
        proc_name = args.get("name", "").strip().lower()
        if not proc_name:
            return "No process name provided."

        try:
            killed_count = 0
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    if proc_name.lower() in proc.info['name'].lower():
                        proc.kill()
                        killed_count += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            if killed_count > 0:
                return f"Force killed {killed_count} process(es) matching '{proc_name}'."
            else:
                return f"No processes found matching '{proc_name}'."
        except Exception as e:
            return f"Failed to kill process: {e}"
