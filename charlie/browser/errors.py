"""Error types for the headless browser subsystem."""


class BrowserError(Exception):
    """Base class for all charlie.browser errors."""


class BrowserUnavailable(BrowserError):
    """Playwright is not installed or BROWSER_ENABLED is off."""


class NavigationTimeout(BrowserError):
    """A page failed to reach the required state within its deadline."""


class MarkNotFound(BrowserError):
    """resolve_mark() was called with an id from a stale observation."""


class Blocked(BrowserError):
    """The site returned a bot-detection challenge or an empty/near-empty page."""
