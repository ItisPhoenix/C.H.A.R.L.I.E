from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import json
import re
import time
import asyncio
import queue
from charlie.brain.llm_client import RateLimitExceeded
from charlie.utils.logger import get_logger
from charlie.security.tiers import CONFIRMATION_PENDING, RiskTier, get_tool_tier
from charlie.config import settings
from charlie.brain.tool_call_parser import ToolCallParser, is_phantom_phrase

logger = get_logger(__name__)


@dataclass
class StepResult:
    step_number: int
    tool_name: str
    args: Dict[str, Any]
    output: str
    tier: RiskTier
    error: bool = False


@dataclass
class ChainContext:
    goal: str
    steps: List[StepResult] = field(default_factory=list)
    max_steps: int = 6
    snapshot_hash: Optional[str] = None
    source: str = "local"  # Tag for routing (local vs telegram)

    @property
    def current_step_count(self) -> int:
        return len(self.steps)

    def should_nudge(self, threshold: int = 5) -> bool:
        """Check if this chain should trigger a skill nudge."""
        return self.current_step_count >= threshold

    def get_session_summary(self) -> dict:
        """Return a structured summary of this chain's execution."""
        tools_used = []
        steps_data = []
        for i, step in enumerate(self.steps):
            tools_used.append(step.tool_name)
            steps_data.append(
                {
                    "step": i + 1,
                    "tool": step.tool_name,
                    "args": step.args if hasattr(step, "args") else {},
                    "output": step.output[:500] if hasattr(step, "output") else "",
                    "success": not step.error if hasattr(step, "error") else True,
                }
            )
        return {
            "goal": self.goal,
            "source": self.source,
            "tools_used": list(dict.fromkeys(tools_used)),  # Dedupe, preserve order
            "steps": steps_data,
            "total_steps": self.current_step_count,
        }

    def to_llm_context(self, brain_ref: Any = None) -> str:
        """Formats the chain history for the LLM with environmental awareness."""
        is_remote = self.source.startswith("telegram")

        # Environmental Awareness
        if is_remote:
            env_context = (
                "CONTEXT: Sir is currently MOBILE/REMOTE via Telegram. "
                "He CANNOT see your PC screen or dashboard. "
                "You MUST retrieve any requested data using tools (like get_system_status or browser_search) "
                "and summarize the results directly in your final_answer using plain English. "
                "DO NOT attempt to use local dashboard widgets for data display."
            )
        else:
            active_win = getattr(brain_ref, "active_window", "Unknown") if brain_ref else "Unknown"
            env_context = f"SIR IS LOOKING AT: {active_win}"

        if not self.steps:
            return f"GOAL: {self.goal}\n{env_context}"

        history = []
        for s in self.steps:
            history.append(
                f"Step {s.step_number}: Called {s.tool_name}({json.dumps(s.args)}) -> Result: {str(s.output)[:1500]}"
            )

        return "\n".join(
            [
                f"GOAL: {self.goal}",
                env_context,
                "CHAIN HISTORY:",
                *history,
                f"\nNEXT STEP: {self.current_step_count + 1}/{self.max_steps}",
            ]
        )


