"""
C.H.A.R.L.I.E. — Browser Automation Tools

Provides browser_agent tool for the Orchestrator, supporting:
- Navigation, click, type, screenshot, extract via Playwright MCP
- Anti-bot detection with Browserless cloud fallback
"""

from __future__ import annotations

import os
from typing import Any

from charlie.utils.logger import get_logger

from charlie.tools.tool_decorator import tool
from charlie.security.tiers import RiskTier
logger = get_logger(__name__)

# Fallback chain for browser automation
_BROWSERLESS_API_KEY = os.environ.get("BROWSERLESS_API_KEY", "")

# Module-level tool registry for the Orchestrator
TOOL_DEFINITIONS = {
    "browser_agent": {
        "description": "Control a web browser to navigate, click, type, take screenshots, and extract content.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["navigate", "click", "type", "screenshot", "extract"],
                    "description": "Browser action to perform",
                },
                "url": {
                    "type": "string",
                    "description": "URL to navigate to (required for 'navigate')",
                },
                "selector": {
                    "type": "string",
                    "description": "CSS selector for click/type actions",
                },
                "text": {
                    "type": "string",
                    "description": "Text to type (required for 'type')",
                },
                "options": {
                    "type": "object",
                    "description": "Additional options (viewport, timeout, etc.)",
                },
            },
            "required": ["action"],
        },
    }
}


@tool(
    name="browser_agent",
    description="Control a web browser to navigate, click, type, screenshot, or extract content",
    risk_tier=RiskTier.TIER_1,
    category="browser",
    timeout=120,
)
async def browser_agent(
    action: str,
    url: str | None = None,
    selector: str | None = None,
    text: str | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Execute a browser automation action.

    Falls back through: local Playwright MCP -> Browserless cloud -> error.
    """
    # Try local Playwright MCP first (stdio server)
    result = await _try_local_playwright(action, url, selector, text, options or {})
    if result.get("success"):
        return result

    # Check if blocked by anti-bot
    error = result.get("error", "")
    if _is_anti_bot(error) and _BROWSERLESS_API_KEY:
        logger.info("browser_anti_bot_detected | falling_back_to_browserless")
        return await _try_browserless(action, url, selector, text, options or {})

    return result


def _is_anti_bot(error: str) -> bool:
    """Check if an error indicates anti-bot blocking."""
    error_lower = error.lower()
    keywords = ["challenge", "cf-ray", "cloudflare", "captcha", "403", "blocked", "denied"]
    return any(k in error_lower for k in keywords)


async def _try_local_playwright(
    action: str,
    url: str | None,
    selector: str | None,
    text: str | None,
    options: dict[str, Any],
) -> dict[str, Any]:
    """
    Attempt browser action via local Playwright MCP stdio server.

    The Playwright MCP server should be running as:
        npx @playwright/mcp@latest --port 8081

    Returns dict with 'success' bool and result data or error.
    """
    try:
        import aiohttp

        base = "http://127.0.0.1:8081"
        async with aiohttp.ClientSession() as session:
            payload: dict[str, Any] = {"action": action}
            if url:
                payload["url"] = url
            if selector:
                payload["selector"] = selector
            if text:
                payload["text"] = text
            if options:
                payload.update(options)

            async with session.post(f"{base}/execute", json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 404:
                    return {"success": False, "error": "Playwright MCP not available (port 8081)"}
                data = await resp.json()
                return {"success": resp.status == 200, **(data if isinstance(data, dict) else {"data": data})}
    except ImportError:
        return {"success": False, "error": "aiohttp not installed"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def _try_browserless(
    action: str,
    url: str | None,
    selector: str | None,
    text: str | None,
    options: dict[str, Any],
) -> dict[str, Any]:
    """
    Attempt browser action via Browserless cloud API.
    """
    if not _BROWSERLESS_API_KEY:
        return {"success": False, "error": "BROWSERLESS_API_KEY not set"}

    try:
        import aiohttp

        if action == "navigate" and url:
            content_payload = {
                "url": url,
                "options": {"waitFor": options.get("wait_for", 2000)},
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"https://chrome.browserless.io/content?token={_BROWSERLESS_API_KEY}",
                    json=content_payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    text_content = await resp.text()
                    return {"success": True, "data": text_content[:5000]}
        elif action == "screenshot" and url:
            screenshot_payload = {
                "url": url,
                "options": {"fullPage": options.get("full_page", False)},
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"https://chrome.browserless.io/screenshot?token={_BROWSERLESS_API_KEY}",
                    json=screenshot_payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    import base64

                    data = await resp.read()
                    b64 = base64.b64encode(data).decode()
                    return {"success": True, "screenshot": b64}
        else:
            return {"success": False, "error": f"Browserless does not support action: {action}"}
    except ImportError:
        return {"success": False, "error": "aiohttp not installed"}
    except Exception as e:
        return {"success": False, "error": str(e)}
