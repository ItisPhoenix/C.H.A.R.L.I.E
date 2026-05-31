import ast
import asyncio
import json
import logging
import operator
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pyautogui
import pygetwindow as gw
import pyperclip
import requests

try:
    from ctypes import POINTER, cast  # noqa: F401 — guarded import

    from comtypes import CLSCTX_ALL  # noqa: F401 — guarded import
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume  # noqa: F401 — guarded import
except ImportError:
    AudioUtilities = None

from charlie.config import settings
from charlie.integrations.github import GitHubIntegration
from charlie.integrations.notion import NotionIntegration
from charlie.security.tiers import CONFIRMATION_PENDING, RiskTier, get_tool_tier, risk_tier

# ── Tool timeout constants (seconds) ─────────────────────────────────────────
VISION_TOOL_TIMEOUT = 45   # Vision model inference (screen analysis, image description)
GLOBE_REFRESH_TIMEOUT = 15 # Globe data refresh
WEATHER_API_TIMEOUT = 10   # Geocoding + weather API calls
HISTORY_MESSAGE_LIMIT = 50 # Max messages persisted to disk

logger = logging.getLogger("charlie.brain.tools")

class ToolHandler:
    def __init__(self, brain):
        self.brain = brain
        self.registry = {}
        self._recent_research: list[dict] = []  # Last N research results for context
        self._discover_tools()

    def _discover_tools(self):
        """Automatically registers all methods starting with _tool_ from self."""
        for attr in dir(self):
            if attr.startswith("_tool_"):
                self.registry[attr[6:]] = getattr(self, attr)
        logger.info("Tool discovery complete. %d tools registered.", len(self.registry))

    def _is_safe_path(self, path: str) -> bool:
        """Standardized project root boundary check."""
        try:
            p = Path(path).resolve()
            root = Path(".").resolve()
            return p.is_relative_to(root)
        except (ValueError, TypeError):
            return False

    def execute_tools(
        self,
        tool_data: dict[str, Any],
        sir_input: str = "",
        source: str = "local",
        skip_guardian: bool = False,
    ) -> str:
        if not tool_data or not isinstance(tool_data, dict):
            logger.error("execute_tools_invalid_input | type=%s", type(tool_data))
            return "Error: Invalid tool call structure."

        try:
            tool = tool_data.get("tool")
            args = tool_data.get("args", {})

            if not tool:
                return "Error: No tool name provided in call."

            if tool in self.registry:
                tool_func = self.registry[tool]

                # 1. Verification Gate
                if not skip_guardian:
                    # Resolve the authoritative tier from the unified catalog when
                    # available; otherwise let the guardian fall back (fail-closed
                    # to TIER_3 for an unknown handler).
                    registry = getattr(self.brain, "tool_registry", None)
                    catalog_tier = registry.get_tier(tool) if registry is not None else None

                    allowed, reason = self.brain.guardian.verify_tool(
                        tool, args, sir_input, tool_func=tool_func, tier=catalog_tier
                    )

                    effective_tier = catalog_tier if catalog_tier is not None else get_tool_tier(tool_func)

                    if effective_tier == RiskTier.TIER_0:
                        allowed = True

                    if allowed == CONFIRMATION_PENDING:
                        tier = effective_tier
                        self.brain.awaiting_confirmation = {
                            "tool": tool,
                            "args": args,
                            "sir_input": sir_input,
                            "tier": tier,
                            "source": source,
                        }
                        self.brain.last_confirmation_time = time.time()
                        if self.brain.confirmation_event:
                            self.brain.loop.call_soon_threadsafe(
                                self.brain.confirmation_event.clear
                            )

                        payload = {
                            "type": "CONFIRM_REQUIRED",
                            "content": {
                                "desc": reason,
                                "tier": effective_tier.value,
                            },
                        }

                        if source == "local" or source == "all":
                            self.brain._safe_put(self.brain.status_q, payload)
                            self.brain._safe_put(
                                self.brain.tts_q, {"type": "SPEAK", "content": reason}
                            )

                        if (
                            source.startswith("telegram") or source == "all"
                        ) and self.brain.telegram_q:
                            self.brain._safe_put(self.brain.telegram_q, payload)

                        return CONFIRMATION_PENDING

                    if not allowed:
                        return reason

                logger.info("tool_executing", tool=tool)
                start = time.perf_counter()
                try:
                    res = tool_func(args)
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    if hasattr(self.brain, "outcome_tracker") and self.brain.outcome_tracker:
                        self.brain.outcome_tracker.record_tool(
                            tool, True, {"elapsed_ms": round(elapsed_ms, 1), "result_length": len(str(res))}
                        )
                    return res
                except Exception as e:
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    if hasattr(self.brain, "outcome_tracker") and self.brain.outcome_tracker:
                        self.brain.outcome_tracker.record_tool(
                            tool, False, {"elapsed_ms": round(elapsed_ms, 1), "error": str(e)}
                        )
                    logger.error("tool_execution_failed | tool=%s | error=%s", tool, e)
                    return f"I encountered an error executing {tool}, Sir. {type(e).__name__}: {e}"
            return f"Error: Tool '{tool}' not found."
        except Exception as e:
            logger.error("execute_tools_failed", error=str(e))
            return f"Error: {str(e)}"

    # ── UI TOOLS ────────────────────────────────────────────────────────────

    # ── SYSTEM & HARDWARE TOOLS ─────────────────────────────────────────────

    @risk_tier(RiskTier.TIER_0)
    def _tool_get_system_status(self, args: dict[str, Any]) -> str:
        """Returns hardware telemetry including VRAM usage."""
        from charlie.utils.system import get_system_vitals
        v = get_system_vitals()


        return f"CPU Usage: {v['cpu']:.1f}%. RAM Usage: {v['ram']:.1f}%. VRAM Usage: {v['vram_pct']:.1f}% ({v['vram_mb']:.0f}MB / {v['vram_limit']}MB)."

    @risk_tier(RiskTier.TIER_0)
    def _tool_move_widget(self, args: dict[str, Any]) -> str:
        """
        Spatially anchors a widget to a specific application window.
        Args: {'name': str, 'target_window': str, 'zone': str}
        Zones: WINDOW_LEFT, WINDOW_RIGHT, WINDOW_TOP, WINDOW_BOTTOM
        """
        name = str(args.get("name", "chat")).lower().replace(" widget", "").strip()
        target = args.get("target_window")
        zone = str(args.get("zone", "WINDOW_RIGHT")).upper()

        if not target:
            return "Sir, I need a target window title to anchor the widget."

        logger.info("tool_move_widget | widget=%s | target=%s | zone=%s", name, target, zone)
        if self.brain.status_q:
            self.brain._safe_put(self.brain.status_q, {
                "type": "MOVE_WIDGET",
                "content": {
                    "name": name,
                    "target": target,
                    "zone": zone
                }
            })
            return f"Moving the {name} widget to the {zone} of {target}, Sir."
        return "Widget link disrupted."

    @risk_tier(RiskTier.TIER_1)
    def _tool_press_key(self, args: dict[str, Any]) -> str:
        key = args.get("key") or args.get("text")
        if key:
            key_str = str(key).strip()
            if len(key_str) > 20:
                return "Key input too long (max 20 chars)."
            allowed_keys = {
                "enter", "tab", "escape", "space", "backspace", "delete",
                "up", "down", "left", "right", "home", "end", "pageup", "pagedown",
                "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
                "ctrl", "alt", "shift", "win", "cmd", "insert", "printscreen",
                "numlock", "capslock", "scrolllock", "pause",
            }
            if len(key_str) == 1 or key_str.lower() in allowed_keys or re.match(r'^[a-zA-Z0-9]$', key_str):
                pyautogui.press(key_str)
                return f"Pressed {key_str}"
            return f"Key '{key_str}' not in allowed set."
        return "No key."

    @risk_tier(RiskTier.TIER_1)
    def _tool_type_text(self, args: dict[str, Any]) -> str:
        text = args.get("text") or args.get("val")
        if text:
            text_str = str(text)
            if len(text_str) > 500:
                return "Text too long (max 500 chars)."
            pyautogui.write(text_str, interval=0.01)
            return "Typed."
        return "No text."

    @risk_tier(RiskTier.TIER_1)
    def _tool_move_mouse(self, args: dict[str, Any]) -> str:
        x, y = args.get("x", 0), args.get("y", 0)
        pyautogui.moveTo(x, y)
        return "Moved."

    @risk_tier(RiskTier.TIER_1)
    def _tool_click_mouse(self, args: dict[str, Any]) -> str:
        x, y = args.get("x"), args.get("y")
        if x is not None and y is not None:
            pyautogui.click(x, y)
        else:
            pyautogui.click()
        return "Clicked."

    @risk_tier(RiskTier.TIER_0)
    def _tool_window_state(self, args: dict[str, Any]) -> str:
        state, title = str(args.get("state", "maximize")).lower(), args.get("name")
        try:
            wins = gw.getWindowsWithTitle(title) if title else [gw.getActiveWindow()]
            if not wins or not wins[0]:
                return "Not found."
            if state == "minimize":
                wins[0].minimize()
            else:
                wins[0].maximize()
            return f"Window {state}d."
        except Exception as e:
            return f"Failed: {e}"

    # ── BROWSER & RESEARCH TOOLS ────────────────────────────────────────────

    @risk_tier(RiskTier.TIER_0)
    @staticmethod
    def _sanitize_news_for_llm(raw: str) -> str:
        """Strips RSS metadata, markdown headers, and source links from news data."""
        if not raw:
            return raw
        import re
        # Remove markdown headers
        text = re.sub(r'#{1,6}\s+', '', raw)
        # Remove "Source: link to ..." lines
        text = re.sub(r'Source:\s*link\s+to\s+\S+[\s,]*', '', text, flags=re.IGNORECASE)
        # Remove "Topic: ..." labels
        text = re.sub(r'\bTopic:\s*[A-Z_]+\s*', '', text, flags=re.IGNORECASE)
        # Remove "Summary:" label
        text = re.sub(r'\bSummary:\s*', '', text, flags=re.IGNORECASE)
        # Remove standalone URLs
        text = re.sub(r'https?://\S+', '', text)
        # Clean up excessive whitespace and dashes
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\s*[-–—]\s*[-–—]\s*', ' — ', text)  # Double dashes to em dash
        text = re.sub(r'^\s*[-–—]\s+', '', text, flags=re.MULTILINE)  # Leading dashes
        return text.strip()

    # ── SERVICE INTEGRATIONS ────────────────────────────────────────────────

    @risk_tier(RiskTier.TIER_1)
    def _tool_get_github_activity(self, args: dict[str, Any]) -> str:
        """Retrieves recent GitHub activity or issues. args: {'repo': str, 'limit': int}"""
        repo = args.get("repo")
        limit = args.get("limit", 10)
        try:
            if not hasattr(self, "_github_integration"):
                self._github_integration = GitHubIntegration()

            activity = self._github_integration.fetch(repo_name=repo, limit=limit)
            if not activity:
                return "No recent activity or issues found on GitHub, Sir."

            header = f"### GITHUB ISSUES: {repo}" if repo else "### RECENT GITHUB REPOSITORIES:"
            output = [header]
            for item in activity:
                if repo:
                    output.append(f"- #{item['number']} **{item['title']}** (by {item['author']})")
                else:
                    output.append(f"- **{item['name']}** (Updated: {item['updated']})")

            return "\n".join(output)
        except Exception as e:
            logger.error("tool_github_failed | %s", e)
            return f"GitHub activity fetch failed: {e}"

    @risk_tier(RiskTier.TIER_1)
    def _tool_get_notion_pages(self, args: dict[str, Any]) -> str:
        """Retrieves recent pages from Notion. args: {'limit': int}"""
        limit = args.get("limit", 10)
        try:
            if not hasattr(self, "_notion_integration"):
                self._notion_integration = NotionIntegration()

            pages = self._notion_integration.fetch(limit=limit)
            if not pages:
                return "No recent Notion pages found, Sir."

            output = ["### RECENT NOTION PAGES:"]
            for p in pages:
                output.append(f"- **{p['title']}** (Edited: {p['last_edited']})")

            return "\n".join(output)
        except Exception as e:
            logger.error("tool_notion_failed | %s", e)
            return f"Notion fetch failed: {e}"

    # ── UTILITY & MEDIA TOOLS ───────────────────────────────────────────────

    @risk_tier(RiskTier.TIER_1)
    def _tool_sync_clipboard(self, args: dict[str, Any]) -> str:
        text = args.get("text")
        if not text: return "Error: No text provided."
        pyperclip.copy(text)
        return "Clipboard synced to workstation, Sir."

    @risk_tier(RiskTier.TIER_0)
    def _tool_cast_media(self, args: dict[str, Any]) -> str:
        query, url = args.get("query"), args.get("url")
        if not query and not url: return "Error: No media source provided."
        if query: return self._tool_open_app({"name": "youtube", "query": query})

        # Path Traversal Guard for local file targets
        if url and not str(url).startswith(("http://", "https://")):
            if not self._is_safe_path(url):
                logger.warning("unauthorized_media_cast_blocked | target=%s", url)
                return "Access denied: I can only cast local files within the project directory, Sir."

        os.startfile(url)
        return "Media casted to workstation monitor, Sir."

    @risk_tier(RiskTier.TIER_1)
    def _tool_lock_workstation(self, args: dict[str, Any]) -> str:
        import ctypes
        ctypes.windll.user32.LockWorkStation()
        return "Workstation locked successfully, Sir."

    @risk_tier(RiskTier.TIER_0)
    def _tool_get_foreground_app(self, args: dict[str, Any]) -> str:
        try:
            win = gw.getActiveWindow()
            return f"The focused window is: '{win.title}', Sir." if win else "No active window detected."
        except Exception: return "Focused window metadata is currently unreadable."

    @risk_tier(RiskTier.TIER_0)
    def _tool_search_codebase(self, args: dict[str, Any]) -> str:
        """Search the codebase using RAG-indexed semantic search."""
        query = args.get("query")
        if not query:
            return "Error: No search query provided."
        try:
            if hasattr(self.brain, 'rag_indexer') and self.brain.rag_indexer:
                results = self.brain.rag_indexer.query(query, n_results=5)
                if not results:
                    return f"No codebase matches for '{query}'."
                lines = []
                for r in results:
                    lines.append(f"[{r.source_file}:{r.line_start}-{r.line_end}]\n{r.text[:500]}")
                return "Codebase search results:\n\n" + "\n---\n".join(lines)
            else:
                return "RAG indexer not available."
        except Exception as e:
            return f"Codebase search error: {e}"

    @risk_tier(RiskTier.TIER_2)
    def _tool_sleep_pc(self, args: dict[str, Any]) -> str:
        self.brain._safe_put(self.brain.tts_q, {"type": "SPEAK", "content": "Entering sleep mode, Sir."})
        threading.Event().wait(1.0)
        subprocess.run(["rundll32.exe", "powprof.dll,SetSuspendState", "0,1,0"])
        return "Command sent to power management unit."

    @risk_tier(RiskTier.TIER_2)
    def _tool_hibernate_pc(self, args: dict[str, Any]) -> str:
        self.brain._safe_put(self.brain.tts_q, {"type": "SPEAK", "content": "Hibernating workstation, Sir."})
        threading.Event().wait(1.0)
        subprocess.run(["shutdown", "/h"])
        return "Hibernation sequence initiated."

    @risk_tier(RiskTier.TIER_0)
    def _tool_get_system_logs(self, args: dict[str, Any]) -> str:
        try:
            log_path = "logs/charlie.log"
            if not os.path.exists(log_path): return "Error: logs unreachable."
            with open(log_path, "r", encoding="utf-8") as f:
                tail = "".join(f.readlines()[-20:])
            ctx = self.brain.chain_mgr.get_active_context()
            if ctx and ctx.source.startswith("telegram"):
                self.brain._safe_put(self.brain.telegram_q, {"type": "CHAT_MSG", "speaker": "CHARLIE", "content": f"<b>SYSTEM LOG (TAIL):</b>\n<pre>{tail}</pre>"})
                return "System log tail forwarded to mobile, Sir."
            return f"Log Tail:\n{tail}"
        except Exception as e: return f"Log retrieval failed: {e}"

    @risk_tier(RiskTier.TIER_0)
    def _tool_get_pc_clipboard(self, args: dict[str, Any]) -> str:
        try:
            content = pyperclip.paste()
            return f"PC Clipboard Content:\n{content[:2000]}" if content else "Clipboard empty."
        except Exception as e: return f"Clipboard fetch failed: {e}"

    @risk_tier(RiskTier.TIER_1)
    def _tool_mute_pc_audio(self, args: dict[str, Any]) -> str:
        return self._tool_set_volume({"level": 0})

    @risk_tier(RiskTier.TIER_1)
    def _tool_unmute_pc_audio(self, args: dict[str, Any]) -> str:
        return self._tool_set_volume({"level": 50})

    @risk_tier(RiskTier.TIER_0)
    def _tool_get_system_manifest(self, args: dict[str, Any]) -> str:
        from charlie.utils.state_reflector import state_reflector
        caps = state_reflector.get_current_capabilities()
        return f"### C.H.A.R.L.I.E. ENGINE MANIFEST\nCAPABILITIES:\n{caps}\n\nROADMAP: All 12 phases complete"

    @risk_tier(RiskTier.TIER_0)
    def _tool_analyze_screen(self, args: dict[str, Any]) -> str:
        """Look at the current screen and answer a question about it using the vision model."""
        query = args.get("query", "Describe what you see on screen.")
        try:
            loop = self.brain.loop
            if loop and loop.is_running():
                future = asyncio.run_coroutine_threadsafe(
                    self.brain.vision_handler.ask_vision(query), loop
                )
                return future.result(timeout=VISION_TOOL_TIMEOUT)
            else:
                return asyncio.run(self.brain.vision_handler.ask_vision(query))
        except Exception as e:
            return f"Screen analysis failed: {e}"

    @risk_tier(RiskTier.TIER_0)
    def _tool_describe_image(self, args: dict[str, Any]) -> str:
        """Analyze an image file and describe its contents using the vision model."""
        path = args.get("path")
        if not path:
            return "Error: No image path provided."
        query = args.get("query", "Describe this image in detail.")
        try:
            loop = self.brain.loop
            if loop and loop.is_running():
                future = asyncio.run_coroutine_threadsafe(
                    self.brain.vision_handler.analyze_image(path, query), loop
                )
                return future.result(timeout=VISION_TOOL_TIMEOUT)
            else:
                return asyncio.run(self.brain.vision_handler.analyze_image(path, query))
        except Exception as e:
            return f"Image analysis failed: {e}"

    @risk_tier(RiskTier.TIER_0)
    def _tool_time(self, args: dict[str, Any]) -> str:
        from datetime import datetime
        loc = args.get("location") or "local"
        now = datetime.now()
        if loc.lower() == "local": return f"The current local time is {now.strftime('%I:%M %p')} on {now.strftime('%A, %B %d, %Y')}."
        return self._tool_search({"query": f"current time in {loc}"})

    @risk_tier(RiskTier.TIER_0)
    def _tool_weather(self, args: dict[str, Any]) -> str:
        location = args.get("location") or os.getenv("CHARLIE_CITY", "London")
        try:
            g_res = requests.get(f"https://geocoding-api.open-meteo.com/v1/search?name={location}&count=1&language=en&format=json", timeout=WEATHER_API_TIMEOUT)
            g_data = g_res.json()
            if not g_data.get("results"): return f"Could not find coordinates for {location}."
            city = g_data["results"][0]
            lat, lon, name = city["latitude"], city["longitude"], city["name"]
            w_res = requests.get(f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m", timeout=WEATHER_API_TIMEOUT)
            w_data = w_res.json()["current"]
            conds = {0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "foggy", 51: "light drizzle", 61: "slight rain", 71: "slight snow", 95: "thunderstorm"}
            return f"Weather in {name}: {w_data['temperature_2m']}°C, {conds.get(w_data['weather_code'], 'stable')}, {w_data['relative_humidity_2m']}% humidity."
        except Exception: return "Weather service unavailable."

    @risk_tier(RiskTier.TIER_0)
    def _tool_timer(self, args: dict[str, Any]) -> str:
        duration, label = args.get("duration_sec", 0), args.get("label", "Timer")
        if duration <= 0: return "Invalid duration."
        def cb():
            if self.brain.tts_q: self.brain._safe_put(self.brain.tts_q, {"type": "SPEAK", "content": f"Sir, your timer for {label} has expired."})
        threading.Timer(duration, cb).start()
        return f"Timer set for {label} in {duration}s."

    @risk_tier(RiskTier.TIER_0)
    def _tool_calculate(self, args: dict[str, Any]) -> str:
        expr = args.get("expression") or args.get("expr")
        if not expr: return "No expression."
        clean = re.sub(r"[^0-9+\-*/().%\s]", "", str(expr))
        def _safe_eval(node):
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)): return node.value
            if isinstance(node, ast.Num): return node.n  # Python < 3.12 compat
            if isinstance(node, ast.BinOp):
                left, right = _safe_eval(node.left), _safe_eval(node.right)
                ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}
                return ops[type(node.op)](left, right)
            if isinstance(node, ast.UnaryOp):
                operand = _safe_eval(node.operand)
                if isinstance(node.op, ast.USub): return -operand
            raise ValueError(f"Unsupported node: {type(node)}")
        try:
            tree = ast.parse(clean, mode="eval")
            return f"Result: {_safe_eval(tree.body)}"
        except Exception as e: return f"Failed: {e}"

    @risk_tier(RiskTier.TIER_1)
    def _tool_update_config(self, args: dict[str, Any]) -> str:
        key, val = args.get("key"), args.get("value")
        if not key: return "Key required."
        return self.brain.self_mod.handle_mod_request("update_config", {"key": key, "value": val})

    @risk_tier(RiskTier.TIER_1)
    def _tool_remember_preference(self, args: dict[str, Any]) -> str:
        pref = args.get("preference") or args.get("text")
        if not pref: return "Preference text required."
        return self.brain.self_mod.handle_mod_request("remember_preference", {"preference": pref})

    @risk_tier(RiskTier.TIER_3)
    def _tool_self_modify(self, args: dict[str, Any]) -> str:
        """Triggers the code modification lifecycle (Simulation -> Diff -> Confirm -> Apply)."""
        # Req 17.3: refuse while the Self_Modify_Engine is disabled (default OFF).
        if not getattr(settings.security, "self_modify_enabled", False):
            logger.info("self_modify_blocked | tool=%s | reason=disabled", "self_modify")
            return "Self-modification is disabled, Sir. Enable 'self_modify_enabled' in charlie_config.json to allow code changes."
        path, content = args.get("file") or args.get("path"), args.get("content")
        if not path or not content: return "File and content required."
        if not self._is_safe_path(path):
            return "Access denied: cannot modify files outside project root."

        # 1. Simulation
        ok, msg = self.brain.self_mod.simulate_edit(path, content)
        if not ok: return f"Simulation failed: {msg}"

        # 2. Trigger Diff Widget
        if self.brain.status_q:
            # We need the old content for the diff
            old_content = ""
            p = Path(path)
            if p.exists():
                old_content = p.read_text(errors="replace")

            self.brain._safe_put(self.brain.status_q, {
                "type": "WIDGET_SHOW",
                "content": "diff",
                "msg_content": {
                    "file": path,
                    "old": old_content,
                    "new": content,
                    "desc": args.get("reason", "Code Optimization")
                }
            })

            # 3. Queue confirmation for the real apply
            self.brain.awaiting_confirmation = {
                "tool": "apply_edit",
                "args": {"path": path, "content": content, "description": args.get("reason", "Self-mod edit")},
                "sir_input": "Apply proposed code changes?",
                "tier": RiskTier.TIER_3,
                "source": "local",
            }
            self.brain.last_confirmation_time = time.time()
            if self.brain.confirmation_event:
                self.brain.loop.call_soon_threadsafe(self.brain.confirmation_event.clear)

            return f"Modification simulated for {path}. Diff preview updated. Awaiting your confirmation to apply, Sir."

        return "Widget link disrupted. Cannot preview diff."

    @risk_tier(RiskTier.TIER_3)
    def _tool_apply_edit(self, args: dict[str, Any]) -> str:
        # Req 17.3: refuse while the Self_Modify_Engine is disabled (default OFF).
        if not getattr(settings.security, "self_modify_enabled", False):
            logger.info("self_modify_blocked | tool=%s | reason=disabled", "apply_edit")
            return "Self-modification is disabled, Sir. Enable 'self_modify_enabled' in charlie_config.json to allow code changes."
        path, content = args.get("path"), args.get("content")
        if not path: return "Missing path."
        p_obj = Path(path).resolve()
        for r in settings.security.restricted_paths:
            if p_obj.is_relative_to(Path(r).resolve()): return f"Access Denied: {path}"
        sim_ok, sim_msg = self.brain.self_mod.simulate_edit(path, content)
        if not sim_ok: return f"Simulation failed: {sim_msg}"
        return self.brain.self_mod.apply_edit(path, content, args.get("description", "Automated edit"))[1]

    @risk_tier(RiskTier.TIER_2)
    def _tool_shutdown(self, args: dict[str, Any]) -> str:
        """Initiates the shutdown sequence for the C.H.A.R.L.I.E. engine."""
        self.brain._emit_status("SHUTTING_DOWN")
        self.brain._safe_put(self.brain.status_q, {"type": "PHASE", "content": "SHUTTING_DOWN"})

        self.brain._safe_put(
            self.brain.tts_q,
            {"type": "SPEAK", "content": "Acknowledged, Sir. Powering down systems. I'll be standing by on the mobile frequency."},
        )

        # 2. Revert audio ducking
        if self.brain.audio_cmd_q:
            self.brain._safe_put(self.brain.audio_cmd_q, {"type": "UNDUCK"})
            threading.Event().wait(0.5)

        # 3. Send SHUTDOWN to audio engine, then kill brain
        if self.brain.audio_cmd_q:
            self.brain._safe_put(self.brain.audio_cmd_q, {"type": "SHUTDOWN"})
            threading.Event().wait(1.0)

        self.brain._safe_put(self.brain.brain_task_q, {"type": "HARD_SHUTDOWN"})
        self.brain._safe_put(self.brain.tts_q, {"type": "CONVERSATION_END"})
        return "Offline."

    @risk_tier(RiskTier.TIER_0)
    def _tool_standby(self, args: dict[str, Any]) -> str:
        """Puts the system into standby/sleep mode."""
        self.brain._safe_put(
            self.brain.tts_q,
            {
                "type": "SPEAK",
                "content": "Standing by, Sir. I'll be here when you need me.",
            },
        )
        self.brain.standby_mode = True
        if self.brain.audio_cmd_q:
            self.brain._safe_put(self.brain.audio_cmd_q, {"type": "SET_STANDBY", "value": True})
        self.brain._safe_put(self.brain.tts_q, {"type": "CONVERSATION_END"})
        return "Standby engaged."

    @risk_tier(RiskTier.TIER_0)
    def _tool_handoff(self, args: dict[str, Any]) -> str:
        """Transfers current workstation context to the Mobile Ecosystem."""
        ctx = self.brain.chain_mgr.get_active_context()
        goal = ctx.goal if ctx else "Active Session"
        summary = f" <b>HANDOFF RECEIVED</b>\n\n<b>Goal:</b> {goal}\n<i>Charlie is now synced.</i>"
        if self.brain.telegram_q:
            self.brain._safe_put(self.brain.telegram_q, {"type": "CHAT_MSG", "speaker": "CHARLIE", "content": summary})
            return "Handoff complete."
        return "Handoff failed."

    @risk_tier(RiskTier.TIER_1)
    def _tool_consolidate_memory(self, args: dict[str, Any]) -> str:
        self.brain.memory.consolidate()
        return "Memory consolidation complete."

    @risk_tier(RiskTier.TIER_1)
    def _tool_submit_background_task(self, args: dict[str, Any]) -> str:
        """Submit a task to run in the background."""
        description = args.get("description", "").strip()
        if not description:
            return "No task description provided."

        if not hasattr(self.brain, 'task_mgr'):
            return "Task manager not available."

        from charlie.brain.task_models import TaskPriority, TaskSpec, make_task_id

        priority_str = args.get("priority", "NORMAL").upper()
        priority = TaskPriority[priority_str] if priority_str in TaskPriority.__members__ else TaskPriority.NORMAL

        async def _noop_handler(ctx):
            return f"Background task placeholder: {description}"

        spec = TaskSpec(
            id=make_task_id(),
            description=description,
            priority=priority,
            handler=_noop_handler,
        )
        task_id = self.brain.task_mgr.submit(spec)
        return f"Background task submitted: {task_id} ({description})"

    @risk_tier(RiskTier.TIER_0)
    def _tool_browser_navigate(self, args: dict[str, Any]) -> str:
        url = args.get("url", "").strip()
        if not url: return "No URL."
        if not url.startswith(('http://', 'https://')): url = 'https://' + url
        try:
            pyautogui.hotkey("ctrl", "l")
            threading.Event().wait(0.1)
            pyautogui.typewrite(url, interval=0.02)
            pyautogui.press("enter")
            return f"Navigated to {url}"
        except Exception as e: return f"Failed: {e}"

    @risk_tier(RiskTier.TIER_0)
    def _tool_browser_refresh(self, args: dict[str, Any]) -> str:
        try:
            pyautogui.hotkey("ctrl", "r")
            return "Refreshed."
        except Exception as e: return f"Failed: {e}"

    @risk_tier(RiskTier.TIER_0)
    def _tool_set_timer(self, args: dict[str, Any]) -> str:
        """Sets a managed timer. args: {'duration_seconds': int, 'label': str}"""
        try:
            duration = int(args.get("duration_seconds", 60))
            timer_id = f"timer_{int(time.time())}"
            with self.brain.timers_lock:
                label = args.get("label") or f"Timer_{len(self.brain.active_timers) + 1}"
                def timer_worker():
                    self.brain._stop_event.wait(duration)
                    with self.brain.timers_lock:
                        if timer_id in self.brain.active_timers:
                            self.brain._safe_put(self.brain.tts_q, {"type": "SPEAK", "content": f"Sir, your timer for {label} is complete."})
                            del self.brain.active_timers[timer_id]
                thread = threading.Thread(target=timer_worker, daemon=True)
                self.brain.active_timers[timer_id] = {"thread": thread, "end_time": time.time() + duration, "duration": duration, "label": label}
                thread.start()
            self.brain._safe_put(self.brain.status_q, {"type": "WIDGET_SHOW", "content": "time"})
            self.brain._emit_time_update()
            return f"Timer '{label}' set for {duration} seconds."
        except Exception as e: return f"Failed to set timer: {e}"

    @risk_tier(RiskTier.TIER_0)
    def _tool_cancel_timer(self, args: dict[str, Any]) -> str:
        """Cancels an active timer by label or latest. args: {'label': str}"""
        label = args.get("label")
        target_id = None
        with self.brain.timers_lock:
            if label:
                for tid, tdata in self.brain.active_timers.items():
                    if tdata["label"].lower() == label.lower():
                        target_id = tid
                        break
            elif self.brain.active_timers: target_id = list(self.brain.active_timers.keys())[-1]
            if target_id:
                label = self.brain.active_timers[target_id]["label"]
                del self.brain.active_timers[target_id]
        if target_id:
            self.brain._emit_time_update()
            return f"Timer '{label}' has been terminated, Sir."
        return "No active timers found."

    @risk_tier(RiskTier.TIER_0)
    def _tool_start_stopwatch(self, args: dict[str, Any]) -> str:
        """Starts a stopwatch. args: {'name': str}"""
        name = args.get("name") or "default"
        with self.brain.timers_lock: self.brain.active_stopwatches[name] = time.time()
        self.brain._safe_put(self.brain.status_q, {"type": "WIDGET_SHOW", "content": "time"})
        self.brain._emit_time_update()
        return f"Stopwatch '{name}' initialized."

    @risk_tier(RiskTier.TIER_0)
    def _tool_check_stopwatch(self, args: dict[str, Any]) -> str:
        """Reports elapsed time on a stopwatch. args: {'name': str}"""
        name = args.get("name") or "default"
        with self.brain.timers_lock:
            if name in self.brain.active_stopwatches:
                elapsed = time.time() - self.brain.active_stopwatches[name]
                return f"Stopwatch '{name}': {elapsed:.2f} seconds."
        return "Not found."

    @risk_tier(RiskTier.TIER_0)
    def _tool_stop_stopwatch(self, args: dict[str, Any]) -> str:
        """Stops a stopwatch and reports final time. args: {'name': str}"""
        name = args.get("name") or "default"
        with self.brain.timers_lock:
            if name in self.brain.active_stopwatches:
                elapsed = time.time() - self.brain.active_stopwatches.pop(name)
                self.brain._emit_time_update()
                return f"Stopwatch '{name}' halted. Duration: {elapsed:.2f}s."
        return "Not found."

    @risk_tier(RiskTier.TIER_0)
    def _tool_set_alarm(self, args: dict[str, Any]) -> str:
        """Sets an alarm for a specific time. args: {'time': str, 'label': str}"""
        time_str = args.get("time")
        if not time_str: return "No time."
        try:
            from datetime import datetime, timedelta
            now = datetime.now()
            target_time = None
            for fmt in ("%I:%M %p", "%H:%M", "%I %p"):
                try:
                    target_time = datetime.strptime(time_str.upper(), fmt)
                    break
                except ValueError: continue
            if not target_time: return "Format unrecognized. Use 'HH:MM AM/PM'."
            target_dt = now.replace(hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0)
            if target_dt < now: target_dt += timedelta(days=1)
            delta = round((target_dt - now).total_seconds())
            return self._tool_set_timer({"duration_seconds": delta, "label": args.get("label", f"Alarm at {time_str}")})
        except Exception as e: return f"Alarm failed: {e}"

    @risk_tier(RiskTier.TIER_0)
    def _tool_system_reboot(self, args: dict[str, Any]) -> str:
        """Restarts the entire C.H.A.R.L.I.E. engine. Fires phoenix restart loop."""
        self.brain._emit_status("RELOADING")
        self.brain._safe_put(self.brain.status_q, {"type": "PHASE", "content": "RELOADING"})
        self.brain._safe_put(self.brain.tts_q, {"type": "SPEAK", "content": "Initiating system reload protocol. Flushing memory buffers. Reinitializing neural core. Coming back online momentarily, Sir."})
        threading.Event().wait(2.0)
        if self.brain.reboot_event: self.brain.reboot_event.set()
        return "Reload sequence initiated."

    @risk_tier(RiskTier.TIER_1)
    def _tool_close_app(self, args: dict[str, Any]) -> str:
        """Fuzzy-kills an application by name or executable."""
        app = args.get("name") or args.get("app")
        if not app: return "No app."
        logger.info("tool_close_app | app=%s", app)
        try:
            from charlie.tools.app_controller import UniversalAppController
            controller = UniversalAppController()
            # UniversalAppController.close_app(args: dict) -> str: pass a dict and
            # consume the str return directly (Req 5.6). The returned message
            # already names the affected application on failure (Req 5.7).
            return controller.close_app({"name": app})
        except Exception as e:
            logger.error("close_app_failed | %s | %s", app, e)
            return f"Error closing {app}: {str(e)}"

    @risk_tier(RiskTier.TIER_3)
    def _tool_delete_file(self, args: dict[str, Any]) -> str:
        path = args.get("path")
        if not path: return "No path."
        if not self._is_safe_path(path):
            return "Access denied: path is outside project root."
        p = Path(path)
        if p.exists():
            p.unlink()
            return "Deleted."
        return "Not found."

    @risk_tier(RiskTier.TIER_0)
    def _tool_save_report(self, args: dict[str, Any]) -> str:
        content = args.get("content", "")
        filename = args.get("filename") or f"report_{int(time.time())}.txt"
        # Path traversal check
        if ".." in filename or "/" in filename or "\\" in filename:
            return "Error: Invalid filename — path separators and '..' are not allowed."
        path = Path("charlie/reports") / filename
        resolved = path.resolve()
        reports_root = Path("charlie/reports").resolve()
        if not resolved.is_relative_to(reports_root):
            return "Error: Path traversal detected."
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Report saved to {path}"

    @risk_tier(RiskTier.TIER_1)
    def _tool_create_rule(self, args: dict[str, Any]) -> str:
        """Create a new automation rule."""
        from charlie.automation.models import AutomationRule
        from charlie.automation.models import RiskTier as ART
        name = args.get("name")
        trigger = args.get("trigger")
        condition = args.get("condition", "True")
        action = args.get("action")
        risk = args.get("risk_tier", 0)
        if not all([name, trigger, action]):
            return "Error: name, trigger, and action are required."
        rule = AutomationRule(
            name=name, trigger=trigger, condition=condition,
            action=action, risk_tier=ART(risk),
            description=args.get("description", ""),
        )
        if hasattr(self.brain, 'rule_engine'):
            self.brain.rule_engine.add_rule(rule)
            self.brain.rule_engine.save_rules()  # Persist to disk
            return f"Rule '{name}' created: {trigger} -> {action}"
        return "Error: Rule engine not available."

    @risk_tier(RiskTier.TIER_0)
    def _tool_list_rules(self, args: dict[str, Any]) -> str:
        """List all automation rules."""
        if not hasattr(self.brain, 'rule_engine'):
            return "Error: Rule engine not available."
        rules = self.brain.rule_engine.get_all_rules()
        if not rules:
            return "No automation rules configured."
        lines = []
        for r in rules:
            status = "ON" if r.enabled else "OFF"
            lines.append(f"[{status}] {r.name}: {r.trigger} -> {r.action} (risk={r.risk_tier.name})")
        return "\n".join(lines)

    @risk_tier(RiskTier.TIER_1)
    def _tool_disable_rule(self, args: dict[str, Any]) -> str:
        """Disable an automation rule by name."""
        name = args.get("name")
        if not name:
            return "Error: rule name is required."
        if hasattr(self.brain, 'rule_engine'):
            rule = self.brain.rule_engine.get_rule(name)
            if rule:
                rule.enabled = False
                self.brain.rule_engine.save_rules()  # Persist to disk
                return f"Rule '{name}' disabled."
            return f"Rule '{name}' not found."
        return "Error: Rule engine not available."

    @risk_tier(RiskTier.TIER_0)
    def _tool_mcp_list_servers(self, args: dict[str, Any]) -> str:
        """List all configured MCP servers and their status."""
        if not hasattr(self.brain, 'mcp_manager'):
            return "Error: MCP manager not available."
        servers = self.brain.mcp_manager.servers
        if not servers:
            return "No MCP servers configured. Add servers to charlie_config.json under 'mcp_servers'."
        lines = []
        for name, client in servers.items():
            status = "connected" if client.connected else "disconnected"
            enabled = "enabled" if client.enabled else "disabled"
            tools = len(client.tools) if client.connected else 0
            lines.append(f"  {name}: {status}, {enabled}, {tools} tools")
        return "MCP Servers:\n" + "\n".join(lines)

    @risk_tier(RiskTier.TIER_1)
    def _tool_mcp_enable_server(self, args: dict[str, Any]) -> str:
        """Enable an MCP server by name."""
        name = args.get("name")
        if not name:
            return "Error: server name is required."
        if not hasattr(self.brain, 'mcp_manager'):
            return "Error: MCP manager not available."
        client = self.brain.mcp_manager.servers.get(name)
        if not client:
            return f"MCP server '{name}' not found."
        client.enabled = True
        return f"MCP server '{name}' enabled. It will connect on next tool call."

    @risk_tier(RiskTier.TIER_1)
    def _tool_mcp_disable_server(self, args: dict[str, Any]) -> str:
        """Disable an MCP server by name."""
        name = args.get("name")
        if not name:
            return "Error: server name is required."
        if not hasattr(self.brain, 'mcp_manager'):
            return "Error: MCP manager not available."
        client = self.brain.mcp_manager.servers.get(name)
        if not client:
            return f"MCP server '{name}' not found."
        client.enabled = False
        return f"MCP server '{name}' disabled."

    @risk_tier(RiskTier.TIER_0)
    def _tool_agent_status(self, args: dict[str, Any]) -> str:
        """Get the status of all agents in the orchestrator."""
        if not hasattr(self.brain, 'orchestrator'):
            return "Error: Agent orchestrator not available."
        registry = self.brain.orchestrator.agent_registry
        agents = registry.get_all_agents()
        if not agents:
            return "No agents registered."
        lines = []
        for agent in agents:
            lines.append(f"  {agent.name}: {agent.description} | Tools: {', '.join(agent.tools[:5])}...")
        return "Agents:\n" + "\n".join(lines)

    @risk_tier(RiskTier.TIER_0)
    def _tool_open_globe(self, args: dict[str, Any]) -> str:
        """Open the 3D World Map globe in the browser. Shows news, earthquakes, weather, calendar, and more."""
        try:
            if hasattr(self.brain, 'globe_server') and self.brain.globe_server:
                if not self.brain.globe_server.is_running:
                    # Start the server
                    import asyncio
                    loop = self.brain.loop
                    if loop and loop.is_running():
                        asyncio.run_coroutine_threadsafe(
                            self.brain.globe_server.start(), loop
                        )
                    else:
                        asyncio.run(self.brain.globe_server.start())
                # Open browser
                self.brain.globe_server.open_browser()
                return "Opening 3D World Map, Sir. The globe will load at http://localhost:8089"
            else:
                return "Globe server not initialized."
        except Exception as e:
            return f"Failed to open globe: {e}"

    @risk_tier(RiskTier.TIER_0)
    def _tool_globe_status(self, args: dict[str, Any]) -> str:
        """Get the current status of the 3D World Map globe."""
        try:
            if hasattr(self.brain, 'globe_server') and self.brain.globe_server:
                gs = self.brain.globe_server
                status = "Running" if gs.is_running else "Stopped"
                clients = len(gs._ws_clients) if hasattr(gs, '_ws_clients') else 0
                data = gs.data_provider.get_all_data()
                counts = {k: len(v) for k, v in data.items() if isinstance(v, list)}
                return (
                    f"Globe Status: {status}\n"
                    f"WebSocket Clients: {clients}\n"
                    f"Data Layers: {counts}\n"
                    f"URL: http://localhost:{gs.port}"
                )
            return "Globe server not initialized."
        except Exception as e:
            return f"Globe status error: {e}"

    @risk_tier(RiskTier.TIER_0)
    def _tool_refresh_globe(self, args: dict[str, Any]) -> str:
        """Refresh the 3D World Map globe data (calendar, memory, workspace) and push to connected browsers."""
        try:
            import aiohttp
            import asyncio

            async def _do_refresh():
                async with aiohttp.ClientSession() as session:
                    # Trigger data refresh + WS broadcast
                    async with session.post("http://localhost:8090/api/globe/refresh", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if not resp.ok:
                            return f"Refresh failed: HTTP {resp.status}"
                    # Fetch current data to report accurate counts
                    async with session.get("http://localhost:8090/api/globe/data", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.ok:
                            data = await resp.json()
                            cal = len(data.get("calendar", []))
                            mem = len(data.get("memory", []))
                            return f"Globe data refreshed. Calendar: {cal} events, Memory: {mem} nodes."
                        return "Globe refresh complete."

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(_do_refresh())

            future = asyncio.run_coroutine_threadsafe(_do_refresh(), loop)
            try:
                result = future.result(timeout=GLOBE_REFRESH_TIMEOUT)
            except TimeoutError:
                return "Globe refresh timed out."
            return result if result else "Globe refresh complete."

        except Exception as e:
            return f"Failed to refresh globe: {e}"

    @risk_tier(RiskTier.TIER_0)
    def _tool_browser_control(self, args: dict[str, Any]) -> str:
        if hasattr(self.brain, 'browser_controller'):
            return self.brain.browser_controller.handle_control(args)
        return "Browser controller not available."

    @risk_tier(RiskTier.TIER_0)
    def _tool_app_focus(self, args: dict[str, Any]) -> str:
        if hasattr(self.brain, 'app_controller'):
            return self.brain.app_controller.focus_window(args)
        return "App controller not available."

    @risk_tier(RiskTier.TIER_0)
    def _tool_app_list(self, args: dict[str, Any]) -> str:
        if hasattr(self.brain, 'app_controller'):
            return self.brain.app_controller.list_running_apps(args)
        return "App controller not available."

    @risk_tier(RiskTier.TIER_1)
    def _tool_app_kill(self, args: dict[str, Any]) -> str:
        if hasattr(self.brain, 'app_controller'):
            return self.brain.app_controller.kill_process(args)
        return "App controller not available."

    @risk_tier(RiskTier.TIER_0)
    def _tool_browser_new_tab(self, args: dict[str, Any]) -> str:
        if hasattr(self.brain, 'browser_controller'):
            return self.brain.browser_controller.new_tab(args)
        return "Browser controller not available."

    @risk_tier(RiskTier.TIER_0)
    def _tool_browser_close_tab(self, args: dict[str, Any]) -> str:
        if hasattr(self.brain, 'browser_controller'):
            return self.brain.browser_controller.close_tab(args)
        return "Browser controller not available."

    @risk_tier(RiskTier.TIER_0)
    def _tool_browser_switch_next(self, args: dict[str, Any]) -> str:
        if hasattr(self.brain, 'browser_controller'):
            args["direction"] = "next"
            return self.brain.browser_controller.switch_tab(args)
        return "Browser controller not available."

    @risk_tier(RiskTier.TIER_0)
    def _tool_browser_switch_previous(self, args: dict[str, Any]) -> str:
        if hasattr(self.brain, 'browser_controller'):
            args["direction"] = "previous"
            return self.brain.browser_controller.switch_tab(args)
        return "Browser controller not available."

    @risk_tier(RiskTier.TIER_0)
    def _tool_browser_go_back(self, args: dict[str, Any]) -> str:
        if hasattr(self.brain, 'browser_controller'):
            return self.brain.browser_controller.go_back(args)
        return "Browser controller not available."

    @risk_tier(RiskTier.TIER_0)
    def _tool_browser_go_forward(self, args: dict[str, Any]) -> str:
        if hasattr(self.brain, 'browser_controller'):
            return self.brain.browser_controller.go_forward(args)
        return "Browser controller not available."

    @risk_tier(RiskTier.TIER_0)
    def _tool_browser_scroll(self, args: dict[str, Any]) -> str:
        if hasattr(self.brain, 'browser_controller'):
            text = args.get("text", "").lower()
            if "up" in text: args["direction"] = "up"
            else: args["direction"] = "down"
            return self.brain.browser_controller.scroll_page(args)
        return "Browser controller not available."

    @risk_tier(RiskTier.TIER_0)
    def _tool_browser_type(self, args: dict[str, Any]) -> str:
        if hasattr(self.brain, 'browser_controller'):
            return self.brain.browser_controller.type_text(args)
        return "Browser controller not available."

    @risk_tier(RiskTier.TIER_0)
    def _tool_browser_click(self, args: dict[str, Any]) -> str:
        if hasattr(self.brain, 'browser_controller'):
            return self.brain.browser_controller.click_element(args)
        return "Browser controller not available."

    @risk_tier(RiskTier.TIER_0)
    def _tool_browser_screenshot(self, args: dict[str, Any]) -> str:
        if hasattr(self.brain, 'browser_controller'):
            return self.brain.browser_controller.take_screenshot(args)
        return "Browser controller not available."

    @risk_tier(RiskTier.TIER_0)
    def _tool_browser_zoom(self, args: dict[str, Any]) -> str:
        if hasattr(self.brain, 'browser_controller'):
            return self.brain.browser_controller.zoom_page(args)
        return "Browser controller not available."

    @risk_tier(RiskTier.TIER_0)
    def _tool_browser_bookmarks(self, args: dict[str, Any]) -> str:
        if hasattr(self.brain, 'browser_controller'):
            return self.brain.browser_controller.open_bookmarks(args)
        return "Browser controller not available."

    @risk_tier(RiskTier.TIER_0)
    def _tool_browser_history(self, args: dict[str, Any]) -> str:
        if hasattr(self.brain, 'browser_controller'):
            return self.brain.browser_controller.open_history(args)
        return "Browser controller not available."

    @risk_tier(RiskTier.TIER_0)
    def _tool_browser_clear_cache(self, args: dict[str, Any]) -> str:
        if hasattr(self.brain, 'browser_controller'):
            return self.brain.browser_controller.clear_cache(args)
        return "Browser controller not available."

    @risk_tier(RiskTier.TIER_0)
    def _tool_deps_analyze(self, args: dict[str, Any]) -> str:
        if hasattr(self.brain, 'research_toolkit'):
            return self.brain.research_toolkit.analyze_dependencies(args)
        return "Research toolkit not available."

    @risk_tier(RiskTier.TIER_0)
    def _tool_read_gmail(self, args: dict[str, Any]) -> str:
        """Read recent emails using Gmail integration."""
        if not hasattr(self, "_gmail_poller"):
            from charlie.integrations.gmail import GmailIntegration
            self._gmail_poller = GmailIntegration()

        # Check credentials before attempting fetch
        if not self._gmail_poller.service and not self._gmail_poller.connect():
            return (
                "Gmail credentials missing or invalid. "
                "Please configure OAuth in config/secure/credentials.json."
            )

        limit = args.get("limit", 5)
        query = args.get("query", "is:unread")
        msgs = self._gmail_poller.fetch(max_results=limit, query=query)
        return json.dumps(msgs) if msgs else "No messages found."

    @risk_tier(RiskTier.TIER_1)
    def _tool_send_gmail(self, args: dict[str, Any]) -> str:
        """Send an email via Gmail. Gated at TIER_1 (requires confirmation)."""
        if not hasattr(self, "_gmail_poller"):
            from charlie.integrations.gmail import GmailIntegration
            self._gmail_poller = GmailIntegration()

        # Check credentials before attempting send
        if not self._gmail_poller.service and not self._gmail_poller.connect():
            return (
                "Gmail credentials missing or invalid. "
                "Please configure OAuth in config/secure/credentials.json."
            )

        to = args.get("to")
        subject = args.get("subject", "No Subject")
        body = args.get("body", "")
        if not to:
            return "Error: 'to' address is required to send an email."

        success = self._gmail_poller.execute("send_email", to=to, subject=subject, body=body)
        if success:
            return f"Email sent to {to}."
        return "Failed to send email. Check logs for details."

    @risk_tier(RiskTier.TIER_1)
    def _tool_manage_notion(self, args: dict[str, Any]) -> str:
        """Manage Notion integration."""
        if not hasattr(self, "_notion_poller"):
            from charlie.integrations.notion import NotionIntegration
            self._notion_poller = NotionIntegration()

        # Check credentials before attempting action
        if not self._notion_poller.client and not self._notion_poller.connect():
            return (
                "Notion credentials missing or invalid. "
                "Please set the NOTION_TOKEN environment variable."
            )

        action = args.get("action")
        if action == "fetch":
            pages = self._notion_poller.fetch(limit=args.get("limit", 5))
            import json
            return json.dumps(pages) if pages else "No Notion pages found."

        filtered = {k: v for k, v in args.items() if k != "action"}
        success = self._notion_poller.execute(action, **filtered)
        return "Notion action successful." if success else "Notion action failed."

    @risk_tier(RiskTier.TIER_0)
    def _tool_search_conversations(self, args: dict[str, Any]) -> str:
        """Search past conversations using full-text search. args: {'query': str, 'limit': int}"""
        query = args.get("query", "").strip()
        if not query:
            return "No search query provided."

        limit = args.get("limit", 5)
        session_search = getattr(self.brain, "session_search", None)
        if not session_search:
            return "Session search not available."

        results = session_search.search(query, limit=limit)
        if not results:
            return f"No past conversations found matching '{query}'."

        output = [f"### Past Conversations Matching '{query}':"]
        for i, r in enumerate(results, 1):
            role = r.get("role", "?")
            content = r.get("content", "")[:200]
            timestamp = r.get("timestamp", 0)
            ts_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp)) if timestamp else "unknown"
            output.append(f"{i}. [{ts_str}] **{role}**: {content}")

        return "\n".join(output)
