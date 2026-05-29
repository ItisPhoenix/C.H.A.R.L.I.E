"""
charlie/tools/android_controller.py

Android device control via ADB (Android Debug Bridge).
"""

import logging
import subprocess

from charlie.tools.tool_decorator import tool, RiskTier

logger = logging.getLogger("charlie.tools.android")


def _adb(args: list[str], timeout: int = 10) -> str:
    """Run an ADB command and return stdout."""
    try:
        result = subprocess.run(
            ["adb"] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return f"ADB error: {result.stderr.strip()}"
        return result.stdout.strip()
    except FileNotFoundError:
        return "ADB not found. Install Android Debug Bridge and add to PATH."
    except subprocess.TimeoutExpired:
        return f"ADB command timed out after {timeout}s"


@tool(
    name="android_battery",
    description="Get Android device battery status.",
    category="android",
    risk_tier=RiskTier.TIER_0,
)
def android_battery() -> str:
    """Get battery info from connected Android device."""
    output = _adb(["shell", "dumpsys", "battery"])
    if output.startswith("ADB") or output.startswith("Error"):
        return output
    # Parse key fields
    lines = output.split("\n")
    info = {}
    for line in lines:
        line = line.strip()
        if ":" in line:
            key, val = line.split(":", 1)
            info[key.strip()] = val.strip()
    level = info.get("level", "?")
    status = info.get("status", "?")
    plugged = info.get("plugged", "none")
    return f"Battery: {level}% | Status: {status} | Plugged: {plugged}"


@tool(
    name="android_screenshot",
    description="Capture a screenshot from the Android device.",
    category="android",
    risk_tier=RiskTier.TIER_0,
)
def android_screenshot(local_path: str = "charlie/scratch/android_screenshot.png") -> str:
    """Capture screenshot from Android device and save locally."""
    remote = "/sdcard/screenshot.png"
    _adb(["shell", "screencap", "-p", remote])
    result = _adb(["pull", remote, local_path])
    _adb(["shell", "rm", remote])
    return f"Screenshot saved to {local_path}: {result}"


@tool(
    name="android_tap",
    description="Tap on the Android screen at specific coordinates.",
    category="android",
    risk_tier=RiskTier.TIER_1,
)
def android_tap(x: int, y: int) -> str:
    """Tap at (x, y) on the Android screen."""
    _adb(["shell", "input", "tap", str(x), str(y)])
    return f"Tapped ({x}, {y})"


@tool(
    name="android_swipe",
    description="Swipe on the Android screen.",
    category="android",
    risk_tier=RiskTier.TIER_1,
)
def android_swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> str:
    """Swipe from (x1,y1) to (x2,y2) over duration_ms."""
    _adb(["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)])
    return f"Swiped ({x1},{y1}) -> ({x2},{y2}) in {duration_ms}ms"


@tool(
    name="android_open_app",
    description="Open an app on the Android device by package name.",
    category="android",
    risk_tier=RiskTier.TIER_1,
)
def android_open_app(package: str) -> str:
    """Launch an app by package name (e.g., com.android.chrome)."""
    _adb(["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"])
    return f"Opened {package}"


@tool(
    name="android_input_text",
    description="Type text on the Android device.",
    category="android",
    risk_tier=RiskTier.TIER_1,
)
def android_input_text(text: str) -> str:
    """Type text on the Android device."""
    # ADB input text doesn't handle spaces well, need to escape
    escaped = text.replace(" ", "%s").replace("'", "\\'")
    _adb(["shell", "input", "text", escaped])
    return f"Typed: {text[:50]}"


@tool(
    name="android_press_key",
    description="Press a key on the Android device.",
    category="android",
    risk_tier=RiskTier.TIER_1,
)
def android_press_key(keycode: str) -> str:
    """Press a key by Android keycode (e.g., KEYCODE_HOME, KEYCODE_BACK)."""
    _adb(["shell", "input", "keyevent", keycode])
    return f"Pressed {keycode}"


@tool(
    name="android_toggle_wifi",
    description="Toggle WiFi on/off on the Android device.",
    category="android",
    risk_tier=RiskTier.TIER_1,
)
def android_toggle_wifi(enable: bool) -> str:
    """Enable or disable WiFi."""
    state = "enable" if enable else "disable"
    _adb(["shell", "svc", "wifi", state])
    return f"WiFi {state}d"


@tool(
    name="android_toggle_bluetooth",
    description="Toggle Bluetooth on/off on the Android device.",
    category="android",
    risk_tier=RiskTier.TIER_1,
)
def android_toggle_bluetooth(enable: bool) -> str:
    """Enable or disable Bluetooth."""
    state = "enable" if enable else "disable"
    _adb(["shell", "svc", "bluetooth", state])
    return f"Bluetooth {state}d"


@tool(
    name="android_toggle_flashlight",
    description="Toggle the flashlight on/off on the Android device.",
    category="android",
    risk_tier=RiskTier.TIER_1,
)
def android_toggle_flashlight(enable: bool) -> str:
    """Enable or disable the flashlight."""
    if enable:
        _adb(["shell", "am", "start", "-a", "android.media.action.STILL_IMAGE_CAMERA"])
    else:
        _adb(["shell", "input", "keyevent", "KEYCODE_CAMERA"])
    return f"Flashlight {'enabled' if enable else 'disabled'}"


@tool(
    name="android_notifications",
    description="Get current notifications from the Android device.",
    category="android",
    risk_tier=RiskTier.TIER_0,
)
def android_notifications() -> str:
    """List current notifications."""
    output = _adb(["shell", "dumpsys", "notification", "--noredact"])
    if output.startswith("ADB") or output.startswith("Error"):
        return output
    # Extract notification titles
    notifications = []
    for line in output.split("\n"):
        line = line.strip()
        if "android.title=" in line:
            title = line.split("android.title=")[-1].strip()
            if title and title != "null":
                notifications.append(title)
    if not notifications:
        return "No active notifications"
    return f"Notifications ({len(notifications)}): " + ", ".join(notifications[:10])


@tool(
    name="android_list_apps",
    description="List installed apps on the Android device.",
    category="android",
    risk_tier=RiskTier.TIER_0,
)
def android_list_apps() -> str:
    """List third-party installed packages."""
    output = _adb(["shell", "pm", "list", "packages", "-3"])
    if output.startswith("ADB") or output.startswith("Error"):
        return output
    packages = [line.replace("package:", "") for line in output.split("\n") if line.startswith("package:")]
    return f"Installed apps ({len(packages)}): " + ", ".join(packages[:20])
