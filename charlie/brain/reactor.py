import asyncio
import time
import queue
import os
from enum import Enum
from typing import Optional
from charlie.utils.logger import get_logger
from charlie.security.tiers import RiskTier
from charlie.config import settings
import psutil
import pygetwindow as gw
from datetime import datetime
from charlie.utils.system import get_vram_used_mb

logger = get_logger(__name__)

from charlie.brain.constants import (  # noqa: E402
    CONFIRMATION_KEYWORDS,
    CORRECTION_KEYWORDS,
    TOPIC_MAP,
)


class InputType(Enum):
    TEXT = "USER_TEXT"
    PROACTIVE_EVENT = "PROACTIVE_EVENT"
    IMAGE = "IMAGE"
    TOOL_RESULT = "TOOL_RESULT"
    INTERRUPT = "INTERRUPT"
    STATUS = "STATUS"
    HEARTBEAT = "HEARTBEAT"
    UNKNOWN = "UNKNOWN"


class Priority(int, Enum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


INTERRUPT_ANNOUNCEMENTS = [
    "Apologies for the interruption, Sir. {event}. Resuming previous task.",
    "Pardon the intrusion, Sir. {event}. Standing by to continue.",
    "Momentary diversion, Sir. {event}. Previous task preserved.",
    "Forgive the interruption. {event}. Shall I pick up where we left off?",
]


class Reactor:
    def __init__(self, brain):
        self.brain = brain
        self.last_responses = []
        self._max_response_history = 10
        self._interrupted_context = None
        self._current_priority = Priority.NORMAL

    def _detect_topic(self, text: str) -> str:
        """Heuristically maps user text to the closest topic key."""
        text_lower = text.lower()
        for topic, keywords in TOPIC_MAP.items():
            if any(k in text_lower for k in keywords):
                return topic
        return "general"

    def _detect_repetition(self, response: str) -> bool:
        """Detects if the proposed response is too similar to the last few."""
        if not response or len(response) < 10:
            return False
        from difflib import SequenceMatcher
        for prev in self.last_responses:
            ratio = SequenceMatcher(None, response.lower(), prev.lower()).ratio()
            if ratio > 0.85:
                return True
        return False

    def _track_response(self, response: str):
        """Adds response to short-term history."""
        if response:
            self.last_responses.append(response)
            if len(self.last_responses) > self._max_response_history:
                self.last_responses.pop(0)

    async def process_query(self, text: str, source: str = "local", progress_cb=None) -> Optional[str]:
        """Public API - delegates to _dispatch."""
        message = {"type": "USER_TEXT", "content": text, "source": source}
        return await self._dispatch(message, progress_cb=progress_cb)

    async def _dispatch(self, message: dict, progress_cb=None) -> Optional[str]:
        """Route message to appropriate handler with error boundary."""
        msg_type = message.get("type", "USER_TEXT")
        try:
            input_type = InputType(msg_type)
        except ValueError:
            input_type = InputType.UNKNOWN

        handler_map = {
            InputType.TEXT: lambda: self._handle_text(message.get("content", ""), message.get("source", "local"), progress_cb),
            InputType.PROACTIVE_EVENT: lambda: self._handle_proactive(message),
            InputType.IMAGE: lambda: self._handle_image(message),
            InputType.TOOL_RESULT: lambda: self._handle_tool_result(message),
            InputType.INTERRUPT: lambda: self._handle_interrupt(message),
        }

        handler = handler_map.get(input_type)
        if handler is None:
            logger.warning("reactor_unknown_type | type=%s", msg_type)
            return None

        try:
            return await handler()
        except Exception as e:
            logger.error("reactor_handler_crash | type=%s | %s", input_type, e, exc_info=True)
            return None

    async def _handle_text(self, text: str, source: str = "local", progress_cb=None) -> Optional[str]:
        """Handle USER_TEXT input."""
        self.brain.is_busy = True
        effective_source = source
        try:
            if not text or len(text.strip()) < 3:
                return None

            text_clean = text.lower().strip().rstrip(".,!?")

            # Track implicit user signals (thanks, no, wrong, rephrase)
            if hasattr(self.brain, "outcome_tracker") and self.brain.outcome_tracker:
                from charlie.intelligence.outcome_tracker import OutcomeTracker
                signal = OutcomeTracker.detect_signal(text)
                if signal:
                    self.brain.outcome_tracker.record_user_signal(signal, {"source": source})

            # Determine effective source
            if source == "system":
                is_at_pc = self.brain.context_builder.is_user_at_pc()
                effective_source = "local" if is_at_pc else "telegram"
            elif source == "telegram":
                effective_source = "telegram"
            else:
                effective_source = source

            # Echo Guard
            if effective_source == "local":
                time_since_wake = time.time() - getattr(self.brain, "_last_wake_time", 0)
                if text_clean in ["charlie", "hey charlie"] and time_since_wake < 5.0:
                    return None

            # Standby Logic
            if getattr(self.brain, "standby_mode", False):
                wake_phrases = ["wake up", "system online", "charlie online", "cancel standby", "charlie"]
                if any(w in text_clean for w in wake_phrases):
                    self.brain.standby_mode = False
                    if self.brain.audio_cmd_q:
                        self.brain._safe_put(self.brain.audio_cmd_q, {"type": "SET_STANDBY", "value": False})
                    self.brain._emit_status("IDLE", source=effective_source)
                    # continue processing
                else:
                    return None

            self.brain.interrupt_event.clear()
            self.brain.chain_mgr.clear_chain()

            # Keywords (Reload/Standby/Shutdown/Clear)
            if "reload" in text_clean and len(text_clean) < 15:
                if self.brain.reboot_event: self.brain.reboot_event.set()
                self.brain.running = False
                return "Reloading engine, Sir."

            self.brain.system_prompt = self.brain.context_builder.get_system_prompt_cached()
            memory_context = self.brain.memory.get_context_injection(text)

            # 3. Add to history (with sanitization)
            sanitized_text = self.brain._sanitize_user_input(text)
            if sanitized_text and isinstance(sanitized_text, str):
                self.brain.history.append({
                    "role": "user",
                    "content": sanitized_text,
                    "source": effective_source
                })
            else:
                logger.warning("dropped_empty_user_input | text=%s", text)
                return None

            # Prune and Execute Chain
            lag_check_task = asyncio.create_task(self.brain.stream_handler.monitor_inference_lag(effective_source))
            try:
                current_history = self.brain.history[-10:]
                self.brain.chain_mgr.start_chain(goal=text, source=effective_source)

                sent_sentences_global = set()
                cumulative_chat_text_ref = [""]

                async with self.brain.async_llm_lock:
                    final_response_text = await self.brain.chain_mgr.execute_chain(
                        self.brain, text, memory_context, current_history, sent_sentences_global, cumulative_chat_text_ref
                    )

                if final_response_text:
                    # Commit assistant response to history
                    self.brain.history.append({
                        "role": "assistant",
                        "content": final_response_text,
                    })
                    self.brain._save_history()

                    # Skill nudge: check if session should be saved as a skill
                    try:
                        chain_ctx = self.brain.chain_mgr.get_active_context()
                        if chain_ctx and hasattr(self.brain, "skill_nudge"):
                            step_count = getattr(chain_ctx, "current_step_count", 0)
                            if self.brain.skill_nudge.should_nudge(step_count):
                                import threading
                                def _nudge_bg():
                                    try:
                                        summary = {
                                            "steps": [{"tool": s.tool_name, "args": s.args, "output": str(s.output)[:500]} for s in chain_ctx.steps],
                                            "tools_used": [s.tool_name for s in chain_ctx.steps],
                                        }
                                        self.brain.skill_nudge.review_session(summary, llm_client=self.brain.llm_client)
                                    except Exception:
                                        pass
                                threading.Thread(target=_nudge_bg, daemon=True).start()
                    except Exception:
                        pass

                    # Final commit - only if different from what was already streamed
                    if final_response_text.strip().lower() != cumulative_chat_text_ref[0].strip().lower():
                        self.brain._safe_put(self.brain.status_q, {
                            "type": "CHAT_MSG",
                            "speaker": "CHARLIE",
                            "content": final_response_text,
                            "stream": False # Explicitly commit the message
                        })

                    # ── Presence Routing ──
                    if effective_source.startswith("telegram") and self.brain.telegram_q:
                        self.brain._safe_put(self.brain.telegram_q, {
                            "type": "CHAT_MSG",
                            "speaker": "CHARLIE",
                            "content": final_response_text
                        })
            finally:
                if not lag_check_task.done(): lag_check_task.cancel()

            # Keyword detection for trust management
            topic = self._detect_topic(text_clean)
            if any(k in text_clean for k in CORRECTION_KEYWORDS):
                self.brain.relationship.log_event("user_correction", f"User corrected Charlie on {topic}")
            if any(k in text_clean for k in CONFIRMATION_KEYWORDS):
                self.brain.relationship.log_event("user_confirmation", f"User confirmed output on {topic}")

            # Trigger preference extraction (drift engine)
            asyncio.create_task(self.brain.drift.extract_preferences(self.brain.history))

            return final_response_text or ""
        finally:
            self.brain._safe_put(self.brain.tts_q, {"type": "TURN_END"})
            self.brain.is_busy = False

    async def _handle_proactive(self, message: dict) -> Optional[str]:
        """Handle PROACTIVE_EVENT input."""
        content = message.get("content", "")
        source = message.get("source", "system")
        return await self.process_query(content, source=source)

    async def _handle_image(self, message: dict) -> Optional[str]:
        """Handle IMAGE input — forward to vision handler."""
        source = message.get("source", "unknown")
        logger.info("reactor_image_received | source=%s", source)
        try:
            if hasattr(self.brain, 'vision_handler') and self.brain.vision_handler:
                query = message.get("query", "Describe this image in detail")
                result = await self.brain.vision_handler.describe_image(
                    image_data=message.get("data", b""),
                    query=query,
                )
                return result
            return "Vision handler not available, Sir."
        except Exception as e:
            logger.error("reactor_image_failed | %s", e)
            return f"Image analysis failed: {e}"

    async def _handle_tool_result(self, message: dict) -> Optional[str]:
        """Handle TOOL_RESULT input — log and relay result."""
        tool = message.get("tool", "unknown")
        result = message.get("result", "")
        logger.info("reactor_tool_result | tool=%s | len=%d", tool, len(str(result)))
        if result:
            return str(result)
        return None

    async def _handle_interrupt(self, message: dict) -> Optional[str]:
        """Handle INTERRUPT input with context preservation and announcement."""
        # Save current context if a task is active
        active_task = getattr(self.brain, '_active_query_task', None)
        if active_task and not active_task.done():
            self._interrupted_context = {
                "history_snapshot": list(self.brain.history[-10:]),
                "original_task": message.get("content", ""),
                "timestamp": time.time(),
            }

        self.brain.interrupt_event.set()
        self.brain.chain_mgr.clear_chain()
        if active_task and not active_task.done():
            self.brain._active_query_task.cancel()
        while not self.brain.tts_q.empty():
            try:
                self.brain.tts_q.get_nowait()
            except Exception:
                break

        # JARVIS-style announcement
        import random
        reason = message.get("reason", message.get("content", "Unknown event"))
        template = random.choice(INTERRUPT_ANNOUNCEMENTS)
        announcement = template.format(event=reason)

        if self.brain.telegram_q:
            self.brain._safe_put(self.brain.telegram_q, {
                "type": "CHAT_MSG", "speaker": "CHARLIE", "content": announcement
            })

        return announcement

    def _check_priority_preempt(self, task: dict) -> bool:
        """Check if a task should preempt the current one. Returns True if preempted."""
        priority_str = task.get("priority", "normal")
        try:
            priority = Priority[priority_str.upper()]
        except (KeyError, AttributeError):
            priority = Priority.NORMAL

        if priority.value < Priority.HIGH.value:
            return False

        active_task = getattr(self.brain, '_active_query_task', None)
        if not active_task or active_task.done():
            return False

        # Save context before preempting
        self._interrupted_context = {
            "history_snapshot": list(self.brain.history[-10:]),
            "original_task": task.get("content", ""),
            "timestamp": time.time(),
        }
        self.brain._active_query_task.cancel()
        self.brain.chain_mgr.clear_chain()
        logger.info("priority_preempt | priority=%s | task=%s", priority.name, task.get("content", "")[:50])
        return True

    def _announce_resumption(self, completed_task: str):
        """Send a JARVIS-style resumption announcement after a priority interrupt."""
        if not self._interrupted_context:
            return
        self._interrupted_context = None
        import random
        template = random.choice(INTERRUPT_ANNOUNCEMENTS)
        announcement = template.format(event=completed_task[:80])
        if self.brain.telegram_q:
            self.brain._safe_put(self.brain.telegram_q, {
                "type": "CHAT_MSG", "speaker": "CHARLIE", "content": announcement
            })

    async def main_async_loop(self):
        """Central event loop for brain processing, delegated from Brain."""
        logger.info("brain_async_loop_ignited")
        self.brain.running = True
        self.brain._active_query_task = None

        try:
            while self.brain.running:
                # ── 1. Confirmation Timeout Check ──
                if self.brain.awaiting_confirmation:
                    tier = self.brain.awaiting_confirmation.get("tier", RiskTier.TIER_1)
                    timeout_seconds = {RiskTier.TIER_1: 30, RiskTier.TIER_2: 60, RiskTier.TIER_3: 120}.get(tier, 30)
                    if time.time() - self.brain.last_confirmation_time > timeout_seconds:
                        self.brain.awaiting_confirmation = None
                        timeout_msg = "Confirmation timed out, Sir. Operation aborted."
                        self.brain._safe_put(self.brain.tts_q, {"type": "SPEAK", "content": timeout_msg})
                        self.brain._safe_put(self.brain.tts_q, {"type": "TURN_END"})
                        if self.brain.telegram_q:
                            self.brain._safe_put(self.brain.telegram_q, {"type": "CHAT_MSG", "speaker": "CHARLIE", "content": f"⏱️ {timeout_msg}"})
                            self.brain._safe_put(self.brain.telegram_q, {"type": "CLEAR_CONFIRMATION"})

                # ── 2. Queue Polling ──
                try:
                    task = self.brain.brain_task_q.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.01)
                    continue

                # ── 3. Task Dispatching ──
                try:
                    mtype = task.get("type")
                    if self.brain.heartbeat:
                        self.brain.heartbeat.value = time.time()

                    # ── 2.1 Periodic News Sync ──
                    if time.time() - self.brain.news_last_update > 600: # Every 10 mins
                        self.brain.news_last_update = time.time()
                        async def _sync():
                            req_id = f"news_sync_{int(time.time())}"
                            if self.brain.browser_req_q:
                                self.brain._safe_put(self.brain.browser_req_q, {"type": "NEWS", "id": req_id, "data": {"silent": True}})
                                # Wait briefly for response in background? No, let the handler update it.
                        asyncio.create_task(_sync())

                    if mtype == "TEXT":
                        text = task.get("content", "").strip()
                        source = task.get("source", "local")

                        # Deduplication
                        now = time.time()
                        if hasattr(self.brain, "_last_text") and self.brain._last_text == text.lower() and (now - getattr(self.brain, "_last_text_time", 0)) < 2.0:
                            continue
                        self.brain._last_text = text.lower()
                        self.brain._last_text_time = now

                        # Priority preemption — critical/high tasks interrupt active work
                        self._check_priority_preempt(task)

                        if source == "local":
                            self.brain._safe_put(self.brain.status_q, {"type": "CHAT_MSG", "speaker": "SIR", "content": text})

                        is_cancellation = any(k in text.lower() for k in ["stop", "abort", "cancel"])
                        if self.brain._active_query_task and not self.brain._active_query_task.done():
                            self.brain._active_query_task.cancel()
                            if self.brain.status_q: self.brain._safe_put(self.brain.status_q, {"type": "PHASE", "content": "IDLE"})

                        if not is_cancellation:
                            _raw_priority = task.get("priority", "normal")
                            priority = _raw_priority if _raw_priority in ("low", "normal", "high", "critical") else "normal"
                            async def _run_and_resume(p, s, t):
                                result = await self.process_query(t, source=s)
                                if p in ("high", "critical"):
                                    self._announce_resumption(t)
                                return result
                            self.brain._active_query_task = asyncio.create_task(_run_and_resume(priority, source, text))

                    elif mtype in ("PROACTIVE_EVENT", "PROACTIVE_HELP"):
                        content = task.get("content", "")
                        source = task.get("source", "system")

                        # ANTI-LOOP: Don't trigger proactive help if already busy or conversation is active
                        if mtype == "PROACTIVE_HELP":
                            if self.brain.is_busy or self.brain.conversation_active:
                                logger.debug("proactive_help_suppressed | brain_busy=%s", self.brain.is_busy)
                                continue
                            # STERNER INSTRUCTION: Avoid recursive task loops
                            content = (
                                f"(SYSTEM: PROACTIVE INTERVENTION. User behavior suggests frustration: {content}. "
                                "IGNORE all previous technical goals or news searches. "
                                "Acknowledge the frustration directly, offer support, and wait for Sir's input. "
                                "Be very concise. DO NOT call any tools unless Sir explicitly asks now.)"
                            )

                        asyncio.create_task(self._dispatch({"type": "PROACTIVE_EVENT", "content": content, "source": source}))

                    elif mtype == "REMOTE_VOICE":
                        path = task.get("path")
                        if path and os.path.exists(path):
                            try:
                                transcribe_fn = getattr(self.brain.model_manager, 'transcribe_file', None)
                                transcription = transcribe_fn(path) if transcribe_fn else None
                                if transcription:
                                    asyncio.create_task(self.process_query(transcription, source="telegram"))
                                else:
                                    self.brain._safe_put(self.brain.telegram_q, {"type": "CHAT_MSG", "speaker": "CHARLIE", "content": "Audio unclear, Sir."})
                            except Exception as e: logger.error("remote_voice_failed | %s", e)
                            finally:
                                try: os.remove(path)
                                except OSError: pass

                    elif mtype == "CONFIRMATION_RESULT":
                        if self.brain.awaiting_confirmation:
                            pending = self.brain.awaiting_confirmation
                            self.brain.confirmation_result = task.get("confirmed", False)
                            self.brain.awaiting_confirmation = None
                            self.brain.confirmation_event.set()
                            if not self.brain.confirmation_result:
                                self.brain.relationship.log_event("action_aborted", f"Sir aborted TIER {pending.get('tier', '?')} action: {pending.get('tool')}")
                                self.brain._safe_put(self.brain.tts_q, {"type": "SPEAK", "content": "Operation aborted, Sir."})
                                self.brain._safe_put(self.brain.tts_q, {"type": "TURN_END"})
                            else:
                                self.brain.relationship.log_event("action_confirmed", f"Sir confirmed TIER {pending.get('tier', '?')} action")

                    elif mtype == "INTERRUPT":
                        self.brain.interrupt_event.set()
                        if self.brain._active_query_task and not self.brain._active_query_task.done():
                            self.brain._active_query_task.cancel()
                        while not self.brain.tts_q.empty():
                            try: self.brain.tts_q.get_nowait()
                            except Exception: break

                    elif mtype == "HARD_SHUTDOWN":
                        self.brain.running = False
                        return

                    elif mtype == "SENSORY_READY":
                        async def delayed_welcome():
                            await asyncio.sleep(2.5)
                            await self.run_welcome_protocol()
                            self.brain.conversation_active = True
                            if self.brain.audio_cmd_q:
                                self.brain._safe_put(self.brain.audio_cmd_q, {"type": "LISTENING"})

                        asyncio.create_task(delayed_welcome())

                    elif mtype == "SHUTDOWN":
                        if self.brain.reboot_event: self.brain.reboot_event.set()
                        self.brain.running = False
                        return

                except Exception as e:
                    logger.error("reactor_dispatch_err | %s", e)

        except Exception as e:
            logger.error("reactor_loop_err | %s", e)
            self.brain.running = False

    async def run_welcome_protocol(self) -> None:
        """
        Welcome Protocol.
        Gathers system vitals, active windows, and recent history to generate
         a personalized startup greeting.
        """
        try:
            # 1. Gather System Vitals
            used_vram = get_vram_used_mb()
            limit_vram = getattr(settings.llm, "vram_limit_mb", 8192)
            vram_pct = min(100.0, (used_vram / limit_vram) * 100)

            cpu = psutil.cpu_percent()
            ram = psutil.virtual_memory().percent

            # 2. Gather Active Windows
            try:
                titles = [w.title for w in gw.getAllWindows() if w.visible and w.title][
                    :10
                ]
                windows_str = ", ".join(titles)
            except Exception as e:
                logger.debug("window_enumeration_failed | %s", e)
                windows_str = "None"

            # 3. Read Last Session & Mobile Context
            now = datetime.now()
            time_ctx = (
                f"Time: {now.strftime('%I:%M %p')}, Date: {now.strftime('%A, %B %d')}"
            )
            mobile_context = ""
            recent_telegram = [
                m for m in self.brain.history[-10:] if m.get("source") == "telegram"
            ]
            if recent_telegram:
                mobile_context = f"\nRECENT MOBILE CONTEXT (Telegram): {recent_telegram[-1]['content']}"

            # 4. Generate Prompt (Diversity Protocol)
            prompt = (
                f"CONTEXT: {time_ctx}\n"
                f"SYSTEM VITALS: VRAM {vram_pct:.1f}%, CPU {cpu}%, RAM {ram}%\n"
                f"ACTIVE WINDOWS: {windows_str}\n"
                f"{mobile_context}\n"
                "Task: Provide exactly ONE short, warm, professional greeting for 'Sir'. "
                "If RECENT MOBILE CONTEXT is provided, briefly reference it (e.g. 'I've noted your mobile request regarding...'). "
                "Otherwise, mention a system stat or the time. Max 2 sentences."
            )

            # 5. Get LLM response (Higher temperature for variety)
            response = await self.brain.stream_handler.simple_llm_call(prompt, temp=0.7)
            clean_response = (response or "").strip().replace('"', "").replace("'", "")

            # 6. Dispatch to status_q and TTS
            if clean_response:
                vitals = {
                    "VRAM": f"{vram_pct:.1f}%",
                    "CPU": f"{cpu}%",
                    "RAM": f"{ram}%",
                }
                self.brain._safe_put(
                    self.brain.status_q,
                    {
                        "type": "WELCOME_SUMMARY",
                        "content": clean_response,
                        "stats": vitals,
                    },
                )
                self.brain._safe_put(
                    self.brain.status_q,
                    {
                        "type": "WIDGET_SHOW",
                        "content": "welcome",
                        "stats": vitals,
                        "msg_content": clean_response,
                    },
                )
                self.brain._safe_put(self.brain.tts_q, {"type": "SPEAK", "content": clean_response})

                # Auto-hide welcome widget
                async def _delayed_hide():
                    await asyncio.sleep(3)
                    self.brain._safe_put(self.brain.status_q, {"type": "WIDGET_HIDE", "content": "welcome"})
                asyncio.create_task(_delayed_hide())

            logger.info(
                f"welcome_protocol_executed | content_len={len(clean_response)}"
            )

        except Exception as e:
            logger.error("welcome_protocol_failed | %s", e)
            fallback = "System ready. Welcome back, Sir."
            self.brain._safe_put(self.brain.tts_q, {"type": "SPEAK", "content": fallback})

    @staticmethod
    def is_task_status_query(text: str) -> bool:
        """Check if the user is asking about running tasks."""
        lower = text.lower().strip()
        patterns = [
            "what are you working on",
            "what tasks are running",
            "what's running",
            "show tasks",
            "list tasks",
            "task status",
            "what are you doing",
        ]
        return any(p in lower for p in patterns)

    @staticmethod
    def parse_task_cancel(text: str) -> str | None:
        """Try to extract a task name to cancel. Returns None if not a cancel command."""
        lower = text.lower().strip()
        cancel_prefixes = ["stop the ", "cancel the ", "kill the ", "abort the "]
        for prefix in cancel_prefixes:
            if lower.startswith(prefix):
                return lower[len(prefix):].strip()
        if lower.startswith("stop ") and len(lower.split()) <= 4:
            return lower.replace("stop ", "").strip()
        return None
