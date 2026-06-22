"""Cross-thread signal bridge between Brain/VoiceEngine and Qt widgets.

All Brain and VoiceEngine callbacks run in non-Qt threads.
WidgetBridge uses Qt signals to safely marshal data to the GUI thread.
"""

import logging
import time
from typing import Optional

try:
    from PySide6.QtCore import QObject, Signal
except ImportError:
    # PySide6 optional: --terminal mode works without it
    QObject = object  # type: ignore[misc,assignment]
    Signal = None  # type: ignore[assignment]

logger = logging.getLogger("charlie.widget_bridge")


class WidgetBridge(QObject):
    """Qt signal hub connecting async backend to Qt widget thread."""

    if Signal is not None:
        # Transcript
        transcript_chunk = Signal(str)

        # Emotional state
        emotional_state_changed = Signal(str)

        # Buddy state
        state_changed = Signal(str)

        # Latency
        latency_updated = Signal(float, float, float)  # asr_to_llm, llm_to_tts, total_e2e

        # Audio level (RMS 0.0–1.0)
        audio_level = Signal(float)

        # Backend label
        backend_changed = Signal(str)

        # Memory stats: (core_facts_count, sessions_count)
        memory_stats = Signal(int, int)

        # Voice activation
        activation_requested = Signal()
        deactivation_requested = Signal()

        # Stance expressed (key from TECH_STANCES)
        stance_expressed = Signal(str)

        # Screen context
        screen_context_changed = Signal(str)

        # Proactive remark text
        proactive_remark = Signal(str)

        # Greeting TTS text
        greeting_ready = Signal(str)
        # Screen category (coding, browsing, work, leisure, error, other)
        screen_category_changed = Signal(str)

    def __init__(self, parent: Optional[object] = None) -> None:
        if Signal is not None and QObject is not object:
            super().__init__(parent)  # type: ignore[arg-type]
        self._emotional_state: str = "neutral"
        self._screen_context: str = "unknown"
        self._state: str = "idle"
        logger.info("WidgetBridge initialized")

    # ── convenience emitters (non-Qt thread safe) ──────────────────────

    def on_transcript(self, text: str) -> None:
        """Called from Brain.chat() streaming output."""
        if Signal is not None and hasattr(self, "transcript_chunk"):
            self.transcript_chunk.emit(text)

    def on_emotional_state(self, state: str) -> None:
        """Called from Brain after persona.detect_emotion()."""
        self._emotional_state = state
        if Signal is not None and hasattr(self, "emotional_state_changed"):
            self.emotional_state_changed.emit(state)

    def on_state_change(self, state: str) -> None:
        """Called when buddy state changes."""
        self._state = state
        if Signal is not None and hasattr(self, "state_changed"):
            self.state_changed.emit(state)

    def on_latency(self, asr_to_llm: float, llm_to_tts: float, total_e2e: float) -> None:
        if Signal is not None and hasattr(self, "latency_updated"):
            self.latency_updated.emit(asr_to_llm, llm_to_tts, total_e2e)

    def on_audio_level(self, rms: float) -> None:
        if Signal is not None and hasattr(self, "audio_level"):
            self.audio_level.emit(rms)

    def on_backend_change(self, label: str) -> None:
        if Signal is not None and hasattr(self, "backend_changed"):
            self.backend_changed.emit(label)

    def on_memory_stats(self, core_facts: int, sessions: int) -> None:
        if Signal is not None and hasattr(self, "memory_stats"):
            self.memory_stats.emit(core_facts, sessions)

    def on_activation(self) -> None:
        if Signal is not None and hasattr(self, "activation_requested"):
            self.activation_requested.emit()

    def on_deactivation(self) -> None:
        if Signal is not None and hasattr(self, "deactivation_requested"):
            self.deactivation_requested.emit()

    def on_stance(self, stance_key: str) -> None:
        if Signal is not None and hasattr(self, "stance_expressed"):
            self.stance_expressed.emit(stance_key)

    def on_screen_context(self, title: str) -> None:
        self._screen_context = title
        if Signal is not None and hasattr(self, "screen_context_changed"):
            self.screen_context_changed.emit(title)
        # Emit category for buddy expression
        if Signal is not None and hasattr(self, "screen_category_changed"):
            category = self._classify_title(title)
            self.screen_category_changed.emit(category)

    @staticmethod
    def _classify_title(title: str) -> str:
        """Classify a window title into a context category."""
        if not title:
            return "other"
        try:
            from charlie.screen_context import ScreenContextMonitor
            return ScreenContextMonitor._classify(title)
        except ImportError:
            return "other"

    def on_proactive_remark(self, text: str) -> None:
        if Signal is not None and hasattr(self, "proactive_remark"):
            self.proactive_remark.emit(text)

    def on_greeting(self, text: str) -> None:
        if Signal is not None and hasattr(self, "greeting_ready"):
            self.greeting_ready.emit(text)
    def record_interaction(self) -> None:
        """Track last user voice interaction time for proactive engine."""
        self._last_interaction_time = time.time()
    
