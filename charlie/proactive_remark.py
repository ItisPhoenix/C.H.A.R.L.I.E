"""Anticipatory proactive remark engine.

Observes patterns (time of day, screen context, silence duration, memory facts)
and generates occasional natural remarks via the fast LLM. Max 1 remark per 15 minutes.
"""

import logging
import random
import time
from datetime import datetime
from typing import Callable, Optional

logger = logging.getLogger("charlie.proactive_remark")

# Hardcoded fallback templates when no fast LLM is available
_FALLBACK_TEMPLATES = [
    "Quiet in here. Need anything?",
    "Still with you. Just say the word.",
    "I noticed you've been at this for a while. Take a break?",
    "Anything I can help with?",
    "Been a while since we talked.",
    "I'm here whenever you need me.",
]


class ProactiveRemarkEngine:
    """Checks triggers on a timer and generates proactive remarks."""

    def __init__(
        self,
        llm_completion_fn: Optional[Callable] = None,
        remark_callback: Optional[Callable[[str], None]] = None,
        has_fast_llm: bool = False,
    ) -> None:
        self._llm_completion = llm_completion_fn
        self._remark_callback = remark_callback
        self._has_fast_llm = has_fast_llm

        # State tracking
        self._last_remark_time: float = 0.0
        self._last_interaction_time: float = time.time()
        self._cooldown_seconds: float = 900.0  # 15 minutes
        self._screen_context: str = "unknown"
        self._emotional_state: str = "neutral"
        self._last_topic: str = ""
        self._user_name: str = "friend"
        self._window_change_count: int = 0
        self._last_window_title: str = ""
        self._core_facts: list[str] = []
        self._user_facts: list[str] = []
        self._screen_category: str = "other"
        self._session_start: float = time.time()

    def update_screen_context(self, title: str) -> None:
        if title != self._last_window_title:
            self._last_window_title = title
            self._window_change_count += 1

    def update_emotional_state(self, state: str) -> None:
        self._emotional_state = state

    def record_interaction(self) -> None:
        """Call on every user voice interaction to reset silence timer."""
        self._last_interaction_time = time.time()

    def set_last_topic(self, topic: str) -> None:
        self._last_topic = topic

    def set_user_name(self, name: str) -> None:
        self._user_name = name

    def update_facts(self, core_facts: list[str], user_facts: list[str]) -> None:
        """Update memory facts for context-aware remarks."""
        self._core_facts = core_facts or []
        self._user_facts = user_facts or []

    def update_screen_category(self, category: str) -> None:
        """Update screen category (coding, browsing, leisure, work, error, other)."""
        self._screen_category = category

    def check(self) -> Optional[str]:
        """Check all triggers. Returns a remark string or None."""
        now = time.time()

        # Enforce cooldown
        if now - self._last_remark_time < self._cooldown_seconds:
            return None

        # Check triggers in priority order
        remark = self._check_morning_greeting(now)
        if remark is None:
            remark = self._check_error_window()
        if remark is None:
            remark = self._check_long_silence(now)
        if remark is None:
            remark = self._check_memory_recall(now)

        if remark:
            self._last_remark_time = now
            if self._remark_callback:
                self._remark_callback(remark)
            return remark
        return None

    def _check_morning_greeting(self, now: float) -> Optional[str]:
        hour = datetime.now().hour
        if 6 <= hour <= 9:
            elapsed = now - self._session_start
            if elapsed < 600:  # within first 10 min of session
                return f"Good {('morning' if hour < 12 else 'afternoon')}, {self._user_name}."
        return None

    def _check_error_window(self) -> Optional[str]:
        title = self._last_window_title
        error_keywords = ("Error", "Exception", "Failed", "Bug", "Traceback", "CRASH")
        if any(kw.lower() in title.lower() for kw in error_keywords):
            return "Looks like something went wrong. Want me to look into it?"
        return None

    def _check_long_silence(self, now: float) -> Optional[str]:
        silence_duration = now - self._last_interaction_time
        if silence_duration > 600 and self._window_change_count >= 2:
            fact_ctx = self._build_fact_context()
            return self._generate_llm_remark(
                f"It has been {int(silence_duration // 60)} minutes of silence. "
                f"User is working in: {self._last_window_title} (category: {self._screen_category}). "
                f"{fact_ctx}"
                f"Generate a short, natural remark (< 15 words)."
            )
        return None

    def _check_memory_recall(self, now: float) -> Optional[str]:
        elapsed = now - self._last_remark_time
        if elapsed > 1800:  # 30 min since last remark
            fact_ctx = self._build_fact_context()
            return self._generate_llm_remark(
                f"It is {datetime.now().strftime('%I:%M %p')}. "
                f"User is working in: {self._last_window_title} (category: {self._screen_category}). "
                f"Emotional state: {self._emotional_state}. "
                f"{fact_ctx}"
                f"Generate a short, natural observation (< 15 words)."
            )
        return None

    def _build_fact_context(self) -> str:
        """Build a fact context string for the LLM prompt."""
        parts: list[str] = []
        if self._core_facts:
            parts.append(f"Known facts: {'; '.join(self._core_facts[:3])}.")
        if self._user_facts:
            parts.append(f"User preferences: {'; '.join(self._user_facts[:2])}.")
        return " ".join(parts) + " " if parts else ""

    def _generate_llm_remark(self, prompt: str) -> Optional[str]:
        """Use fast LLM to generate a remark, or fall back to templates."""
        if self._has_fast_llm and self._llm_completion:
            try:
                import asyncio
                # Try to get existing loop or create one
                try:
                    _ = asyncio.get_running_loop()
                    # We're in async context — use create_task
                    # But check() is called synchronously from QTimer
                    # So we use asyncio.run_coroutine_threadsafe or return None
                    logger.debug("Proactive remark: async context detected, skipping LLM")
                    return random.choice(_FALLBACK_TEMPLATES)
                except RuntimeError:
                    # No running loop — safe to use asyncio.run
                    result = asyncio.run(self._llm_completion(prompt, max_tokens=30, temperature=0.8))
                    if result:
                        return result.strip()
            except Exception as e:
                logger.debug(f"Proactive LLM remark failed: {e}")

        return random.choice(_FALLBACK_TEMPLATES)