class ChainExecutor:
    def __init__(self):
        self.active_chains: Dict[str, ChainContext] = {}
        self._recovery_retries = 0
        self._last_status_emit: Dict[str, float] = {}
        self._last_bridge_time = 0.0
        self._emit_throttle = 1.0  # seconds between same-type updates
        self._parser = ToolCallParser()
        self._max_chain_depth = 10
        self._chain_step_timeout = 30

    def start_chain(self, goal: str, source: str = "local") -> ChainContext:
        ctx = ChainContext(goal=goal, source=source)
        # In a real app we might use a unique ID, but for now we track one main chain
        self.active_chains["primary"] = ctx
        logger.info(f"chain_started | goal='{goal[:50]}...' | source={source}")
        return ctx

    def get_active_context(self) -> Optional[ChainContext]:
        return self.active_chains.get("primary")

    def add_step(self, tool_name: str, args: Dict[str, Any], output: str, tier: RiskTier):
        ctx = self.get_active_context()
        if ctx:
            is_error = isinstance(output, str) and (output.startswith("Error") or "error" in output[:100].lower())
            step = StepResult(
                step_number=ctx.current_step_count + 1,
                tool_name=tool_name,
                args=args,
                output=output,
                tier=tier,
                error=is_error,
            )
            ctx.steps.append(step)
            logger.info("chain_step_added", step=step.step_number, tool=tool_name, error=is_error)

    def is_chain_complete(self) -> bool:
        ctx = self.get_active_context()
        if not ctx:
            return True

        # Early termination if we got a good answer
        if ctx.steps and any("final_answer" in str(s.output).lower() for s in ctx.steps[-2:]):
            return True

        return ctx.current_step_count >= ctx.max_steps

    def clear_chain(self):
        self.active_chains.pop("primary", None)
        logger.info("chain_cleared")

    def _prune_history_smart(self, history, max_tokens=3000):
        """Keep recent messages + important system context"""
        if not history:
            return []

        # Always keep the first system message (persona)
        system_msgs = [m for m in history if m.get("role") == "system"]
        recent_msgs = [m for m in history[-8:] if m.get("role") != "system"]

        # Calculate rough token count (4 chars ≈ 1 token)
        total_chars = sum(len(m.get("content", "")) for m in system_msgs + recent_msgs)

        # If still too big, truncate only the middle messages
        if total_chars > max_tokens * 4:
            return system_msgs[:1] + recent_msgs[-4:]

        return system_msgs + recent_msgs

    def _prune_current_history(self, history: list, keep_last: int = 10, trigger_at: int = 15) -> list:
        """Deprecated — count-based heuristic.

        Kept as a thin shim that delegates to the token-accurate
        ``ContextBuilder.truncate_to_budget`` so any out-of-tree caller
        still works. New code should call
        ``brain.context_builder.truncate_to_budget`` directly.
        """
        # The trigger is informational only; truncate_to_budget is a
        # budget-based walk that already short-circuits when the
        # history fits.
        return history[:trigger_at] + history[-keep_last:] if len(history) > trigger_at else history

    def _compress_tool_output(self, output):
        """Smart compression that preserves key information"""
        if len(output) <= 600:
            return output

        # If it's structured data, keep structure with ellipsis
        if output.strip().startswith(("{", "[", "<")):
            if len(output) > 1000:
                return output[:400] + "\n... [truncated] ...\n" + output[-400:]
            return output

        # For text, keep first and last sentences
        sentences = output.split(". ")
        if len(sentences) > 4:
            return ". ".join(sentences[:2]) + ". ... " + ". ".join(sentences[-2:])

        return output[:600]

    def _check_memory_pressure(self):
        """Detect if we're approaching VRAM limits"""
        try:
            import torch

            if torch.cuda.is_available():
                allocated = torch.cuda.memory_allocated() / 1024**3
                total = torch.cuda.memory_reserved() / 1024**3
                pressure = allocated / total if total > 0 else 0

                if pressure > 0.85:
                    logger.warning(f"memory_pressure_high | {pressure:.2%}")
                    return True
        except Exception as e:
            logger.debug(f"vram_critical_check_failed | {e}")
        return False

    def _emit_progress(self, brain, step: int, total: int, tool_name: str):
        """Dispatches real-time progress signals to status and audio cores."""
        import random
        import time
        from charlie.utils.persona import DIVERSITY_TEMPLATES

        now = time.time()
        last = self._last_status_emit.get(tool_name, 0)

        # Heavy tools get prioritized feedback
        is_heavy = tool_name in ["search", "get_news", "browser_search", "browser_fetch"]

        if now - last < self._emit_throttle and not is_heavy:
            return

        status_label = f"STEP {step}/{total} · {tool_name.upper()}"

        # 1. Update status
        brain._safe_put(brain.status_q, {"type": "THINKING_STATUS", "content": status_label})

        # 2. Vocal Presence (Human Bridges)
        # Only bridge if it's a heavy task and we haven't spoken in a while
        if is_heavy and now - self._last_bridge_time > 15:
            bridge = random.choice(DIVERSITY_TEMPLATES.get("thinking", ["Mmm..."]))
            brain._safe_put(brain.tts_q, {"type": "SPEAK", "content": bridge})
            self._last_bridge_time = now

        self._last_status_emit[tool_name] = now

    async def execute_chain(
        self,
        brain,
        text: str,
        memory_context: str,
        current_history: list,
        sent_sentences_global: set[str],
        cumulative_chat_text_ref: list[str],
    ) -> str:
        """
        Runs tools sequentially. Each tool's output is available to the next tool as context.
        If a TIER 1+ tool is encountered, execution pauses and emits a ConfirmationRequired event.
        """
        # Route through Orchestrator first for complex/multi-agent goals
        if hasattr(brain, "orchestrator") and brain.orchestrator:
            try:
                orchestrator_result = await brain.orchestrator.route_goal(text)
                if orchestrator_result and len(orchestrator_result) > 10:
                    logger.info("chain_orchestrator_route | goal='%s' | result_len=%d", text[:40], len(orchestrator_result))
                    return orchestrator_result
            except Exception as e:
                logger.debug("chain_orchestrator_skip | %s", e)

        chain = self.get_active_context()
        if not chain:
            return "No active chain."

        final_response_text = ""
        loop_counter = 0

        try:
            while not self.is_chain_complete():
                logger.debug(f"chain_loop_start | step={chain.current_step_count + 1}")
                if chain.current_step_count >= chain.max_steps:
                    logger.warning("max_chain_depth_reached")
                    final_response_text = "I've reached my maximum reasoning depth for this task, Sir. It appears to be more complex than anticipated."
                    break

                current_depth = chain.current_step_count
                if current_depth >= self._max_chain_depth:
                    logger.warning(f"max_chain_depth_reached | depth={current_depth}")
                    break

                try:
                    while not brain.brain_task_q.empty():
                        task = brain.brain_task_q.get_nowait()
                        if task.get("type") == "INTERRUPT":
                            brain.interrupt_event.set()
                        else:
                            brain.brain_task_q.put(task)
                            break
                except queue.Empty:
                    logger.debug("brain_task_q_empty")

                if brain.interrupt_event.is_set():
                    logger.info("brain_query_halted_by_interrupt")
                    self.clear_chain()  # FULL RESET
                    brain._safe_put(brain.audio_cmd_q, {"type": "INTERRUPT"})
                    brain._safe_put(brain.tts_q, {"type": "CONVERSATION_END"})
                    return None

                status_label = "THINKING..." if current_depth == 0 else f"PLANNING STEP {current_depth}"

                # Throttle inline status updates
                now = time.monotonic()
                if now - self._last_status_emit.get("THINKING_STATUS", 0) > 0.5:
                    brain._emit_status("THINKING" if current_depth == 0 else "PLANNING")
                    brain._safe_put(
                        brain.status_q,
                        {"type": "THINKING_STATUS", "content": status_label},
                    )
                    self._last_status_emit["THINKING_STATUS"] = now

                # PRUNE STALE SYSTEM MESSAGES from history to avoid time/stats confusion
                cleaned_history = self._prune_history_smart(
                    [
                        m
                        for m in current_history
                        if m and (m.get("role") != "system" or "Current Time:" not in m.get("content", ""))
                    ]
                )

                # Build payload with streaming enabled for real-time token output
                # NVIDIA NIM API supports streaming with proper delta parsing
                payload = {
                    "model": brain.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                f"{brain.system_prompt}\n"
                                f"{memory_context}\n"
                                f"SIR IS LOOKING AT: {brain.active_window}\n"
                                f"{chain.to_llm_context()}\n"
                                + (
                                    "\n(NOTE: Sir is on mobile/Telegram. He CANNOT see the PC screen. You MUST summarize requested data or status directly in your final_answer using plain conversational English. Your final_answer must be plain conversational English only. No JSON, no code blocks, no technical formatting.)"
                                    if chain.source.startswith("telegram")
                                    else ""
                                )
                                + (
                                    "\nCONTINUE YOUR TASK SEQUENCE. If more actions are needed to reach the goal, perform them now. If done, provide your final_answer."
                                    if current_depth > 0
                                    else ""
                                )
                            ),
                        }
                    ]
                    + cleaned_history,
                    "stream": True,  # NVIDIA NIM API supports streaming
                    "temperature": 0.1,
                    "stop": [
                        "<|endoftext|>",
                        "<endofturn>",
                        "<startofturn>",
                        "user:",
                    ],
                }

                # DIAGNOSTIC LOGGING
                logger.info(f"chain_llm_request_start | step={current_depth + 1} | goal='{text[:30]}...'")

                # STREAMING RESPONSE PARSING
                # LLMClient yields pre-parsed SSE data dicts
                full_response_parts = []
                reasoning_content_buffer_parts = []
                in_thought_block = False
                self._last_fa_streamed_len = 0
                source = chain.source

                try:
                    async for data in brain.llm_client.stream(
                        payload["messages"],
                        temperature=payload.get("temperature", 0.1),
                        **{k: v for k, v in payload.items() if k not in ("messages", "model", "temperature")},
                    ):
                        if isinstance(data, str):
                            try:
                                import json as _json

                                data = _json.loads(data)
                            except (ValueError, TypeError):
                                logger.debug(f"chain_raw_string_chunk | {data[:100]}")
                                continue
                        choices = data.get("choices", [])
                        if not choices or choices[0] is None:
                            continue

                        delta = choices[0].get("delta", {})
                        content = delta.get("content")
                        reasoning_content = delta.get("reasoning_content")
                        reasoning = delta.get("reasoning", "") or delta.get("thought", "")

                        # ── Thought Detection (Stateful) ──────────────────────────────
                        if content:
                            if "<thought" in content.lower() or "<reasoning" in content.lower():
                                in_thought_block = True
                                self._speculative_triggered = False

                            # ── Live "Thinking" Streaming to Chat Widget ──────────────────
                            # Only stream the extracted final_answer content (not raw JSON tokens)
                            # Dashboard and Telegram streaming moved to final_answer extraction block below

                            # ── Live Pipelining: final_answer -> TTS (Issue: natural-convo.md) ──
                            # Detect when the model starts outputting the final_answer field
                            current_full = "".join(full_response_parts)
                            if (
                                not in_thought_block
                                and '"final_answer": "' in current_full
                                and re.search(r'"action"\s*:\s*"none"', current_full.lower())
                            ):
                                fa_marker = '"final_answer": "'
                                fa_start = current_full.find(fa_marker) + len(fa_marker)
                                current_fa = current_full[fa_start:]

                                # If we find a closing quote (not escaped), stop pipelining
                                if '"' in current_fa and not current_fa.endswith('\\"'):
                                    current_fa = current_fa[: current_fa.find('"')]

                                # ── Stream clean final_answer to Dashboard & Telegram ──
                                fa_clean = current_fa.replace('\\"', '"').replace("\\n", " ").replace("\\t", " ")
                                fa_clean = re.sub(r"<[^>]+>", "", fa_clean)
                                fa_clean = re.sub(
                                    r"(?i)\b(user|assistant|system|sir|tts|charlie):\s*",
                                    "",
                                    fa_clean,
                                )
                                fa_clean = fa_clean.replace("<endofturn>", "").replace("<startofturn>", "").strip()

                                if fa_clean:
                                    # Track what we've already streamed to avoid re-sending
                                    if not hasattr(self, "_last_fa_streamed_len"):
                                        self._last_fa_streamed_len = 0
                                    if len(fa_clean) > self._last_fa_streamed_len:
                                        new_text = fa_clean[self._last_fa_streamed_len :]
                                        self._last_fa_streamed_len = len(fa_clean)

                                        # Dashboard: THINKING_STATUS with clean text
                                        if new_text.strip():
                                            if source == "local" or source == "all":
                                                brain._safe_put(
                                                    brain.status_q,
                                                    {"type": "THINKING_STATUS", "content": new_text},
                                                )
                                            # Telegram: STREAM_PARTIAL with clean text
                                            if (source.startswith("telegram") or source == "all") and brain.telegram_q:
                                                brain._safe_put(
                                                    brain.telegram_q,
                                                    {"type": "STREAM_PARTIAL", "content": new_text},
                                                )

                                # Split into fragments (sentences or long clauses) and push to TTS immediately
                                fa_fragments = re.split(r"(?<=[.!?])\s+|(?<=[,;:])\s+", current_fa)
                                # We speak fragments immediately if they end in terminal punctuation
                                # or if the fragment is long enough to be a standalone clause.
                                if len(fa_fragments) > 1:
                                    for fa_s in fa_fragments[:-1]:
                                        fa_s_clean = fa_s.strip().replace('\\"', '"').replace("\\n", " ")
                                        # Use the same stripping logic as normal path
                                        fa_s_clean = re.sub(r"<[^>]+>", "", fa_s_clean)
                                        fa_s_clean = re.sub(
                                            r"(?i)\b(user|assistant|system|sir|tts|charlie):\s*",
                                            "",
                                            fa_s_clean,
                                        )
                                        fa_s_clean = (
                                            fa_s_clean.replace("<endofturn>", "").replace("<startofturn>", "").strip()
                                        )

                                        if fa_s_clean and len(fa_s_clean) > 5:
                                            norm_fa = fa_s_clean.lower().strip()
                                            if norm_fa not in sent_sentences_global:
                                                if chain.source == "local":
                                                    # ── TTS Token Limit Hardening ──
                                                    # Split long sentences at ~200 chars to stay under 50-token limit
                                                    speech_chunks = []
                                                    if (
                                                        len(fa_s_clean) > 150
                                                    ):  # Reduced from 200 to avoid 50-token limit warnings
                                                        speech_chunks = re.split(r"(?<=[,;])\s+", fa_s_clean)
                                                    else:
                                                        speech_chunks = [fa_s_clean]

                                                    for chunk in speech_chunks:
                                                        if len(chunk) > 5:
                                                            brain._safe_put(
                                                                brain.tts_q,
                                                                {
                                                                    "type": "SPEAK",
                                                                    "content": chunk,
                                                                },
                                                            )
                                                elif chain.source.startswith("telegram") and brain.telegram_q:
                                                    brain._safe_put(
                                                        brain.telegram_q,
                                                        {"type": "STREAM_PARTIAL", "content": fa_s_clean + " "},
                                                    )
                                                sent_sentences_global.add(norm_fa)

                            # Update thought block state
                            if "</thought>" in content.lower() or "</reasoning>" in content.lower():
                                in_thought_block = False

                            full_response_parts.append(content)

                        elif reasoning_content:
                            full_response_parts.append(reasoning_content)
                            reasoning_content_buffer_parts.append(reasoning_content)
                        elif reasoning:
                            full_response_parts.append(reasoning)

                    full_response = "".join(full_response_parts)
                    reasoning_content_buffer = "".join(reasoning_content_buffer_parts)
                    logger.debug(
                        f"chain_stream_complete | response_len={len(full_response)} | reasoning_buffer={len(reasoning_content_buffer)}"
                    )

                    if not full_response.strip():
                        logger.warning(f"chain_empty_response | step={current_depth}")

                except RateLimitExceeded:
                    logger.warning("chain_rate_limited")
                    final_response_text = "Rate limit exceeded. Please wait a moment, Sir."
                    break
                except RuntimeError as rt_err:
                    logger.warning(f"chain_step_failed | {rt_err}")
                    break

                logger.debug(f"chain_raw_llm_res | {full_response[:100]}")

                # RECOVERY: If LLM returns empty, it's likely a context/VRAM choke.
                if not full_response.strip() and chain.current_step_count < chain.max_steps:
                    retry_count = getattr(self, "_recovery_retries", 0)
                    if retry_count < 3:
                        self._recovery_retries = retry_count + 1
                        memory_pressure = self._check_memory_pressure()
                        logger.warning(
                            f"neural_link_choke | empty_response | attempt={self._recovery_retries} | memory_pressure={memory_pressure} | re_prompting"
                        )

                        # Extract last tool output to build compressed re-prompt
                        last_obs = ""
                        for step in reversed(chain.steps):
                            if hasattr(step, "output") and step.output:
                                last_obs = self._compress_tool_output(str(step.output))
                                break

                        # Progressive recovery based on retry count
                        if retry_count == 0:
                            # Retry 1: Simple re-prompt with minimal context
                            history_slice = current_history[-4:]
                        elif retry_count == 1:
                            # Retry 2: Compress context significantly (last 4 messages)
                            history_slice = current_history[-4:]
                            if last_obs:
                                last_obs = last_obs[:400]  # Further compress
                        else:
                            # Retry 3: Emergency mode with minimal context (last 2 messages)
                            history_slice = current_history[-2:]
                            if last_obs:
                                last_obs = last_obs[:200]  # Maximum compression

                        if last_obs:
                            history_slice.append(
                                {
                                    "role": "user",
                                    "content": (
                                        f"TOOL RESULT:\n{last_obs}\n\n"
                                        "Using the data above, provide your final_answer NOW. "
                                        "You MUST recite the specific values or information found in the tool result (e.g. times, numbers, names). "
                                        "Do NOT just say you have the information. Provide the information. "
                                        "Be direct and professional. No tool calls. Respond in JSON: "
                                        '{"action": "none", "action_input": {}, "final_answer": "..."}'
                                    ),
                                }
                            )
                        else:
                            history_slice.append(
                                {
                                    "role": "user",
                                    "content": "Respond with your final_answer based on what you know. Keep it short. JSON format required.",
                                }
                            )
                        current_history = history_slice
                        await asyncio.sleep(0.3)
                        continue
                    else:
                        logger.error("neural_link_failure | max_retries_exceeded | aborting_chain")
                        self._recovery_retries = 0
                        return "Neural link strained, Sir. The model couldn't synthesize that — try a simpler phrasing."

                self._recovery_retries = 0
                logger.debug(f"chain_full_llm_res | {full_response}")
                res_json = self._parser.parse_response(full_response)
                logger.debug(f"chain_parsed_json | {res_json}")

                action = str(res_json.get("action", "none")).lower().replace("()", "").strip()
                action_input = res_json.get("action_input", {})
                final_answer = res_json.get("final_answer", "")

                # FALLBACK: If parser returned no final_answer, try to
                # extract plain text directly from the raw response.
                if not final_answer and action == "none":
                    final_answer = self._parser.sanitize_final_answer(full_response)
                    if final_answer and final_answer != full_response.strip():
                        logger.debug(f"chain_json_leak_recovered | cleaned_len={len(final_answer)}")

                # Clean any residual JSON artifacts from final_answer
                if final_answer:
                    final_answer = self._parser.sanitize_final_answer(final_answer)

                # 3. ONLY Speak final_answer if the action is 'none' (Chain end)
                if action == "none" and final_answer:
                    is_phantom = is_phantom_phrase(final_answer)

                    if is_phantom and chain.current_step_count < chain.max_steps:
                        # Speak the phantom phrase (it was already committed to TTS)
                        # but then force a follow-up synthesis pass
                        logger.warning(f"phantom_processing_detected | injecting_followup | phrase={final_answer[:60]}")
                        current_history.append({"role": "assistant", "content": full_response})
                        current_history.append(
                            {
                                "role": "user",
                                "content": (
                                    "You said you were processing or researching. "
                                    "The tool has already completed — the results are in OBSERVATIONS above. "
                                    "Stop processing. Provide the actual synthesized answer NOW as final_answer. "
                                    "Do NOT call any more tools."
                                ),
                            }
                        )
                        # Speak a brief bridge line so the user hears continuity
                        brain._safe_put(
                            brain.status_q,
                            {
                                "type": "RESEARCH_LOG",
                                "content": "🧠 SUMMARIZING FINDINGS...",
                            },
                        )
                        brain._safe_put(
                            brain.tts_q,
                            {
                                "type": "SPEAK",
                                "content": "Synthesizing results, Sir.",
                            },
                        )
                        # Continue loop — next iteration will synthesize real answer
                        continue

                    # Normal path: speak fragments
                    fragments = re.split(r"(?<=[.!?])\s+", final_answer)
                    # INTERNAL THOUGHT BLACKLIST
                    thought_markers = [
                        "thought",
                        "internal",
                        "logic",
                        "executing",
                        "calling",
                        "reasoning",
                        "analyzing",
                        "summarizing",
                    ]

                    current_full_text = ""
                    for s in fragments:
                        s_clean = s.strip()

                        # AGGRESSIVE STRIPPING for streaming fragments
                        s_clean = re.sub(r"<[^>]+>", "", s_clean)
                        s_clean = re.sub(
                            r"(?i)\b(user|assistant|system|sir|tts|charlie):\s*",
                            "",
                            s_clean,
                        )
                        s_clean = s_clean.replace("<endofturn>", "").replace("<startofturn>", "")
                        s_clean = s_clean.strip()

                        # Skip if sentence looks like internal logic
                        if any(m in s_clean.lower() for m in thought_markers) and len(s_clean) < 100:
                            continue

                        if s_clean and len(s_clean) > 2:
                            # Update current_full_text (always grows)
                            if current_full_text:
                                current_full_text += " "
                            current_full_text += s_clean

                            # SPEECH ROUTING (only if new)
                            norm_s = s_clean.lower().strip()
                            if norm_s not in sent_sentences_global:
                                if chain.source == "local":
                                    # ── TTS Token Limit Hardening ──
                                    speech_chunks = []
                                    if len(s_clean) > 150:  # Reduced from 200
                                        speech_chunks = re.split(r"(?<=[,;])\s+", s_clean)
                                    else:
                                        speech_chunks = [s_clean]

                                    for chunk in speech_chunks:
                                        if len(chunk) > 5:
                                            brain._safe_put(
                                                brain.tts_q,
                                                {"type": "SPEAK", "content": chunk},
                                            )
                                elif chain.source.startswith("telegram") and brain.telegram_q:
                                    brain._safe_put(
                                        brain.telegram_q, {"type": "STREAM_PARTIAL", "content": s_clean + " "}
                                    )
                                sent_sentences_global.add(norm_s)

                        if cumulative_chat_text_ref:
                            cumulative_chat_text_ref[0] = current_full_text

                # 4. Detect Neural Loop (Oscillation/Repetition)
                is_loop = False
                for h_idx, past_step in enumerate(chain.steps):
                    # Exact signature match
                    if action == past_step.tool_name and action_input == past_step.args:
                        is_loop = True
                        break

                    # Similarity match (oscillating between same tool with slightly different args)
                    if action == past_step.tool_name:
                        from difflib import SequenceMatcher

                        ratio = SequenceMatcher(None, str(action_input), str(past_step.args)).ratio()
                        if ratio > 0.9:
                            is_loop = True
                            break

                # Hard cap on consecutive tools to prevent infinite thrashing
                if len(chain.steps) >= 5:
                    logger.warning("max_chain_steps_reached | forcing_summary")
                    action = "none"
                    res_json["action"] = "none"
                    final_answer = "I've performed several operations but reached a complexity limit. Here is what I found so far, Sir."
                    res_json["final_answer"] = final_answer
                    is_loop = False  # Proceed to final answer

                if is_loop:
                    logger.warning(f"loop_detected | tool={action} | forcing_answer")
                    action = "none"
                    res_json["action"] = "none"
                    # Inform the LLM it's looping via thinking status if possible,
                    # but for now, we force 'none' to trigger the terminal response branch.

                else:
                    loop_counter = 0

                # Interrupt Check
                if brain.interrupt_event.is_set():
                    logger.warning("chain_interrupted")
                    final_answer = "Operation halted by your request, Sir."
                    break

                if action not in ("none", ""):
                    brain._emit_status("EXECUTING", source=chain.source)

                    # Vocal Bridge: Eliminate dead air during tool calls
                    if chain.source == "local" and action != "none":
                        import random

                        bridges = [
                            "Checking that, Sir.",
                            "Looking into it.",
                            "One moment, Sir.",
                            "Processing data.",
                            "Gathering info.",
                        ]
                        if random.random() < 0.3:  # Only ping 30% of the time to avoid annoyance
                            brain._safe_put(brain.tts_q, {"type": "SPEAK", "content": random.choice(bridges)})

                    # Update neural telemetry with the tool being called
                    if chain.source == "local":
                        brain._safe_put(
                            brain.status_q,
                            {
                                "type": "THINKING_STATUS",
                                "content": f"EXECUTING {action.upper()}...",
                            },
                        )

                    try:
                        obs = await asyncio.to_thread(
                            brain.execute_tools,
                            {"tool": action, "args": action_input},
                            text,
                            chain.source,
                        )

                        if obs == CONFIRMATION_PENDING:
                            logger.info("chain_paused_awaiting_confirmation")
                            # Get tool tier to determine timeout
                            entry = brain.tool_registry.get(action) if hasattr(brain, "tool_registry") else None
                            tool_func = entry.handler if entry else None
                            tier = get_tool_tier(tool_func) if tool_func else RiskTier.TIER_1
                            timeout_seconds = 60  # default for TIER_1
                            if tier == RiskTier.TIER_2:
                                timeout_seconds = settings.security.tier_2_countdown
                            elif tier == RiskTier.TIER_3:
                                timeout_seconds = 120  # longer for destructive actions

                            try:
                                await asyncio.wait_for(
                                    brain.confirmation_event.wait(),
                                    timeout=timeout_seconds,
                                )
                                if brain.confirmation_result:
                                    # RESUME via brain.execute_tools to ensure COM init and routing logic apply
                                    # Bypasses guardian check since we just got the result
                                    obs = await asyncio.to_thread(
                                        brain.execute_tools,
                                        {"tool": action, "args": action_input},
                                        text,
                                        chain.source,
                                        skip_guardian=True,
                                    )
                                    brain.confirmation_event.clear()
                                else:
                                    obs = "Action aborted by user."
                                    brain.confirmation_event.clear()
                            except asyncio.TimeoutError:
                                logger.warning(
                                    f"confirmation_timeout_in_chain | tier={tier.name} timeout={timeout_seconds}s"
                                )
                                obs = f"Action timed out after {timeout_seconds} seconds."
                                brain.confirmation_event.clear()
                                brain._safe_put(
                                    brain.tts_q,
                                    {
                                        "type": "SPEAK",
                                        "content": f"Confirmation timed out after {timeout_seconds} seconds, Sir. Aborting tool execution.",
                                    },
                                )

                        logger.info(f"tool_complete | tool={action}")

                        entry = brain.tool_registry.get(action) if hasattr(brain, "tool_registry") else None
                        tool_func = entry.handler if entry else None
                        tier = get_tool_tier(tool_func) if tool_func else RiskTier.TIER_0

                        self._emit_progress(
                            brain,
                            chain.current_step_count + 1,
                            chain.max_steps,
                            action,
                        )

                        self.add_step(action, action_input, str(obs), tier)

                        # CRITICAL FIX: Update history for the next turn in the chain
                        current_history.append({"role": "assistant", "content": full_response})
                        current_history.append({"role": "system", "content": f"OBSERVATION: {obs}"})

                        # Prevent history from growing unbounded during long chains.
                        # Delegate to the token-accurate pruner in context_builder.
                        current_history = brain.context_builder.truncate_to_budget(current_history)

                        if action in ("shutdown", "system_reboot", "clarification", "standby"):
                            if str(obs).startswith(("Error", "Failed")):
                                final_response_text = (
                                    f"I'm sorry, Sir, but I failed to execute the `{action}` tool. {obs}"
                                )
                            elif action in ("clarification", "shutdown", "standby"):
                                final_response_text = ""
                            else:
                                final_response_text = final_answer or f"Executed {action}."
                            break

                        continue
                    except Exception as e:
                        logger.error(f"chain_tool_err | {e}")
                        self.add_step(action, action_input, f"Error: {e}", RiskTier.TIER_0)
                        continue

                elif final_answer:
                    final_response_text = self._parser.sanitize_final_answer(final_answer, goal=text)
                    # ── TTS Fallback ──
                    if final_response_text and brain:
                        fr_fragments = re.split(r"(?<=[.!?])\s+", final_response_text)
                        for fr_s in fr_fragments:
                            fr_s_clean = fr_s.strip()
                            if fr_s_clean and len(fr_s_clean) > 5:
                                norm_fr = fr_s_clean.lower().strip()
                                if norm_fr not in sent_sentences_global:
                                    # Split if too long for 50-token limit
                                    speech_chunks = (
                                        [fr_s_clean]
                                        if len(fr_s_clean) <= 150
                                        else re.split(r"(?<=[,;])\s+", fr_s_clean)
                                    )
                                    for chunk in speech_chunks:
                                        if len(chunk) > 5:
                                            brain._safe_put(brain.tts_q, {"type": "SPEAK", "content": chunk})
                                    sent_sentences_global.add(norm_fr)

                    # SINGLE BUBBLE: Emit the full response once at the end
                    if final_response_text and brain:
                        brain._safe_put(
                            brain.status_q,
                            {"type": "CHAT_MSG", "speaker": "CHARLIE", "content": final_response_text, "stream": False},
                        )
                    break
                else:
                    raw_res = full_response.strip()
                    # NEURAL SILENCE: Only suppress if it's CLEARLY a tool-like shorthand or JSON leak.
                    is_json = raw_res.startswith("{") and raw_res.endswith("}")
                    is_shorthand = any(
                        raw_res.lower().startswith(f"{m}:")
                        for m in ["thought", "action", "observation", "final_answer"]
                    )

                    if is_json or is_shorthand:
                        logger.debug(f"speech_suppressed | raw_leak_detected | {raw_res[:30]}...")
                        final_response_text = "Task complete, Sir." if action != "none" else ""
                        # Ensure we speak the fallback
                        if final_response_text and brain:
                            brain._safe_put(brain.tts_q, {"type": "SPEAK", "content": final_response_text})
                    else:
                        # If it's just plain text (even with colons), let it through
                        final_response_text = self._parser.sanitize_final_answer(raw_res, goal=text)
                        # ── TTS Fallback for raw text ──
                        if final_response_text and brain:
                            fr_fragments = re.split(r"(?<=[.!?])\s+", final_response_text)
                            for fr_s in fr_fragments:
                                fr_s_clean = fr_s.strip()
                                if fr_s_clean and len(fr_s_clean) > 5:
                                    norm_fr = fr_s_clean.lower().strip()
                                    if norm_fr not in sent_sentences_global:
                                        speech_chunks = (
                                            [fr_s_clean]
                                            if len(fr_s_clean) <= 150
                                            else re.split(r"(?<=[,;])\s+", fr_s_clean)
                                        )
                                        for chunk in speech_chunks:
                                            if len(chunk) > 5:
                                                brain._safe_put(brain.tts_q, {"type": "SPEAK", "content": chunk})
                                        sent_sentences_global.add(norm_fr)

                    # SINGLE BUBBLE: Emit the full response once at the end
                    if final_response_text and brain:
                        brain._safe_put(
                            brain.status_q,
                            {"type": "CHAT_MSG", "speaker": "CHARLIE", "content": final_response_text, "stream": False},
                        )
                    break

        except Exception as e:
            logger.exception(f"query_cycle_failed | error_type={type(e).__name__} | msg={e}")
            if "brain" in locals():
                brain._safe_put(
                    brain.tts_q,
                    {
                        "type": "SPEAK",
                        "content": "I've encountered a neural link error, Sir.",
                    },
                )
            return ""

        if final_response_text:
            brain.relationship.log_event("tool_success", f"Chain completed: {text[:50]}")

        return final_response_text
