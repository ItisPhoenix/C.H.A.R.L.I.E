"""
C.H.A.R.L.I.E. — Advanced Browser Controller
Provides comprehensive browser automation and control capabilities.
"""

import base64
import io
import time
from typing import Any, Dict, Optional

import pyautogui
import pygetwindow as gw

from charlie.security.tiers import RiskTier, risk_tier
from charlie.utils.logger import get_logger

logger = get_logger("BrowserController")


class AdvancedBrowserController:
    """Advanced browser automation and control."""

    def __init__(self):
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.5

    def handle_control(self, args: Dict[str, Any]) -> str:
        """Universal dispatcher for browser_control tool.

        Maps action names from persona.py to actual methods.
        Supported actions: play_pause, search, fullscreen, scroll_down, scroll_up,
        next, prev, mute, close_tab, new_tab.
        """
        action = args.get("action", "").strip().lower()
        browser = args.get("browser", "chrome")

        action_map = {
            "play_pause": lambda: pyautogui.press("playpause") if self._activate_browser(browser) else f"Could not find {browser}",
            "search": lambda: self._activate_browser(browser) or f"Could not find {browser}",
            "fullscreen": lambda: pyautogui.press("f11") if self._activate_browser(browser) else f"Could not find {browser}",
            "scroll_down": lambda: self.scroll_page({"direction": "down", "amount": 3, "browser": browser}),
            "scroll_up": lambda: self.scroll_page({"direction": "up", "amount": 3, "browser": browser}),
            "next": lambda: self.switch_tab({"direction": "next", "browser": browser}),
            "prev": lambda: self.switch_tab({"direction": "previous", "browser": browser}),
            "mute": lambda: pyautogui.press("volumemute") if self._activate_browser(browser) else f"Could not find {browser}",
            "close_tab": lambda: self.close_tab({"browser": browser}),
            "new_tab": lambda: self.new_tab({"browser": browser}),
        }

        if action in action_map:
            try:
                return action_map[action]()
            except Exception as e:
                return f"Failed to execute '{action}': {e}"
        else:
            return f"Unknown browser action: '{action}'. Supported: {', '.join(action_map.keys())}"

    def _find_browser_window(self, browser_name: str = "chrome") -> Optional[Any]:
        """Find and return the browser window."""
        try:
            windows = gw.getWindowsWithTitle('')
            for window in windows:
                title_lower = window.title.lower()
                if browser_name.lower() in title_lower and window.visible:
                    return window
            return None
        except Exception as e:
            logger.error(f"Failed to find browser window: {e}")
            return None

    def _activate_browser(self, browser_name: str = "chrome") -> bool:
        """Activate/focus the browser window."""
        window = self._find_browser_window(browser_name)
        if window:
            try:
                window.activate()
                time.sleep(0.5)
                return True
            except Exception as e:
                logger.error(f"Failed to activate browser: {e}")
        return False

    @risk_tier(RiskTier.TIER_0)
    def navigate_to_url(self, args: Dict[str, Any]) -> str:
        """Navigate to a specific URL."""
        url = args.get("url", "").strip()
        browser = args.get("browser", "chrome")

        if not url:
            return "No URL provided."

        # Add protocol if missing
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url

        if not self._activate_browser(browser):
            return f"Could not find or activate {browser} browser."

        try:
            # Focus address bar (Ctrl+L) and type URL
            pyautogui.hotkey('ctrl', 'l')
            time.sleep(0.2)
            pyautogui.typewrite(url, interval=0.02)
            pyautogui.press('enter')
            return f"Navigated to {url}"
        except Exception as e:
            return f"Failed to navigate: {e}"

    @risk_tier(RiskTier.TIER_0)
    def new_tab(self, args: Dict[str, Any]) -> str:
        """Open a new tab."""
        browser = args.get("browser", "chrome")

        if not self._activate_browser(browser):
            return f"Could not find or activate {browser} browser."

        try:
            pyautogui.hotkey('ctrl', 't')
            return "Opened new tab."
        except Exception as e:
            return f"Failed to open new tab: {e}"

    @risk_tier(RiskTier.TIER_0)
    def close_tab(self, args: Dict[str, Any]) -> str:
        """Close current tab."""
        browser = args.get("browser", "chrome")

        if not self._activate_browser(browser):
            return f"Could not find or activate {browser} browser."

        try:
            pyautogui.hotkey('ctrl', 'w')
            return "Closed current tab."
        except Exception as e:
            return f"Failed to close tab: {e}"

    @risk_tier(RiskTier.TIER_0)
    def switch_tab(self, args: Dict[str, Any]) -> str:
        """Switch to next or previous tab."""
        direction = args.get("direction", "next")
        browser = args.get("browser", "chrome")

        if not self._activate_browser(browser):
            return f"Could not find or activate {browser} browser."

        try:
            if direction == "next":
                pyautogui.hotkey('ctrl', 'tab')
                return "Switched to next tab."
            elif direction == "previous":
                pyautogui.hotkey('ctrl', 'shift', 'tab')
                return "Switched to previous tab."
            else:
                return "Invalid direction. Use 'next' or 'previous'."
        except Exception as e:
            return f"Failed to switch tab: {e}"

    @risk_tier(RiskTier.TIER_0)
    def go_back(self, args: Dict[str, Any]) -> str:
        """Go back in browser history."""
        browser = args.get("browser", "chrome")

        if not self._activate_browser(browser):
            return f"Could not find or activate {browser} browser."

        try:
            pyautogui.hotkey('alt', 'left')
            return "Went back in history."
        except Exception as e:
            return f"Failed to go back: {e}"

    @risk_tier(RiskTier.TIER_0)
    def go_forward(self, args: Dict[str, Any]) -> str:
        """Go forward in browser history."""
        browser = args.get("browser", "chrome")

        if not self._activate_browser(browser):
            return f"Could not find or activate {browser} browser."

        try:
            pyautogui.hotkey('alt', 'right')
            return "Went forward in history."
        except Exception as e:
            return f"Failed to go forward: {e}"

    @risk_tier(RiskTier.TIER_0)
    def scroll_page(self, args: Dict[str, Any]) -> str:
        """Scroll the page up or down."""
        direction = args.get("direction", "down")
        amount = args.get("amount", 3)  # Number of scroll actions
        browser = args.get("browser", "chrome")

        if not self._activate_browser(browser):
            return f"Could not find or activate {browser} browser."

        try:
            for _ in range(amount):
                if direction == "down":
                    pyautogui.scroll(-100)
                elif direction == "up":
                    pyautogui.scroll(100)
                time.sleep(0.1)

            return f"Scrolled {direction} {amount} times."
        except Exception as e:
            return f"Failed to scroll: {e}"

    @risk_tier(RiskTier.TIER_0)
    def type_text(self, args: Dict[str, Any]) -> str:
        """Type text into the current focused element."""
        text = args.get("text", "")
        browser = args.get("browser", "chrome")

        if not text:
            return "No text provided to type."

        if not self._activate_browser(browser):
            return f"Could not find or activate {browser} browser."

        try:
            pyautogui.typewrite(text, interval=0.05)
            return f"Typed: {text}"
        except Exception as e:
            return f"Failed to type text: {e}"

    @risk_tier(RiskTier.TIER_0)
    def press_key(self, args: Dict[str, Any]) -> str:
        """Press a specific key or key combination."""
        key = args.get("key", "")
        browser = args.get("browser", "chrome")

        if not key:
            return "No key specified."

        if not self._activate_browser(browser):
            return f"Could not find or activate {browser} browser."

        try:
            if '+' in key:
                # Handle key combinations like "ctrl+c"
                keys = key.split('+')
                pyautogui.hotkey(*keys)
            else:
                pyautogui.press(key)
            return f"Pressed: {key}"
        except Exception as e:
            return f"Failed to press key: {e}"

    @risk_tier(RiskTier.TIER_0)
    def click_element(self, args: Dict[str, Any]) -> str:
        """Click at specific coordinates or perform a general click."""
        x = args.get("x")
        y = args.get("y")
        button = args.get("button", "left")
        browser = args.get("browser", "chrome")

        if not self._activate_browser(browser):
            return f"Could not find or activate {browser} browser."

        try:
            if x is not None and y is not None:
                pyautogui.click(x=x, y=y, button=button)
                return f"Clicked at coordinates ({x}, {y}) with {button} button."
            else:
                pyautogui.click(button=button)
                return f"Clicked with {button} button at current position."
        except Exception as e:
            return f"Failed to click: {e}"

    @risk_tier(RiskTier.TIER_0)
    def take_screenshot(self, args: Dict[str, Any]) -> str:
        """Take a screenshot of the browser window."""
        browser = args.get("browser", "chrome")
        region = args.get("region")  # Optional: [x, y, width, height]

        if not self._activate_browser(browser):
            return f"Could not find or activate {browser} browser."

        try:
            if region:
                screenshot = pyautogui.screenshot(region=region)
            else:
                screenshot = pyautogui.screenshot()

            # Convert to base64
            buffer = io.BytesIO()
            screenshot.save(buffer, format='PNG')
            base64.b64encode(buffer.getvalue()).decode()

            return f"Screenshot taken ({screenshot.size[0]}x{screenshot.size[1]}). Data ready for display."
        except Exception as e:
            return f"Failed to take screenshot: {e}"

    @risk_tier(RiskTier.TIER_0)
    def zoom_page(self, args: Dict[str, Any]) -> str:
        """Zoom in or out on the page."""
        action = args.get("action", "in")  # "in" or "out"
        browser = args.get("browser", "chrome")

        if not self._activate_browser(browser):
            return f"Could not find or activate {browser} browser."

        try:
            if action == "in":
                pyautogui.hotkey('ctrl', '+')
                return "Zoomed in."
            elif action == "out":
                pyautogui.hotkey('ctrl', '-')
                return "Zoomed out."
            elif action == "reset":
                pyautogui.hotkey('ctrl', '0')
                return "Reset zoom to 100%."
            else:
                return "Invalid zoom action. Use 'in', 'out', or 'reset'."
        except Exception as e:
            return f"Failed to zoom: {e}"

    @risk_tier(RiskTier.TIER_0)
    def open_bookmarks(self, args: Dict[str, Any]) -> str:
        """Open browser bookmarks menu."""
        browser = args.get("browser", "chrome")

        if not self._activate_browser(browser):
            return f"Could not find or activate {browser} browser."

        try:
            pyautogui.hotkey('ctrl', 'shift', 'o')
            return "Opened bookmarks."
        except Exception as e:
            return f"Failed to open bookmarks: {e}"

    @risk_tier(RiskTier.TIER_0)
    def open_history(self, args: Dict[str, Any]) -> str:
        """Open browser history."""
        browser = args.get("browser", "chrome")

        if not self._activate_browser(browser):
            return f"Could not find or activate {browser} browser."

        try:
            pyautogui.hotkey('ctrl', 'h')
            return "Opened browsing history."
        except Exception as e:
            return f"Failed to open history: {e}"

    @risk_tier(RiskTier.TIER_0)
    def clear_cache(self, args: Dict[str, Any]) -> str:
        """Clear browser cache and cookies."""
        browser = args.get("browser", "chrome")

        if not self._activate_browser(browser):
            return f"Could not find or activate {browser} browser."

        try:
            pyautogui.hotkey('ctrl', 'shift', 'delete')
            return "Opened clear browsing data dialog."
        except Exception as e:
            return f"Failed to open clear data dialog: {e}"
