"""One-time headed login helper: `python -m charlie.browser login <url>`.

Opens the same persistent profile controller.py uses, but headed, so the user can log in by hand;
cookies then persist for all future headless runs. Never imported by the rest of the app.
"""

import sys

from charlie.browser import BROWSER_AVAILABLE
from charlie.config import config


def login(url: str) -> None:
    if not BROWSER_AVAILABLE:
        print("playwright is not installed -- run: uv sync --extra browser")
        return
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=config.browser_profile_path, channel="chrome", headless=False,
            )
            page = context.new_page()
            page.goto(url)
            input("Log in, then press Enter here to close the browser and save cookies...")
            context.close()
    except Exception as exc:
        if "ProcessSingleton" in str(exc) or "already in use" in str(exc).lower():
            print("Charlie's browser is running -- stop Charlie or wait for idle shutdown, then retry.")
        else:
            raise


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "login":
        print("Usage: python -m charlie.browser login <url>")
        sys.exit(1)
    login(sys.argv[2])
