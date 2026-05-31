"""System monitoring tools — process list, disk usage, VRAM, uptime."""

import os
import platform
import subprocess
import time

from charlie.tools.tool_decorator import tool, RiskTier


@tool(
    name="get_pc_status",
    description="Get system status including CPU, memory, disk usage",
    category="system",
)
def get_pc_status() -> str:
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        return (
            f"CPU: {cpu}% | "
            f"Memory: {mem.percent}% ({mem.used // (1024**3)}GB/{mem.total // (1024**3)}GB) | "
            f"Disk: {disk.percent}% ({disk.used // (1024**3)}GB/{disk.total // (1024**3)}GB)"
        )
    except ImportError:
        return "psutil not available"


@tool(
    name="get_active_processes",
    description="List top processes by CPU or memory usage",
    category="system",
)
def get_active_processes(sort_by: str = "cpu", count: int = 10) -> str:
    """Return top processes sorted by cpu or memory."""
    try:
        import psutil
        procs = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
            try:
                info = p.info
                procs.append(info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        key = "cpu_percent" if sort_by == "cpu" else "memory_percent"
        procs.sort(key=lambda x: x.get(key, 0) or 0, reverse=True)

        lines = [f"Top {count} processes by {sort_by}:"]
        for p in procs[:count]:
            lines.append(
                f"  {p['pid']:>6} {p['name']:<30} CPU:{p.get('cpu_percent', 0):>5.1f}% MEM:{p.get('memory_percent', 0):>5.1f}%"
            )
        return "\n".join(lines)
    except ImportError:
        return "psutil not available"


@tool(
    name="get_system_uptime",
    description="Get system uptime",
    category="system",
)
def get_system_uptime() -> str:
    """Return system uptime as a human-readable string."""
    try:
        import psutil
        uptime = time.time() - psutil.boot_time()
        hours = int(uptime // 3600)
        minutes = int((uptime % 3600) // 60)
        return f"Uptime: {hours}h {minutes}m"
    except ImportError:
        return "psutil not available"


@tool(
    name="get_vram_usage",
    description="Get GPU VRAM usage (NVIDIA only)",
    category="system",
)
def get_vram_usage() -> str:
    """Return GPU VRAM usage if available."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            used, total = result.stdout.strip().split(",")
            return f"VRAM: {used.strip()}MB / {total.strip()}MB"
        return "nvidia-smi not available"
    except Exception as e:
        return f"GPU info unavailable: {e}"


@tool(
    name="run_command",
    description="Execute a shell command and return output",
    risk_tier=RiskTier.TIER_2,
    category="system",
)
def run_command(command: str, timeout: int = 30) -> str:
    """Execute a shell command with timeout."""
    from charlie.utils.command_validator import validate_command
    try:
        validate_command(command)
    except ValueError as e:
        return str(e)
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout,
        )
        output = result.stdout
        if result.stderr:
            output += f"\nSTDERR: {result.stderr}"
        return output[:2000]  # Truncate
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s"
    except Exception as e:
        return f"Command failed: {e}"


@tool(
    name="open_app",
    description="Open an application by name",
    risk_tier=RiskTier.TIER_2,
    category="system",
)
def open_app(app_name: str) -> str:
    # Block path traversal
    if ".." in app_name or "/" in app_name or "\\" in app_name:
        return "Error: Invalid application name."
    # Block dangerous file types (safety_guard check — .lnk, .url, .pif, .hta)
    from charlie.security.safety_guard import check_dangerous_file_type

    if check_dangerous_file_type(app_name):
        return (
            f"Error: Cannot open '{app_name}' — dangerous file type. "
            "Files with extensions .lnk, .url, .pif, .hta can execute arbitrary code."
        )
    # Block dangerous executable types
    dangerous_exts = {".exe", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".msi", ".com", ".scr"}
    from pathlib import Path
    p = Path(app_name)
    if p.suffix.lower() in dangerous_exts:
        return f"Error: Cannot open executable files directly ({p.suffix}). Use run_command instead."
    try:
        if platform.system() == "Windows":
            os.startfile(app_name)
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", "-a", app_name])
        else:
            subprocess.Popen([app_name])
        return f"Opened: {app_name}"
    except Exception as e:
        return f"Failed to open {app_name}: {e}"


@tool(
    name="open_website",
    description="Open a URL in the default browser",
    category="system",
)
def open_website(url: str) -> str:
    """Open a URL in the default browser."""
    import webbrowser
    # Validate URL scheme to prevent javascript: or file: attacks
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        webbrowser.open(url)
        return f"Opened: {url}"
    except Exception as e:
        return f"Failed to open {url}: {e}"
