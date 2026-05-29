import logging
import time
from typing import Optional

from charlie.config import settings
from charlie.utils.persona import get_system_prompt

logger = logging.getLogger("charlie.brain.context")


class ContextBuilder:
    def __init__(self, brain):
        self.brain = brain
        self._cached_realtime_context = ""
        self._last_context_update = 0.0
        self._cached_system_prompt = ""
        self._prompt_cached_at = 0.0
        self._token_budget = 8192
        self._tokenizer = None  # Lazy-load tiktoken
        self._cached_memory_context = ""
        self._memory_context_cached_at = 0.0

    def _get_tokenizer(self):
        """Lazy-load tiktoken encoder (cl100k_base for GPT-style models)."""
        if self._tokenizer is None:
            try:
                import tiktoken
                self._tokenizer = tiktoken.get_encoding("cl100k_base")
            except ImportError:
                logger.warning("tiktoken_not_installed | falling_back_to_char_estimate")
                self._tokenizer = False  # Sentinel: don't retry
        return self._tokenizer

    def count_tokens(self, text: str) -> int:
        """Count tokens in text using tiktoken. Falls back to char/4 estimate."""
        if not text:
            return 0
        tokenizer = self._get_tokenizer()
        if tokenizer:
            return len(tokenizer.encode(text))
        # Fallback: rough estimate ~4 chars per token
        return len(text) // 4

    def truncate_to_budget(self, messages: list, max_tokens: int = None) -> list:
        """Truncate message history to fit within token budget.

        Keeps system prompt (index 0) and recent messages, drops oldest first.
        Messages are dicts with 'role' and 'content' keys.
        """
        budget = max_tokens if max_tokens is not None else self._token_budget
        if not messages:
            return messages

        # Always keep the system message (first message)
        system_msg = None
        history = messages
        if messages and messages[0].get("role") == "system":
            system_msg = messages[0]
            history = messages[1:]

        system_tokens = self.count_tokens(system_msg["content"]) if system_msg else 0
        remaining_budget = budget - system_tokens

        if remaining_budget <= 0:
            # System prompt alone exceeds budget; return just system prompt
            return [system_msg] if system_msg else []

        # Walk backwards from most recent, accumulating tokens
        # Each message has ~4 tokens of structural overhead (role, separators)
        _MSG_OVERHEAD = 4
        kept = []
        used_tokens = 0
        for msg in reversed(history):
            msg_tokens = self.count_tokens(msg.get("content", "")) + _MSG_OVERHEAD
            if used_tokens + msg_tokens > remaining_budget:
                break
            kept.append(msg)
            used_tokens += msg_tokens

        kept.reverse()

        result = ([system_msg] if system_msg else []) + kept
        if len(result) < len(messages):
            dropped = len(messages) - len(result)
            logger.debug("context_truncated | dropped=%d msgs, budget=%d", dropped, budget)
        return result

    def _trim_context_if_needed(
        self,
        system_prompt: str,
        history: list,
        memory_context: str,
    ) -> tuple[str, list, str]:
        """Ensure total context fits within the token budget.

        Trimming priority: memory context first (truncate or drop), then oldest
        history messages. The system prompt is never trimmed.

        Returns:
            (system_prompt, history, memory_context) — potentially shortened.
        """
        budget = self._token_budget
        system_tokens = self.count_tokens(system_prompt)
        history_tokens = sum(
            self.count_tokens(m.get("content", "")) + 4 for m in history
        )
        memory_tokens = self.count_tokens(memory_context)
        total = system_tokens + history_tokens + memory_tokens

        if total <= budget:
            return system_prompt, history, memory_context

        remaining = budget - system_tokens
        logger.debug(
            f"context_overflow | total={total} budget={budget} "
            f"system={system_tokens} history={history_tokens} memory={memory_tokens}"
        )

        # Phase 1: Trim memory context if it's consuming too much
        memory_max = max(remaining // 4, 256)  # Memory gets at most 25% of remaining
        if memory_tokens > memory_max:
            # Truncate memory context text to fit
            tokenizer = self._get_tokenizer()
            if tokenizer:
                encoded = tokenizer.encode(memory_context)
                if len(encoded) > memory_max:
                    memory_context = tokenizer.decode(encoded[:memory_max]) + "..."
            else:
                # Char-based fallback
                char_limit = memory_max * 4
                memory_context = memory_context[:char_limit] + "..."
            memory_tokens = self.count_tokens(memory_context)
            logger.debug("memory_context_trimmed | now=%d tokens", memory_tokens)

        # Phase 2: Trim oldest history messages if still over budget
        history_budget = remaining - memory_tokens
        if history_budget <= 0:
            # Drop all history except most recent message
            return system_prompt, history[-1:] if history else [], memory_context

        used = 0
        kept = []
        for msg in reversed(history):
            msg_tokens = self.count_tokens(msg.get("content", "")) + 4
            if used + msg_tokens > history_budget:
                break
            kept.append(msg)
            used += msg_tokens
        kept.reverse()

        if len(kept) < len(history):
            logger.debug(
                f"history_trimmed | kept={len(kept)}/{len(history)} messages"
            )

        return system_prompt, kept, memory_context

    def get_memory_context(self, query: str) -> str:
        """Pull relevant memories from MemoryManager for injection."""
        try:
            memory = getattr(self.brain, "memory", None)
            if memory is None:
                return ""
            return memory.get_context_string(query)
        except Exception as e:
            logger.error("memory_context_failed | %s", e)
            return ""

    def get_realtime_context(self) -> str:
        """Fetches current time, system stats, and ambient context for prompt injection (Cached)."""
        now = time.time()
        if now - self._last_context_update < 1 and self._cached_realtime_context:
            return self._cached_realtime_context

        from charlie.utils.system import get_system_vitals
        vitals = get_system_vitals()
        time_str = time.strftime("%A, %I:%M %p")
        cpu, ram, vram_pct = vitals["cpu"], vitals["ram"], vitals["vram_pct"]

        world = self.brain.world
        active_app = world.active_app
        current_task = world.current_task_inferred
        idle_sec = world.user_idle_seconds
        frustration = world.frustration_score

        ctx = (
            f"- Current Time: {time_str}\n"
            f"- System Load: CPU {cpu:.0f}% | RAM {ram:.0f}% | VRAM {vram_pct:.0f}%\n"
            f"- Active App: {active_app}\n"
            f"- Inferred Task: {current_task} (Idle: {idle_sec:.0f}s)\n"
            f"- User Frustration: {frustration:.2f} (0=Calm, 1=Critical)"
        )
        self._cached_realtime_context = ctx
        self._last_context_update = now
        return ctx

    def build_system_prompt(self) -> str:
        """Rebuilds system prompt with fresh adaptive context and real-time data."""
        relationship_ctx = self.brain.relationship.get_trust_context()
        drift_ctx = self.brain.drift.get_drift_context()
        adaptive_ctx = self.brain.mentor.get_adaptive_prompt_injection()

        # Emotional Calibration
        frustration = self.brain.world.frustration_score
        emotional_directive = ""
        if frustration > 0.7:
            emotional_directive = "\nEMOTIONAL STATE: Sir is FRUSTRATED. Drop all humor. Be purely operational, concise, and efficient."
        elif frustration < 0.2:
            emotional_directive = "\nEMOTIONAL STATE: Calm. You may use dry wit or a more conversational CHARLIE tone."

        # Pass unified tool registry so all @tool-decorated functions are visible
        tool_registry = getattr(self.brain, "tool_registry", None)

        prompt = get_system_prompt(
            adaptive_context=f"{relationship_ctx}{drift_ctx}{emotional_directive}\n\n{adaptive_ctx}",
            realtime_data=self.get_realtime_context(),
            tool_registry=tool_registry,
        )

        # Append MCP tool descriptions if any are registered
        mcp_tools_desc = self.brain.mcp_bridge.build_system_prompt_tools()
        if mcp_tools_desc:
            prompt += f"\n\n{mcp_tools_desc}"

        # MCP capability
        prompt += (
            "\n\n## MCP (Model Context Protocol) Tools"
            "\nYou can connect to external tool servers for extended capabilities (security scanning, "
            "web search, database access, etc.)."
            "\n- `mcp_list_servers`: See configured MCP servers and their status"
            "\n- `mcp_enable_server`: Enable an MCP server by name"
            "\n- `mcp_disable_server`: Disable an MCP server by name"
            "\nTo add a new MCP server, tell the user to add it to charlie_config.json under 'mcp_servers'."
        )

        # Inject learned user preferences
        learned_prefs = self._get_learned_preferences()
        if learned_prefs:
            prompt += f"\n\n{learned_prefs}"

        # Inject memory context (episodic, semantic, working memory)
        memory_ctx = self._get_memory_context()
        if memory_ctx:
            prompt += f"\n\n{memory_ctx}"

        # Inject user profile (USER.md)
        user_ctx = self._get_user_profile()
        if user_ctx:
            prompt += f"\n\n{user_ctx}"

        # Inject session search results for cross-session recall
        session_ctx = self._get_session_context()
        if session_ctx:
            prompt += f"\n\n{session_ctx}"

        # Vision capability
        if settings.llm.vision_model:
            prompt += (
                "\n\n## Vision Capability"
                "\nYou have eyes. Use these tools when visual information would help:"
                "\n- `analyze_screen`: Look at the current screen and answer questions about it"
                "\n- `describe_image`: Analyze any image file by path"
                "\n- `read_screen_text`: Extract all text visible on screen via OCR"
                "\nUse these proactively when: user mentions what's on screen, asks 'what do you see', "
                "reports an error, or when screen context would improve your answer."
            )

        # Inject recent research context for follow-up awareness
        recent_research = self._get_recent_research()
        if recent_research:
            prompt += f"\n\n{recent_research}"

        # Inject outcome feedback (what worked, what didn't)
        outcome_ctx = self._get_outcome_context()
        if outcome_ctx:
            prompt += f"\n\n{outcome_ctx}"

        # Anti-hallucination directive
        prompt += (
            "\n\n## Grounding Rules"
            "\n- NEVER fabricate information. If you don't know, say 'I don't have that information' and offer to search."
            "\n- ALWAYS use tools (search, browser_fetch, read_file) to verify facts before stating them."
            "\n- When uncertain, PREFACE with 'Based on my knowledge...' or 'I'll need to verify...'"
            "\n- CITE sources when providing factual claims from research."
            "\n- If a tool returns an error, report the error honestly — don't improvise."
        )

        # Inject user profile (dialectic user model)
        user_ctx = self._get_user_context()
        if user_ctx:
            prompt += f"\n\n{user_ctx}"

        return prompt

    def _get_user_context(self) -> str:
        """Get user profile from the dialectic user model."""
        try:
            user_model = getattr(self.brain, "user_model", None)
            if user_model and hasattr(user_model, "get_profile_summary"):
                summary = user_model.get_profile_summary(max_chars=1000)
                if summary and len(summary) > 50:
                    return "## User Profile (Auto-Learned)\n" + summary
        except Exception:
            pass
        return ""

    def _get_session_context(self) -> str:
        """Get relevant past conversation context via FTS5 search."""
        try:
            session_search = getattr(self.brain, "session_search", None)
            if not session_search:
                return ""
            # Use recent user message as query
            history = getattr(self.brain, "history", [])
            if not history:
                return ""
            last_user = [m for m in history[-3:] if m.get("role") == "user"]
            if not last_user:
                return ""
            query = last_user[-1].get("content", "")[:200]
            if not query:
                return ""
            results = session_search.search(query, limit=3)
            if not results:
                return ""
            lines = []
            for r in results:
                snippet = r.get("content", "")[:200]
                lines.append(f"- [{r.get('role', '?')}] {snippet}")
            return "## Related Past Conversations\n" + "\n".join(lines)
        except Exception:
            return ""

    def get_system_prompt_cached(self) -> str:
        """Returns cached system prompt. Rebuilds every 5s or on first call."""
        now = time.time()
        if self._cached_system_prompt and (now - self._prompt_cached_at) < 5.0:
            return self._cached_system_prompt

        if self.brain.interrupt_event.is_set():
            self._cached_realtime_context = None

        self._cached_system_prompt = self.build_system_prompt()
        self._prompt_cached_at = now
        return self._cached_system_prompt

    def _get_learned_preferences(self) -> str:
        """Query PatternDetector for learned preferences, format as system prompt section."""
        if not hasattr(self.brain, "pattern_detector") or not self.brain.pattern_detector:
            return ""
        try:
            prefs = self.brain.pattern_detector.get_user_preferences()
            if not prefs:
                return ""
            bullets = "\n".join(f"- {p}" for p in prefs)
            return f"## Learned User Preferences\n{bullets}"
        except Exception:
            return ""

    def _get_memory_context(self) -> str:
        """Query MemoryCoordinator for relevant memories and inject into context.

        Results are cached for 30 seconds to avoid redundant queries on every
        system prompt rebuild.
        """
        now = time.time()
        if self._cached_memory_context and (now - self._memory_context_cached_at) < 30.0:
            return self._cached_memory_context
        try:
            memory = getattr(self.brain, "memory", None)
            if not memory or not hasattr(memory, "get_context_injection"):
                self._cached_memory_context = ""
                self._memory_context_cached_at = now
                return ""
            # Use recent conversation topic as query
            query = ""
            history = getattr(self.brain, "history", [])
            if history:
                last_user = [m for m in history[-5:] if m.get("role") == "user"]
                if last_user:
                    query = last_user[-1].get("content", "")[:200]
            if not query:
                self._cached_memory_context = ""
                self._memory_context_cached_at = now
                return ""
            result = memory.get_context_injection(query)
            self._cached_memory_context = result
            self._memory_context_cached_at = now
            return result
        except Exception:
            return ""

    def _get_recent_research(self) -> str:
        """Get recent research results from the tool handler for follow-up context."""
        try:
            tool_handler = getattr(self.brain, "tool_handler", None)
            if not tool_handler or not hasattr(tool_handler, "_recent_research"):
                return ""
            research = tool_handler._recent_research
            if not research:
                return ""
            lines = []
            for r in research[-3:]:  # Last 3 research results
                topic = r.get("topic", "")
                summary = r.get("summary", "")
                if topic:
                    lines.append(f"- {topic}: {summary}")
            if not lines:
                return ""
            return "## Recent Research Context\n" + "\n".join(lines) + \
                "\n(Use this to provide better follow-up answers and suggestions)"
        except Exception:
            return ""

    def _get_outcome_context(self) -> str:
        """Inject recent outcome feedback into context for learning."""
        try:
            outcome_tracker = getattr(self.brain, "outcome_tracker", None)
            if not outcome_tracker or not hasattr(outcome_tracker, "get_recent_outcomes"):
                return ""
            outcomes = outcome_tracker.get_recent_outcomes(limit=5)
            if not outcomes:
                return ""
            # Outcome is a dataclass — use attribute access, not .get()
            successes = [o for o in outcomes if getattr(o, "outcome_type", "") == "success"]
            failures = [o for o in outcomes if getattr(o, "outcome_type", "") == "failure"]
            lines = []
            if successes:
                lines.append(f"Recent successes ({len(successes)}): " +
                    ", ".join(getattr(o, "tool_name", None) or "?" for o in successes[:3]))
            if failures:
                lines.append(f"Recent failures ({len(failures)}): " +
                    ", ".join(getattr(o, "tool_name", None) or "?" for o in failures[:3]))
            if not lines:
                return ""
            return "## Recent Outcome Feedback\n" + "\n".join(lines)
        except Exception:
            return ""

    def is_user_at_pc(self) -> bool:
        """Determines if Sir is physically at the workstation."""
        from charlie.utils.system import is_system_active
        return is_system_active(300)

    def vram_warning_check(self, threshold_override: Optional[float] = None, silent: bool = False) -> bool:
        """Returns True if VRAM is safe to proceed."""
        used = self.brain._get_vram_used_mb()
        if used == 0.0:
            return True
        limit = getattr(settings.llm, "vram_limit_mb", 8192)
        pct = min(100.0, (used / limit) * 100)
        target = threshold_override if threshold_override is not None else 95
        if pct > target:
            if not silent and self.brain.status_q:
                self.brain._safe_put(self.brain.status_q, {"type": "VRAM_ALERT", "pct": pct})
            return False
        if pct > 90 and self.brain.status_q:
            self.brain._safe_put(self.brain.status_q, {"type": "VRAM_WARN", "pct": pct})
        return True
