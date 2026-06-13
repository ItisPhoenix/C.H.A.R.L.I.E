"""
charlie/tools/desktop_automation.py

Desktop automation tools for mouse, keyboard, and window control.
"""

import logging

try:
    import pyautogui
except ImportError:
    pyautogui = None

from charlie.tools.tool_decorator import tool, RiskTier

logger = logging.getLogger("charlie.tools.desktop")
if pyautogui is None:
    logger.warning("pyautogui not installed — desktop automation tools will return errors")
else:
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05


@tool(
    name="mouse_click",
    description="Click the mouse at specific screen coordinates.",
    category="desktop",
    risk_tier=RiskTier.TIER_1,
)
def mouse_click(x: int, y: int, button: str = "left", clicks: int = 1) -> str:
    """Click at (x, y). button: 'left', 'right', or 'middle'."""
    pyautogui.click(x, y, button=button, clicks=clicks)
    return f"Clicked {button} at ({x}, {y})"


@tool(
    name="mouse_move",
    description="Move the mouse cursor to specific screen coordinates.",
    category="desktop",
    risk_tier=RiskTier.TIER_0,
)
def mouse_move(x: int, y: int, duration: float = 0.2) -> str:
    """Move cursor to (x, y) over duration seconds."""
    pyautogui.moveTo(x, y, duration=duration)
    return f"Moved cursor to ({x}, {y})"


@tool(
    name="mouse_scroll",
    description="Scroll the mouse wheel.",
    category="desktop",
    risk_tier=RiskTier.TIER_0,
)
def mouse_scroll(clicks: int = 3, x: int | None = None, y: int | None = None) -> str:
    """Scroll wheel. Positive = up, negative = down."""
    if x is not None and y is not None:
        pyautogui.scroll(clicks, x=x, y=y)
    else:
        pyautogui.scroll(clicks)
    direction = "up" if clicks > 0 else "down"
    return f"Scrolled {direction} {abs(clicks)} clicks"


@tool(
    name="keyboard_type",
    description="Type text on the keyboard.",
    category="desktop",
    risk_tier=RiskTier.TIER_1,
)
def keyboard_type(text: str, interval: float = 0.02) -> str:
    """Type the given text character by character."""
    pyautogui.typewrite(text, interval=interval)
    return f"Typed {len(text)} characters"


@tool(
    name="keyboard_hotkey",
    description="Press a keyboard shortcut (e.g., ctrl+c, alt+tab).",
    category="desktop",
    risk_tier=RiskTier.TIER_1,
)
def keyboard_hotkey(*keys: str) -> str:
    """Press a hotkey combination. Pass keys as arguments."""
    pyautogui.hotkey(*keys)
    return f"Pressed {'+'.join(keys)}"


@tool(
    name="keyboard_press",
    description="Press a single key (e.g., enter, escape, tab).",
    category="desktop",
    risk_tier=RiskTier.TIER_0,
)
def keyboard_press(key: str) -> str:
    """Press a single key."""
    pyautogui.press(key)
    return f"Pressed '{key}'"


@tool(
    name="window_focus",
    description="Bring a window to the foreground by title.",
    category="desktop",
    risk_tier=RiskTier.TIER_0,
)
def window_focus(title: str) -> str:
    """Focus a window by partial title match."""
    import pygetwindow as gw

    windows = gw.getWindowsWithTitle(title)
    if not windows:
        return f"No window found matching '{title}'"
    win = windows[0]
    if win.isMinimized:
        win.restore()
    win.activate()
    return f"Focused window: {win.title}"


@tool(
    name="window_resize",
    description="Resize a window by title.",
    category="desktop",
    risk_tier=RiskTier.TIER_1,
)
def window_resize(title: str, width: int, height: int) -> str:
    """Resize a window to the given dimensions."""
    import pygetwindow as gw

    windows = gw.getWindowsWithTitle(title)
    if not windows:
        return f"No window found matching '{title}'"
    win = windows[0]
    win.resizeTo(width, height)
    return f"Resized '{win.title}' to {width}x{height}"


@tool(
    name="window_minimize",
    description="Minimize a window by title.",
    category="desktop",
    risk_tier=RiskTier.TIER_0,
)
def window_minimize(title: str) -> str:
    """Minimize a window."""
    import pygetwindow as gw

    windows = gw.getWindowsWithTitle(title)
    if not windows:
        return f"No window found matching '{title}'"
    win = windows[0]
    win.minimize()
    return f"Minimized '{win.title}'"


@tool(
    name="window_close",
    description="Close a window by title.",
    category="desktop",
    risk_tier=RiskTier.TIER_2,
)
def window_close(title: str) -> str:
    """Close a window."""
    import pygetwindow as gw

    windows = gw.getWindowsWithTitle(title)
    if not windows:
        return f"No window found matching '{title}'"
    win = windows[0]
    win.close()
    return f"Closed '{win.title}'"
