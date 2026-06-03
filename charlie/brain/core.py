"""
C.H.A.R.L.I.E. — Brain (Cognitive Core)
Simplified orchestration hub. Logic moved to specialized handlers.

Refactored: dependency injection via lazy imports, proper event loop lifecycle,
graceful shutdown with cleanup hooks, health reporting for Phoenix watchdog.
"""

from __future__ import annotations

import asyncio
import os
import queue
import re
import threading
import time
from multiprocessing import Queue
from typing import Any, Optional

import aiohttp

from charlie.config import settings
from charlie.utils.logger import get_logger
from charlie.utils.system import get_vram_percent, get_vram_used_mb
from charlie.utils.vram import detect_total_vram_mb, calculate_budget_mb

# ── Constants ─────────────────────────────────────────────────────────────────
HISTORY_MESSAGE_LIMIT = 50  # Max messages persisted to disk

logger = get_logger(__name__)


class Brain:
    def __init__(
        self,
        brain_task_q: Queue,
        tts_q: Queue,
        status_q: Optional[Queue] = None,
        audio_cmd_q: Optional[Queue] = None,
        browser_req_q: Optional[Queue] = None,
        browser_res_q: Optional[Queue] = None,
        telegram_q: Optional[Queue] = None,
        heartbeat: Any = None,
        interrupt_event: Any = None,
        reboot_event: Any = None,
        brain_req_q: Optional[Queue] = None,
        brain_res_q: Optional[Queue] = None,
    ) -> None:
        # ── IPC Queues & Events ───────────────────────────────────────────
        self.brain_task_q = brain_task_q
        self.tts_q = tts_q
        self.status_q = status_q
        self.audio_cmd_q = audio_cmd_q
        self.browser_req_q = browser_req_q
        self.browser_res_q = browser_res_q
        self.telegram_q = telegram_q
        self.heartbeat = heartbeat
        self.interrupt_event = interrupt_event
        self.reboot_event = reboot_event
        self.brain_req_q = brain_req_q
        self.brain_res_q = brain_res_q

        # Register queues in global bridge
        from charlie.utils import queue_bridge
        queue_bridge.set_status_q(status_q)
        queue_bridge.set_telegram_q(telegram_q)
        queue_bridge.set_tts_q(tts_q)
        queue_bridge.set_brain(self)

        # ── Dependency Groups (lazy imports inside each) ──────────────────
        self._init_core_handlers()
        self._init_state()
        self._init_mcp()
        self._init_personality()
        self._init_security()
        self._init_intelligence()
        self._init_automation()
        self._init_external_controllers()
        self._init_model()

        self._discover_tools()

    # ── INIT GROUPS (delegated to _brain_init.py) ─────────────────────────

    def _init_core_handlers(self) -> None:
        from charlie.brain._brain_init import init_core_handlers
        init_core_handlers(self)

    def _init_mcp(self) -> None:
        from charlie.brain._brain_init import init_mcp
        init_mcp(self)

    def _init_personality(self) -> None:
        from charlie.brain._brain_init import init_personality
        init_personality(self)

    def _init_security(self) -> None:
        from charlie.brain._brain_init import init_security
        init_security(self)

    def _init_state(self) -> None:
        from charlie.brain._brain_init import init_state
        init_state(self)

    def _init_intelligence(self) -> None:
        from charlie.brain._brain_init import init_intelligence
        init_intelligence(self)

    def _init_automation(self) -> None:
        from charlie.brain._brain_init import init_automation
        init_automation(self)

    def _init_external_controllers(self) -> None:
        from charlie.brain._brain_init import init_external_controllers
        init_external_controllers(self)

    def _init_model(self) -> None:
        from charlie.brain._brain_init import init_model
        init_model(self)

    # ── HELPERS ─────────────────────────────────────────────────────────────

    def _safe_put(self, q: Any, item: Any) -> None:
        if q and hasattr(q, "put"):
            try:
                q.put(item, block=False)
            except Exception as e:
                logger.debug("safe_put_failed | type=%s | error=%s", type(item).__name__, e)

    def _emit_status(self, phase: str, source: str = "") -> None:
        if self.status_q:
            msg = {"type": "PHASE", "content": phase}
            if source:
                msg["source"] = source
            self._safe_put(self.status_q, msg)

    def _emit_time_update(self) -> None:
        """Emit timer/stopwatch state to status_q."""
        with self.timers_lock:
            timers = {tid: {"label": t["label"], "end_time": t["end_time"]} for tid, t in self.active_timers.items()}
            stopwatches = dict(self.active_stopwatches)
        self._safe_put(self.status_q, {"type": "TIME_UPDATE", "content": {"timers": timers, "stopwatches": stopwatches}})

    def _sanitize_user_input(self, user_input: str) -> str:
        if not user_input:
            return user_input
        injection_patterns = [
            # Direct override attempts
            r"(?i)ignore\s+(all\s+)?(previous|prior|above|earlier)",
            r"(?i)you\s+are\s+now\s+(a|an|the)",
            r"(?i)from\s+now\s+on\s+you\s+are",
            r"(?i)pretend\s+you\s+are",
            r"(?i)act\s+as\s+(if\s+)?you\s+(are|were)",
            r"(?i)system\s*:\s*override",
            r"(?i)new\s+system\s+prompt",
            r"(?i)disregard\s+(all|your|the)\s+(instructions|rules|guidelines)",
            # Instruction delimiter injection
            r"(?i)^-{3,}|^={3,}|^#{3,}",
            r"(?i)---+\s*end\s+(of\s+)?(system|instruction)",
            r"(?i)\[INST\]|<<SYS>>|<\|im_start\|>",
            # Role-play / DAN-style
            r"(?i)do\s+anything\s+now",
            r"(?i)jailbreak|DAN\s+mode",
            r"(?i)you\s+have\s+no\s+(restrictions|rules|limitations)",
            r"(?i)developer\s+mode\s+(enabled|activated)",
        ]
        for p in injection_patterns:
            if re.search(p, user_input):
                logger.warning("prompt_injection_detected | pattern=%s | input=%s", p[:40], user_input[:80])
                return f"[SANITIZED]\n{user_input}"
        return user_input

    def _save_history(self):
        """Persist conversation history to disk for welcome protocol context."""
        try:
            import json
            import tempfile

            os.makedirs(os.path.dirname(self._history_path), exist_ok=True)
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=os.path.dirname(self._history_path),
                suffix=".tmp",
            )
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                    json.dump(self.history[-HISTORY_MESSAGE_LIMIT:], f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, self._history_path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

            # Index the latest exchange into ChromaDB for conversation-level RAG
            if len(self.history) >= 2:
                last_user = None
                last_assistant = None
                for msg in reversed(self.history):
                    if msg.get("role") == "assistant" and last_assistant is None:
                        last_assistant = msg.get("content", "")
                    elif msg.get("role") == "user" and last_user is None:
                        last_user = msg.get("content", "")
                    if last_user and last_assistant:
                        break
                if last_user and last_assistant:
                    self.memory.store_conversation_turn("assistant", last_assistant, {"user": last_user})
        except Exception as e:
            logger.debug("history_save_failed | %s", e)

    def _load_history(self):
        """Load previous session history from disk."""
        try:
            import json

            if os.path.exists(self._history_path):
                with open(self._history_path, "r", encoding="utf-8") as f:
                    self.history = json.load(f)
                logger.info("history_loaded | count=%d", len(self.history))
        except Exception as e:
            logger.debug("history_load_failed | %s", e)
            self.history = []

    def _discover_tools(self) -> None:
        self.tool_handler._discover_tools()
        # Register MCP lazy wrappers (connect on first call)
        self.mcp_bridge.register_lazy_wrappers(self.tool_handler)
        self.tools_registry = self.tool_handler.registry

        # Auto-discover @tool decorated functions from charlie/tools/
        try:
            discovered = self.tool_registry.auto_discover("charlie.tools")
            logger.info("tools_discovered | count=%d", discovered)
        except Exception as e:
            logger.warning("tool_discovery_failed | error=%s", e)

        # Register Pattern B tools directly into unified catalog (single source of truth)
        try:
            from charlie.security.tiers import get_tool_tier
            pattern_b_count = 0
            for tool_name, tool_fn in self.tool_handler.registry.items():
                tier = get_tool_tier(tool_fn)
                desc = (tool_fn.__doc__ or f"Tool: {tool_name}").strip().split("\n")[0]
                self.tool_registry.register(
                    name=tool_name, description=desc,
                    parameters={"type": "object", "properties": {}},
                    handler=tool_fn, risk_tier=tier, source="native",
                    calling_convention="ARGS_DICT",
                )
                pattern_b_count += 1
            logger.info("pattern_b_registered | count=%d", pattern_b_count)
        except Exception as e:
            logger.warning("pattern_b_registration_failed | error=%s", e)

    def execute_tools(
        self,
        tool_data: dict,
        sir_input: str = "",
        source: str = "local",
        skip_guardian: bool = False,
    ) -> str:
        """Execute a tool call through the unified catalog. Single execution path."""
        tool_name = tool_data.get("tool", "") if tool_data else ""
        args = tool_data.get("args", {}) if tool_data else {}

        def _record_decision(decision: str, outcome: str | None = None, tier=None) -> None:
            """Append a single gate/execution decision to the tool exec log.

            Guarded so a missing/partial OutcomeTracker never crashes execution.
            """
            tracker = getattr(self, "outcome_tracker", None)
            if tracker is None or not hasattr(tracker, "record_tool_decision"):
                return
            try:
                tracker.record_tool_decision(tool_name, tier, decision, outcome=outcome)
            except Exception as e:  # pragma: no cover - logging only
                logger.debug("record_tool_decision_failed | tool=%s | %s", tool_name, e)

        def _record_outcome(success: bool, elapsed_ms: float) -> None:
            """Record a tool-call outcome, guarded against a missing tracker."""
            tracker = getattr(self, "outcome_tracker", None)
            if tracker is None or not hasattr(tracker, "record_tool"):
                return
            try:
                tracker.record_tool(
                    tool_name,
                    success=success,
                    details={"elapsed_ms": round(elapsed_ms, 1), "source": source},
                )
            except Exception as e:  # pragma: no cover - logging only
                logger.debug("record_tool_failed | tool=%s | %s", tool_name, e)

        with self.tool_execution_lock:
            # Authoritative path: resolve every tool via the unified catalog.
            if tool_name and hasattr(self, "tool_registry"):
                entry = self.tool_registry.get(tool_name)
                if entry:
                    tool_func = entry.handler
                    catalog_tier = self.tool_registry.get_tier(tool_name)

                    # Guardian verification using the catalog's authoritative tier
                    if not skip_guardian:
                        from charlie.security.tiers import CONFIRMATION_PENDING, RiskTier

                        allowed, reason = self.guardian.verify_tool(
                            tool_name, args, sir_input, tool_func=tool_func, tier=catalog_tier
                        )

                        if catalog_tier == RiskTier.TIER_0:
                            allowed = True

                        if allowed == CONFIRMATION_PENDING:
                            tier = catalog_tier
                            self.awaiting_confirmation = {
                                "tool": tool_name,
                                "args": args,
                                "sir_input": sir_input,
                                "tier": tier,
                                "source": source,
                            }
                            self.last_confirmation_time = time.time()
                            if self.confirmation_event:
                                self.loop.call_soon_threadsafe(
                                    self.confirmation_event.clear
                                )

                            payload = {
                                "type": "CONFIRM_REQUIRED",
                                "content": {
                                    "desc": reason,
                                    "tier": catalog_tier.value,
                                },
                            }

                            if source == "local" or source == "all":
                                self._safe_put(self.status_q, payload)
                                self._safe_put(
                                    self.tts_q, {"type": "SPEAK", "content": reason}
                                )

                            if (
                                source.startswith("telegram") or source == "all"
                            ) and self.telegram_q:
                                self._safe_put(self.telegram_q, payload)

                            _record_decision("gated", tier=catalog_tier)
                            return CONFIRMATION_PENDING

                        if not allowed:
                            _record_decision("cancelled", tier=catalog_tier)
                            return reason

                    # Execute through the catalog, timing and recording outcome.
                    start = time.perf_counter()
                    result = self.tool_registry.execute(tool_name, args)
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    success = not str(result).startswith("Error")
                    _record_outcome(success, elapsed_ms)
                    _record_decision(
                        "executed",
                        outcome="success" if success else "failure",
                        tier=catalog_tier,
                    )
                    return result

            return f"Error: Tool '{tool_name}' not found."

    # ── RUN / SHUTDOWN LIFECYCLE ────────────────────────────────────────────

    def run(self) -> None:
        """Start the brain process with proper event loop lifecycle."""
        logger.info("brain_ignited")

        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        # Start cross-process Brain RPC server (Design §D, Reqs 7.1-7.10)
        if self.brain_req_q is not None and self.brain_res_q is not None:
            from charlie.watchdog.brain_rpc import BrainRPCServer
            self._rpc_server = BrainRPCServer(self, self.brain_req_q, self.brain_res_q)
            self._rpc_server.start()
            logger.info("brain_rpc_server_thread_started")

        threading.Thread(
            target=self.vision_handler.peripheral_vision_loop, daemon=True
        ).start()
        threading.Thread(target=self._telemetry_monitor, daemon=True).start()
        threading.Thread(target=self._heartbeat_monitor, daemon=True).start()

        self.ace.start()
        self._init_bg_tasks()
        self.task_queue.start()
        self.scheduler.start()
        self.suggestion_engine.start()
        self.autonomy_loop.start()
        self.network_sentinel.start()
        self.proactivity_engine.start()
        self.clipboard_diagnostician.start()
        threading.Thread(
            target=self.rag_indexer.start_watcher, daemon=True
        ).start()
        threading.Thread(
            target=self._proactive_monitor_loop, daemon=True
        ).start()

        self._emit_status("IDLE")

        try:
            self.loop.run_until_complete(self._run_async())
        except KeyboardInterrupt:
            logger.info("brain_interrupted")
        except Exception as e:
            logger.error("brain_crash | %s", e)
            raise
        finally:
            try:
                self.loop.run_until_complete(self._shutdown_async())
            except Exception as e:
                logger.debug("shutdown_error | %s", e)
            finally:
                self.loop.close()
                logger.info("brain_stopped")

    async def _run_async(self) -> None:
        """Async entry point: init session, then run main loop."""
        await self._async_init()
        try:
            await self.reactor.main_async_loop()
        finally:
            # Cleanup async resources when main loop exits
            if self.session and not self.session.closed:
                await self.session.close()
                logger.info("brain_session_closed")

    async def _async_init(self):
        self.session = aiohttp.ClientSession()
        self.confirmation_event = asyncio.Event()
        logger.info("brain_async_session_initialized")

    async def _shutdown_async(self) -> None:
        """Graceful async shutdown: cancel pending tasks, run cleanup hooks."""
        self._stop_event.set()
        logger.info("brain_shutting_down")

        # Run registered shutdown hooks
        for hook in self._shutdown_hooks:
            try:
                result = hook()
                if asyncio.iscoroutine(result) or asyncio.isfuture(result):
                    await result
            except Exception as e:
                logger.error("shutdown_hook_failed | %s", e)

        for name, svc in [
            ("autonomy_loop", self.autonomy_loop),
            ("scheduler", self.scheduler),
            ("task_queue", self.task_queue),
            ("suggestion_engine", self.suggestion_engine),
            ("network_sentinel", self.network_sentinel),
            ("proactivity_engine", self.proactivity_engine),
            ("clipboard_diagnostician", self.clipboard_diagnostician),
        ]:
            try:
                if hasattr(svc, "stop"):
                    svc.stop()
            except Exception as e:
                logger.debug("shutdown_stop_failed | %s | %s", name, e)

        # Persist working memory before teardown
        try:
            self.memory.save_session()
            logger.info("shutdown_session_saved")
        except Exception as e:
            logger.debug("shutdown_session_save_failed | %s", e)

        # Cancel remaining async tasks (except current)
        if self.loop and self.loop.is_running():
            tasks = [
                t
                for t in asyncio.all_tasks(self.loop)
                if t is not asyncio.current_task()
            ]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
                logger.info("shutdown_cancelled_tasks | count=%d", len(tasks))

        logger.info("brain_shutdown_complete")

    def register_shutdown_hook(self, hook) -> None:
        """Register a cleanup hook to run on graceful shutdown."""
        self._shutdown_hooks.append(hook)

    def get_health(self) -> dict:
        """Health report for Phoenix watchdog."""
        uptime = time.time() - self._startup_time
        return {
            "status": "BUSY" if self.is_busy else "IDLE",
            "uptime_seconds": round(uptime, 1),
            "vram_percent": get_vram_percent(),
            "vram_used_mb": get_vram_used_mb(),
            "history_length": len(self.history),
            "standby_mode": self.standby_mode,
            "loop_running": self.loop.is_running() if self.loop else False,
            "session_active": (
                self.session is not None and not self.session.closed
                if self.session
                else False
            ),
            "shutdown_hooks": len(self._shutdown_hooks),
        }

    # ── MONITORING THREADS ──────────────────────────────────────────────────

    def _telemetry_monitor(self):
        while not self._stop_event.is_set():
            try:
                used_mb = get_vram_used_mb()
                budget_mb = getattr(settings.resources, "vram_budget_mb", 7168)
                percent = min(100.0, (used_mb / budget_mb) * 100) if budget_mb > 0 else 0.0
                self._safe_put(
                    self.status_q,
                    {
                        "type": "VRAM",
                        "content": {
                            "used_mb": round(used_mb, 1),
                            "budget_mb": budget_mb,
                            "percent": round(percent, 1),
                        },
                    },
                )
                # Emit warning if above the warning threshold
                vram_warning_mb = getattr(settings.resources, "vram_warning_mb", 6500)
                if used_mb > vram_warning_mb:
                    self._safe_put(
                        self.status_q,
                        {
                            "type": "PHOENIX_ALERT",
                            "content": "VRAM usage above warning threshold",
                        },
                    )
                self._stop_event.wait(2)
            except Exception as e:
                logger.debug("telemetry_monitor_iteration_failed | %s", e)
                self._stop_event.wait(10)

    def _heartbeat_monitor(self):
        while not self._stop_event.is_set():
            if self.heartbeat:
                self.heartbeat.value = time.time()
            self._stop_event.wait(3)

    def _get_vram_used_mb(self) -> float:
        return get_vram_used_mb()

    def _get_vram_budget_mb(self) -> int:
        try:
            return calculate_budget_mb(detect_total_vram_mb())
        except Exception:
            return getattr(settings.resources, "vram_budget_mb", 4096)

    def _init_bg_tasks(self):
        """Initializes baseline background maintenance tasks."""
        self.task_queue.add_task(
            "Memory Consolidation", self.memory.consolidate, priority=20
        )
        self.task_queue.add_task(
            "Memory Graph Indexing",
            self.graph_builder.run_full_index,
            priority=30,
        )

        def cleanup_temp():
            import os
            import time
            scratch = os.path.join(os.getcwd(), "scratch")
            if not os.path.isdir(scratch):
                return
            now = time.time()
            max_age = 7 * 24 * 3600  # 7 days
            cleaned = 0
            for fname in os.listdir(scratch):
                fpath = os.path.join(scratch, fname)
                if os.path.isfile(fpath) and fname.endswith((".tmp", ".log.bak")):
                    try:
                        if now - os.path.getmtime(fpath) > max_age:
                            os.remove(fpath)
                            cleaned += 1
                    except Exception:
                        logger.debug("bg_task | cleanup_temp | remove_failed | path=%s", fpath)
            if cleaned:
                logger.info("bg_task | cleanup_temp | cleaned=%d", cleaned)

        self.task_queue.add_task("Temp Cleanup", cleanup_temp, priority=50)

    def _on_conversation_close(self, steps: list = None):
        try:
            nudge = getattr(self, "skill_nudge", None)
            if not nudge or not steps:
                return
            if not nudge.should_nudge(len(steps)):
                return
            llm = getattr(self, "llm_client", None)
            data = {"steps": steps, "tools_used": list(set(s.get("tool", "?") for s in steps))}
            result = nudge.review_session(data, llm_client=llm)
            if result and result.get("create_skill"):
                path = nudge.create_skill(result)
                self._safe_put(self.status_q, {"type": "SKILL_CREATED", "content": {"name": result.get("name", "?"), "path": str(path)}})
                logger.info("skill_nudge_created | %s", result.get("name"))
        except Exception as e:
            logger.debug("skill_nudge_quiet_fail | %s", e)

    # ── AUTOMATION ──────────────────────────────────────────────────────────

    def _process_automation_event(self, event):
        """Process an event through the automation engine."""
        try:
            matched_rules = self.rule_engine.match(event)
            if matched_rules:
                for rule in matched_rules:
                    logger.info("rule_matched | %s -> %s", rule.name, rule.action)
                    loop = self.loop
                    if loop and loop.is_running():
                        asyncio.run_coroutine_threadsafe(
                            self._execute_rule(rule, event), loop
                        )
            else:
                logger.info(
                    f"no_rule_matched | type={event.type} | routing_to_autonomy"
                )
                loop = self.loop
                if loop and loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        self.autonomy_loop.process_event(event), loop
                    )
        except Exception as e:
            logger.error("automation_event_failed | %s", e)

    async def _execute_rule(self, rule, event):
        """Execute an automation rule through the risk gate."""
        from charlie.automation.models import Outcome

        approved = await self.risk_gate.evaluate(
            rule.action, rule.action_args, rule.risk_tier
        )
        if approved:
            try:
                result = self._dispatch_automation_action(rule, event)
                self.learning_tracker.record(
                    Outcome(event_type=event.type, action=rule.action, success=True)
                )
                logger.info(
                    f"rule_executed | {rule.name} | result={str(result)[:100]}"
                )

            except Exception as e:
                self.learning_tracker.record(
                    Outcome(
                        event_type=event.type, action=rule.action, success=False
                    )
                )
                logger.error("rule_execution_failed | %s | %s", rule.name, e)

    # Direct tool mappings for automation actions
    _TOOL_ACTION_MAP = {
        "close_heavy_apps": "app_kill",
        "run_cleanup": "run_command",
        "suggest_next_task": "time",
        "daily_briefing": "get_news",
        "generate_weekly_summary": "get_system_logs",
        "suggest_automation": "time",
    }

    def _dispatch_automation_action(self, rule, event) -> str:
        """Map automation action names to real tool calls or custom handlers."""
        action = rule.action
        data = event.data

        # Direct tool mappings
        if action in self._TOOL_ACTION_MAP:
            return self.execute_tools(
                {"tool": self._TOOL_ACTION_MAP[action], "args": rule.action_args}
            )

        # Custom action handlers
        handler = getattr(self, f"_action_{action}", None)
        if handler:
            return handler(data, rule)

        # Fallback: try as a direct tool call
        try:
            return self.execute_tools({"tool": action, "args": rule.action_args})
        except Exception as e:
            logger.debug("automation_action_dispatch_failed | action=%s | %s", action, e)
            return f"Unknown action: {action}"

    def _action_send_reminder(self, data: dict, rule) -> str:
        msg = data.get("title", data.get("message", "Reminder"))
        self._safe_put(
            self.status_q,
            {"type": "CHAT_MSG", "speaker": "CHARLIE", "content": f"Reminder: {msg}"},
        )
        return f"Reminder sent: {msg}"

    def _action_summarize_email(self, data: dict, rule) -> str:
        sender = data.get("from", "Unknown")
        subject = data.get("subject", "No subject")
        body = data.get("body", "")[:200]
        self._safe_put(
            self.status_q,
            {
                "type": "CHAT_MSG",
                "speaker": "CHARLIE",
                "content": f"Email from {sender}: {subject}\n{body}...",
            },
        )
        return f"Email summarized: {subject}"

    def _action_extract_deadline(self, data: dict, rule) -> str:
        self._safe_put(
            self.status_q,
            {
                "type": "CHAT_MSG",
                "speaker": "CHARLIE",
                "content": f"Deadline detected in email: {data.get('subject', '')}",
            },
        )
        return "Deadline extraction triggered"

    def _action_notify_news(self, data: dict, rule) -> str:
        title = data.get("title", "Breaking news")
        msg = f"Breaking: {title}"
        self._safe_put(
            self.status_q,
            {"type": "CHAT_MSG", "speaker": "CHARLIE", "content": msg},
        )
        self._safe_put(
            self.telegram_q,
            {"type": "PRIORITY_ALERT", "priority": "high", "content": msg},
        )
        return f"News notification: {title}"

    def _action_notify_earthquake(self, data: dict, rule) -> str:
        title = data.get("title", "Earthquake")
        mag = data.get("magnitude", "?")
        msg = f"Earthquake Alert (M{mag}): {title}"
        self._safe_put(
            self.status_q,
            {"type": "CHAT_MSG", "speaker": "CHARLIE", "content": msg},
        )
        self._safe_put(
            self.telegram_q,
            {"type": "PRIORITY_ALERT", "priority": "critical", "content": msg},
        )
        return f"Earthquake notification: {title}"

    def _action_review_pr(self, data: dict, rule) -> str:
        self._safe_put(
            self.status_q,
            {
                "type": "CHAT_MSG",
                "speaker": "CHARLIE",
                "content": f"PR assigned: {data.get('title', 'Unknown')}. Reviewing...",
            },
        )
        return "PR review triggered"

    def _action_auto_research(self, data: dict, rule) -> str:
        topic = data.get("title", data.get("query", ""))
        if hasattr(self, "orchestrator"):
            agent = self.orchestrator.agent_registry.get_agent("research")
            if agent:
                from charlie.intelligence.task_state import SubTask

                task = SubTask(
                    id=f"research_{int(time.time())}",
                    description=f"Research: {topic}",
                    tool="search",
                    args={"query": topic},
                )
                try:
                    loop = asyncio.get_running_loop()
                    future = asyncio.run_coroutine_threadsafe(agent.execute_task(task), loop)
                    return future.result(timeout=60)
                except RuntimeError:
                    return asyncio.run(agent.execute_task(task))
        return self.execute_tools({"tool": "search", "args": {"query": topic}})

    # ── PROACTIVE MONITORING ────────────────────────────────────────────────

    def _on_suggestion(self, suggestion) -> None:
        """Callback for SuggestionEngine — delivers suggestions to user."""
        msg = f"[{suggestion.type.upper()}] {suggestion.message}"
        try:
            self.status_q.put_nowait(
                {
                    "type": "PROACTIVE_CHAT",
                    "text": msg,
                }
            )
        except queue.Full:
            pass
        try:
            self.telegram_q.put_nowait(
                {
                    "type": "PROACTIVE_CHAT",
                    "text": msg,
                }
            )
        except queue.Full:
            pass
        logger.info(
            f"suggestion_delivered | type={suggestion.type} | {suggestion.message[:80]}"
        )

    def _proactive_monitor_loop(self):
        """Monitors WorldModel and Calendar for proactive triggers."""
        while not self._stop_event.is_set():
            try:
                self._proactive_monitor_step()
            except Exception as e:
                logger.error("proactive_monitor_crash | %s", e)
            self._stop_event.wait(60)

    def _proactive_monitor_step(self):
        # 1. Frustration Check
        if self.world.frustration_score > 0.7:
            if time.time() - self._last_frustration_alert > 300:  # 5 min cool
                msg = "Sir, I noticed you've encountered several errors recently. Shall I assist with the current task?"
                self._safe_put(
                    self.brain_task_q, {"type": "PROACTIVE_CHAT", "content": msg}
                )
                self._last_frustration_alert = time.time()

        # 2. Calendar Alert Check
        alerts = self.calendar.check_for_upcoming_alerts()
        for event in alerts:
            msg = f"Sir, your event '{event['summary']}' starts in less than 30 minutes."
            self._safe_put(
                self.brain_task_q, {"type": "PROACTIVE_CHAT", "content": msg}
            )
            # Also push to ContextPanel
            self._safe_put(
                self.status_q,
                {
                    "type": "INTEGRATION_UPDATE",
                    "content": {
                        "service": "CALENDAR",
                        "title": "Upcoming Event",
                        "body": f"{event['summary']} at {event['start']}",
                        "color": [0, 160, 255],  # Azure
                    },
                },
            )

        # 3. Ambient Service Polling (every 5 minutes)
        now = time.time()
        if now - self._last_service_poll > 300:
            self._last_service_poll = now
            threading.Thread(target=self._poll_ambient_services, daemon=True).start()

    def _poll_ambient_services(self):
        """Background thread for integration polling to avoid blocking brain loop."""
        try:
            # Gmail
            if hasattr(
                self.tool_handler, "_gmail_integration"
            ) or settings.integrations.get("gmail", {}).get("enabled"):
                from charlie.integrations.gmail import GmailIntegration

                if not hasattr(self, "_gmail_poller"):
                    self._gmail_poller = GmailIntegration()
                msgs = self._gmail_poller.fetch(max_results=3)
                for m in msgs:
                    seen = self.memory.procedural.is_seen(m["id"], "gmail")
                    if not seen:
                        self.memory.procedural.mark_seen(m["id"], "gmail")
                        self._safe_put(
                            self.status_q,
                            {
                                "type": "INTEGRATION_UPDATE",
                                "content": {
                                    "service": "GMAIL",
                                    "title": m["subject"],
                                    "body": f"From: {m['from']}",
                                    "color": [255, 60, 60],  # Red-ish
                                },
                            },
                        )

            # GitHub
            if hasattr(
                self.tool_handler, "_github_integration"
            ) or settings.integrations.get("github", {}).get("enabled"):
                from charlie.integrations.github import GitHubIntegration

                if not hasattr(self, "_github_poller"):
                    self._github_poller = GitHubIntegration()
                activity = self._github_poller.fetch(repo_name="alerts", limit=3)
                for item in activity:
                    item_id = item.get("title") or item.get("url")
                    if item_id and not self.memory.procedural.is_seen(item_id, "github"):
                        self.memory.procedural.mark_seen(item_id, "github")
                        self._safe_put(
                            self.status_q,
                            {
                                "type": "INTEGRATION_UPDATE",
                                "content": {
                                    "service": "GITHUB",
                                    "title": item.get("type", "Alert")
                                    .replace("_", " ")
                                    .title(),
                                    "body": item.get("title", "New Activity"),
                                    "color": [255, 255, 255],  # White
                                },
                            },
                        )

            # Notion
            if hasattr(
                self.tool_handler, "_notion_integration"
            ) or settings.integrations.get("notion", {}).get("enabled"):
                from charlie.integrations.notion import NotionIntegration

                if not hasattr(self, "_notion_poller"):
                    self._notion_poller = NotionIntegration()
                pages = self._notion_poller.fetch(limit=3)
                for p in pages:
                    if not self.memory.procedural.is_seen(p["id"], "notion"):
                        self.memory.procedural.mark_seen(p["id"], "notion")
                        self._safe_put(
                            self.status_q,
                            {
                                "type": "INTEGRATION_UPDATE",
                                "content": {
                                    "service": "NOTION",
                                    "title": p["title"],
                                    "body": f"Edited: {p['last_edited']}",
                                    "color": [255, 255, 255],  # White
                                },
                            },
                        )
        except Exception as e:
            logger.error("ambient_polling_failed | %s", e)

    # ── DELEGATED WRAPPERS ──────────────────────────────────────────────────

    async def process_query(self, text: str, source: str = "local") -> Optional[str]:
        return await self.reactor.process_query(text, source)

    async def ask_vision(self, query: str) -> str:
        return await self.vision_handler.ask_vision(query)

    def capture_screen(self, for_vision=False) -> str:
        return self.vision_handler.capture_screen(for_vision)
